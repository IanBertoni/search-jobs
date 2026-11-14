import json
from pathlib import Path

import pytest

from freelance_radar import (
    hn_leads,
    location_ok,
    remoteok_leads,
    render,
    wwr_leads,
)

FIXTURES = Path(__file__).parent / "fixtures"
REMOTEOK = json.loads((FIXTURES / "remoteok_api.json").read_text("utf-8"))
WWR = (FIXTURES / "wwr_feed.xml").read_bytes()


def test_remoteok_keeps_remote_contract_tech_jobs_only():
    leads = remoteok_leads(REMOTEOK)

    assert [lead["empresa"] for lead in leads] == ["RagCo", "AutoInc"]
    assert leads[0] == {
        "fuente": "Remote OK",
        "titulo": "Python RAG Contractor",
        "empresa": "RagCo",
        "tarifa": "$60/hr",
        "link": "https://remoteOK.com/remote-jobs/1",
    }


def test_remoteok_uses_salary_fields_when_present():
    assert remoteok_leads(REMOTEOK)[1]["tarifa"] == "$50,000 - $70,000"


def test_wwr_keeps_contract_tech_jobs_in_allowed_regions():
    leads = wwr_leads(WWR)

    assert [(lead["empresa"], lead["titulo"]) for lead in leads] == [
        ("LatamBot", "Python Automation Developer"),
        ("GlobalAI", "RAG Engineer"),
    ]
    assert leads[0]["tarifa"] == "$40-55/hr"
    assert leads[1]["tarifa"] == "?"


def test_wwr_broken_xml_gives_no_leads():
    assert wwr_leads(b"<rss><channel>") == []


@pytest.mark.parametrize(
    ("where", "ok"),
    [
        ("Remote", True),
        ("Worldwide", True),
        ("Anywhere in the World", True),
        ("LATAM", True),
        ("Americas", True),
        ("", True),  # remote-only boards: no location means anywhere
        ("US Only", False),
        ("US based", False),
        ("EU Only", False),
        ("Remote, US only", False),
        ("Berlin, Germany", False),
    ],
)
def test_location_filter(where, ok):
    assert location_ok(where) is ok


def thread(*texts: str) -> dict:
    return {
        "children": [
            {"id": i, "author": "x", "text": t, "children": []}
            for i, t in enumerate(texts)
        ]
    }


def test_hn_keeps_only_contract_posts_and_doesnt_require_junior():
    leads = hn_leads(
        thread(
            "Gig | Data Engineer | Remote | Contract<p>Python, $70/hr.",
            "Perm | Data Engineer | Remote | Full-time<p>Python.",
            "Mixed | Data Engineer | Remote | Full-time, Contract<p>Python.",
        )
    )

    assert leads == [
        {
            "fuente": "HN",
            "titulo": "Data Engineer",
            "empresa": "Gig",
            "tarifa": "$70/hr",
            "link": "https://news.ycombinator.com/item?id=0",
        }
    ]


def test_render_is_a_table_with_the_requested_columns():
    text = render(remoteok_leads(REMOTEOK))

    assert "| Fuente | Título del Proyecto | Empresa | Tarifa | Link |" in text
    assert "| Remote OK | Python RAG Contractor | RagCo | $60/hr |" in text


def test_render_escapes_pipes_in_cells():
    lead = {
        "fuente": "HN",
        "titulo": "A | B",
        "empresa": "C",
        "tarifa": "?",
        "link": "l",
    }

    assert "| HN | A / B | C | ? | l |" in render([lead])


def test_remoteok_ignores_contract_words_in_the_description():
    job = {
        "id": "9",
        "company": "X",
        "position": "Python Engineer",
        "tags": ["python"],
        "location": "Remote",
        "description": "Translate needs into contract deliverables.",
        "url": "u",
    }

    assert remoteok_leads([job]) == []
