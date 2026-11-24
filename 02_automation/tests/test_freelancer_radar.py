import json
from pathlib import Path

import pytest
import requests

import freelancer_radar
from freelancer_radar import (
    budget_label,
    candidates,
    fetch_all,
    fetch_projects,
    parse_projects,
    render,
    write_pitches,
)

FIXTURES = Path(__file__).parent / "fixtures"
PAYLOAD = json.loads((FIXTURES / "freelancer_projects.json").read_text("utf-8"))


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr(freelancer_radar.time, "sleep", lambda _s: None)


def by_id(projects: list[dict]) -> dict[int, dict]:
    return {p["id"]: p for p in projects}


def test_parse_extracts_the_fields_and_converts_budget_to_usd():
    projects = by_id(parse_projects(PAYLOAD))

    assert projects[101] == {
        "id": 101,
        "titulo": "Python web scraper for e-commerce prices",
        "link": "https://www.freelancer.com/projects/python/"
        "Python-web-scraper-for-e-commerce-prices-101",
        "descripcion": "Scrape 3 shops daily into a CSV.",
        "tipo": "fixed",
        "moneda": "USD",
        "min": 100.0,
        "max": 300.0,
        "min_usd": 100.0,
        "max_usd": 300.0,
        "bids": 3,
        "publicado": "2026-11-24",
        "skills": ["Python", "Web Scraping"],
    }
    assert projects[104]["min_usd"] == pytest.approx(135.0)


def test_parse_rejects_an_api_error():
    with pytest.raises(ValueError, match="rate"):
        parse_projects({"status": "error", "message": "rate limited"})


def test_parse_skips_malformed_projects():
    payload = {"status": "success", "result": {"projects": [{"id": 1}, "x"]}}

    assert parse_projects(payload) == []


def test_candidates_apply_the_hard_filter():
    kept = [p["id"] for p in candidates(parse_projects(PAYLOAD))]

    # 102: 15 bids; 103: < $50; 105: senior; 106: no keyword;
    # 108: hourly ~$8/h; 109: UX/UI; 112: "data" but only data-entry skills
    assert sorted(kept) == [101, 104, 107, 110, 111]


def test_candidates_need_a_dev_skill_not_just_a_keyword():
    project = {**by_id(parse_projects(PAYLOAD))[101], "skills": ["Home Automation"]}

    assert candidates([project]) == []


def test_candidates_put_fewest_bids_first():
    kept = [p["id"] for p in candidates(parse_projects(PAYLOAD))]

    assert kept == [101, 104, 107, 111, 110]


@pytest.mark.parametrize(
    ("project_id", "label"),
    [
        (101, "USD 100-300"),
        (104, "INR 12,500-37,500 (~USD 135-405)"),
        (107, "USD 20-30/h"),
    ],
)
def test_budget_label(project_id, label):
    assert budget_label(by_id(parse_projects(PAYLOAD))[project_id]) == label


def test_budget_label_without_maximum():
    project = {**by_id(parse_projects(PAYLOAD))[107], "max": 0.0, "max_usd": 0.0}

    assert budget_label(project) == "USD 20+/h"


def test_write_pitches_keeps_pitch_and_drops_skip(monkeypatch):
    replies = iter(["Line 1\nLine 2\nLine 3", "SKIP"])
    prompts = []

    def fake_chat(system, user, session):
        prompts.append(user)
        return next(replies)

    monkeypatch.setattr(freelancer_radar, "groq_chat", fake_chat)
    projects = candidates(parse_projects(PAYLOAD))[:2]

    pitched = write_pitches(projects, session=None)

    assert [p["id"] for p in pitched] == [101]
    assert pitched[0]["pitch"] == "Line 1\nLine 2\nLine 3"
    assert "Scrape 3 shops daily" in prompts[0]


def test_write_pitches_retries_a_429_once_and_skips_other_errors(monkeypatch):
    calls = []

    def fake_chat(system, user, session):
        calls.append(user)
        if len(calls) == 1:
            raise requests.HTTPError("429: slow down")
        if len(calls) == 3:
            raise requests.HTTPError("500: boom")
        return "pitch"

    monkeypatch.setattr(freelancer_radar, "groq_chat", fake_chat)
    projects = candidates(parse_projects(PAYLOAD))[:2]

    pitched = write_pitches(projects, session=None)

    assert [p["id"] for p in pitched] == [101]
    assert len(calls) == 3


def test_render_is_a_table_with_one_line_pitches():
    project = {**by_id(parse_projects(PAYLOAD))[101], "pitch": "A | B\nC"}

    text = render([project])

    assert "| Título | Presupuesto | Bids | Publicado | Link | Pitch |" in text
    assert (
        "| Python web scraper for e-commerce prices | USD 100-300 | 3 | 2026-11-24 "
        "| https://www.freelancer.com/projects/python/"
        "Python-web-scraper-for-e-commerce-prices-101 | A / B<br>C |"
    ) in text


class FakeResponse:
    def __init__(self, payload, status=200):
        self.payload, self.status_code = payload, status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code}")

    def json(self):
        return self.payload


class FakeSession:
    def __init__(self, pages):
        self.pages, self.calls = list(pages), []

    def get(self, url, params=None, timeout=None):
        self.calls.append(params)
        return self.pages.pop(0)


def page(projects, total):
    return {"status": "success", "result": {"projects": projects, "total_count": total}}


def test_fetch_projects_pages_until_total_count():
    projects = PAYLOAD["result"]["projects"]
    session = FakeSession(
        [FakeResponse(page(projects[:6], 12)), FakeResponse(page(projects[6:], 12))]
    )

    fetched = fetch_projects(session, "python", page_size=6)

    assert len(fetched) == 12
    assert [c["offset"] for c in session.calls] == [0, 6]
    assert session.calls[0]["query"] == "python"
    assert session.calls[0]["full_description"] == "true"


def test_fetch_all_dedupes_and_skips_a_failing_query(monkeypatch):
    monkeypatch.setattr(freelancer_radar, "QUERIES", ("python", "data", "rag"))
    projects = PAYLOAD["result"]["projects"]
    session = FakeSession(
        [
            FakeResponse(page(projects[:3], 3)),
            FakeResponse({}, status=403),
            FakeResponse(page(projects[2:4], 2)),
        ]
    )

    fetched = fetch_all(session)

    assert [p["id"] for p in fetched] == [101, 102, 103, 104]
