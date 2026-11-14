"""Freelance radar: Remote OK API + We Work Remotely RSS + HN "Who is hiring?" contracts.

Only collects and filters (no Groq, no emails): applying stays manual (see CLAUDE.md).
Remote OK API terms ask for a link back and a source credit: every row keeps both.
Output: 03_tracking/freelance_radar.md
Usage (from 02_automation/): python freelance_radar.py [--verbose]
"""

import argparse
import html
import logging
import time
import xml.etree.ElementTree as ET

import requests

from cold_outreach import BUDGET, CONTRACT, FULL_TIME, fetch_offers, parse_offers
from config import REQUEST_DELAY_S, TRACKING_DIR, setup_logging
from scrapers.getonboard_scraper import TIMEOUT_S, html_to_text, whole_words

REMOTEOK_API = "https://remoteok.com/api"
# the remote-contract-jobs category feed is gone (301 / Cloudflare 403); the main
# feed tags each item <type>Contract</type>
WWR_FEED = "https://weworkremotely.com/remote-jobs.rss"
OUTPUT = TRACKING_DIR / "freelance_radar.md"
WWR_CONTRACT = "Contract"

KEYWORDS = whole_words(
    ["python", "data", "ai", "rag", "fastapi", "automation", "llm", "machine learning"]
)
ALLOWED = whole_words(
    ["remote", "worldwide", "anywhere", "latam", "latin america", "americas"]
)
BLOCKED = whole_words(
    [
        "us only",
        "usa only",
        "u.s. only",
        "us based",
        "us-based",
        "eu only",
        "europe only",
    ]
)

logger = logging.getLogger(__name__)


def location_ok(location: str) -> bool:
    # an empty location on a remote-only board means anywhere
    return not BLOCKED.search(location) and (
        not location.strip() or bool(ALLOWED.search(location))
    )


def _lead(fuente: str, titulo: str, empresa: str, tarifa: str, link: str) -> dict:
    return {
        "fuente": fuente,
        "titulo": titulo,
        "empresa": empresa,
        "tarifa": tarifa,
        "link": link,
    }


def _rate(text: str) -> str:
    return ", ".join(dict.fromkeys(BUDGET.findall(text))) or "?"


def _is_contract(text: str) -> bool:
    return bool(CONTRACT.search(text)) and not FULL_TIME.search(text)


def remoteok_leads(jobs: list[dict]) -> list[dict]:
    """The API's first item is the legal notice, not a job: skipped by the "id" check."""
    leads = []
    for job in jobs:
        if not isinstance(job, dict) or "id" not in job:
            continue
        tags = " ".join(job.get("tags") or [])
        title = job.get("position") or ""
        description = html_to_text(job.get("description"))
        if not (
            KEYWORDS.search(f"{title} {tags}")
            and _is_contract(f"{title} {tags} {description}")
            and location_ok(job.get("location") or "")
        ):
            continue
        low, high = job.get("salary_min") or 0, job.get("salary_max") or 0
        rate = f"${low:,} - ${high:,}" if low and high else _rate(description)
        leads.append(
            _lead(
                "Remote OK",
                title,
                job.get("company") or "?",
                rate,
                job.get("url") or "?",
            )
        )
    return leads


def wwr_leads(xml: bytes) -> list[dict]:
    """WWR titles read "Company: Role"; <region> is the location."""
    try:
        items = list(ET.fromstring(xml).iter("item"))
    except ET.ParseError as error:
        logger.error("wwr: feed skipped: %s", error)
        return []
    leads = []
    for item in items:
        company, _, title = (item.findtext("title") or "").partition(": ")
        if not title:
            company, title = "?", company
        if not (
            item.findtext("type") == WWR_CONTRACT
            and KEYWORDS.search(f"{title} {item.findtext('skills') or ''}")
            and location_ok(item.findtext("region") or "")
        ):
            continue
        description = html_to_text(html.unescape(item.findtext("description") or ""))
        leads.append(
            _lead(
                "WWR", title, company, _rate(description), item.findtext("link") or "?"
            )
        )
    return leads


def _hn_contracts(offers: list[dict]) -> list[dict]:
    # tipo "freelance" = says contract/freelance/part-time and never full-time
    return [
        _lead("HN", o["rol"], o["empresa"], o["presupuesto"], o["link"])
        for o in offers
        if o["tipo"] == "freelance"
    ]


def hn_leads(thread: dict) -> list[dict]:
    """cold_outreach rules minus the junior requirement: contracts rarely say junior."""
    return _hn_contracts(parse_offers(thread, junior_only=False))


def render(leads: list[dict]) -> str:
    def cell(value: str) -> str:
        return value.replace("|", "/").strip()

    rows = [
        "| "
        + " | ".join(
            cell(lead[k]) for k in ("fuente", "titulo", "empresa", "tarifa", "link")
        )
        + " |"
        for lead in leads
    ]
    header = [
        "# Freelance radar (Remote OK, We Work Remotely, HN)\n",
        "Fuentes: [Remote OK](https://remoteok.com), We Work Remotely, Hacker News.\n",
        "| Fuente | Título del Proyecto | Empresa | Tarifa | Link |",
        "|---|---|---|---|---|",
    ]
    return "\n".join(header + rows) + "\n"


def fetch_all(session: requests.Session) -> list[dict]:
    """One request per source; a failing source is logged and skipped."""
    leads = []
    try:
        response = session.get(REMOTEOK_API, timeout=TIMEOUT_S)
        response.raise_for_status()
        leads += remoteok_leads(response.json())
    except (requests.RequestException, ValueError) as error:
        logger.error("remoteok: skipped: %s", error)
    time.sleep(REQUEST_DELAY_S)
    try:
        response = session.get(WWR_FEED, timeout=TIMEOUT_S)
        response.raise_for_status()
        leads += wwr_leads(response.content)
    except requests.RequestException as error:
        logger.error("wwr: skipped: %s", error)
    time.sleep(REQUEST_DELAY_S)
    try:
        leads += _hn_contracts(fetch_offers(session, junior_only=False))
    except (requests.RequestException, LookupError, ValueError) as error:
        logger.error("hn: skipped: %s", error)
    return leads


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verbose", action="store_true", help="log INFO too")
    setup_logging(parser.parse_args().verbose)
    leads = fetch_all(requests.Session())
    OUTPUT.write_text(render(leads), encoding="utf-8")
    print(f"{len(leads)} leads -> {OUTPUT}")


if __name__ == "__main__":
    main()
