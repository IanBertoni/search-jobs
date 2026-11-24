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
        "publicado_ts": 1795500000,
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

    # 102: 25 bids; 103: < $50; 105: senior; 106: no keyword;
    # 108: hourly ~$8/h; 109: UX/UI; 112: "data" but only data-entry skills;
    # 104: only "Data Processing" and "SQL", no strictly technical skill
    assert sorted(kept) == [101, 107, 110, 111]


@pytest.mark.parametrize(
    "skills",
    [
        ["Home Automation"],
        ["Automation"],
        ["Data Processing"],
        ["AI Automation", "Data Entry", "Excel"],
    ],
)
def test_candidates_drop_projects_without_a_strict_dev_skill(skills):
    project = {**by_id(parse_projects(PAYLOAD))[101], "skills": skills}

    assert candidates([project]) == []


@pytest.mark.parametrize(
    "skill", ["Python", "Web Scraping", "scraping", "RAG", "LLM", "FastAPI"]
)
def test_candidates_keep_any_strict_dev_skill_ignoring_case(skill):
    project = {**by_id(parse_projects(PAYLOAD))[101], "skills": ["Excel", skill]}

    assert candidates([project]) != []


def test_candidates_put_under_5_bids_first_then_newest_first():
    kept = [p["id"] for p in candidates(parse_projects(PAYLOAD))]

    # 101 (3 bids) is the oldest but leads; 5-24 bids go newest first
    assert kept == [101, 111, 110, 107]


def test_candidates_keep_up_to_24_bids():
    project = by_id(parse_projects(PAYLOAD))[101]

    assert candidates([{**project, "bids": 24}]) != []
    assert candidates([{**project, "bids": 25}]) == []


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


def test_render_is_a_table_plus_full_descriptions():
    description = "A | B\nC " * 50
    project = {**by_id(parse_projects(PAYLOAD))[101], "descripcion": description}

    text = render([project])

    assert "| Título | Presupuesto | Bids | Publicado | Link | Descripción |" in text
    assert (
        "| Python web scraper for e-commerce prices | USD 100-300 | 3 | 2026-11-24 "
        "| https://www.freelancer.com/projects/python/"
        "Python-web-scraper-for-e-commerce-prices-101 | A / B C A / B C"
    ) in text
    # the table cell is clipped; the full text sits below, ready to paste
    assert "## Python web scraper for e-commerce prices" in text
    assert description.strip() in text
    assert "| Pitch" not in text


def test_radar_never_calls_groq():
    assert not hasattr(freelancer_radar, "groq_chat")
    assert not hasattr(freelancer_radar, "write_pitches")


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
