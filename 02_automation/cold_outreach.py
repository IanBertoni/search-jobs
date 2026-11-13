"""Cold outreach to founders: HN "Who is hiring?" -> email drafts with Groq.

Only drafts the text: sending stays manual (see CLAUDE.md), no smtplib here.
Output: 03_tracking/outreach/founders_outreach.md
Usage (from 02_automation/): python cold_outreach.py [--top N] [--verbose]
"""

import argparse
import logging
import re
import sys
import time

import requests

from config import (
    GROQ_API_KEY,
    GROQ_BASE_URL,
    GROQ_MODEL,
    REQUEST_DELAY_S,
    TRACKING_DIR,
    setup_logging,
)
from scrapers.getonboard_scraper import html_to_text, whole_words

ALGOLIA_URL = "https://hn.algolia.com/api/v1"
HN_ITEM_URL = "https://news.ycombinator.com/item?id="
OUTPUT = TRACKING_DIR / "outreach" / "founders_outreach.md"
TIMEOUT_S = 60
TOP_N = 20
MAX_TOKENS = 4096  # gpt-oss reasons before answering
MAX_YEARS = 5  # "5+ years" or more counts as senior
logger = logging.getLogger(__name__)

# at least one of these: the roles Ian actually fits
TARGET_ROLE = whole_words(
    ["python", "fastapi", "rag", "backend", "full-stack", "full stack", "llm",
     "data engineer", "software engineer", "ai engineer"]
)
CLOSED = whole_words(["no longer", "closed", "filled"])
# dealbreakers: dropped before Groq, no email is worth these
VISA = whole_words(
    ["us only", "us-only", "us citizen", "us citizens", "us citizenship",
     "u.s. citizen", "u.s. citizenship", "green card", "h1b", "h-1b",
     "defense eligibility", "u.s. defense"]
)
ON_SITE = whole_words(
    ["uk", "london", "europe", "eu", "ireland", "germany", "hybrid"]
)
GLOBAL = whole_words(["worldwide", "global", "latam"])  # lifts ON_SITE
PROFILE = whole_words(
    ["phd", "master's", "master’s", "research scientist", "research engineer",
     "deep learning", "pytorch", "cuda", "transformers",
     "senior", "lead", "staff", "principal", "director"]
)
OFF_TITLE = whole_words(["designer", "sales", "marketing", "product manager"])
YEARS = re.compile(r"(\d+)\s*\+?\s*(?:years|yrs)", re.IGNORECASE)
REMOTE = whole_words(["remote", "worldwide", "latam"])
LATAM = whole_words(
    ["latam", "latin america", "south america", "americas", "argentina"]
)
DANAGRO = re.compile(r"danagro precision", re.IGNORECASE)
AI_TONE = re.compile(
    r"I am writing to|express my interest|I hope this (?:email|message) finds you",
    re.IGNORECASE,
)

SYSTEM_PROMPT = """You write cold emails from Ian, a Data & AI Engineer in Argentina,
to startup founders hiring on Hacker News. Reply with ONLY the email body, no subject.
Rules:
- Under 5 lines. Short, direct, slightly messy and informal, like a real person
  typing fast. Lowercase starts or a missing comma are fine.
- It must NOT sound like AI. Forbidden: "I am writing to express...", "I hope this
  email finds you well", "passionate", "excited to", "leverage", bullet points.
- Start with "Hey <author>," using the HN username given, then name the role.
- Mention: no CS degree, but built a production RAG pipeline in Python, and
  Rust work at DanAgro Precision (always written exactly like that). Only these
  facts about Ian, invent nothing else.
- Add ONE specific line about what the company does, taken from the post (e.g.
  "been following PostHog's open-source product analytics approach").
- The Role field may be wrong ("Full-Time", "Remote", a city): if so, infer the
  real role from the post text.
- Reply with exactly SKIP and nothing else (no email) if the post mentions visa
  or citizenship restrictions, asks for PyTorch, Deep Learning or a PhD, or if
  the role is NOT software/application development with Python.
- Include the GitHub: github.com/IanBertoni
- Say he's in Argentina, fully remote. End asking for a quick chat.
Example tone: "Hey jane, saw you are hiring for a Data Engineer. I don't have a CS
degree, but I built a production RAG pipeline... Here is my GitHub:
github.com/IanBertoni. I'm in Argentina, fully remote. Open to a quick chat?\""""


def latest_hiring_thread(session: requests.Session) -> str:
    """Id of the newest "Ask HN: Who is hiring?" (whoishiring also posts others)."""
    response = session.get(
        f"{ALGOLIA_URL}/search_by_date",
        params={"tags": "story,author_whoishiring", "hitsPerPage": 10},
        timeout=TIMEOUT_S,
    )
    response.raise_for_status()
    for hit in response.json()["hits"]:
        if hit["title"].startswith("Ask HN: Who is hiring?"):
            return hit["objectID"]
    raise LookupError("no 'Who is hiring?' thread in the latest whoishiring stories")


def _dealbreaker(text: str) -> bool:
    years = [int(n) for n in YEARS.findall(text)]
    on_site = ON_SITE.search(text) and not GLOBAL.search(text)
    off_title = OFF_TITLE.search(text.splitlines()[0])
    return bool(
        VISA.search(text) or PROFILE.search(text) or on_site or off_title
        or any(n >= MAX_YEARS for n in years)
    )


def _offer(comment: dict) -> dict | None:
    # HN never closes <p>, and html_to_text only breaks lines on closing tags
    text = html_to_text((comment.get("text") or "").replace("<p>", "\n"))
    if (
        not text
        or text.startswith("UPDATE")  # "UPDATE: no longer hiring" banners
        or CLOSED.search(text)
        or not (REMOTE.search(text) and TARGET_ROLE.search(text))
        or _dealbreaker(text)
    ):
        return None
    # HN convention: first line is "Company | Role | Location | ..."
    fields = [f.strip() for f in text.splitlines()[0].split("|")]
    remote = "Remote/LATAM" if LATAM.search(text) else "Remote"
    return {
        "empresa": fields[0],
        "rol": fields[1] if len(fields) > 1 else "?",
        "remoto": remote,
        "autor": comment.get("author") or "there",
        "link": f"{HN_ITEM_URL}{comment['id']}",
        "texto": text,
    }


def parse_offers(thread: dict) -> list[dict]:
    """Top-level comments only: replies are questions, not job posts."""
    offers = (_offer(c) for c in thread.get("children", []))
    return [o for o in offers if o]


def fetch_offers(session: requests.Session) -> list[dict]:
    thread_id = latest_hiring_thread(session)
    time.sleep(REQUEST_DELAY_S)
    response = session.get(f"{ALGOLIA_URL}/items/{thread_id}", timeout=TIMEOUT_S)
    response.raise_for_status()
    return parse_offers(response.json())


def ask_groq(offer: dict, session: requests.Session) -> str:
    post = (
        f"HN username: {offer['autor']}\nCompany: {offer['empresa']}\n"
        f"Role: {offer['rol']}\nPost:\n{offer['texto'][:3000]}"
    )
    response = session.post(
        f"{GROQ_BASE_URL}/chat/completions",
        headers={"Authorization": f"Bearer {GROQ_API_KEY}"},
        json={
            "model": GROQ_MODEL,
            "max_completion_tokens": MAX_TOKENS,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": post},
            ],
        },
        timeout=TIMEOUT_S,
    )
    if not response.ok:
        raise requests.HTTPError(f"{response.status_code}: {response.text[:300]}")
    return response.json()["choices"][0]["message"]["content"].strip()


def write_emails(offers: list[dict], session: requests.Session) -> list[dict]:
    """One Groq call per offer; a failed offer is logged and skipped."""
    drafts = []
    for offer in offers:
        try:
            email = ask_groq(offer, session)
        except (requests.RequestException, KeyError, TypeError, ValueError) as error:
            logger.error("%s | %s: %s", offer["empresa"], offer["rol"], error)
            continue
        if email == "SKIP":
            logger.info("%s | %s: SKIP de Groq (rol o restricción)", offer["empresa"], offer["rol"])
            continue
        # the model lowercases it to sound casual; the company name must stay right
        email = DANAGRO.sub("DanAgro Precision", email)
        drafts.append({**offer, "email": email})
    return drafts


def render(drafts: list[dict]) -> str:
    blocks = ["# Cold outreach a fundadores (HN Who is hiring)\n"]
    for d in drafts:
        # the prompt forbids it, but the model may still slip into AI tone
        flag = "**REVISAR: suena a IA**\n\n" if AI_TONE.search(d["email"]) else ""
        blocks.append(
            f"## {d['empresa']} — {d['rol']}\n\n"
            f"- Post: {d['link']}\n- Remoto: {d['remoto']}\n\n"
            f"{flag}```text\n{d['email']}\n```\n"
        )
    return "\n".join(blocks)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--top", type=int, default=TOP_N, help="max emails")
    parser.add_argument("--verbose", action="store_true", help="log INFO too")
    args = parser.parse_args()
    setup_logging(args.verbose)
    if not GROQ_API_KEY:
        sys.exit("GROQ_API_KEY missing in 02_automation/.env")
    session = requests.Session()
    offers = fetch_offers(session)[: args.top]
    drafts = write_emails(offers, session)
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(render(drafts), encoding="utf-8")
    print(f"{len(drafts)}/{len(offers)} emails -> {OUTPUT}")


if __name__ == "__main__":
    main()
