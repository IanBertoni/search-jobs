import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from cold_outreach import (
    SYSTEM_PROMPT,
    latest_hiring_thread,
    parse_offers,
    render,
    render_freelance,
    split_by_type,
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
    # every post must say it's junior to pass, so tests of other rules add it
    text = f"{text}<p>Junior role."
    return {"children": [{"id": 1, "author": "x", "text": text, "children": []}]}


def raw_comment(text: str) -> dict:
    return {"children": [{"id": 1, "author": "x", "text": text, "children": []}]}


@pytest.mark.parametrize(
    "seniority",
    [
        "Junior",
        "Entry level",
        "Entry-level",
        "New grad",
        "Trainee",
        "Associate",
        "Apprentice",
    ],
)
def test_parse_keeps_posts_that_say_junior(seniority):
    text = f"Co | {seniority} Data Engineer | Remote<p>Python."

    assert len(parse_offers(raw_comment(text))) == 1


def test_parse_drops_posts_that_dont_say_junior():
    assert parse_offers(raw_comment("Co | Data Engineer | Remote<p>Python.")) == []


@pytest.mark.parametrize(
    "text",
    [
        "Co | Founding Engineer | Remote<p>Python.",
        "Co | Data Engineer | Remote<p>Python, 3+ years.",
        "Co | Data Engineer | Remote<p>Python, 4+ years.",
        "Co | Staff Data Engineer | Remote<p>Python.",
        "Co | Tech Lead | Remote<p>Python.",
    ],
)
def test_parse_drops_senior_signals_even_if_junior(text):
    assert parse_offers(comment(text)) == []


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


@pytest.mark.parametrize(
    "text",
    [
        # PostHog
        "PostHog | AI Engineer | Remote or Hybrid UK<p>Python, PyTorch, maths.",
        # Lyra
        "Lyra | Applied AI Engineer | Remote (US)<p>Python. US Citizenship Required.",
        # Juro
        "Juro | AI Engineer | London, UK or Remote<p>Python, LLM agents.",
        "Kraus | Software Engineer | Remote<p>Python. Must meet U.S. defense eligibility.",
        "Co | Software Engineer | Remote<p>Python, Deep Learning.",
        "Co | AI Research Engineer | Remote<p>Python, LLM.",
        "Co | Software Engineer | Remote<p>Python. Director of platform.",
        "Co | Marketing Engineer | Remote<p>Python scripts.",
    ],
)
def test_parse_drops_dealbreakers_without_calling_groq(text):
    assert parse_offers(comment(text)) == []


def test_parse_keeps_full_stack_python_remote_latam():
    text = "Co | Full-Stack Python Engineer | Remote LATAM<p>We use FastAPI."

    assert [o["remoto"] for o in parse_offers(comment(text))] == ["Remote/LATAM"]


def test_location_dealbreaker_is_lifted_by_worldwide():
    text = "Co | Backend Engineer | Remote Worldwide, office in London<p>Python."

    assert len(parse_offers(comment(text))) == 1


def test_parse_needs_a_target_role_keyword():
    text = "Co | Frontend Developer | Remote<p>React, data viz, AI features."

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


@pytest.mark.parametrize(
    "kind", ["Contract", "Contractor", "Freelance", "Part-time", "part time"]
)
def test_parse_marks_contract_posts_as_freelance(kind):
    text = f"Co | Data Engineer | Remote | {kind}<p>Python."

    assert parse_offers(comment(text))[0]["tipo"] == "freelance"


def test_parse_keeps_mixed_full_time_and_contract_as_full_time():
    text = "Co | Data Engineer | Remote | Full-time, Contract<p>Python."

    assert parse_offers(comment(text))[0]["tipo"] == "full-time"


def test_parse_reads_budget_and_hours_when_the_post_says_them():
    text = "Co | Python Dev | Remote | Contract<p>$60-80/hr, 20 hours/week."

    assert parse_offers(comment(text))[0]["presupuesto"] == "$60-80/hr, 20 hours/week"


def test_contract_offers_never_reach_groq():
    full = parse_offers(comment("Co | Data Engineer | Remote<p>Python."))
    gig = parse_offers(comment("Gig | Data Engineer | Remote | Freelance<p>Python."))
    session = json_session(groq_reply())

    emails, leads = split_by_type(full + gig)
    drafts = write_emails(emails, session)

    assert session.post.call_count == 1
    assert [d["empresa"] for d in drafts] == ["Co"]
    assert [lead["empresa"] for lead in leads] == ["Gig"]


def test_render_freelance_puts_remote_or_worldwide_first_in_a_table():
    latam = parse_offers(comment("Latam | Data Engineer | LATAM | Contract<p>Py."))
    world = parse_offers(comment("World | Data Engineer | Worldwide | Contract<p>Py."))

    text = render_freelance(latam + world)

    assert "| Empresa | Rol | Link | Presupuesto/Horas |" in text
    assert text.index("World") < text.index("Latam")
    assert "| ? |" in text  # no budget in the post


def gmail_draft(link: str, texto: str = "Email jobs@acme.io") -> dict:
    return {
        "empresa": "Acme",
        "rol": "Data Engineer",
        "link": link,
        "texto": texto,
        "email": "Hey jane, saw the Data Engineer role.",
    }


def test_subject_names_the_role_and_company():
    from cold_outreach import subject_for

    assert subject_for(gmail_draft("x")) == "Data Engineer @ Acme (HN Who is hiring)"
    assert subject_for({**gmail_draft("x"), "rol": "?"}) == "Acme (HN Who is hiring)"


def test_push_gmail_drafts_creates_one_draft_per_new_email_and_never_sends():
    from cold_outreach import push_gmail_drafts

    session = MagicMock()
    session.post.return_value = MagicMock(ok=True, json=lambda: {"id": "d1"})
    drafts = [gmail_draft("hn/1"), gmail_draft("hn/2", texto="no address here")]

    created = push_gmail_drafts(drafts, session, done={"hn/0"})

    assert created == ["hn/1", "hn/2"]
    urls = [c.args[0] for c in session.post.call_args_list]
    assert all(url.endswith("/drafts") for url in urls)
    assert not any("send" in url for url in urls)


def test_push_gmail_drafts_skips_done_links_and_logs_failures(caplog):
    from cold_outreach import push_gmail_drafts

    session = MagicMock()
    session.post.side_effect = [
        MagicMock(ok=False, status_code=403, text="denied"),
        MagicMock(ok=True, json=lambda: {"id": "d2"}),
    ]
    drafts = [gmail_draft("hn/0"), gmail_draft("hn/1"), gmail_draft("hn/2")]

    assert push_gmail_drafts(drafts, session, done={"hn/0"}) == ["hn/2"]
    assert "403" in caplog.text


@pytest.mark.parametrize(
    "location",
    ["REMOTE (US)", "Remote (USA)", "remote (U.S. only)", "Remote (US-only)"],
)
def test_parse_drops_remote_us_posts(location):
    post = f"Acme | Python Engineer | {location} | Contract<p>Python backend."

    assert parse_offers(raw_comment(post), junior_only=False) == []


def test_parse_keeps_remote_us_plus_other_regions():
    post = "Acme | Python Engineer | Remote (US, LATAM) | Contract<p>Python backend."

    assert len(parse_offers(raw_comment(post), junior_only=False)) == 1
