from unittest.mock import MagicMock

import pytest
import requests

import pitch
from pitch import PLACEHOLDER, draft, load_system_prompt, system_prompt

GIGA = (
    f"Perfil: Ian, Data & AI Engineer.\n\nLA OFERTA:\n{PLACEHOLDER}\n\nSalida: pitch."
)


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


def test_load_system_prompt_reads_the_private_giga_prompt(tmp_path, monkeypatch):
    giga = tmp_path / "giga.txt"
    giga.write_text(GIGA, encoding="utf-8")
    monkeypatch.setattr(pitch, "GIGA_PROMPT", giga)

    assert load_system_prompt() == system_prompt(GIGA)


def test_draft_sends_the_giga_prompt_as_system_and_the_offer_as_user():
    session = groq_session("THE PITCH")

    assert draft("SYS", "the offer", session) == "THE PITCH"
    messages = session.post.call_args.kwargs["json"]["messages"]
    assert messages == [
        {"role": "system", "content": "SYS"},
        {"role": "user", "content": "the offer"},
    ]


def test_draft_returns_none_and_logs_when_groq_fails(caplog):
    session = MagicMock()
    session.post.side_effect = requests.ConnectionError("down")

    assert draft("SYS", "offer", session) is None
    assert "down" in caplog.text
