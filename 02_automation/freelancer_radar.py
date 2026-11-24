"""Freelancer.com radar: low-competition Python/data projects from the public API -> pitches.

Freelancer.com has an official JSON API (no login needed for active projects), so
this uses it instead of scraping. Hard filter: under 15 bids, budget >= USD 50
(USD 15/h for hourly), a profile keyword and no senior/lead/UX-UI title.
Only drafts the pitch: bidding stays manual (see CLAUDE.md).
If the API starts answering 403/429, see "Freelance Radar" in CLAUDE.md.
Output: 03_tracking/freelancer_leads.md
Usage (from 02_automation/): python freelancer_radar.py [--verbose]
"""

import argparse
import logging
import sys
import time
from datetime import UTC, datetime

import requests

from cold_outreach import groq_chat
from config import GROQ_API_KEY, TRACKING_DIR, setup_logging
from freelance_client_finder import PITCH_PROMPT
from scrapers.getonboard_scraper import TIMEOUT_S, html_to_text, whole_words

API_URL = "https://www.freelancer.com/api/projects/0.1/projects/active/"
PROJECT_URL = "https://www.freelancer.com/projects/"
# "python" alone returns ~130 projects, nearly all past 15 bids: widen the net
QUERIES = ("python", "scraping", "automation", "data", "rag")
PAGE_SIZE = 100
MAX_PAGES = 3  # per query; "data" has ~800 active projects
API_DELAY_S = 3  # between Freelancer.com requests
OUTPUT = TRACKING_DIR / "freelancer_leads.md"

MAX_BIDS = 15  # exclusive: 15 bids is already too crowded
MIN_FIXED_USD = 50
MIN_HOURLY_USD = 15  # a $50/h floor would drop every hourly project
KEYWORDS = whole_words(["python", "scraping", "scraper", "automation", "data", "rag"])
# exact Freelancer.com skill names (job_details=true): under 15 bids, "data" and
# "automation" are mostly data entry, sales and smart-home installs
DEV_SKILLS = {
    "python", "web scraping", "data extraction", "data processing", "data mining",
    "automation", "ai automation", "api development", "api integration", "n8n",
    "software development", "ai development", "machine learning (ml)", "data science",
    "artificial intelligence", "natural language processing", "chatbot", "sql",
    "etl", "django", "flask", "fastapi", "selenium", "scrapy",
}  # fmt: skip
# title only, and "lead" only as a role: "lead generation" scraping is real work
EXCLUDED = whole_words(
    ["senior", "sr", "tech lead", "team lead", "lead developer", "lead engineer"]
    + ["ux", "ui/ux", "ux/ui"]
)

# Groq free tier is tokens-per-minute bound: pace calls, wait out a 429 once
GROQ_DELAY_S = 3
RATE_LIMIT_WAIT_S = 30
MAX_POST_CHARS = 3000

logger = logging.getLogger(__name__)


def _project(raw: dict) -> dict:
    """KeyError/TypeError/AttributeError on a malformed project."""
    budget, currency = raw["budget"], raw["currency"]
    rate = float(currency["exchange_rate"])
    low, high = float(budget.get("minimum") or 0), float(budget.get("maximum") or 0)
    submitted = datetime.fromtimestamp(raw["time_submitted"], UTC)
    return {
        "id": raw["id"],
        "titulo": raw["title"].strip(),
        "link": PROJECT_URL + raw["seo_url"],
        "descripcion": html_to_text(
            raw.get("description") or raw.get("preview_description")
        ),
        "tipo": raw.get("type") or "fixed",
        "moneda": currency["code"],
        "min": low,
        "max": high,
        "min_usd": low * rate,
        "max_usd": high * rate,
        "bids": int(raw["bid_stats"]["bid_count"]),
        "publicado": submitted.date().isoformat(),
        "skills": [job["name"] for job in raw.get("jobs") or []],
    }


def parse_projects(payload: dict) -> list[dict]:
    """Projects of one API page; ValueError if the API answered an error."""
    if payload.get("status") != "success":
        raise ValueError(f"freelancer api: {payload.get('message') or payload}")
    projects = []
    for raw in payload.get("result", {}).get("projects") or []:
        try:
            projects.append(_project(raw))
        except (KeyError, TypeError, ValueError, AttributeError) as error:
            logger.warning("malformed project skipped: %r", error)
    return projects


def _passes(project: dict) -> bool:
    floor = MIN_HOURLY_USD if project["tipo"] == "hourly" else MIN_FIXED_USD
    return (
        project["bids"] < MAX_BIDS
        and project["min_usd"] >= floor
        and bool(KEYWORDS.search(f"{project['titulo']} {project['descripcion']}"))
        and not EXCLUDED.search(project["titulo"])
        and any(skill.lower() in DEV_SKILLS for skill in project["skills"])
    )


def candidates(projects: list[dict]) -> list[dict]:
    """Hard filter, fewest bids first (ties: newest first)."""
    kept = [p for p in projects if _passes(p)]
    return sorted(kept, key=lambda p: (p["bids"], -p["id"]))


def budget_label(project: dict) -> str:
    unit = "/h" if project["tipo"] == "hourly" else ""

    def span(low: float, high: float) -> str:
        return f"{low:,.0f}-{high:,.0f}" if high else f"{low:,.0f}+"

    label = f"{project['moneda']} {span(project['min'], project['max'])}{unit}"
    if project["moneda"] != "USD":
        label += f" (~USD {span(project['min_usd'], project['max_usd'])})"
    return label


def _pitch(project: dict, session: requests.Session) -> str:
    post = (
        f"Source: Freelancer.com\nTitle: {project['titulo']}\n"
        f"Budget: {budget_label(project)}\n"
        f"Post:\n{project['descripcion'][:MAX_POST_CHARS]}"
    )
    for attempt in range(2):
        try:
            return groq_chat(PITCH_PROMPT, post, session)
        except requests.HTTPError as error:
            if attempt or not str(error).startswith("429"):
                raise
            logger.warning("groq rate limit, waiting %ss", RATE_LIMIT_WAIT_S)
            time.sleep(RATE_LIMIT_WAIT_S)
    raise AssertionError("unreachable")


def write_pitches(projects: list[dict], session: requests.Session) -> list[dict]:
    """One Groq call per project; failures are logged and skipped, SKIP is dropped."""
    pitched = []
    for project in projects:
        try:
            pitch = _pitch(project, session)
        except (requests.RequestException, KeyError, TypeError, ValueError) as error:
            logger.error("%s: %s", project["link"], error)
            continue
        finally:
            time.sleep(GROQ_DELAY_S)
        if pitch == "SKIP":
            logger.info("%s: SKIP de Groq", project["link"])
            continue
        pitched.append({**project, "pitch": pitch})
    return pitched


def render(projects: list[dict]) -> str:
    def cell(value) -> str:
        return str(value).replace("|", "/").strip().replace("\n", "<br>")

    header = [
        "# Freelancer.com leads (< 15 bids) con pitch\n",
        (
            f"Generado: {datetime.now(UTC):%Y-%m-%d %H:%M} UTC. "
            "Fuente: API pública de Freelancer.com. El bid se manda a mano.\n"
        ),
        "| Título | Presupuesto | Bids | Publicado | Link | Pitch |",
        "|---|---|---|---|---|---|",
    ]
    rows = [
        "| "
        + " | ".join(
            cell(v)
            for v in (
                p["titulo"],
                budget_label(p),
                p["bids"],
                p["publicado"],
                p["link"],
                p["pitch"],
            )
        )
        + " |"
        for p in projects
    ]
    return "\n".join(header + rows) + "\n"


def fetch_projects(
    session: requests.Session, query: str, page_size: int = PAGE_SIZE
) -> list[dict]:
    """Every page of one query, up to MAX_PAGES, API_DELAY_S apart."""
    projects: list[dict] = []
    for page in range(MAX_PAGES):
        if page:
            time.sleep(API_DELAY_S)
        params = {
            "query": query,
            "limit": page_size,
            "offset": page * page_size,
            "job_details": "true",
            "full_description": "true",
        }
        response = session.get(API_URL, params=params, timeout=TIMEOUT_S)
        response.raise_for_status()
        payload = response.json()
        projects += parse_projects(payload)
        total = payload.get("result", {}).get("total_count") or 0
        if (page + 1) * page_size >= total:
            break
    return projects


def fetch_all(session: requests.Session) -> list[dict]:
    """All queries, deduped by id; a failing query is logged and skipped."""
    seen: dict[int, dict] = {}
    for i, query in enumerate(QUERIES):
        if i:
            time.sleep(API_DELAY_S)
        try:
            for project in fetch_projects(session, query):
                seen.setdefault(project["id"], project)
        except (requests.RequestException, ValueError) as error:
            logger.error("freelancer %r: skipped: %s", query, error)
            if "403" in str(error) or "429" in str(error):
                logger.error("API blocked? see 'Freelance Radar' in CLAUDE.md")
    return list(seen.values())


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verbose", action="store_true", help="log INFO too")
    setup_logging(parser.parse_args().verbose)
    if not GROQ_API_KEY:
        sys.exit("GROQ_API_KEY missing in 02_automation/.env")
    session = requests.Session()
    projects = fetch_all(session)
    shortlist = candidates(projects)
    pitched = write_pitches(shortlist, session)
    OUTPUT.write_text(render(pitched), encoding="utf-8")
    print(
        f"{len(projects)} proyectos, {len(shortlist)} pasan el filtro, "
        f"{len(pitched)} con pitch -> {OUTPUT}"
    )


if __name__ == "__main__":
    main()
