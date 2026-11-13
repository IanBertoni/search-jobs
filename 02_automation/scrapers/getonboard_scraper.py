"""Get on Board API + Remotive/AI-training feeds -> 03_tracking/leads_YYYY-MM-DD.csv + leads_consolidated.csv.

Usage (from 02_automation/): python -m scrapers.getonboard_scraper [--verbose]
"""

import argparse
import html
import logging
import re
import time
from collections.abc import Callable, Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd
import requests

from config import (
    AI_TRAINING_KEYWORDS,
    AI_TRAINING_TECH_KEYWORDS,
    API_URL,
    REQUEST_DELAY_S,
    SENIORITY_IDS,
    STACK_KEYWORDS,
    TRACKING_DIR,
    setup_logging,
)
from matcher.jd_decoder import MANUAL_REVIEW_PENDING, extract_requirements, score_jd

SOURCES = [
    ("categories/data-science-analytics/jobs", {}),
    ("categories/machine-learning-ai/jobs", {}),
    ("search/jobs", {"query": "python"}),
    # AI training / evaluation vertical; the title filter drops the full-text noise
    ("search/jobs", {"query": "evaluator"}),
    ("search/jobs", {"query": "trainer"}),
    ("companies/toloka-ai/jobs", {}),  # Mindrift
]
PER_PAGE = 100
TIMEOUT_S = 10
RETRY_BACKOFF_S = (1, 2, 4)  # wait before each retry, so 4 attempts in total
UNKNOWN = "Unknown"
# json_normalize columns clean_jobs reads; reindexing to them survives absent fields
RAW_COLUMNS = [
    "id",
    "attributes.title",
    "attributes.company.data.attributes.name",
    "links.public_url",
    "attributes.description",
    "attributes.projects",
    "attributes.published_at",
    "attributes.min_salary",
    "attributes.max_salary",
    "attributes.seniority.data.id",
    "attributes.remote_modality",
    "attributes.countries",
]
LEAD_COLUMNS = [
    "titulo",
    "empresa",
    "link",
    "score_jd",
    "requisitos",
    "fecha_scrapeo",
    "manual_review",
    "vertical",
    "pais",
    "priority",
]
SNAPSHOT_GLOB = "leads_????-??-??.csv"  # dated files only, never leads_consolidated.csv
CONSOLIDATED_CSV = "leads_consolidated.csv"
CSV_ENCODING = "utf-8-sig"  # BOM so Excel shows accents

BLOCK_END = re.compile(r"</(li|p|div|h\d)>|<br\s*/?>", re.IGNORECASE)
TAG = re.compile(r"<[^>]+>")

logger = logging.getLogger(__name__)


def whole_words(words: Iterable[str]) -> re.Pattern[str]:
    # whole words so "ai" doesn't hit "maintain"; an empty list matches nothing
    alternatives = "|".join(map(re.escape, words))
    pattern = rf"\b(?:{alternatives})\b" if alternatives else r"(?!)"
    return re.compile(pattern, re.IGNORECASE)


AI_TRAINING_TITLE = whole_words(AI_TRAINING_KEYWORDS)
STACK = whole_words(STACK_KEYWORDS)
TECH = whole_words(AI_TRAINING_TECH_KEYWORDS)


def vertical_of(titles: pd.Series) -> pd.Series:
    """Label each title "ai_training" (evaluator, trainer...) or "data_ai"."""
    is_training = titles.str.contains(AI_TRAINING_TITLE).astype(bool)
    return is_training.map({True: "ai_training", False: "data_ai"})


REMOTE_MODALITY = {
    "fully_remote": "Remote",
    "remote_local": "Remote (local)",  # remote, but from the company's country
    "hybrid": "Hybrid",
    "no_remote": "On-site",
}
# country is a priority, never a filter: P1 anywhere/LATAM, P2 unclear or
# country-specific, P3 must live elsewhere (still listed, ranked last)
ANYWHERE = re.compile(
    r"\b(?:argentina|latam|latin america|world ?wide|anywhere|global|most countries)\b",
    re.IGNORECASE,
)
ONLY_THERE = re.compile(r"\bonly\b|^hybrid\b|^on-site\b", re.IGNORECASE)
MUST_LIVE_THERE = re.compile(
    r"must be (?:based|located|residing) in|\b(?:eu|uk|us|usa)[- ]only\b"
    r"|\bonly (?:in )?(?:the )?(?:eu|uk|us|usa|united states)\b",
    re.IGNORECASE,
)
TARGET_LEVEL = re.compile(
    # no ssr/semi-senior: in LATAM that's 3-5 years, not entry-level
    r"\b(?:junior|trainee|entry[- ]level|jr|aprendiz"
    r"|practicante|sin experiencia)\b",
    re.IGNORECASE,
)


def _jobs_in(payload: Any, path: str) -> list[dict]:
    jobs = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(jobs, list) or not jobs:
        logger.warning("%s: empty or malformed JSON payload, no jobs taken", path)
        return []
    return jobs


def _fetch_source(
    session: requests.Session,
    path: str,
    params: dict[str, str],
    sleep: Callable[[float], None],
) -> list[dict] | None:
    # a source that keeps failing returns None so the other sources still run
    for attempt, wait in enumerate((*RETRY_BACKOFF_S, None), start=1):
        try:
            response = session.get(
                f"{API_URL}/{path}",
                params={**params, "per_page": PER_PAGE, "expand": '["company"]'},
                timeout=TIMEOUT_S,
            )
            response.raise_for_status()
            # a body that isn't JSON raises requests.JSONDecodeError: retried too
            return _jobs_in(response.json(), path)
        except requests.RequestException as error:
            last_error = error
            status = getattr(error.response, "status_code", None)
            # a 4xx won't fix itself on retry, except 429 (rate limit)
            if wait is None or (status in range(400, 500) and status != 429):
                break
            logger.warning("%s: %s; retrying in %ss", path, error, wait)
            sleep(wait)
    logger.error("%s: skipped after %d attempts: %s", path, attempt, last_error)
    return None


def fetch_jobs(
    session: requests.Session | None = None,
    delay: float = REQUEST_DELAY_S,
    sleep: Callable[[float], None] = time.sleep,
) -> tuple[list[dict], list[str]]:
    """Download the newest jobs of every source in ``SOURCES``.

    Each request has a 10 s timeout and is retried with exponential backoff
    (1 s, 2 s, 4 s); a 4xx other than 429 fails at once. A source that still
    fails, or answers with empty or malformed JSON, is logged and skipped.

    Args:
        session: HTTP session to use. Defaults to a new ``requests.Session``.
        delay: Seconds to wait after each source (rate limit).
        sleep: Function used to wait; tests inject a fake one.

    Returns:
        Raw job dicts from the API, possibly repeated across sources (empty
        if every source failed), and the paths of the sources that failed.
    """
    # shortcut: first page only (newest 100 per source); add pagination if that misses leads
    session = session or requests.Session()
    jobs, failed = [], []
    for path, params in SOURCES:
        source_jobs = _fetch_source(session, path, params, sleep)
        if source_jobs is None:
            failed.append(path)
        else:
            jobs.extend(source_jobs)
        sleep(delay)
    return jobs, failed


def html_to_text(raw: str | None) -> str:
    """Strip HTML tags, keeping one line per block element.

    Args:
        raw: HTML fragment from the API. Anything that isn't a string (None,
            NaN) counts as empty.

    Returns:
        Plain text with entities unescaped.
    """
    if not isinstance(raw, str):
        return ""
    return html.unescape(TAG.sub("", BLOCK_END.sub("\n", raw))).strip()


def _text_or_unknown(values: pd.Series, field: str) -> pd.Series:
    text = values.map(
        lambda value: value.strip() if isinstance(value, str) else ""
    ).astype(object)
    missing = text == ""
    if missing.any():
        logger.warning("%d jobs without %s, using %r", missing.sum(), field, UNKNOWN)
    return text.where(~missing, UNKNOWN)


def clean_jobs(raw_jobs: list[dict]) -> pd.DataFrame:
    """Flatten raw API jobs into one row per job with the fields we use.

    Args:
        raw_jobs: Job dicts as returned by ``fetch_jobs``. Entries that aren't
            dicts are ignored.

    Returns:
        DataFrame with columns titulo, empresa, link, descripcion, proyecto,
        fecha, min_salary, max_salary and seniority_id, deduplicated by job
        id. A missing titulo, empresa or link becomes "Unknown" (with a
        WARNING); an unparseable fecha becomes NaT.
    """
    df = pd.json_normalize([job for job in raw_jobs if isinstance(job, dict)])
    df = df.reindex(columns=RAW_COLUMNS)
    df = df[~df["id"].duplicated() | df["id"].isna()]
    # shortcut: every link-less job shares link "Unknown", so consolidate keeps only
    # one of them; drop or key them differently if the API ever really omits links
    return pd.DataFrame(
        {
            "titulo": _text_or_unknown(df["attributes.title"], "title"),
            "empresa": _text_or_unknown(
                df["attributes.company.data.attributes.name"], "company"
            ),
            "link": _text_or_unknown(df["links.public_url"], "link"),
            "descripcion": df["attributes.description"].map(html_to_text),
            "proyecto": df["attributes.projects"].map(html_to_text),
            "fecha": pd.to_datetime(
                df["attributes.published_at"], unit="s", utc=True, errors="coerce"
            ),
            "min_salary": df["attributes.min_salary"],
            "max_salary": df["attributes.max_salary"],
            "seniority_id": df["attributes.seniority.data.id"],
            "pais": _getonboard_pais(
                df["attributes.remote_modality"], df["attributes.countries"]
            ),
        }
    ).reset_index(drop=True)


def priority_of(pais: pd.Series, descripcion: pd.Series) -> pd.Series:
    """Rank where a posting can be worked from: "P1", "P2" or "P3".

    Args:
        pais: Location as the source states it ("Remote, LATAM", "Unknown"...).
        descripcion: Plain-text description, checked for "must be based in".

    Returns:
        P1 for Argentina, LATAM, worldwide or plain "Remote"; P3 when the
        posting restricts where you live (only/hybrid/on-site, must be based
        in); P2 for the rest, including an unknown country.
    """
    pais = pais.fillna(UNKNOWN).astype(str)
    restricted = pais.str.contains(ONLY_THERE) | descripcion.fillna("").astype(
        str
    ).str.contains(MUST_LIVE_THERE)
    anywhere = pais.str.contains(ANYWHERE) | (
        pais.str.strip().str.casefold() == "remote"
    )
    return pd.Series("P2", index=pais.index).mask(anywhere, "P1").mask(restricted, "P3")


def _getonboard_pais(modality: pd.Series, countries: pd.Series) -> pd.Series:
    def one(mode: Any, where: Any) -> str:
        label = REMOTE_MODALITY.get(mode) if isinstance(mode, str) else None
        if label is None:
            return UNKNOWN
        if mode in ("hybrid", "no_remote") and isinstance(where, list) and where:
            return f"{label}: {', '.join(where)}"
        return label

    return pd.Series(
        [one(mode, where) for mode, where in zip(modality, countries)],
        index=modality.index,
        dtype=object,
    )


def keep_target_levels(jobs: pd.DataFrame) -> pd.DataFrame:
    """Keep junior to semi-senior jobs.

    API seniority is a hard filter; keywords only decide when it's missing.
    Without seniority, a posting that names the target stack (Python, LLM...)
    is kept too: many entry-level roles aren't labelled "Junior". So are AI
    training roles, whatever their seniority, as long as the title or the
    description is technical (drops philosophy, audio and content gigs).

    Args:
        jobs: Output of ``clean_jobs``.

    Returns:
        The rows of ``jobs`` at a target seniority level.
    """
    text = jobs["titulo"] + " " + jobs["descripcion"]
    by_keyword = text.str.contains(TARGET_LEVEL) | text.str.contains(STACK)
    by_api = jobs["seniority_id"].isin(SENIORITY_IDS)
    by_level = by_api.where(jobs["seniority_id"].notna(), by_keyword).astype(bool)
    training = vertical_of(jobs["titulo"]) == "ai_training"
    return jobs[(by_level & ~training) | (training & text.str.contains(TECH))]


def dedupe_jobs(jobs: pd.DataFrame) -> pd.DataFrame:
    """Drop a posting seen twice: only the exact same URL counts.

    The same title and company on another URL is kept: platforms list one role
    per country or language ("AI Coding Evaluator" for Argentina and Brazil).

    Args:
        jobs: ``clean_jobs``-shaped rows from every source, preferred first.

    Returns:
        The first row of each URL.
    """
    return jobs.drop_duplicates("link")


def build_leads(jobs: pd.DataFrame, now: datetime) -> pd.DataFrame:
    """Score each job and shape it into the tracking CSV layout.

    Args:
        jobs: Output of ``clean_jobs`` (optionally filtered).
        now: Timezone-aware scrape time; dates the leads and ages the postings.

    Returns:
        DataFrame with ``LEAD_COLUMNS``, highest score_jd first.
    """
    return (
        jobs.assign(
            score_jd=[score_jd(job, now) for job in jobs.to_dict("records")],
            requisitos=jobs["descripcion"].map(
                lambda d: " | ".join(extract_requirements(d))
            ),
            fecha_scrapeo=now.date().isoformat(),
            manual_review=MANUAL_REVIEW_PENDING,
            vertical=vertical_of(jobs["titulo"]),
            priority=priority_of(jobs["pais"], jobs["descripcion"]),
        )
        .sort_values("score_jd", ascending=False, kind="stable")
        .loc[:, LEAD_COLUMNS]
    )


def save_snapshot(
    leads: pd.DataFrame, folder: Path, now: datetime, keep_existing: bool = False
) -> Path:
    """Write the leads to ``leads_YYYY-MM-DD.csv``, replacing a same-day file.

    Args:
        leads: Output of ``build_leads``.
        folder: Destination folder; created if missing.
        now: Scrape time; its date names the file.
        keep_existing: Merge into the same-day file instead of replacing it (a
            link in both keeps the new row). For runs where a source failed,
            so the leads only that source found earlier in the day survive.

    Returns:
        Path of the written snapshot.

    Raises:
        OSError: If the file can't be written (e.g. it is open in Excel).
    """
    path = folder / f"leads_{now.date().isoformat()}.csv"
    folder.mkdir(parents=True, exist_ok=True)
    if keep_existing and path.exists():
        try:
            leads = (
                pd.concat([read_leads(path), leads])
                .drop_duplicates("link", keep="last")
                .sort_values("score_jd", ascending=False, kind="stable")
            )
        except ValueError as error:
            logger.error("replacing corrupt snapshot %s: %s", path.name, error)
    leads.to_csv(path, index=False, encoding=CSV_ENCODING)
    return path


def read_leads(path: Path) -> pd.DataFrame:
    """Read a leads CSV (snapshot or consolidated), rejecting corrupt files.

    Args:
        path: CSV written by ``save_snapshot`` or ``consolidate``.

    Returns:
        The leads, with score_jd as a number. Files written before the
        manual_review column existed get it as "PENDING", and those before
        the vertical column get it from the title.

    Raises:
        OSError: If the file is missing or can't be opened.
        ValueError: If the file is empty, isn't valid UTF-8 CSV, lacks any of
            ``LEAD_COLUMNS`` or has non-numeric scores.
    """
    leads = pd.read_csv(path, encoding=CSV_ENCODING)
    optional = {"manual_review", "vertical", "pais", "priority"}
    missing = set(LEAD_COLUMNS) - optional - set(leads.columns)
    if missing:
        raise ValueError(f"{path.name}: missing columns {sorted(missing)}")
    if "manual_review" not in leads:
        leads["manual_review"] = MANUAL_REVIEW_PENDING
    if "vertical" not in leads:
        leads["vertical"] = vertical_of(leads["titulo"].astype(str))
    if "pais" not in leads:
        leads["pais"] = UNKNOWN
    if "priority" not in leads:
        leads["priority"] = priority_of(leads["pais"], leads["requisitos"])
    return leads.assign(score_jd=pd.to_numeric(leads["score_jd"]))


def _read_snapshots(folder: Path) -> list[pd.DataFrame]:
    snapshots = []
    for path in sorted(folder.glob(SNAPSHOT_GLOB)):  # ISO dates sort chronologically
        try:
            snapshots.append(read_leads(path))
        except ValueError as error:
            logger.error("skipping corrupt snapshot %s: %s", path.name, error)
    return snapshots


def consolidate(folder: Path) -> Path:
    """Rebuild ``leads_consolidated.csv`` from every dated snapshot.

    A link seen twice keeps its latest scrape. The previous consolidated file
    is never read, so a corrupt one is simply replaced; corrupt snapshots are
    logged and skipped.

    Args:
        folder: Folder holding the ``leads_YYYY-MM-DD.csv`` snapshots.

    Returns:
        Path of the consolidated CSV.

    Raises:
        OSError: If the consolidated file can't be written.
    """
    snapshots = _read_snapshots(folder)
    merged = pd.concat(snapshots) if snapshots else pd.DataFrame(columns=LEAD_COLUMNS)
    merged = merged.drop_duplicates("link", keep="last").sort_values(
        "score_jd", ascending=False, kind="stable"
    )
    path = folder / CONSOLIDATED_CSV
    merged.to_csv(path, index=False, encoding=CSV_ENCODING)
    return path


def parse_args(description: str | None) -> argparse.Namespace:
    """Parse the command line shared by the radar scripts.

    Args:
        description: Help text, usually the script's module docstring.

    Returns:
        Namespace with the boolean ``verbose``.
    """
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument("--verbose", action="store_true", help="log INFO too")
    return parser.parse_args()


def main() -> None:
    """Scrape, snapshot and consolidate.

    Raises:
        SystemExit: If no source returned jobs; nothing is written then.
    """
    # local import: remotive_scraper reuses this module's helpers
    from scrapers import ai_training_scraper, remotive_scraper

    setup_logging(parse_args(__doc__).verbose)
    now = datetime.now(UTC)
    raw_jobs, failed_sources = fetch_jobs()
    feeds = {
        "remotive": remotive_scraper.fetch_remotive,
        "aitrainer.work": ai_training_scraper.fetch_aitrainer_work,
        "aitraining.jobs": ai_training_scraper.fetch_aitraining_jobs,
    }
    found = [clean_jobs(raw_jobs)]
    for name, fetch in feeds.items():
        feed = fetch()
        if feed is None:
            failed_sources.append(name)
        else:
            found.append(feed)
    found = [df for df in found if not df.empty]
    if not found:
        # an empty snapshot would overwrite today's good one
        logger.error("no jobs fetched from any source; leads files left untouched")
        raise SystemExit(1)
    # Get on Board first: on a duplicate URL its richer posting wins
    jobs = keep_target_levels(dedupe_jobs(pd.concat(found, ignore_index=True)))
    snapshot = save_snapshot(
        build_leads(jobs, now), TRACKING_DIR, now, keep_existing=bool(failed_sources)
    )
    logger.info("%d leads -> %s", len(jobs), snapshot)
    logger.info("consolidated -> %s", consolidate(TRACKING_DIR))


if __name__ == "__main__":
    main()
