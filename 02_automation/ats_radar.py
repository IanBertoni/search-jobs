"""ATS radar: startup careers pages (Greenhouse, Lever, Ashby) -> Groq fit check.

Job boards repost the same Senior/US-only roles; startups that hire in LatAm
publish first (or only) on their own careers page. Greenhouse, Lever and Ashby
serve those pages from public, documented JSON endpoints meant for embedding,
so no HTML scraping and no login.
1. One request per company in COMPANIES (rate-limited).
2. Cheap filters: tech title, no Senior/Lead/Staff, doable from Argentina.
3. Groq reads the requirements (matcher.seniority_judge): apply/stretch/skip.
Applying stays manual (see CLAUDE.md).
Output: 03_tracking/ats_leads.md
Usage (from 02_automation/): python ats_radar.py [--verbose]
"""

import argparse
import html
import logging
import re
import sys
import time
from datetime import UTC, datetime

import requests

from config import GROQ_API_KEY, REQUEST_DELAY_S, TRACKING_DIR, setup_logging
from matcher.seniority_judge import judge_fit
from scrapers.getonboard_scraper import TIMEOUT_S, html_to_text, whole_words

OUTPUT = TRACKING_DIR / "ats_leads.md"
BOARD_URLS = {
    "greenhouse": "https://boards-api.greenhouse.io/v1/boards/{}/jobs",
    "lever": "https://api.lever.co/v0/postings/{}",
    "ashby": "https://api.ashbyhq.com/posting-api/job-board/{}",
}
BOARD_PARAMS = {"greenhouse": {"content": "true"}, "lever": {"mode": "json"}}
# boards verified live on 2026-11-14: LatAm startups + remote-first data/AI tools
COMPANIES = [
    *(
        ("greenhouse", s)
        for s in ["wizeline", "clara", "cobre", "vtex", "quintoandar", "ebanx"]
    ),
    *(("greenhouse", s) for s in ["gympass", "vercel"]),
    *(("lever", s) for s in ["kavak", "fintual", "xepelin", "yuno", "toku"]),
    *(
        ("ashby", s)
        for s in ["nubank", "belvo", "addi", "truelogic", "kueski", "flink"]
    ),
    *(("ashby", s) for s in ["posthog", "airbyte", "prefect", "astronomer"]),
    *(("ashby", s) for s in ["supabase", "n8n", "zapier", "langchain", "llamaindex"]),
    *(("ashby", s) for s in ["pinecone", "weaviate", "modal"]),
]
# remote-first companies from 03_tracking/company_portals.csv, boards verified
# live on 2026-11-27 (GitHub, Automattic, Doist, Sourcegraph: not on these ATS)
PORTAL_COMPANIES = [
    *(
        ("greenhouse", s)
        for s in ["canonical", "stripe", "udacity", "gitlab", "reddit", "twilio"]
    ),
    *(
        ("greenhouse", s)
        for s in ["discord", "elastic", "datadog", "mongodb", "grafanalabs"]
    ),
    *(
        ("greenhouse", s)
        for s in ["tailscale", "dropbox", "algolia", "circleci", "pagerduty"]
    ),
    *(("greenhouse", s) for s in ["customerio", "mattermost"]),
    *(("lever", s) for s in ["metabase", "fly", "collabora", "spotify", "toptal"]),
    *(("ashby", s) for s in ["percona", "capchase", "webflow", "replit"]),
    *(("ashby", s) for s in ["mapbox", "buffer", "helpscout"]),
]
COMPANIES = [*COMPANIES, *PORTAL_COMPANIES]

TECH_TITLE = whole_words(
    [
        "data",
        "ai",
        "ml",
        "machine learning",
        "python",
        "llm",
        "analytics",
        "automation",
        "backend",
        "software",
        "developer",
        "engineer",
        "scientist",
        "integrations",
    ]
)
SENIOR_TITLE = whole_words(
    [
        "senior",
        "sr",
        "staff",
        "principal",
        "lead",
        "head",
        "director",
        "manager",
        "architect",
        "vp",
        "founding",
        "iii",
        "iv",
    ]
)
FROM_ARGENTINA = whole_words(
    [
        "argentina",
        "buenos aires",
        "latam",
        "latin america",
        "latinoamerica",
        "americas",
        "amer",
        "global",
        "worldwide",
        "anywhere",
    ]
)
REMOTE_WORDS = re.compile(r"remote|remoto|[\W_]", re.IGNORECASE)

# Groq free tier is tokens-per-minute bound: pace calls, wait out a 429 once
GROQ_DELAY_S = 3
RATE_LIMIT_WAIT_S = 30
# requirements sit late in long postings: keep the head (role) and the tail
MAX_TEXT_CHARS, HEAD_CHARS = 4000, 1000
VERDICT_ORDER = {"apply": 0, "stretch": 1, "?": 2}

logger = logging.getLogger(__name__)


def _job(empresa, titulo, ubicacion, remoto, link, texto, publicado) -> dict:
    return {
        "empresa": empresa,
        "titulo": (titulo or "").strip(),
        "ubicacion": (ubicacion or "").strip(),
        "remoto": bool(remoto),
        "link": link or "?",
        "texto": texto.strip(),
        "publicado": publicado or "?",
    }


def _items(payload, key: str | None) -> list[dict]:
    items = payload.get(key) if key and isinstance(payload, dict) else payload
    return [i for i in items if isinstance(i, dict)] if isinstance(items, list) else []


def greenhouse_jobs(slug: str, payload) -> list[dict]:
    """``content`` is HTML escaped once more (``&lt;p&gt;``)."""
    jobs = []
    for j in _items(payload, "jobs"):
        location = (j.get("location") or {}).get("name") or ""
        title = j.get("title") or ""
        jobs.append(
            _job(
                slug,
                title,
                location,
                "remote" in f"{title} {location}".lower(),
                j.get("absolute_url"),
                html_to_text(html.unescape(j.get("content") or "")),
                (j.get("first_published") or j.get("updated_at") or "")[:10],
            )
        )
    return jobs


def lever_jobs(slug: str, payload) -> list[dict]:
    """Requirements live in ``lists``: they go first so truncation keeps them."""
    jobs = []
    for j in _items(payload, None):
        categories = j.get("categories") or {}
        locations = categories.get("allLocations") or [categories.get("location")]
        lists = "\n".join(
            f"{part.get('text', '')}\n{html_to_text(part.get('content'))}"
            for part in j.get("lists") or []
        )
        created = j.get("createdAt")
        jobs.append(
            _job(
                slug,
                j.get("text"),
                " / ".join(filter(None, locations)),
                j.get("workplaceType") == "remote",
                j.get("hostedUrl"),
                f"{lists}\n{j.get('descriptionPlain') or ''}",
                datetime.fromtimestamp(created / 1000, UTC).date().isoformat()
                if isinstance(created, int | float)
                else "",
            )
        )
    return jobs


def ashby_jobs(slug: str, payload) -> list[dict]:
    jobs = []
    for j in _items(payload, "jobs"):
        others = [
            (loc.get("location") if isinstance(loc, dict) else loc) or ""
            for loc in j.get("secondaryLocations") or []
        ]
        jobs.append(
            _job(
                slug,
                j.get("title"),
                " / ".join(filter(None, [j.get("location"), *others])),
                j.get("isRemote"),
                j.get("jobUrl"),
                j.get("descriptionPlain") or "",
                (j.get("publishedAt") or "")[:10],
            )
        )
    return jobs


PARSERS = {"greenhouse": greenhouse_jobs, "lever": lever_jobs, "ashby": ashby_jobs}


def location_ok(ubicacion: str, remoto: bool) -> bool:
    """Argentina/LatAm/global, or plain "Remote" with no country attached."""
    return bool(FROM_ARGENTINA.search(ubicacion)) or (
        remoto and not REMOTE_WORDS.sub("", ubicacion)
    )


def candidates(jobs: list[dict]) -> list[dict]:
    """Free filters before spending Groq calls; seniority words only in the title."""
    return [
        j
        for j in jobs
        if TECH_TITLE.search(j["titulo"])
        and not SENIOR_TITLE.search(j["titulo"])
        and location_ok(j["ubicacion"], j["remoto"])
    ]


def _clip(text: str) -> str:
    if len(text) <= MAX_TEXT_CHARS:
        return text
    return f"{text[:HEAD_CHARS]}\n[...]\n{text[-(MAX_TEXT_CHARS - HEAD_CHARS) :]}"


def _judge(job: dict, session: requests.Session) -> dict:
    for attempt in range(2):
        try:
            return judge_fit(
                job["titulo"], job["ubicacion"], _clip(job["texto"]), session
            )
        except requests.HTTPError as error:
            if attempt or not str(error).startswith("429"):
                raise
            logger.warning("groq rate limit, waiting %ss", RATE_LIMIT_WAIT_S)
            time.sleep(RATE_LIMIT_WAIT_S)
    raise AssertionError("unreachable")


def screen(jobs: list[dict], session: requests.Session) -> list[dict]:
    """Groq verdict per job; skip is dropped, a failed call is kept as "?"."""
    screened = []
    for job in jobs:
        try:
            verdict = _judge(job, session)
        except (requests.RequestException, KeyError, TypeError, ValueError) as error:
            logger.error("%s | %s: %s", job["empresa"], job["titulo"], error)
            verdict = {"verdict": "?", "min_years": None, "reason": "Groq falló"}
        time.sleep(GROQ_DELAY_S)
        if verdict["verdict"] == "skip":
            logger.info("skip %s | %s: %s", job["empresa"], job["titulo"], verdict)
            continue
        screened.append({**job, **verdict})
    return screened


def render(rows: list[dict]) -> str:
    def cell(value) -> str:
        return "?" if value is None else str(value).replace("|", "/").strip()

    newest = sorted(rows, key=lambda r: r["publicado"], reverse=True)
    ordered = sorted(newest, key=lambda r: VERDICT_ORDER[r["verdict"]])
    lines = [
        "# ATS radar: careers pages de startups (Greenhouse, Lever, Ashby)\n",
        "apply = podés aplicar hoy; stretch = un gap blando; ? = Groq no respondió.\n",
        "| Empresa | Rol | Ubicación | Veredicto | Años mín. | Motivo | Publicado | Link |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for r in ordered:
        fields = ("empresa", "titulo", "ubicacion", "verdict", "min_years", "reason")
        lines.append(
            "| "
            + " | ".join(cell(r.get(k)) for k in (*fields, "publicado", "link"))
            + " |"
        )
    return "\n".join(lines) + "\n"


def fetch_all(session: requests.Session) -> list[dict]:
    """One request per company; a failing board is logged and skipped."""
    jobs = []
    for ats, slug in COMPANIES:
        try:
            response = session.get(
                BOARD_URLS[ats].format(slug),
                params=BOARD_PARAMS.get(ats),
                timeout=TIMEOUT_S,
            )
            response.raise_for_status()
            jobs += PARSERS[ats](slug, response.json())
        except (requests.RequestException, ValueError) as error:
            logger.error("%s/%s: skipped: %s", ats, slug, error)
        time.sleep(REQUEST_DELAY_S)
    return jobs


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verbose", action="store_true", help="log INFO too")
    setup_logging(parser.parse_args().verbose)
    if not GROQ_API_KEY:
        sys.exit("GROQ_API_KEY missing in 02_automation/.env")
    session = requests.Session()
    jobs = fetch_all(session)
    shortlist = candidates(jobs)
    print(f"{len(jobs)} ofertas en {len(COMPANIES)} boards -> {len(shortlist)} a Groq")
    rows = screen(shortlist, session)
    OUTPUT.write_text(render(rows), encoding="utf-8")
    counts = {v: sum(r["verdict"] == v for r in rows) for v in VERDICT_ORDER}
    print(f"apply {counts['apply']}, stretch {counts['stretch']}, ? {counts['?']}")
    print(f"-> {OUTPUT}")


if __name__ == "__main__":
    main()
