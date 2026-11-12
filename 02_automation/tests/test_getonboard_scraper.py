import json
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd
import pytest

from scrapers.getonboard_scraper import (
    LEAD_COLUMNS,
    SOURCES,
    build_leads,
    clean_jobs,
    fetch_jobs,
)

FIXTURE = Path(__file__).parent / "fixtures" / "getonboard_jobs.json"
NOW = datetime(2026, 11, 12, tzinfo=UTC)


@pytest.fixture
def raw_jobs():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))["data"]


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self.payload


class FakeSession:
    def __init__(self, payload):
        self.payload = payload
        self.urls = []

    def get(self, url, params=None, timeout=None):
        self.urls.append(url)
        return FakeResponse(self.payload)


def test_fetch_jobs_queries_every_source(raw_jobs):
    session = FakeSession({"data": raw_jobs})

    jobs = fetch_jobs(session=session, delay=0)

    assert len(session.urls) == len(SOURCES)
    assert len(jobs) == len(raw_jobs) * len(SOURCES)


def test_clean_jobs_extracts_fields(raw_jobs):
    df = clean_jobs(raw_jobs)

    first = df.iloc[0]
    assert len(df) == 3
    assert first["titulo"] == "Systems Analyst (Fluent English)"
    assert first["empresa"] == "Institute for Portfolio Alternative"
    assert first["link"].startswith("https://www.getonbrd.com/jobs/")
    assert "<" not in first["descripcion"]
    assert "Meticulous and systematic" in first["descripcion"]
    assert first["fecha"].year == 2026


def test_clean_jobs_drops_duplicates_across_sources(raw_jobs):
    assert len(clean_jobs(raw_jobs + raw_jobs)) == 3


def test_build_leads_has_tracking_columns(raw_jobs):
    leads = build_leads(clean_jobs(raw_jobs), NOW)

    assert list(leads.columns) == LEAD_COLUMNS
    assert leads["score_jd"].between(0, 6).all()
    assert leads["requisitos"].str.len().gt(0).all()
    assert (leads["fecha_scrapeo"] == "2026-11-12").all()


def test_build_leads_sorts_most_real_first(raw_jobs):
    leads = build_leads(clean_jobs(raw_jobs), NOW)

    assert leads["score_jd"].is_monotonic_decreasing


def test_build_leads_roundtrips_csv(raw_jobs, tmp_path):
    out = tmp_path / "leads.csv"

    build_leads(clean_jobs(raw_jobs), NOW).to_csv(out, index=False)

    assert list(pd.read_csv(out).columns) == LEAD_COLUMNS
