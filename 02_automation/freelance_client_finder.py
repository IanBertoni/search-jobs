"""Freelance client finder: people hiring for Python/RAG/automation gigs -> pitches.

Sources (public and allowed by robots.txt / terms):
- Reddit official API (OAuth app-only token): anonymous JSON answers 403.
  Without REDDIT_CLIENT_ID / REDDIT_CLIENT_SECRET Reddit is skipped.
- GitHub issue search (public API, no token): open issues labelled bounty,
  help wanted or freelance in Python/Rust repos that mention a payment.
- HN monthly "Freelancer? Seeking freelancer?": SEEKING FREELANCER posts.

How to set up Reddit (one time, free):
  1. Log in to Reddit and open https://www.reddit.com/prefs/apps
  2. Click "create another app..." (or "are you a developer? create an app...").
  3. name: freelance-client-finder; type: choose "script";
     redirect uri: http://localhost:8080 (required by the form, never used).
  4. Click "create app". The client id is the short string right under
     "personal use script"; the client secret is the "secret" field.
  5. Paste both in 02_automation/.env (never in .env.example, it is committed):
       REDDIT_CLIENT_ID=<the string under "personal use script">
       REDDIT_CLIENT_SECRET=<the secret>
  6. Run again: python freelance_client_finder.py
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
GITHUB_SEARCH = "https://api.github.com/search/issues"
# one query per label: OR-ing them lets the ~90k "help wanted" issues bury bounties
GITHUB_LABELS = ["bounty", "help wanted", "freelance"]
GITHUB_LANGUAGES = ["python", "rust"]
# no token: 10 searches/minute, so 6 queries with a delay stay under it
GITHUB_DELAY_S = 7
# the label alone doesn't count: the issue text must mention the payment
PAYMENT = re.compile(
    r"\$\s?\d[\d,.]*k?|\b\d[\d,.]*\s?(?:usd|usdc)\b|\bbounty\b", re.IGNORECASE
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

PITCH_PROMPT = """You write a short business pitch from Ian, a freelance developer
in Argentina (stack: Python, FastAPI, RAG and Rust), to someone who posted a
paid gig or a GitHub issue with a bounty. Reply with ONLY the message, in English.
Follow this structure, filling [problem] with the concrete thing they need:
"I saw your post/issue looking for [problem]. I build production systems with
Python, FastAPI, and RAG. I can solve this for you. Here is my GitHub:
github.com/IanBertoni."
Always add one short line saying he also writes Rust, tied to their problem if it fits.
Business tone, not a CV, no bullet points, max 5 lines. Invent nothing about Ian.
Reply with exactly SKIP if it is not paid software work."""

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


def github_queries() -> list[str]:
    return [
        f'is:issue is:open label:"{label}" language:{language}'
        if " " in label
        else f"is:issue is:open label:{label} language:{language}"
        for label in GITHUB_LABELS
        for language in GITHUB_LANGUAGES
    ]


def github_clients(payload: dict) -> list[dict]:
    """Issues from a search payload that mention a payment; the rest is unpaid work."""
    clients = []
    for issue in payload.get("items") or []:
        title = issue.get("title") or ""
        text = f"{title}\n{issue.get('body') or ''}"
        payment = PAYMENT.search(text)
        if not payment:
            continue
        repo = (issue.get("repository_url") or "").removeprefix(
            "https://api.github.com/repos/"
        )
        client = _client("GitHub bounty", title, repo, issue.get("html_url"), text)
        clients.append({**client, "pago": payment.group(0)})
    return clients


def fetch_github(session: requests.Session) -> list[dict]:
    clients = []
    for query in github_queries():
        response = session.get(
            GITHUB_SEARCH,
            params={"q": query, "sort": "created", "per_page": 100},
            headers={"Accept": "application/vnd.github+json", "User-Agent": USER_AGENT},
            timeout=TIMEOUT_S,
        )
        response.raise_for_status()
        clients += github_clients(response.json())
        time.sleep(GITHUB_DELAY_S)
    # the same issue can carry two of the labels
    return list({c["link"]: c for c in clients}.values())


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
    blocks = ["# Freelance clients (Reddit, GitHub bounties, HN Seeking freelancer)\n"]
    for c in clients:
        payment = f"- Pago: {c['pago']}\n" if c.get("pago") else ""
        blocks.append(
            f"## {c['titulo']}\n\n- Fuente: {c['fuente']}\n- Autor: {c['autor']}\n"
            f"- Link: {c['link']}\n{payment}\n```text\n{c['pitch']}\n```\n"
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
    sources = (("reddit", fetch_reddit), ("github", fetch_github), ("hn", fetch_hn))
    for name, fetch in sources:
        try:
            clients += fetch(session)
        except (requests.RequestException, LookupError, KeyError, ValueError) as error:
            logger.error("%s: skipped: %s", name, error)
    pitched = write_pitches(clients, session)
    OUTPUT.write_text(render(pitched), encoding="utf-8")
    print(f"{len(pitched)}/{len(clients)} clientes con pitch -> {OUTPUT}")


if __name__ == "__main__":
    main()
