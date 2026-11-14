"""Freelance client finder: people hiring for Python/RAG/automation gigs -> pitches.

Sources (public and allowed by robots.txt / terms):
- Reddit official API (OAuth app-only token): anonymous JSON answers 403.
  Needs REDDIT_CLIENT_ID / REDDIT_CLIENT_SECRET (a free "script" app at
  reddit.com/prefs/apps); without them Reddit is skipped.
- HN monthly "Freelancer? Seeking freelancer?": SEEKING FREELANCER posts.
Skipped on purpose: Indie Hackers (Cloudflare challenge, /api/ disallowed,
AI crawlers blocked in robots.txt) and X (search needs the paid API).
Only drafts the pitch: sending stays manual (see CLAUDE.md).
Output: 03_tracking/freelance_clients.md
Usage (from 02_automation/): python freelance_client_finder.py [--verbose]
"""

import argparse
import logging
import re
import sys
import time

import requests

from cold_outreach import (
    ALGOLIA_URL,
    HN_ITEM_URL,
    TIMEOUT_S,
    groq_chat,
)
from config import (
    GROQ_API_KEY,
    REDDIT_CLIENT_ID,
    REDDIT_CLIENT_SECRET,
    REQUEST_DELAY_S,
    TRACKING_DIR,
    setup_logging,
)
from scrapers.getonboard_scraper import html_to_text, whole_words

OUTPUT = TRACKING_DIR / "freelance_clients.md"
REDDIT_TOKEN_URL = "https://www.reddit.com/api/v1/access_token"
REDDIT_API = "https://oauth.reddit.com"
REDDIT_URL = "https://www.reddit.com"
# Reddit's API rules ask for a unique, descriptive User-Agent
USER_AGENT = "script:freelance-client-finder:0.1 (by github.com/IanBertoni)"
SUBREDDITS = ["forhire", "Python", "startups", "SaaS"]
REDDIT_QUERY = (
    '"hiring" OR "looking for a dev" OR "looking for a developer"'
    ' OR "need help with python"'
)
# posted by a regular user now, not by whoishiring: found by title
HN_THREAD = "Ask HN: Freelancer? Seeking freelancer?"
SEEKING_FREELANCER = re.compile(r"^\s*seeking freelancer", re.IGNORECASE)

# "[For Hire]" posts are devs offering work, not clients: the title must say hiring
HIRING = re.compile(
    r"\[hiring\]|looking for an? (?:python )?dev(?:eloper)?|need help with python",
    re.IGNORECASE,
)
SKILLS = whole_words(
    [
        "python",
        "rag",
        "fastapi",
        "automation",
        "automate",
        "scrape",
        "scraper",
        "scraping",
        "api",
        "llm",
    ]
)
DEALBREAKER = whole_words(["senior", "us only", "us-only", "usa only", "us based"])
YEARS = re.compile(r"(\d+)\s*\+?\s*(?:years|yrs)", re.IGNORECASE)
MAX_YEARS = 5

PITCH_PROMPT = """You write a short business pitch from Ian, a freelance Python
developer in Argentina, to someone who posted that they need a developer.
Reply with ONLY the message, no subject, in the language of the post.
Follow this structure, filling [X] with the concrete thing they need, in a few words:
"Hola, vi que necesitan [X]. Yo construyo exactamente eso usando Python y RAG.
Ya tengo sistemas en producción. Te dejo mi GitHub (github.com/IanBertoni).
¿Te parece si hablamos 10 minutos?"
Business tone, not a CV, no bullet points, max 4 lines. Invent nothing about Ian.
Reply with exactly SKIP if the post is not someone hiring for software work."""

logger = logging.getLogger(__name__)


def _wanted(text: str) -> bool:
    years = [int(n) for n in YEARS.findall(text)]
    return bool(
        SKILLS.search(text)
        and not DEALBREAKER.search(text)
        and not any(n >= MAX_YEARS for n in years)
    )


def _client(fuente: str, titulo: str, autor: str, link: str, texto: str) -> dict:
    return {
        "fuente": fuente,
        "titulo": titulo,
        "autor": autor,
        "link": link,
        "texto": texto,
    }


def reddit_clients(listing: dict) -> list[dict]:
    """Hiring posts from a Reddit search listing that ask for Ian's skills."""
    children = (listing.get("data") or {}).get("children") or []
    clients = []
    for child in children:
        post = child.get("data") or {}
        title = post.get("title") or ""
        text = f"{title}\n{post.get('selftext') or ''}".strip()
        if HIRING.search(title) and _wanted(text):
            clients.append(
                _client(
                    f"Reddit r/{post.get('subreddit')}",
                    title,
                    post.get("author") or "?",
                    f"{REDDIT_URL}{post.get('permalink', '')}",
                    text,
                )
            )
    return clients


def hn_clients(thread: dict) -> list[dict]:
    """SEEKING FREELANCER top-level comments; SEEKING WORK ones are other devs."""
    clients = []
    for comment in thread.get("children", []):
        # HN never closes <p>, and html_to_text only breaks lines on closing tags
        text = html_to_text((comment.get("text") or "").replace("<p>", "\n"))
        if SEEKING_FREELANCER.search(text) and _wanted(text):
            clients.append(
                _client(
                    "HN Seeking freelancer",
                    text.splitlines()[0],
                    comment.get("author") or "?",
                    f"{HN_ITEM_URL}{comment['id']}",
                    text,
                )
            )
    return clients


def reddit_token(session: requests.Session, client_id: str, secret: str) -> str | None:
    """App-only OAuth token, or None when the credentials aren't set."""
    if not (client_id and secret):
        return None
    response = session.post(
        REDDIT_TOKEN_URL,
        auth=(client_id, secret),
        data={"grant_type": "client_credentials"},
        headers={"User-Agent": USER_AGENT},
        timeout=TIMEOUT_S,
    )
    response.raise_for_status()
    return response.json()["access_token"]


def fetch_reddit(session: requests.Session) -> list[dict]:
    token = reddit_token(session, REDDIT_CLIENT_ID, REDDIT_CLIENT_SECRET)
    if token is None:
        logger.warning("reddit: skipped, REDDIT_CLIENT_ID/SECRET missing in .env")
        return []
    headers = {"Authorization": f"bearer {token}", "User-Agent": USER_AGENT}
    params = {"q": REDDIT_QUERY, "restrict_sr": 1, "sort": "new", "t": "month"}
    clients = []
    for sub in SUBREDDITS:
        time.sleep(REQUEST_DELAY_S)
        response = session.get(
            f"{REDDIT_API}/r/{sub}/search",
            params={**params, "limit": 100},
            headers=headers,
            timeout=TIMEOUT_S,
        )
        response.raise_for_status()
        clients += reddit_clients(response.json())
    return clients


def latest_freelancer_thread(session: requests.Session) -> str:
    """Id of the newest "Freelancer? Seeking freelancer?" story."""
    response = session.get(
        f"{ALGOLIA_URL}/search_by_date",
        params={"query": f'"{HN_THREAD}"', "tags": "story", "hitsPerPage": 10},
        timeout=TIMEOUT_S,
    )
    response.raise_for_status()
    for hit in response.json()["hits"]:
        if hit["title"].startswith(HN_THREAD):
            return hit["objectID"]
    raise LookupError(f"no '{HN_THREAD}' story found")


def fetch_hn(session: requests.Session) -> list[dict]:
    thread_id = latest_freelancer_thread(session)
    time.sleep(REQUEST_DELAY_S)
    response = session.get(f"{ALGOLIA_URL}/items/{thread_id}", timeout=TIMEOUT_S)
    response.raise_for_status()
    return hn_clients(response.json())


def write_pitches(clients: list[dict], session: requests.Session) -> list[dict]:
    """One Groq call per client; failures are logged and skipped, SKIP is dropped."""
    pitched = []
    for client in clients:
        post = (
            f"Source: {client['fuente']}\nAuthor: {client['autor']}\n"
            f"Post:\n{client['texto'][:3000]}"
        )
        try:
            pitch = groq_chat(PITCH_PROMPT, post, session)
        except (requests.RequestException, KeyError, TypeError, ValueError) as error:
            logger.error("%s: %s", client["link"], error)
            continue
        if pitch == "SKIP":
            logger.info("%s: SKIP de Groq", client["link"])
            continue
        pitched.append({**client, "pitch": pitch})
    return pitched


def render(clients: list[dict]) -> str:
    blocks = ["# Freelance clients (Reddit, HN Seeking freelancer)\n"]
    for c in clients:
        blocks.append(
            f"## {c['titulo']}\n\n- Fuente: {c['fuente']}\n- Autor: {c['autor']}\n"
            f"- Link: {c['link']}\n\n```text\n{c['pitch']}\n```\n"
        )
    return "\n".join(blocks)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verbose", action="store_true", help="log INFO too")
    setup_logging(parser.parse_args().verbose)
    if not GROQ_API_KEY:
        sys.exit("GROQ_API_KEY missing in 02_automation/.env")
    session = requests.Session()
    clients = []
    for name, fetch in (("reddit", fetch_reddit), ("hn", fetch_hn)):
        try:
            clients += fetch(session)
        except (requests.RequestException, LookupError, KeyError, ValueError) as error:
            logger.error("%s: skipped: %s", name, error)
    pitched = write_pitches(clients, session)
    OUTPUT.write_text(render(pitched), encoding="utf-8")
    print(f"{len(pitched)}/{len(clients)} clientes con pitch -> {OUTPUT}")


if __name__ == "__main__":
    main()
