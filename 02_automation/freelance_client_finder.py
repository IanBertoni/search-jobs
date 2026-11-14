"""Freelance client finder: paid Python gigs, contracts and bounties -> pitches.

Sources (public and allowed by robots.txt / terms):
- Remotive RSS (software-development, data): contract/freelance items only.
  robots.txt disallows /api/*, so not the JSON API; ~4 requests/day allowed.
- Get on Board public API: remote jobs with modality Freelance.
- Remote OK API + We Work Remotely RSS + HN "Who is hiring?" contracts,
  through freelance_radar.
- GitHub issue search (public API, no token): open issues labelled bounty,
  help wanted, freelance or "💎 Bounty" in Python/Rust repos, filtered
  against bounty-scam patterns (see GITHUB RULES below).
- Algora, through GitHub: Algora has no working public API (docs.algora.io is
  gone and its legacy /api/trpc/bounty.list always answers an empty list),
  but its bot labels each bounty issue "💎 Bounty" plus the amount ("$150").
- HN monthly "Freelancer? Seeking freelancer?": SEEKING FREELANCER posts.
Removed / skipped on purpose:
- Reddit: its Responsible Builder Policy needs written approval even for
  non-commercial API use; using it without approval breaks its terms.
- Indie Hackers (Cloudflare challenge, /api/ disallowed, AI crawlers blocked
  in robots.txt) and X (search needs the paid API).
Only drafts the pitch: sending stays manual (see CLAUDE.md).
Output: 03_tracking/freelance_leads.md
Usage (from 02_automation/): python freelance_client_finder.py [--verbose]
"""

import argparse
import logging
import re
import sys
import time
import xml.etree.ElementTree as ET

import requests

import freelance_radar
from cold_outreach import (
    ALGOLIA_URL,
    HN_ITEM_URL,
    TIMEOUT_S,
    groq_chat,
)
from config import (
    GROQ_API_KEY,
    REQUEST_DELAY_S,
    TRACKING_DIR,
    setup_logging,
)
from scrapers.getonboard_scraper import _fetch_source, html_to_text, whole_words

OUTPUT = TRACKING_DIR / "freelance_leads.md"
USER_AGENT = "freelance-client-finder/0.2 (github.com/IanBertoni)"

# ===== GITHUB RULES: scam patterns seen in the 2026-11-14 runs =====
GITHUB_API = "https://api.github.com"
GITHUB_SEARCH = f"{GITHUB_API}/search/issues"
GITHUB_HEADERS = {"Accept": "application/vnd.github+json", "User-Agent": USER_AGENT}
ALGORA_LABEL = "\U0001f48e Bounty"  # "💎 Bounty", added by the Algora bot
# one query per label: OR-ing them lets the ~90k "help wanted" issues bury bounties
GITHUB_LABELS = ["bounty", "help wanted", "freelance", ALGORA_LABEL]
GITHUB_LANGUAGES = ["python", "rust"]
# no token: 10 searches/minute (search) and 60 requests/hour (repo info)
GITHUB_DELAY_S = 7
# USD only: "$500" or "500 USD"; USDC escrow is the farms' currency
PAYMENT = re.compile(
    r"\$\s?(\d[\d,]*(?:\.\d+)?)(k?)\b|\b(\d[\d,]*(?:\.\d+)?)()\s?usd\b",
    re.IGNORECASE,
)
MIN_PAYMENT_USD = 50  # "$2 site credit" issues are bait, not clients
MIN_ALGORA_USD = 100  # Algora bounties must pay more than this
MAX_PER_REPO = 2  # a single farm repo filled half the list
MIN_STARS = 50
MAX_OPEN_BOUNTIES = 100  # more open bounty issues than this = bounty farm
# checked against the repo name and the issue title
BLACKLIST = re.compile(
    r"bounty-plaza|escrowed|bot-trap|honeypot|trap-benchmark", re.IGNORECASE
)

# ===== HN "Seeking freelancer" =====
# posted by a regular user now, not by whoishiring: found by title
HN_THREAD = "Ask HN: Freelancer? Seeking freelancer?"
SEEKING_FREELANCER = re.compile(r"^\s*seeking freelancer", re.IGNORECASE)
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
SENIOR = whole_words(["senior", "sr", "lead", "staff", "principal"])
DEALBREAKER = whole_words(
    ["senior", "lead", "us only", "us-only", "usa only", "us based"]
)
YEARS = re.compile(r"(\d+)\s*\+?\s*(?:years|yrs)", re.IGNORECASE)
MAX_YEARS = 5

# ===== Job boards =====
REMOTIVE_FEEDS = [
    "https://remotive.com/remote-jobs/feed/software-development",
    "https://remotive.com/remote-jobs/feed/data",
]
REMOTIVE_TYPES = {"contract", "freelance"}
GETONBOARD_QUERIES = ["python", "llm", "automation"]
GETONBOARD_FREELANCE = "3"  # /api/v0/modalities: 3 = Freelance

PITCH_PROMPT = """You write a short pitch from Ian, a freelance Python developer
in Argentina, offering his services as a contractor to someone who posted a paid
gig, a contract role or a GitHub issue with a bounty.
Reply with ONLY the message, in English, exactly 3 lines:
1. The concrete thing they need, in a few words, taken from the post.
2. How Ian would deliver it as a contractor with his stack: Python, FastAPI and RAG.
3. His GitHub (github.com/IanBertoni) and a short call to action.
Professional and direct, no bullet points, not a CV. Invent nothing about the client.
Reply with exactly SKIP if it is not paid software work."""

logger = logging.getLogger(__name__)


def _client(fuente: str, titulo: str, autor: str, link: str, texto: str) -> dict:
    return {
        "fuente": fuente,
        "titulo": titulo,
        "autor": autor,
        "link": link,
        "texto": texto,
    }


def github_queries() -> list[str]:
    base = "is:issue is:open archived:false"
    return [
        f'{base} label:"{label}" language:{language}'
        if " " in label
        else f"{base} label:{label} language:{language}"
        for label in GITHUB_LABELS
        for language in GITHUB_LANGUAGES
    ]


def _payment(text: str, minimum: float) -> str | None:
    """First USD amount of at least ``minimum`` in the text, as written."""
    for match in PAYMENT.finditer(text):
        number, thousands = match.group(1) or match.group(3), match.group(2) or ""
        amount = float(number.replace(",", "")) * (1000 if thousands else 1)
        if amount >= minimum:
            return match.group(0).strip()
    return None


def github_clients(payload: dict) -> list[dict]:
    """Cheap filters, no extra requests: USD amount, blacklist, MAX_PER_REPO."""
    clients, per_repo = [], {}
    for issue in payload.get("items") or []:
        title = issue.get("title") or ""
        repo = (issue.get("repository_url") or "").removeprefix(f"{GITHUB_API}/repos/")
        labels = [label.get("name") or "" for label in issue.get("labels") or []]
        algora = ALGORA_LABEL in labels
        # Algora puts the amount in a label ("$150"), not in the issue text
        text = f"{title}\n{issue.get('body') or ''}\n{' '.join(labels)}"
        # Algora needs MORE than MIN_ALGORA_USD: the next cent is the minimum
        payment = _payment(text, MIN_ALGORA_USD + 0.01 if algora else MIN_PAYMENT_USD)
        if not payment or BLACKLIST.search(f"{repo} {title}"):
            continue
        per_repo[repo] = per_repo.get(repo, 0) + 1
        if per_repo[repo] > MAX_PER_REPO:
            continue
        fuente = "Algora (GitHub)" if algora else "GitHub bounty"
        client = _client(fuente, title, repo, issue.get("html_url"), text)
        clients.append({**client, "pago": payment})
    return clients


def repo_ok(info: dict, open_bounties: int) -> bool:
    """Real repo: not archived, MIN_STARS+, not a farm of open bounty issues."""
    return (
        not info.get("archived")
        and (info.get("stargazers_count") or 0) >= MIN_STARS
        and open_bounties <= MAX_OPEN_BOUNTIES
    )


def _get(session: requests.Session, url: str, params: dict | None = None) -> dict:
    response = session.get(
        url, params=params, headers=GITHUB_HEADERS, timeout=TIMEOUT_S
    )
    response.raise_for_status()
    return response.json()


def _repo_passes(session: requests.Session, repo: str) -> bool:
    info = _get(session, f"{GITHUB_API}/repos/{repo}")
    if not repo_ok(info, 0):  # stars/archived first: costs no search request
        return False
    time.sleep(GITHUB_DELAY_S)
    query = f'repo:{repo} is:issue is:open label:bounty,"{ALGORA_LABEL}"'
    count = _get(session, GITHUB_SEARCH, {"q": query, "per_page": 1})
    return repo_ok(info, count.get("total_count") or 0)


def fetch_github(session: requests.Session) -> list[dict]:
    items = []
    for query in github_queries():
        payload = _get(
            session, GITHUB_SEARCH, {"q": query, "sort": "created", "per_page": 100}
        )
        items += payload.get("items") or []
        time.sleep(GITHUB_DELAY_S)
    # the same issue can carry two of the labels; filter once so the repo cap holds
    unique = {item.get("html_url"): item for item in items}
    candidates = github_clients({"items": list(unique.values())})
    passed = {}
    for repo in dict.fromkeys(c["autor"] for c in candidates):
        passed[repo] = _repo_passes(session, repo)
        if not passed[repo]:
            logger.info("github: %s dropped (stars, archived or bounty farm)", repo)
    return [c for c in candidates if passed[c["autor"]]]


def _wanted(text: str) -> bool:
    years = [int(n) for n in YEARS.findall(text)]
    return bool(
        SKILLS.search(text)
        and not DEALBREAKER.search(text)
        and not any(n >= MAX_YEARS for n in years)
    )


def _job_wanted(title: str, text: str) -> bool:
    """Seniority only counts in the title: descriptions mention senior peers."""
    years = [int(n) for n in YEARS.findall(text)]
    return bool(
        SKILLS.search(f"{title}\n{text}")
        and not SENIOR.search(title)
        and not any(n >= MAX_YEARS for n in years)
    )


def remotive_clients(xml: bytes) -> list[dict]:
    try:
        items = list(ET.fromstring(xml).iter("item"))
    except ET.ParseError as error:
        logger.error("remotive: feed skipped: %s", error)
        return []
    clients = []
    for item in items:
        title = (item.findtext("title") or "").strip()
        text = html_to_text(item.findtext("description"))
        if (
            item.findtext("type") in REMOTIVE_TYPES
            and freelance_radar.location_ok(item.findtext("location") or "")
            and _job_wanted(title, text)
        ):
            company = (item.findtext("company") or "?").strip()
            link = item.findtext("link") or "?"
            clients.append(_client("Remotive", title, company, link, text))
    return clients


def getonboard_clients(jobs: list[dict]) -> list[dict]:
    """Remote Freelance jobs, once each (the queries overlap)."""
    clients = {}
    for job in jobs:
        attrs = job.get("attributes") or {}
        title = attrs.get("title") or ""
        text = html_to_text(attrs.get("description"))
        modality = ((attrs.get("modality") or {}).get("data") or {}).get("id")
        if (
            str(modality) != GETONBOARD_FREELANCE
            or not attrs.get("remote")
            or not _job_wanted(title, text)
        ):
            continue
        company = (attrs.get("company") or {}).get("data") or {}
        name = (company.get("attributes") or {}).get("name") or "?"
        link = (job.get("links") or {}).get("public_url") or "?"
        clients[link] = _client("Get on Board", title, name, link, text)
    return list(clients.values())


def radar_clients(leads: list[dict]) -> list[dict]:
    """freelance_radar leads: already contract, keyword and location filtered."""
    return [
        {
            **_client(
                lead["fuente"],
                lead["titulo"],
                lead["empresa"],
                lead["link"],
                f"{lead['titulo']} at {lead['empresa']}. Rate: {lead['tarifa']}",
            ),
            "pago": lead["tarifa"],
        }
        for lead in leads
        if not SENIOR.search(lead["titulo"])
    ]


def fetch_remotive(session: requests.Session) -> list[dict]:
    clients = []
    for url in REMOTIVE_FEEDS:
        response = session.get(url, timeout=TIMEOUT_S)
        response.raise_for_status()
        clients += remotive_clients(response.content)
        time.sleep(REQUEST_DELAY_S)
    return list({c["link"]: c for c in clients}.values())


def fetch_getonboard(session: requests.Session) -> list[dict]:
    jobs = []
    for query in GETONBOARD_QUERIES:
        params = {"query": query}
        jobs += _fetch_source(session, "search/jobs", params, time.sleep) or []
        time.sleep(REQUEST_DELAY_S)
    return getonboard_clients(jobs)


def fetch_radar(session: requests.Session) -> list[dict]:
    return radar_clients(freelance_radar.fetch_all(session))


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
    blocks = ["# Freelance leads (job boards, GitHub bounties, HN) con pitch\n"]
    for c in clients:
        payment = f"- Pago: {c['pago']}\n" if c.get("pago") not in (None, "?") else ""
        blocks.append(
            f"## {c['titulo']}\n\n- Empresa/Fuente: {c['autor']} ({c['fuente']})\n"
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
    sources = (
        ("remotive", fetch_remotive),
        ("getonboard", fetch_getonboard),
        ("radar", fetch_radar),
        ("github", fetch_github),
        ("hn", fetch_hn),
    )
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
