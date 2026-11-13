import json
import logging
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd
import pytest
import requests

from scrapers.ai_training_scraper import (
    AITRAINER_WORK_JOBS,
    AITRAINING_JOBS_FEED,
    fetch_aitrainer_work,
    fetch_aitraining_jobs,
)
from scrapers.getonboard_scraper import clean_jobs, dedupe_jobs

FIXTURES = Path(__file__).parent / "fixtures"
NOW = datetime(2026, 11, 12, 16, tzinfo=UTC)


class Response:
    def __init__(self, content=b"", status=200):
        self.content = content
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code} Error", response=self)

    def json(self):
        return json.loads(self.content)


class Session:
    def __init__(self, response):
        self.response = response
        self.urls = []

    def get(self, url, timeout=None):
        self.urls.append(url)
        return self.response


def aitrainer_work(**kwargs):
    content = (FIXTURES / "aitrainer_work_jobs.json").read_bytes()
    return fetch_aitrainer_work(Session(Response(content)), now=NOW, **kwargs)


def test_aitrainer_work_keeps_fresh_ai_training_titles_only():
    jobs = aitrainer_work()

    # the stale perle listing and the non-training "Chemistry Expert" are dropped
    assert list(jobs["titulo"]) == [
        "Software Engineering AI Trainer",
        "AI Coding Evaluator",
    ]
    assert set(jobs["empresa"]) == {"anyoneai"}
    assert list(jobs.columns) == list(clean_jobs([]).columns)


def test_aitrainer_work_gives_each_listing_its_own_link_and_pay_band():
    trainer = aitrainer_work().iloc[0]

    # every anyoneai apply_url is the same referral link, so it can't be the key
    assert (
        trainer["link"]
        == "https://aitrainer.work/jobs/anyoneai/software-engineering-ai-trainer"
    )
    assert (trainer["min_salary"], trainer["max_salary"]) == (45, 80)
    assert trainer["fecha"] == pd.Timestamp("2026-11-01T13:03:21.549Z")
    assert "Spot failing edge cases" in trainer["descripcion"]


def test_aitrainer_work_reads_the_open_data_feed_not_the_api():
    session = Session(Response((FIXTURES / "aitrainer_work_jobs.json").read_bytes()))

    fetch_aitrainer_work(session, now=NOW)

    assert session.urls == [AITRAINER_WORK_JOBS]
    assert "/data/jobs.json" in AITRAINER_WORK_JOBS  # robots.txt disallows /api/


def test_aitraining_jobs_splits_title_platform_and_pay():
    session = Session(Response((FIXTURES / "aitraining_jobs_feed.xml").read_bytes()))

    jobs = fetch_aitraining_jobs(session)

    assert session.urls == [AITRAINING_JOBS_FEED]
    assert "/feeds/rss.xml" in AITRAINING_JOBS_FEED  # robots.txt disallows /api/ (MCP)
    evaluator = jobs.iloc[0]
    assert len(jobs) == 1  # the Sales role isn't AI training
    assert evaluator["titulo"] == "AI Conversation Evaluator"
    assert evaluator["empresa"] == "micro1"
    assert (evaluator["min_salary"], evaluator["max_salary"]) == (40, 70)
    assert "Rate assistant conversations" in evaluator["descripcion"]


@pytest.mark.parametrize(
    ("fetch", "response"),
    [
        (fetch_aitrainer_work, Response(status=503)),
        (fetch_aitrainer_work, Response(b"{not json")),
        (fetch_aitrainer_work, Response(b'{"jobs": "nope"}')),
        (fetch_aitraining_jobs, Response(status=503)),
        (fetch_aitraining_jobs, Response(b"<rss><channel><item>")),
    ],
)
def test_source_failure_logs_error_and_returns_none(fetch, response, caplog):
    with caplog.at_level(logging.ERROR):
        assert fetch(Session(response)) is None

    assert "skipped" in caplog.text


def test_dedupe_keeps_the_same_role_listed_on_two_aggregators():
    jobs = pd.DataFrame(
        {
            "titulo": ["AI Coding Evaluator", "AI Coding Evaluator"],
            "empresa": ["anyoneai", "Anyone AI"],
            "link": ["https://aitrainer.work/jobs/x", "https://aitraining.jobs/jobs/y"],
        }
    )

    assert len(dedupe_jobs(jobs)) == 2  # different URLs: only exact URLs dedupe
