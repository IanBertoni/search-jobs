"""Get on Board public API -> 03_tracking/leads_YYYY-MM-DD.csv + leads_consolidated.csv.

Usage (from 02_automation/): python -m scrapers.getonboard_scraper [--verbose]
"""

import argparse
import html
import logging
import re
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd
import requests

from config import API_URL, REQUEST_DELAY_S, TRACKING_DIR, setup_logging
from matcher.jd_decoder import MANUAL_REVIEW_PENDING, extract_requirements, score_jd

SOURCES = [
    ("categories/data-science-analytics/jobs", {}),
    ("categories/machine-learning-ai/jobs", {}),
    ("search/jobs", {"query": "python"}),
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
]
LEAD_COLUMNS = [
    "titulo",
    "empresa",
    "link",
    "score_jd",
    "requisitos",
    "fecha_scrapeo",
    "manual_review",
]
SNAPSHOT_GLOB = "leads_????-??-??.csv"  # dated files only, never leads_consolidated.csv
CONSOLIDATED_CSV = "leads_consolidated.csv"
CSV_ENCODING = "utf-8-sig"  # BOM so Excel shows accents

BLOCK_END = re.compile(r"</(li|p|div|h\d)>|<br\s*/?>", re.IGNORECASE)
TAG = re.compile(r"<[^>]+>")

logger = logging.getLogger(__name__)

TARGET_SENIORITY_IDS = {
    1,
    2,
    3,
}  # /api/v0/seniorities: Sin experiencia, Junior, Semi Senior
TARGET_LEVEL = re.compile(
    r"\b(?:junior|trainee|entry[- ]level|ssr|semi[- ]?senior|jr|aprendiz"
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
) -> list[dict]:
    # a source that keeps failing returns [] so the other sources still run
    for wait in (*RETRY_BACKOFF_S, None):
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
            if wait is not None:
                logger.warning("%s: %s; retrying in %ss", path, error, wait)
                sleep(wait)
    logger.error(
        "%s: skipped after %d attempts: %s",
        path,
        len(RETRY_BACKOFF_S) + 1,
        last_error,
    )
    return []


def fetch_jobs(
    session: requests.Session | None = None,
    delay: float = REQUEST_DELAY_S,
    sleep: Callable[[float], None] = time.sleep,
) -> list[dict]:
    """Download the newest jobs of every source in ``SOURCES``.

    Each request has a 10 s timeout and is retried with exponential backoff
    (1 s, 2 s, 4 s). A source that still fails, or answers with empty or
    malformed JSON, is logged and skipped.

    Args:
        session: HTTP session to use. Defaults to a new ``requests.Session``.
        delay: Seconds to wait after each source (rate limit).
        sleep: Function used to wait; tests inject a fake one.

    Returns:
        Raw job dicts from the API, possibly repeated across sources. Empty
        if every source failed.
    """
    # shortcut: first page only (newest 100 per source); add pagination if that misses leads
    session = session or requests.Session()
    jobs = []
    for path, params in SOURCES:
        jobs.extend(_fetch_source(session, path, params, sleep))
        sleep(delay)
    return jobs


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
        }
    ).reset_index(drop=True)


def keep_target_levels(jobs: pd.DataFrame) -> pd.DataFrame:
    """Keep junior to semi-senior jobs.

    API seniority is a hard filter; keywords only decide when it's missing.

    Args:
        jobs: Output of ``clean_jobs``.

    Returns:
        The rows of ``jobs`` at a target seniority level.
    """
    by_keyword = (jobs["titulo"] + " " + jobs["descripcion"]).str.contains(TARGET_LEVEL)
    by_api = jobs["seniority_id"].isin(TARGET_SENIORITY_IDS)
    return jobs[by_api.where(jobs["seniority_id"].notna(), by_keyword).astype(bool)]


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
        )
        .sort_values("score_jd", ascending=False, kind="stable")
        .loc[:, LEAD_COLUMNS]
    )


def save_snapshot(leads: pd.DataFrame, folder: Path, now: datetime) -> Path:
    """Write the leads to ``leads_YYYY-MM-DD.csv``, replacing a same-day file.

    Args:
        leads: Output of ``build_leads``.
        folder: Destination folder; created if missing.
        now: Scrape time; its date names the file.

    Returns:
        Path of the written snapshot.

    Raises:
        OSError: If the file can't be written (e.g. it is open in Excel).
    """
    path = folder / f"leads_{now.date().isoformat()}.csv"
    folder.mkdir(parents=True, exist_ok=True)
    leads.to_csv(path, index=False, encoding=CSV_ENCODING)
    return path


def read_leads(path: Path) -> pd.DataFrame:
    """Read a leads CSV (snapshot or consolidated), rejecting corrupt files.

    Args:
        path: CSV written by ``save_snapshot`` or ``consolidate``.

    Returns:
        The leads, with score_jd as a number. Files written before the
        manual_review column existed get it as "PENDING".

    Raises:
        OSError: If the file is missing or can't be opened.
        ValueError: If the file is empty, isn't valid UTF-8 CSV, lacks any of
            ``LEAD_COLUMNS`` or has non-numeric scores.
    """
    leads = pd.read_csv(path, encoding=CSV_ENCODING)
    missing = set(LEAD_COLUMNS) - {"manual_review"} - set(leads.columns)
    if missing:
        raise ValueError(f"{path.name}: missing columns {sorted(missing)}")
    if "manual_review" not in leads:
        leads["manual_review"] = MANUAL_REVIEW_PENDING
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
    setup_logging(parse_args(__doc__).verbose)
    now = datetime.now(UTC)
    raw_jobs = fetch_jobs()
    if not raw_jobs:
        # an empty snapshot would overwrite today's good one
        logger.error("no jobs fetched from any source; leads files left untouched")
        raise SystemExit(1)
    jobs = keep_target_levels(clean_jobs(raw_jobs))
    snapshot = save_snapshot(build_leads(jobs, now), TRACKING_DIR, now)
    logger.info("%d leads -> %s", len(jobs), snapshot)
    logger.info("consolidated -> %s", consolidate(TRACKING_DIR))


if __name__ == "__main__":
    main()
