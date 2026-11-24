from unittest.mock import MagicMock

import pytest
import requests

import radar_watch
from radar_watch import (
    TELEGRAM_MAX_CHARS,
    fresh,
    message,
    notify,
    send_telegram,
)


def offer(pid: int, tipo: str = "freelance") -> dict:
    return {
        "empresa": f"Acme {pid}",
        "rol": "Python developer",
        "remoto": "Remote",
        "autor": "cto",
        "link": f"https://news.ycombinator.com/item?id={pid}",
        "texto": "Need a Python scraping pipeline.",
        "tipo": tipo,
        "presupuesto": "$60/hr",
    }


@pytest.fixture
def telegram(monkeypatch):
    monkeypatch.setattr(radar_watch, "TELEGRAM_BOT_TOKEN", "TOKEN")
    monkeypatch.setattr(radar_watch, "TELEGRAM_CHAT_ID", "42")


def ok_session() -> MagicMock:
    session = MagicMock()
    session.post.return_value = MagicMock(ok=True)
    return session


def test_fresh_drops_notified_posts_and_caps_the_batch():
    offers = [offer(5), offer(4), offer(3), offer(2)]
    notified = {offer(4)["link"]}

    assert [o["empresa"] for o in fresh(offers, notified, limit=2)] == [
        "Acme 5",
        "Acme 3",
    ]


def test_message_has_company_role_type_budget_link_and_the_pitch():
    text = message(offer(1), "Hi, I can build it.")

    for part in ("Acme 1", "Python developer", "freelance", "$60/hr", "Remote"):
        assert part in text
    assert "https://news.ycombinator.com/item?id=1" in text
    assert "Hi, I can build it." in text


def test_message_without_a_pitch_points_to_the_giga_prompt():
    assert "giga prompt" in message(offer(1), None)


def test_message_fits_in_one_telegram_message():
    assert len(message(offer(1), "x" * 10_000)) <= TELEGRAM_MAX_CHARS


def test_send_telegram_posts_to_the_bot_api(telegram):
    session = ok_session()

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


def test_send_telegram_never_leaks_the_token_in_a_network_error(telegram):
    session = MagicMock()
    session.post.side_effect = requests.ConnectionError(
        "Max retries exceeded with url: /botTOKEN/sendMessage"
    )

    with pytest.raises(RuntimeError) as error:
        send_telegram("hola", session)

    assert "TOKEN" not in str(error.value)
    assert error.value.__cause__ is None


def test_notify_drafts_a_pitch_per_post_and_returns_the_alerted_links(
    telegram, monkeypatch
):
    pitches = []
    monkeypatch.setattr(
        radar_watch,
        "draft",
        lambda system, user, _s: pitches.append((system, user)) or "PITCH",
    )
    session = ok_session()

    alerted = notify([offer(1), offer(2)], "SYS", session)

    assert alerted == [offer(1)["link"], offer(2)["link"]]
    assert pitches[0][0] == "SYS"
    assert "Need a Python scraping pipeline." in pitches[0][1]
    assert "PITCH" in session.post.call_args_list[0].kwargs["json"]["text"]


def test_notify_without_a_system_prompt_alerts_without_groq(telegram, monkeypatch):
    monkeypatch.setattr(radar_watch, "draft", MagicMock())
    session = ok_session()

    assert len(notify([offer(1)], None, session)) == 1
    radar_watch.draft.assert_not_called()


def test_notify_does_not_mark_a_failed_send_as_notified(telegram, caplog):
    session = MagicMock()
    session.post.side_effect = [
        MagicMock(ok=False, status_code=500, text="boom"),
        MagicMock(ok=True),
    ]

    assert notify([offer(1), offer(2)], None, session) == [offer(2)["link"]]
    assert "500" in caplog.text


@pytest.fixture
def watch(tmp_path, monkeypatch, telegram):
    monkeypatch.setattr(radar_watch, "NOTIFIED", tmp_path / "notified.json")
    monkeypatch.setattr(radar_watch, "load_system_prompt", lambda: "SYS")
    monkeypatch.setattr(radar_watch, "draft", lambda *_a: "PITCH")
    monkeypatch.setattr(radar_watch, "load_applied", dict)  # not your real list
    return tmp_path


def test_run_alerts_only_new_hn_posts_and_remembers_them(watch, monkeypatch):
    offers = [offer(3), offer(2), offer(1)]
    monkeypatch.setattr(radar_watch, "fetch_hn_offers", lambda _s: offers)
    monkeypatch.setattr(radar_watch, "GROQ_API_KEY", "key")
    radar_watch.NOTIFIED.write_text(f'["{offer(2)["link"]}"]', encoding="utf-8")
    session = ok_session()

    summary = radar_watch.run(session)

    assert summary == {"posts": 3, "nuevos": 2, "avisados": 2}
    assert session.post.call_count == 2
    saved = radar_watch._load_notified()
    assert saved == [offer(2)["link"], offer(3)["link"], offer(1)["link"]]


def test_run_still_alerts_without_groq(watch, monkeypatch, caplog):
    monkeypatch.setattr(radar_watch, "fetch_hn_offers", lambda _s: [offer(1)])
    monkeypatch.setattr(radar_watch, "GROQ_API_KEY", "")

    assert radar_watch.run(ok_session())["avisados"] == 1
    assert "GROQ_API_KEY" in caplog.text


def test_run_alerts_without_a_pitch_if_the_giga_prompt_is_missing(
    watch, monkeypatch, caplog
):
    monkeypatch.setattr(radar_watch, "fetch_hn_offers", lambda _s: [offer(1)])
    monkeypatch.setattr(radar_watch, "GROQ_API_KEY", "key")

    def missing():
        raise OSError("no giga prompt")

    monkeypatch.setattr(radar_watch, "load_system_prompt", missing)

    assert radar_watch.run(ok_session())["avisados"] == 1
    assert "no giga prompt" in caplog.text


def test_load_notified_starts_empty_when_corrupt(watch, caplog):
    radar_watch.NOTIFIED.write_text("{bad", encoding="utf-8")

    assert radar_watch._load_notified() == []
    assert "corrupt" in caplog.text


def test_run_never_alerts_a_company_in_applied_txt(watch, monkeypatch):
    offers = [offer(2), offer(1)]
    monkeypatch.setattr(radar_watch, "fetch_hn_offers", lambda _s: offers)
    monkeypatch.setattr(radar_watch, "GROQ_API_KEY", "key")
    monkeypatch.setattr(radar_watch, "load_applied", lambda: {"acme 2": "rechazado"})
    session = ok_session()

    summary = radar_watch.run(session)

    assert summary["nuevos"] == 1
    assert "Acme 1" in session.post.call_args.kwargs["json"]["text"]
