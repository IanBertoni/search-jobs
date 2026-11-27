import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest
import requests

import ats_radar
from ats_radar import (
    ashby_jobs,
    candidates,
    fetch_all,
    greenhouse_jobs,
    lever_jobs,
    location_ok,
    render,
    screen,
)

FIXTURES = Path(__file__).parent / "fixtures"


def fixture(name: str):
    return json.loads((FIXTURES / name).read_text("utf-8"))


def job(titulo="Data Engineer", ubicacion="LatAm", remoto=True, **extra) -> dict:
    return {
        "empresa": "acme",
        "titulo": titulo,
        "ubicacion": ubicacion,
        "remoto": remoto,
        "link": f"https://jobs.example.com/{titulo}",
        "texto": "1+ years with Python",
        "publicado": "2026-11-01",
        **extra,
    }


def test_greenhouse_jobs_unescape_and_strip_the_html_content():
    jobs = greenhouse_jobs("clara", fixture("ats_greenhouse.json"))

    assert jobs[0]["titulo"] == "AI-Native Engineer, New Grad (Hybrid)"
    assert jobs[0]["empresa"] == "clara"
    assert jobs[0]["ubicacion"] == "Monterrey / NLE / Mexico"
    assert jobs[0]["link"].startswith("https://job-boards.greenhouse.io/clara/")
    assert jobs[0]["publicado"] == "2026-10-02"
    assert "<" not in jobs[0]["texto"] and "&lt;" not in jobs[0]["texto"]
    assert jobs[0]["remoto"] is False


def test_lever_jobs_include_requirement_lists_and_remote_flag():
    jobs = lever_jobs("xepelin", fixture("ats_lever.json"))

    assert [j["titulo"] for j in jobs] == ["Data Scientist I", "Python Data Engineer"]
    assert "Santiago" in jobs[0]["ubicacion"]
    assert jobs[0]["remoto"] is False
    assert jobs[1]["remoto"] is True
    assert "1+ years with Python" in jobs[1]["texto"]
    assert jobs[1]["link"] == "https://jobs.lever.co/acme/r1"
    assert jobs[1]["publicado"] == "2026-10-26"


def test_ashby_jobs_read_remote_flag_and_locations():
    jobs = ashby_jobs("truelogic", fixture("ats_ashby.json"))

    assert jobs[0]["titulo"] == "Data Engineer - Open Application"
    assert jobs[0]["ubicacion"] == "LatAm"
    assert jobs[0]["remoto"] is True
    assert jobs[0]["publicado"] == "2025-01-15"
    assert "TRUELOGIC" in jobs[0]["texto"]


@pytest.mark.parametrize("payload", [{}, [], {"jobs": None}, None, "boom"])
def test_malformed_payloads_give_no_jobs(payload):
    assert greenhouse_jobs("x", payload) == []
    assert lever_jobs("x", payload) == []
    assert ashby_jobs("x", payload) == []


@pytest.mark.parametrize(
    ("ubicacion", "remoto", "ok"),
    [
        ("LatAm", True, True),
        ("Buenos Aires / C / Argentina", False, True),  # hybrid in Argentina
        ("Remote, Global", True, True),
        ("Remote, AMER", True, True),
        ("Remote", True, True),
        ("Monterrey / NLE / Mexico", False, False),  # hybrid abroad
        ("Remote, San Francisco, CA, US", True, False),
        ("Portugal (Remote)", True, False),
        ("Brazil (Remote)", True, False),  # remote but residency-bound
    ],
)
def test_location_ok(ubicacion, remoto, ok):
    assert location_ok(ubicacion, remoto) is ok


def test_candidates_keep_tech_roles_doable_from_argentina_without_senior_titles():
    jobs = [
        job("Data Engineer - Open Application"),
        job("Senior Data Engineer"),
        job("Staff ML Engineer"),
        job("Account Executive"),
        job("AI-Native Engineer, New Grad", "Monterrey / NLE / Mexico", False),
        job("Python SDK Engineer", "Remote, Global"),
    ]

    titles = [j["titulo"] for j in candidates(jobs)]

    assert titles == ["Data Engineer - Open Application", "Python SDK Engineer"]


def groq_session(*replies) -> MagicMock:
    session = MagicMock()
    session.post.side_effect = [
        r
        if isinstance(r, MagicMock)
        else MagicMock(
            ok=True,
            json=MagicMock(return_value={"choices": [{"message": {"content": r}}]}),
        )
        for r in replies
    ]
    return session


def verdict(kind: str, years: int | None = 1) -> str:
    return json.dumps(
        {
            "verdict": kind,
            "min_years": years,
            "degree_required": False,
            "from_argentina": True,
            "reason": f"{kind} reason",
        }
    )


def test_screen_keeps_apply_and_stretch_drops_skip_and_flags_failures(monkeypatch):
    monkeypatch.setattr(ats_radar.time, "sleep", lambda _s: None)
    jobs = [job("A"), job("B"), job("C"), job("D")]
    session = groq_session(
        verdict("apply"),
        verdict("skip", 5),
        MagicMock(ok=False, status_code=500, text="boom"),
        verdict("stretch", 3),
    )

    screened = screen(jobs, session)

    assert [(j["titulo"], j["verdict"]) for j in screened] == [
        ("A", "apply"),
        ("C", "?"),  # Groq failed: kept for a manual look, never lost
        ("D", "stretch"),
    ]
    assert screened[0]["reason"] == "apply reason"


def test_screen_retries_once_after_a_rate_limit(monkeypatch):
    waits = []
    monkeypatch.setattr(ats_radar.time, "sleep", waits.append)
    session = groq_session(
        MagicMock(ok=False, status_code=429, text="rate limited"), verdict("apply")
    )

    screened = screen([job("A")], session)

    assert screened[0]["verdict"] == "apply"
    assert ats_radar.RATE_LIMIT_WAIT_S in waits


def test_render_orders_apply_then_stretch_then_unjudged_newest_first():
    rows = [
        job(
            "Old apply",
            verdict="apply",
            min_years=1,
            reason="r",
            publicado="2026-01-01",
        ),
        job("Unjudged", verdict="?", min_years=None, reason="Groq failed"),
        job("Stretch", verdict="stretch", min_years=3, reason="r"),
        job("New apply", verdict="apply", min_years=None, reason="r"),
    ]

    text = render(rows)

    order = [text.index(t) for t in ("New apply", "Old apply", "Stretch", "Unjudged")]
    assert order == sorted(order)
    assert "| acme | New apply | LatAm | apply | ? |" in text


def test_fetch_all_reads_each_board_and_skips_a_failing_one(monkeypatch):
    monkeypatch.setattr(ats_radar.time, "sleep", lambda _s: None)
    monkeypatch.setattr(
        ats_radar,
        "COMPANIES",
        [("greenhouse", "clara"), ("lever", "down"), ("ashby", "truelogic")],
    )
    payloads = {
        "https://boards-api.greenhouse.io/v1/boards/clara/jobs": fixture(
            "ats_greenhouse.json"
        ),
        "https://api.ashbyhq.com/posting-api/job-board/truelogic": fixture(
            "ats_ashby.json"
        ),
    }

    def get(url, **_):
        if url not in payloads:
            raise requests.ConnectionError("down")
        return MagicMock(json=MagicMock(return_value=payloads[url]))

    session = MagicMock()
    session.get.side_effect = get

    jobs = fetch_all(session)

    assert {j["empresa"] for j in jobs} == {"clara", "truelogic"}
    assert len(jobs) == 4


def test_portal_companies_join_the_radar_without_duplicate_boards():
    assert ats_radar.PORTAL_COMPANIES
    assert set(ats_radar.PORTAL_COMPANIES) <= set(ats_radar.COMPANIES)
    assert len(set(ats_radar.COMPANIES)) == len(ats_radar.COMPANIES)
    assert {ats for ats, _ in ats_radar.COMPANIES} <= set(ats_radar.BOARD_URLS)
