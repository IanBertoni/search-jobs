"""Cold outreach to founders: HN "Who is hiring?" -> email drafts with Groq.

Only drafts the text: sending stays manual (see CLAUDE.md), no smtplib here.
--gmail-drafts also leaves each email as a Gmail draft (gmail_drafts.py, API scope
gmail.compose): you open it, read it and click Send yourself.
Contract/freelance posts skip Groq: you apply inside the platform, no email needed.
Output: 03_tracking/outreach/founders_outreach.md (full-time emails)
        03_tracking/hn_contract_leads.md (contract/freelance table)
        03_tracking/outreach/gmail_drafted.json (posts already drafted in Gmail)
Usage (from 02_automation/): python cold_outreach.py [--top N] [--gmail-drafts]
                             [--all-levels] [--verbose]
"""

import argparse
import json
import logging
import re
import sys
import time

import requests

from applied import drop_applied, load_applied
from config import (
    GROQ_API_KEY,
    GROQ_BASE_URL,
    GROQ_MODEL,
    REQUEST_DELAY_S,
    TRACKING_DIR,
    setup_logging,
)
from gmail_drafts import create_draft, find_email, gmail_session
from scrapers.getonboard_scraper import html_to_text, whole_words

# Base URL for API requests
ALGOLIA_URL = "https://hn.algolia.com/api/v1"
HN_ITEM_URL = "https://news.ycombinator.com/item?id="
OUTPUT = TRACKING_DIR / "outreach" / "founders_outreach.md"
FREELANCE_OUTPUT = TRACKING_DIR / "hn_contract_leads.md"
GMAIL_DRAFTED = TRACKING_DIR / "outreach" / "gmail_drafted.json"
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
# "Remote (Canada)", "(EEA)", "(US)" tie the job to a region: dropped unless the
# parentheses open it to Argentina; "Remote (US, LATAM)" and timezones stay
REMOTE_REGION = re.compile(r"remote\s*\(([^)]*)\)", re.IGNORECASE)
OPEN_REGION = whole_words(
    [
        "worldwide",
        "global",
        "latam",
        "latin america",
        "argentina",
        "americas",
        "anywhere",
        "utc",
        "gmt",
        "timezone",
        "timezones",
        "time zone",
        "time zones",
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
# candidates who post in "Who is hiring?" by mistake
JOB_SEEKER = re.compile(
    r"looking for (?:[\w/-]+,? ){0,8}?(?:opportunities|roles|positions)\b"
    r"|\bresume:|\bmy resume\b|\bopen to work\b|\bseeking work\b|\bhire me\b",
    re.IGNORECASE,
)
# Ian builds with Python and Claude: a Rust-only stack or a team against AI code
RUST = whole_words(["rust"])
PYTHON = whole_words(["python"])
ANTI_AI = whole_words(["ai generated code", "ai-generated code"])
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
# "SKIP", "SKIP: reason" or "SKIP - reason": only at the start of the reply
SKIP = re.compile(r"SKIP\b[\s:\-–]*(.*)", re.DOTALL)
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
  Rust work at DanAgro Precision (always written exactly like that). For
  backend, data pipeline or distributed systems roles you may add: he runs
  inmo-monitor in production (1,600+ tests, Telegram alerts); its code is
  private, the architecture is public at
  github.com/IanBertoni/inmo-monitor-showcase. Only these facts about Ian,
  invent nothing else.
- Add ONE specific line about what the company does, taken from the post (e.g.
  "been following PostHog's open-source product analytics approach").
- The Role field may be wrong ("Full-Time", "Remote", a city): if so, infer the
  real role from the post text.
- Reply with exactly "SKIP: <reason in a few words>" and no email if the post
  requires a visa, citizenship or residency Ian lacks (US, EU/EEA, UK...),
  asks for PyTorch, training Deep Learning models or a PhD, or if the role is
  NOT hands-on software/application development with Python.
- Building AI agents, multi-agent systems, tool use, RAG, LLM or GenAI products
  in Python IS software development: never SKIP a role for that. "Deep Agents"
  is an agent framework, not Deep Learning.
- Include the GitHub: github.com/IanBertoni
- Say he's in Argentina, fully remote. End asking for a quick chat.
Example tone: "Hey jane, saw you are hiring for a Data Engineer. I don't have a CS
degree, but I built a production RAG pipeline... Here is my GitHub:
github.com/IanBertoni. I'm in Argentina, fully remote. Open to a quick chat?\""""


# ===== STEP 1 - OBSERVE: read the latest HN "Who is hiring?" thread =====


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


# ===== STEP 2 - EVALUATE: deterministic filter, a rejected post never reaches Groq =====


def _dealbreaker(text: str) -> bool:
    years = [int(n) for n in YEARS.findall(text)]
    on_site = ON_SITE.search(text) and not GLOBAL.search(text)
    off_title = OFF_TITLE.search(text.splitlines()[0])
    return bool(
        VISA.search(text)
        or any(not OPEN_REGION.search(r) for r in REMOTE_REGION.findall(text))
        or PROFILE.search(text)
        or on_site
        or off_title
        or JOB_SEEKER.search(text)
        or (RUST.search(text) and not PYTHON.search(text))
        or ANTI_AI.search(text)
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
        skip = SKIP.match(email)
        if skip:
            logger.warning(
                "%s | %s: SKIP de Groq: %s",
                offer["empresa"],
                offer["rol"],
                skip.group(1).strip() or "sin motivo",
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


# ===== STEP 5 - HAND OFF: Gmail drafts, Send stays a human click =====


def subject_for(draft: dict) -> str:
    role = "" if draft["rol"] == "?" else f"{draft['rol']} @ "
    return f"{role}{draft['empresa']} (HN Who is hiring)"


def push_gmail_drafts(
    drafts: list[dict], session: requests.Session, done: set[str]
) -> list[str]:
    """One Gmail draft per email not drafted before; links created (none sent)."""
    created = []
    for d in drafts:
        if d["link"] in done:
            continue
        try:
            create_draft(session, find_email(d["texto"]), subject_for(d), d["email"])
        except (requests.RequestException, RuntimeError, KeyError) as error:
            logger.error("gmail draft %s: %s", d["link"], error)
            continue
        created.append(d["link"])
    return created


def _gmail_drafts(drafts: list[dict]) -> int:
    done = (
        json.loads(GMAIL_DRAFTED.read_text("utf-8")) if GMAIL_DRAFTED.exists() else []
    )
    created = push_gmail_drafts(drafts, gmail_session(), set(done))
    GMAIL_DRAFTED.write_text(json.dumps(done + created), encoding="utf-8")
    return len(created)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--top", type=int, default=TOP_N, help="max emails")
    parser.add_argument(
        "--gmail-drafts", action="store_true", help="also create Gmail drafts"
    )
    parser.add_argument(
        "--all-levels",
        action="store_true",
        help="drop the junior-only rule (same filter as radar_watch.py)",
    )
    parser.add_argument("--verbose", action="store_true", help="log INFO too")
    args = parser.parse_args()
    setup_logging(args.verbose)
    if not GROQ_API_KEY:
        sys.exit("GROQ_API_KEY missing in 02_automation/.env")
    session = requests.Session()
    full_time, freelance = split_by_type(
        drop_applied(
            fetch_offers(session, junior_only=not args.all_levels), load_applied()
        )[: args.top]
    )
    drafts = write_emails(full_time, session)
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(render(drafts), encoding="utf-8")
    FREELANCE_OUTPUT.write_text(render_freelance(freelance), encoding="utf-8")
    print(f"{len(drafts)}/{len(full_time)} emails full-time -> {OUTPUT}")
    print(f"{len(freelance)} leads freelance -> {FREELANCE_OUTPUT}")
    if args.gmail_drafts:
        print(f"{_gmail_drafts(drafts)} borradores nuevos en Gmail (sin enviar)")


if __name__ == "__main__":
    main()
