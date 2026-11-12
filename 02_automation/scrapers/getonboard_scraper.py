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
LEADS_CSV = Path(__file__).resolve().parents[2] / "03_tracking" / "leads.csv"

BLOCK_END = re.compile(r"</(li|p|div|h\d)>|<br\s*/?>", re.IGNORECASE)
TAG = re.compile(r"<[^>]+>")


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
        }
    ).reset_index(drop=True)


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


def main() -> None:
    leads = build_leads(clean_jobs(fetch_jobs()), datetime.now(UTC))
    LEADS_CSV.parent.mkdir(parents=True, exist_ok=True)
    leads.to_csv(
        LEADS_CSV, index=False, encoding="utf-8-sig"
    )  # BOM so Excel shows accents
    print(f"{len(leads)} leads -> {LEADS_CSV}")


if __name__ == "__main__":
    main()
