import json
from unittest.mock import MagicMock

import pytest
import requests

import outbox
from outbox import (
    PLACEHOLDER,
    draft_new,
    load_state,
    mark_sent,
    offer_message,
    render,
    save_state,
    system_prompt,
)

GIGA = (
    f"Perfil: Ian, Data & AI Engineer.\n\nLA OFERTA:\n{PLACEHOLDER}\n\nSalida: pitch."
)


def project(pid: int, bids: int = 2, **extra) -> dict:
    return {
        "id": pid,
        "titulo": f"Python scraper {pid}",
        "link": f"https://www.freelancer.com/projects/python/p-{pid}",
        "descripcion": "Scrape 3 shops daily into a CSV.",
        "tipo": "fixed",
        "moneda": "USD",
        "min": 100.0,
        "max": 300.0,
        "min_usd": 100.0,
        "max_usd": 300.0,
        "bids": bids,
        "publicado": "2026-11-24",
        "publicado_ts": 1795500000,
        "skills": ["Python", "Web Scraping"],
        **extra,
    }


def groq_session(*contents: str) -> MagicMock:
    session = MagicMock()
    session.post.side_effect = [
        MagicMock(ok=True, json=lambda c=c: {"choices": [{"message": {"content": c}}]})
        for c in contents
    ]
    return session


def test_system_prompt_is_the_giga_prompt_without_the_paste_placeholder():
    prompt = system_prompt(GIGA)

    assert PLACEHOLDER not in prompt
    assert prompt.startswith("Perfil: Ian, Data & AI Engineer.")
    assert "mensaje del usuario" in prompt


def test_system_prompt_rejects_the_unfilled_public_template():
    with pytest.raises(ValueError, match="giga_prompt_private"):
        system_prompt("Soy [TU_NOMBRE], [TU_ROL]\n" + PLACEHOLDER)


def test_offer_message_carries_the_raw_offer_data():
    message = offer_message(project(7, descripcion="Need a RAG bot."))

    assert "Python scraper 7" in message
    assert "USD 100-300" in message
    assert "Python, Web Scraping" in message
    assert "Need a RAG bot." in message


def test_load_state_returns_an_empty_state_when_missing_or_corrupt(tmp_path, caplog):
    missing = load_state(tmp_path / "nope.json")
    corrupt_path = tmp_path / "bad.json"
    corrupt_path.write_text("{not json", encoding="utf-8")

    assert missing == {"drafts": {}, "sent": [], "order": []}
    assert load_state(corrupt_path) == missing
    assert "corrupt" in caplog.text


def test_state_round_trips_through_disk(tmp_path):
    path = tmp_path / "state.json"
    state = {"drafts": {"7": {"pitch": "hi"}}, "sent": [3], "order": [7]}

    save_state(path, state)

    assert load_state(path) == state


def test_draft_new_calls_groq_once_per_new_project_with_the_giga_prompt():
    session = groq_session("pitch for 1", "pitch for 2")
    state = load_state(None)

    new = draft_new([project(1), project(2)], state, session, "SYS", limit=5)

    assert new["drafts"]["1"]["pitch"] == "pitch for 1"
    assert new["drafts"]["2"]["project"]["titulo"] == "Python scraper 2"
    assert new["order"] == [1, 2]
    messages = session.post.call_args_list[0].kwargs["json"]["messages"]
    assert messages[0] == {"role": "system", "content": "SYS"}
    assert "Python scraper 1" in messages[1]["content"]
    assert state == {"drafts": {}, "sent": [], "order": []}  # not mutated


def test_draft_new_skips_drafted_and_sent_projects_and_respects_the_limit():
    session = groq_session("pitch for 4")
    state = {"drafts": {"1": {"pitch": "old"}}, "sent": [2], "order": []}

    new = draft_new(
        [project(1), project(2), project(4), project(5)], state, session, "S", limit=1
    )

    assert session.post.call_count == 1
    assert set(new["drafts"]) == {"1", "4"}
    assert new["drafts"]["1"]["pitch"] == "old"


def test_draft_new_logs_and_skips_a_failed_groq_call(caplog):
    session = MagicMock()
    session.post.side_effect = [
        requests.ConnectionError("down"),
        MagicMock(ok=True, json=lambda: {"choices": [{"message": {"content": "ok"}}]}),
    ]

    new = draft_new([project(1), project(2)], load_state(None), session, "S", limit=5)

    assert set(new["drafts"]) == {"2"}
    assert "down" in caplog.text


def test_mark_sent_adds_ids_without_duplicates_or_mutation():
    state = {"drafts": {}, "sent": [3], "order": []}

    new = mark_sent(state, [3, 9])

    assert new["sent"] == [3, 9]
    assert state["sent"] == [3]


def test_render_lists_unsent_drafts_in_candidate_order_with_a_checkbox():
    state = draft_new(
        [project(1, bids=12), project(2, bids=1)],
        load_state(None),
        groq_session("PITCH ONE", "PITCH TWO"),
        "S",
        limit=5,
    )
    state = {**mark_sent(state, [1]), "order": [2, 1]}

    text = render(state)

    assert "- [ ] **Python scraper 2**" in text
    assert "PITCH TWO" in text
    assert "PITCH ONE" not in text
    assert "python outbox.py --sent 2" in text


def test_render_says_so_when_the_outbox_is_empty():
    assert "vacío" in render(load_state(None))


def test_build_drafts_saves_state_and_writes_the_outbox(tmp_path, monkeypatch):
    giga = tmp_path / "giga.txt"
    giga.write_text(GIGA, encoding="utf-8")
    monkeypatch.setattr(outbox, "GIGA_PROMPT", giga)
    monkeypatch.setattr(outbox, "STATE", tmp_path / "state.json")
    monkeypatch.setattr(outbox, "OUTPUT", tmp_path / "OUTBOX.md")

    state = outbox.build([project(1)], groq_session("THE PITCH"))

    assert state["drafts"]["1"]["pitch"] == "THE PITCH"
    assert json.loads((tmp_path / "state.json").read_text("utf-8"))["order"] == [1]
    assert "THE PITCH" in (tmp_path / "OUTBOX.md").read_text("utf-8")
