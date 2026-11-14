"""Cold outreach to founders: HN "Who is hiring?" -> email drafts with Groq.

Only drafts the text: sending stays manual (see CLAUDE.md), no smtplib here.
Contract/freelance posts skip Groq: you apply inside the platform, no email needed.
Output: 03_tracking/outreach/founders_outreach.md (full-time emails)
        03_tracking/freelance_leads.md (contract/freelance table)
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

# Base URL for API requests
ALGOLIA_URL = "https://hn.algolia.com/api/v1"
HN_ITEM_URL = "https://news.ycombinator.com/item?id="
OUTPUT = TRACKING_DIR / "outreach" / "founders_outreach.md"
FREELANCE_OUTPUT = TRACKING_DIR / "freelance_leads.md"
TIMEOUT_S = 60
TOP_N = 20
MAX_TOKENS = 4096  # gpt-oss reasons before answering
MAX_YEARS = 3  # "3+ years" or more counts as senior
logger = logging.getLogger(__name__)

# ===== EVALUATION RULES: hard filters, applied in _offer() before any LLM call =====
# at least one of these: the roles Ian actually fits
TARGET_ROLE = whole_words(
    [
        "python",
        "fastapi",
        "rag",
        "backend",
        "full-stack",
        "full stack",
        "llm",
        "data engineer",
        "software engineer",
        "ai engineer",
    ]
)
CLOSED = whole_words(["no longer", "closed", "filled"])
# dealbreakers: dropped before Groq, no email is worth these
VISA = whole_words(
    [
        "us only",
        "us-only",
        "us citizen",
        "us citizens",
        "us citizenship",
        "u.s. citizen",
        "u.s. citizenship",
        "green card",
        "h1b",
        "h-1b",
        "defense eligibility",
        "u.s. defense",
    ]
)
ON_SITE = whole_words(["uk", "london", "europe", "eu", "ireland", "germany", "hybrid"])
GLOBAL = whole_words(["worldwide", "global", "latam"])  # lifts ON_SITE
PROFILE = whole_words(
    [
        "phd",
        "master's",
        "master’s",
        "research scientist",
        "research engineer",
        "deep learning",
        "pytorch",
        "cuda",
        "transformers",
        "senior",
        "founding",
        "lead",
        "staff",
        "principal",
        "director",
    ]
)
# must say it's junior: a post that doesn't is assumed to want experience
JUNIOR = whole_words(
    [
        "junior",
        "entry level",
        "entry-level",
        "new grad",
        "trainee",
        "associate",
        "apprentice",
    ]
)
OFF_TITLE = whole_words(["designer", "sales", "marketing", "product manager"])
YEARS = re.compile(r"(\d+)\s*\+?\s*(?:years|yrs)", re.IGNORECASE)
REMOTE = whole_words(["remote", "worldwide", "latam"])
LATAM = whole_words(
    ["latam", "latin america", "south america", "americas", "argentina"]
)
# a post naming full-time stays full-time even if it also takes contractors
CONTRACT = whole_words(
    ["contract", "contractor", "freelance", "part-time", "part time"]
)
FULL_TIME = whole_words(["full-time", "full time", "fulltime"])
BUDGET = re.compile(
    r"\$\s?\d[\d,.]*k?(?:\s*[-–]\s*\$?\d[\d,.]*k?)?"
    r"(?:\s*(?:/|per\s+)\s*(?:hour|hr|h\b|month|mo\b|year|yr))?"
    r"|\d+\s*\+?\s*(?:hours|hrs?)\s*(?:/|per\s+|a\s+)\s*(?:week|wk)",
    re.IGNORECASE,
)
REMOTE_FIRST = whole_words(["remote", "worldwide"])
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


# ===== STEP 1 - OBSERVE: read the latest HN "Who is hiring?" thread =====


def latest_hiring_thread(
    session: requests.Session, title: str = "Ask HN: Who is hiring?"
) -> str:
    """Id of the newest whoishiring story starting with ``title`` (it posts several)."""
    response = session.get(
        f"{ALGOLIA_URL}/search_by_date",
        params={"tags": "story,author_whoishiring", "hitsPerPage": 10},
        timeout=TIMEOUT_S,
    )
    response.raise_for_status()
    for hit in response.json()["hits"]:
        if hit["title"].startswith(title):
            return hit["objectID"]
    raise LookupError(f"no '{title}' thread in the latest whoishiring stories")


# ===== STEP 2 - EVALUATE: deterministic filter, a rejected post never reaches Groq =====


def _dealbreaker(text: str) -> bool:
    years = [int(n) for n in YEARS.findall(text)]
    on_site = ON_SITE.search(text) and not GLOBAL.search(text)
    off_title = OFF_TITLE.search(text.splitlines()[0])
    return bool(
        VISA.search(text)
        or PROFILE.search(text)
        or on_site
        or off_title
        or any(n >= MAX_YEARS for n in years)
    )


def _offer(comment: dict, junior_only: bool = True) -> dict | None:
    # HN never closes <p>, and html_to_text only breaks lines on closing tags
    text = html_to_text((comment.get("text") or "").replace("<p>", "\n"))
    if (
        not text
        or text.startswith("UPDATE")  # "UPDATE: no longer hiring" banners
        or CLOSED.search(text)
        or not (
            REMOTE.search(text)
            and TARGET_ROLE.search(text)
            and (JUNIOR.search(text) or not junior_only)
        )
        or _dealbreaker(text)
    ):
        return None
    # HN convention: first line is "Company | Role | Location | ..."
    fields = [f.strip() for f in text.splitlines()[0].split("|")]
    remote = "Remote/LATAM" if LATAM.search(text) else "Remote"
    freelance = CONTRACT.search(text) and not FULL_TIME.search(text)
    return {
        "empresa": fields[0],
        "rol": fields[1] if len(fields) > 1 else "?",
        "remoto": remote,
        "autor": comment.get("author") or "there",
        "link": f"{HN_ITEM_URL}{comment['id']}",
        "texto": text,
        "tipo": "freelance" if freelance else "full-time",
        "presupuesto": ", ".join(dict.fromkeys(BUDGET.findall(text))) or "?",
    }


def parse_offers(thread: dict, junior_only: bool = True) -> list[dict]:
    """Top-level comments only: replies are questions, not job posts."""
    offers = (_offer(c, junior_only) for c in thread.get("children", []))
    return [o for o in offers if o]


def split_by_type(offers: list[dict]) -> tuple[list[dict], list[dict]]:
    """(full-time -> Groq email, contract/freelance -> leads table, no Groq)."""
    full_time = [o for o in offers if o["tipo"] == "full-time"]
    freelance = [o for o in offers if o["tipo"] == "freelance"]
    return full_time, freelance


def fetch_offers(session: requests.Session, junior_only: bool = True) -> list[dict]:
    thread_id = latest_hiring_thread(session)
    time.sleep(REQUEST_DELAY_S)
    response = session.get(f"{ALGOLIA_URL}/items/{thread_id}", timeout=TIMEOUT_S)
    response.raise_for_status()
    return parse_offers(response.json(), junior_only)


# ===== STEP 3 - ACT: the Groq LLM call that drafts the email =====


def ask_groq(offer: dict, session: requests.Session) -> str:
    post = (
        f"HN username: {offer['autor']}\nCompany: {offer['empresa']}\n"
        f"Role: {offer['rol']}\nPost:\n{offer['texto'][:3000]}"
    )
    return groq_chat(SYSTEM_PROMPT, post, session)


def groq_chat(system: str, user: str, session: requests.Session) -> str:
    """One Groq chat completion; a non-2xx answer raises requests.HTTPError."""
    response = session.post(
        f"{GROQ_BASE_URL}/chat/completions",
        headers={"Authorization": f"Bearer {GROQ_API_KEY}"},
        json={
            "model": GROQ_MODEL,
            "max_completion_tokens": MAX_TOKENS,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        },
        timeout=TIMEOUT_S,
    )
    if not response.ok:
        raise requests.HTTPError(f"{response.status_code}: {response.text[:300]}")
    return response.json()["choices"][0]["message"]["content"].strip()


# ===== STEP 4 - GUARDRAILS on the LLM output: SKIP, name fix, AI-tone flag =====


def write_emails(offers: list[dict], session: requests.Session) -> list[dict]:
    """One Groq call per offer; a failed offer is logged and skipped."""
    drafts = []
    for offer in offers:
        try:
            email = ask_groq(offer, session)
        except (requests.RequestException, KeyError, TypeError, ValueError) as error:
            logger.error("%s | %s: %s", offer["empresa"], offer["rol"], error)
            continue
        # guardrail 1: the LLM itself vetoes the offer (visa, PhD, non-dev role)
        if email == "SKIP":
            logger.info(
                "%s | %s: SKIP de Groq (rol o restricción)",
                offer["empresa"],
                offer["rol"],
            )
            continue
        # guardrail 2: the model lowercases it to sound casual; the company name must stay right
        email = DANAGRO.sub("DanAgro Precision", email)
        drafts.append({**offer, "email": email})
    return drafts


def render(drafts: list[dict]) -> str:
    blocks = ["# Cold outreach a fundadores (HN Who is hiring)\n"]
    for d in drafts:
        # guardrail 3: the prompt forbids it, but the model may still slip into AI tone
        flag = "**REVISAR: suena a IA**\n\n" if AI_TONE.search(d["email"]) else ""
        blocks.append(
            f"## {d['empresa']} — {d['rol']}\n\n"
            f"- Post: {d['link']}\n- Remoto: {d['remoto']}\n\n"
            f"{flag}```text\n{d['email']}\n```\n"
        )
    return "\n".join(blocks)


def render_freelance(leads: list[dict]) -> str:
    # stable sort: "Remote"/"Worldwide" first, LATAM-only after
    ordered = sorted(leads, key=lambda o: not REMOTE_FIRST.search(o["texto"]))
    rows = [
        f"| {o['empresa']} | {o['rol']} | {o['link']} | {o['presupuesto']} |"
        for o in ordered
    ]
    header = [
        "# Freelance / Contract leads (HN Who is hiring)\n",
        "| Empresa | Rol | Link | Presupuesto/Horas |",
        "|---|---|---|---|",
    ]
    return "\n".join(header + rows) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--top", type=int, default=TOP_N, help="max emails")
    parser.add_argument("--verbose", action="store_true", help="log INFO too")
    args = parser.parse_args()
    setup_logging(args.verbose)
    if not GROQ_API_KEY:
        sys.exit("GROQ_API_KEY missing in 02_automation/.env")
    session = requests.Session()
    full_time, freelance = split_by_type(fetch_offers(session)[: args.top])
    drafts = write_emails(full_time, session)
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(render(drafts), encoding="utf-8")
    FREELANCE_OUTPUT.write_text(render_freelance(freelance), encoding="utf-8")
    print(f"{len(drafts)}/{len(full_time)} emails full-time -> {OUTPUT}")
    print(f"{len(freelance)} leads freelance -> {FREELANCE_OUTPUT}")


if __name__ == "__main__":
    main()
