"""HN radar: "Who is hiring?" + "Freelancer? Seeking freelancer?", newest first.

Reuses cold_outreach's deterministic filter without the junior-only rule: remote,
a target role (Python, backend, data, AI...), and none of the dealbreakers (US
only/visa, Senior/Lead/Staff, 3+ years, PhD/deep learning, on-site Europe).
In the freelancer thread only "SEEKING FREELANCER" posts count: "SEEKING WORK"
posts are other freelancers, i.e. the competition.
Read-only: radar_watch.py alerts, applying stays manual (see CLAUDE.md).
"""

import logging
import re
import time

import requests

from cold_outreach import (
    ALGOLIA_URL,
    TIMEOUT_S,
    latest_hiring_thread,
    parse_offers,
)
from config import REQUEST_DELAY_S
from pitch import OFFER_CHARS

FREELANCER_TITLE = "Ask HN: Freelancer? Seeking freelancer?"
# no "story" author filter: since 2026 the thread is posted by a regular user
FREELANCER_QUERY = "Freelancer? Seeking freelancer?"
SEEKING_FREELANCER = re.compile(r"\s*(?:<p>)?\s*seeking freelancer", re.IGNORECASE)

logger = logging.getLogger(__name__)


def latest_freelancer_thread(session: requests.Session) -> str:
    response = session.get(
        f"{ALGOLIA_URL}/search_by_date",
        params={"query": FREELANCER_QUERY, "tags": "story", "hitsPerPage": 10},
        timeout=TIMEOUT_S,
    )
    response.raise_for_status()
    for hit in response.json()["hits"]:
        if hit["title"].startswith(FREELANCER_TITLE):
            return hit["objectID"]
    raise LookupError("no 'Freelancer? Seeking freelancer?' thread found")


def parse_seeking_freelancer(thread: dict) -> list[dict]:
    """Clients only, through the same filter; all of them are freelance work."""
    clients = [
        c
        for c in thread.get("children", [])
        if SEEKING_FREELANCER.match(c.get("text") or "")
    ]
    offers = parse_offers({"children": clients}, junior_only=False)
    return [{**o, "tipo": "freelance"} for o in offers]


def _thread(session: requests.Session, thread_id: str) -> dict:
    time.sleep(REQUEST_DELAY_S)
    response = session.get(f"{ALGOLIA_URL}/items/{thread_id}", timeout=TIMEOUT_S)
    response.raise_for_status()
    return response.json()


def _post_id(offer: dict) -> int:
    return int(offer["link"].rsplit("=", 1)[1])


def fetch_hn_offers(session: requests.Session) -> list[dict]:
    """Both threads, newest post first; a failing thread is logged and skipped."""
    sources = (
        (latest_hiring_thread, lambda t: parse_offers(t, junior_only=False)),
        (latest_freelancer_thread, parse_seeking_freelancer),
    )
    offers: dict[str, dict] = {}
    for find, parse in sources:
        try:
            for offer in parse(_thread(session, find(session))):
                offers.setdefault(offer["link"], offer)
        except (requests.RequestException, LookupError, KeyError, ValueError) as error:
            logger.error("hn %s: skipped: %s", find.__name__, error)
    return sorted(offers.values(), key=_post_id, reverse=True)


def offer_message(offer: dict) -> str:
    return (
        f"Empresa: {offer['empresa']}\n"
        f"Rol: {offer['rol']}\n"
        f"Tipo: {offer['tipo']} · {offer['remoto']}\n"
        f"Presupuesto: {offer['presupuesto']}\n\n"
        f"Post de Hacker News:\n{offer['texto'][:OFFER_CHARS]}"
    )
