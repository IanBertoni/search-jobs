import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from cold_outreach import (
    SYSTEM_PROMPT,
    latest_hiring_thread,
    parse_offers,
    render,
    write_emails,
)

FIXTURES = Path(__file__).parent / "fixtures"
SEARCH = json.loads((FIXTURES / "hn_whoishiring_search.json").read_text("utf-8"))
ITEMS = json.loads((FIXTURES / "hn_whoishiring_items.json").read_text("utf-8"))
EMAIL = "Hey jane_founder, saw you're hiring a Data Engineer.\ngithub.com/IanBertoni"


def json_session(*payloads):
    responses = [MagicMock(ok=True, json=MagicMock(return_value=p)) for p in payloads]
    session = MagicMock()
    session.get.side_effect = responses
    session.post.side_effect = responses
    return session


def groq_reply(content: str = EMAIL) -> dict:
    return {"choices": [{"message": {"content": content}}]}


def test_latest_thread_skips_who_wants_to_be_hired():
    session = json_session(SEARCH)

    assert latest_hiring_thread(session) == "45800001"


def comment(text: str) -> dict:
    return {"children": [{"id": 1, "author": "x", "text": text, "children": []}]}


def test_parse_keeps_only_matching_remote_non_senior_offers():
    offers = parse_offers(ITEMS)

    assert [o["empresa"] for o in offers] == ["Acme AI", "Ferrous", "Globo"]
    assert offers[2]["remoto"] == "Remote"  # Worldwide counts as remote
    acme = offers[0]
    assert acme["rol"] == "Data Engineer"
    assert acme["remoto"] == "Remote/LATAM"
    assert acme["autor"] == "jane_founder"
    assert acme["link"] == "https://news.ycombinator.com/item?id=101"
    assert offers[1]["remoto"] == "Remote"


@pytest.mark.parametrize(
    "text",
    [
        "Co | Data Engineer | Remote<p>Python. Position filled.",
        "Co | Data Engineer | Remote<p>Python. Role filled, thanks.",
        "Co | Data Engineer | Remote<p>Python. Applications closed.",
        "Co | Data Engineer | Remote<p>Python. We are no longer hiring.",
        "UPDATE (Nov 13): paused.Co | Data Engineer | Remote<p>Python.",
    ],
)
def test_parse_drops_closed_posts(text):
    assert parse_offers(comment(text)) == []


@pytest.mark.parametrize(
    "text",
    [
        "Brandly | Brand Designer | Remote<p>Figma, branding.",  # remote only
        "OnsiteAI | Python Developer | Paris<p>Python, AI.",  # tech only
    ],
)
def test_parse_needs_remote_and_a_tech_keyword(text):
    assert parse_offers(comment(text)) == []


@pytest.mark.parametrize("where", ["Remote", "Worldwide", "LATAM"])
def test_parse_accepts_any_remote_wording(where):
    assert len(parse_offers(comment(f"Co | Software Engineer | {where}"))) == 1


def test_parse_ignores_replies_to_job_posts():
    offers = parse_offers(ITEMS)

    assert all("curious" != o["autor"] for o in offers)


def test_prompt_forbids_ai_tone_and_names_the_github():
    assert "I am writing to express" in SYSTEM_PROMPT
    assert "github.com/IanBertoni" in SYSTEM_PROMPT
    assert "5" in SYSTEM_PROMPT


def test_write_emails_calls_groq_once_per_offer_and_never_sends():
    offers = parse_offers(ITEMS)[:2]
    session = json_session(groq_reply(), groq_reply("Hey bob"))

    drafts = write_emails(offers, session)

    assert session.post.call_count == 2
    assert drafts[0]["email"] == EMAIL
    sent = session.post.call_args_list[0].kwargs["json"]["messages"][1]["content"]
    assert "Acme AI" in sent and "Data Engineer" in sent


def test_prompt_asks_for_skip_company_line_and_role_inference():
    assert "SKIP" in SYSTEM_PROMPT
    assert "what the company does" in SYSTEM_PROMPT
    assert "Full-Time" in SYSTEM_PROMPT


def test_write_emails_drops_offers_groq_marks_as_skip():
    offers = parse_offers(ITEMS)
    session = json_session(groq_reply("SKIP"), groq_reply(), groq_reply(" SKIP\n"))

    drafts = write_emails(offers, session)

    assert [d["empresa"] for d in drafts] == ["Ferrous"]


def test_write_emails_capitalizes_danagro_precision():
    offers = parse_offers(ITEMS)[:1]
    session = json_session(groq_reply("did rust work at danagro precision."))

    drafts = write_emails(offers, session)

    assert drafts[0]["email"] == "did rust work at DanAgro Precision."


def test_write_emails_skips_an_offer_when_groq_fails(caplog):
    offers = parse_offers(ITEMS)[:2]
    failed = MagicMock(ok=False, status_code=500, text="boom")
    session = MagicMock()
    session.post.side_effect = [failed, MagicMock(ok=True, json=lambda: groq_reply())]

    drafts = write_emails(offers, session)

    assert [d["empresa"] for d in drafts] == ["Ferrous"]
    assert "Acme AI" in caplog.text


def test_render_flags_emails_that_sound_like_ai():
    drafts = [
        {**parse_offers(ITEMS)[0], "email": EMAIL},
        {**parse_offers(ITEMS)[1], "email": "I am writing to express my interest."},
    ]

    text = render(drafts)

    assert "## Acme AI — Data Engineer" in text
    assert "https://news.ycombinator.com/item?id=101" in text
    assert EMAIL in text
    assert text.count("REVISAR") == 1
