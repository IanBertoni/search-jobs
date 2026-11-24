from unittest.mock import MagicMock

import pytest
import requests

import radar_watch
from radar_watch import TELEGRAM_MAX_CHARS, hot, message, notify, send_telegram


def project(pid: int, bids: int = 2) -> dict:
    return {
        "id": pid,
        "titulo": f"Python scraper {pid}",
        "link": f"https://www.freelancer.com/projects/python/p-{pid}",
        "descripcion": "Scrape 3 shops daily.",
        "tipo": "fixed",
        "moneda": "USD",
        "min": 100.0,
        "max": 300.0,
        "min_usd": 100.0,
        "max_usd": 300.0,
        "bids": bids,
        "publicado": "2026-11-24",
        "publicado_ts": 1795500000,
        "skills": ["Python"],
    }


@pytest.fixture
def telegram(monkeypatch):
    monkeypatch.setattr(radar_watch, "TELEGRAM_BOT_TOKEN", "TOKEN")
    monkeypatch.setattr(radar_watch, "TELEGRAM_CHAT_ID", "42")


def test_hot_keeps_only_unnotified_projects_under_five_bids():
    projects = [project(1, bids=0), project(2, bids=4), project(3, bids=5)]

    assert [p["id"] for p in hot(projects, {2})] == [1]


def test_message_has_title_budget_bids_link_and_the_pitch():
    text = message(project(1, bids=3), "Hi, I can build it.")

    assert "Python scraper 1" in text
    assert "USD 100-300" in text
    assert "3 bids" in text
    assert "https://www.freelancer.com/projects/python/p-1" in text
    assert "Hi, I can build it." in text


def test_message_without_a_pitch_points_to_the_giga_prompt():
    assert "giga prompt" in message(project(1), None)


def test_message_fits_in_one_telegram_message():
    assert len(message(project(1), "x" * 10_000)) <= TELEGRAM_MAX_CHARS


def test_send_telegram_posts_to_the_bot_api(telegram):
    session = MagicMock()
    session.post.return_value = MagicMock(ok=True)

    send_telegram("hola", session)

    url = session.post.call_args.args[0]
    payload = session.post.call_args.kwargs["json"]
    assert url == "https://api.telegram.org/botTOKEN/sendMessage"
    assert payload["chat_id"] == "42"
    assert payload["text"] == "hola"


def test_send_telegram_raises_on_an_api_error(telegram):
    session = MagicMock()
    session.post.return_value = MagicMock(ok=False, status_code=401, text="nope")

    with pytest.raises(RuntimeError, match="401"):
        send_telegram("hola", session)


def test_send_telegram_requires_the_bot_settings(monkeypatch):
    monkeypatch.setattr(radar_watch, "TELEGRAM_BOT_TOKEN", "")

    with pytest.raises(RuntimeError, match="TELEGRAM_BOT_TOKEN"):
        send_telegram("hola", MagicMock())


def test_notify_sends_each_hot_project_once_and_returns_the_new_ids(telegram):
    session = MagicMock()
    session.post.return_value = MagicMock(ok=True)
    drafts = {"1": {"pitch": "PITCH ONE"}}

    sent = notify([project(1), project(2)], drafts, session)

    assert sent == [1, 2]
    assert "PITCH ONE" in session.post.call_args_list[0].kwargs["json"]["text"]


def test_notify_does_not_mark_a_failed_send_as_notified(telegram, caplog):
    session = MagicMock()
    session.post.side_effect = [
        MagicMock(ok=False, status_code=500, text="boom"),
        MagicMock(ok=True),
    ]

    assert notify([project(1), project(2)], {}, session) == [2]
    assert "500" in caplog.text


def test_run_writes_leads_drafts_pitches_and_notifies_new_hot_projects(
    tmp_path, monkeypatch, telegram
):
    projects = [project(1, bids=1), project(2, bids=10), project(3, bids=2)]
    monkeypatch.setattr(radar_watch, "fetch_all", lambda _s: projects)
    monkeypatch.setattr(radar_watch, "candidates", lambda ps: ps)
    monkeypatch.setattr(radar_watch, "LEADS_OUTPUT", tmp_path / "leads.md")
    monkeypatch.setattr(radar_watch, "NOTIFIED", tmp_path / "notified.json")
    monkeypatch.setattr(radar_watch, "GROQ_API_KEY", "key")
    radar_watch.NOTIFIED.write_text("[3]", encoding="utf-8")
    built = MagicMock(return_value={"drafts": {"1": {"pitch": "P1"}}})
    monkeypatch.setattr(radar_watch.outbox, "build", built)
    session = MagicMock()
    session.post.return_value = MagicMock(ok=True)

    summary = radar_watch.run(session)

    assert summary == {"proyectos": 3, "candidatos": 3, "avisados": 1}
    built.assert_called_once_with(projects, session)
    assert session.post.call_count == 1  # only project 1: 2 is crowded, 3 was sent
    assert "Python scraper 1" in (tmp_path / "leads.md").read_text("utf-8")
    assert radar_watch.NOTIFIED.read_text("utf-8").strip() == "[3, 1]"


def test_run_still_notifies_without_groq(tmp_path, monkeypatch, telegram, caplog):
    monkeypatch.setattr(radar_watch, "fetch_all", lambda _s: [project(1)])
    monkeypatch.setattr(radar_watch, "candidates", lambda ps: ps)
    monkeypatch.setattr(radar_watch, "LEADS_OUTPUT", tmp_path / "leads.md")
    monkeypatch.setattr(radar_watch, "NOTIFIED", tmp_path / "notified.json")
    monkeypatch.setattr(radar_watch, "GROQ_API_KEY", "")
    session = MagicMock()
    session.post.return_value = MagicMock(ok=True)

    assert radar_watch.run(session)["avisados"] == 1
    assert "GROQ_API_KEY" in caplog.text


def test_send_telegram_never_leaks_the_token_in_a_network_error(telegram):
    session = MagicMock()
    session.post.side_effect = requests.ConnectionError(
        "Max retries exceeded with url: /botTOKEN/sendMessage"
    )

    with pytest.raises(RuntimeError) as error:
        send_telegram("hola", session)

    assert "TOKEN" not in str(error.value)
    assert error.value.__cause__ is None
