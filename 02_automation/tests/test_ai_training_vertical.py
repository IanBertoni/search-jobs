import json
import logging
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd
import pytest
import requests

from scrapers import getonboard_scraper, remotive_scraper
from scrapers.getonboard_scraper import (
    SOURCES,
    build_leads,
    clean_jobs,
    dedupe_jobs,
    keep_target_levels,
)
from scrapers.remotive_scraper import REMOTIVE_FEED, fetch_remotive

FIXTURES = Path(__file__).parent / "fixtures"
REMOTIVE_XML = (FIXTURES / "remotive_ai_feed.xml").read_bytes()
TOLOKA = json.loads((FIXTURES / "toloka_jobs.json").read_text(encoding="utf-8"))
GETONBOARD = json.loads((FIXTURES / "getonboard_jobs.json").read_text(encoding="utf-8"))
NOW = datetime(2026, 11, 12, tzinfo=UTC)


class FeedResponse:
    def __init__(self, content=REMOTIVE_XML, status=200):
        self.content = content
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code} Error", response=self)


class FeedSession:
    def __init__(self, response):
        self.response = response
        self.urls = []

    def get(self, url, timeout=None):
        self.urls.append(url)
        return self.response


def test_fetch_remotive_reads_the_ai_category_rss():
    session = FeedSession(FeedResponse())

    jobs = fetch_remotive(session=session)

    assert session.urls == [REMOTIVE_FEED]
    assert "remote-jobs/feed/artificial-intelligence" in REMOTIVE_FEED  # not /api/*
    trainer = jobs.iloc[0]
    assert trainer["titulo"] == "AI Trainer - Video Annotation"
    assert trainer["empresa"] == "iMerit Technology"
    assert trainer["link"].endswith("ai-trainer-video-annotation-2091161")
    assert trainer["fecha"] == pd.Timestamp("2026-10-22 18:40:19", tz="UTC")
    assert "Review model outputs for accuracy" in trainer["descripcion"]
    assert "<" not in trainer["descripcion"]
    assert list(jobs.columns) == list(clean_jobs([]).columns)


@pytest.mark.parametrize(
    "response",
    [FeedResponse(status=503), FeedResponse(content=b"<rss><channel><item>")],
    ids=["http-503", "broken-xml"],
)
def test_fetch_remotive_failure_logs_error_and_returns_none(response, caplog):
    with caplog.at_level(logging.ERROR):
        assert fetch_remotive(session=FeedSession(response)) is None

    assert "remotive" in caplog.text.lower()


def test_toloka_company_page_and_training_searches_are_sources():
    assert ("companies/toloka-ai/jobs", {}) in SOURCES
    assert ("search/jobs", {"query": "evaluator"}) in SOURCES
    assert ("search/jobs", {"query": "trainer"}) in SOURCES


def test_leads_carry_vertical_column():
    jobs = clean_jobs(TOLOKA["data"] + GETONBOARD["data"])

    leads = build_leads(jobs, NOW).set_index("empresa")

    assert leads.loc["Toloka AI", "vertical"] == "ai_training"
    assert leads.loc["Institute for Portfolio Alternative", "vertical"] == "data_ai"


def test_ai_training_without_seniority_skips_the_junior_keyword_check():
    remotive = fetch_remotive(FeedSession(FeedResponse()))
    jobs = pd.concat([clean_jobs(TOLOKA["data"]), remotive], ignore_index=True)
    jobs.loc[jobs["titulo"].str.startswith("Systems"), "descripcion"] = "no level"

    kept = keep_target_levels(jobs)

    # both training roles stay; the data role without seniority or keyword goes
    assert set(kept["empresa"]) == {"Toloka AI", "iMerit Technology"}


def test_dedupe_by_link_or_by_title_and_company():
    remotive = fetch_remotive(FeedSession(FeedResponse()))
    getonboard = clean_jobs(GETONBOARD["data"])
    same_link = getonboard.iloc[[0]]

    jobs = dedupe_jobs(pd.concat([getonboard, same_link, remotive]))

    analysts = jobs[jobs["titulo"] == "Systems Analyst (Fluent English)"]
    assert len(analysts) == 1
    assert analysts["link"].iloc[0].startswith("https://www.getonbrd.com")
    assert len(jobs) == len(getonboard) + 1  # + the Remotive AI trainer


def test_main_merges_remotive_into_the_snapshot(tmp_path, monkeypatch):
    monkeypatch.setattr(getonboard_scraper, "fetch_jobs", lambda: (TOLOKA["data"], []))
    monkeypatch.setattr(
        remotive_scraper,
        "fetch_remotive",
        lambda: fetch_remotive(FeedSession(FeedResponse())),
    )
    monkeypatch.setattr(getonboard_scraper, "setup_logging", lambda verbose: None)
    monkeypatch.setattr(getonboard_scraper, "TRACKING_DIR", tmp_path)
    monkeypatch.setattr("sys.argv", ["getonboard_scraper"])

    getonboard_scraper.main()

    (snapshot,) = tmp_path.glob("leads_????-??-??.csv")
    leads = pd.read_csv(snapshot, encoding="utf-8-sig")
    assert set(leads["empresa"]) >= {"Toloka AI", "iMerit Technology"}
    assert set(leads["vertical"]) >= {"ai_training"}
