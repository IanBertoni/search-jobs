"""Get on Board public API -> 03_tracking/leads.csv.

Usage (from 02_automation/): python -m scrapers.getonboard_scraper
"""

import html
import re
import time
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd
import requests

from matcher.jd_decoder import extract_requirements, score_jd

API = "https://www.getonbrd.com/api/v0"
SOURCES = [
    ("categories/data-science-analytics/jobs", {}),
    ("categories/machine-learning-ai/jobs", {}),
    ("search/jobs", {"query": "python"}),
]
PER_PAGE = 100
REQUEST_DELAY_S = 1.0
TIMEOUT_S = 30
LEAD_COLUMNS = ["titulo", "empresa", "link", "score_jd", "requisitos", "fecha_scrapeo"]
TRACKING_DIR = Path(__file__).resolve().parents[2] / "03_tracking"
SNAPSHOT_GLOB = "leads_????-??-??.csv"  # dated files only, never leads_consolidated.csv
CONSOLIDATED_CSV = "leads_consolidated.csv"
CSV_ENCODING = "utf-8-sig"  # BOM so Excel shows accents

BLOCK_END = re.compile(r"</(li|p|div|h\d)>|<br\s*/?>", re.IGNORECASE)
TAG = re.compile(r"<[^>]+>")

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


def fetch_jobs(session=requests, delay: float = REQUEST_DELAY_S) -> list[dict]:
    # shortcut: first page only (newest 100 per source); add pagination if that misses leads
    jobs = []
    for path, params in SOURCES:
        response = session.get(
            f"{API}/{path}",
            params={**params, "per_page": PER_PAGE, "expand": '["company"]'},
            timeout=TIMEOUT_S,
        )
        response.raise_for_status()
        jobs.extend(response.json()["data"])
        time.sleep(delay)
    return jobs


def html_to_text(raw: str | None) -> str:
    return html.unescape(TAG.sub("", BLOCK_END.sub("\n", raw or ""))).strip()


def clean_jobs(raw_jobs: list[dict]) -> pd.DataFrame:
    df = pd.json_normalize(raw_jobs).drop_duplicates("id")
    return pd.DataFrame(
        {
            "titulo": df["attributes.title"].str.strip(),
            "empresa": df["attributes.company.data.attributes.name"],
            "link": df["links.public_url"],
            "descripcion": df["attributes.description"].map(html_to_text),
            "proyecto": df["attributes.projects"].map(html_to_text),
            "fecha": pd.to_datetime(df["attributes.published_at"], unit="s", utc=True),
            "min_salary": df["attributes.min_salary"],
            "max_salary": df["attributes.max_salary"],
            "seniority_id": df.get("attributes.seniority.data.id"),
        }
    ).reset_index(drop=True)


def keep_target_levels(jobs: pd.DataFrame) -> pd.DataFrame:
    """API seniority is a hard filter; keywords only decide when it's missing."""
    by_keyword = (jobs["titulo"] + " " + jobs["descripcion"]).str.contains(TARGET_LEVEL)
    by_api = jobs["seniority_id"].isin(TARGET_SENIORITY_IDS)
    return jobs[by_api.where(jobs["seniority_id"].notna(), by_keyword).astype(bool)]


def build_leads(jobs: pd.DataFrame, now: datetime) -> pd.DataFrame:
    return (
        jobs.assign(
            score_jd=[score_jd(job, now) for job in jobs.to_dict("records")],
            requisitos=jobs["descripcion"].map(
                lambda d: " | ".join(extract_requirements(d))
            ),
            fecha_scrapeo=now.date().isoformat(),
        )
        .sort_values("score_jd", ascending=False, kind="stable")
        .loc[:, LEAD_COLUMNS]
    )


def save_snapshot(leads: pd.DataFrame, folder: Path, now: datetime) -> Path:
    path = folder / f"leads_{now.date().isoformat()}.csv"
    folder.mkdir(parents=True, exist_ok=True)
    leads.to_csv(path, index=False, encoding=CSV_ENCODING)
    return path


def consolidate(folder: Path) -> Path:
    """Merge every dated snapshot; a link seen twice keeps its latest scrape."""
    snapshots = sorted(folder.glob(SNAPSHOT_GLOB))  # ISO dates sort chronologically
    merged = (
        pd.concat([pd.read_csv(p, encoding=CSV_ENCODING) for p in snapshots])
        .drop_duplicates("link", keep="last")
        .sort_values("score_jd", ascending=False, kind="stable")
    )
    path = folder / CONSOLIDATED_CSV
    merged.to_csv(path, index=False, encoding=CSV_ENCODING)
    return path


def main() -> None:
    now = datetime.now(UTC)
    jobs = keep_target_levels(clean_jobs(fetch_jobs()))
    snapshot = save_snapshot(build_leads(jobs, now), TRACKING_DIR, now)
    print(f"snapshot -> {snapshot}\nconsolidated -> {consolidate(TRACKING_DIR)}")


if __name__ == "__main__":
    main()
