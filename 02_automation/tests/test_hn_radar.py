import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

import hn_radar
from hn_radar import (
    fetch_hn_offers,
    latest_freelancer_thread,
    offer_message,
    parse_seeking_freelancer,
)

FIXTURES = Path(__file__).parent / "fixtures"


def load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text("utf-8"))


def json_session(*payloads) -> MagicMock:
    session = MagicMock()
    session.get.side_effect = [
        MagicMock(ok=True, json=MagicMock(return_value=p)) for p in payloads
    ]
    return session


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr(hn_radar.time, "sleep", lambda _s: None)


def test_latest_freelancer_thread_matches_the_title_from_any_author():
    session = json_session(load("hn_freelancer_search.json"))

    assert latest_freelancer_thread(session) == "46000002"


def test_latest_freelancer_thread_raises_when_there_is_none():
    with pytest.raises(LookupError):
        latest_freelancer_thread(json_session({"hits": []}))


def test_seeking_freelancer_keeps_only_clients_that_fit():
    offers = parse_seeking_freelancer(load("hn_freelancer_items.json"))

    # 301 is a freelancer selling, 303 is US-only, 304 is not a dev role
    assert [o["link"].rsplit("=", 1)[1] for o in offers] == ["302", "305"]
    assert {o["tipo"] for o in offers} == {"freelance"}
    assert offers[0]["presupuesto"] == "$60/hr"


def test_fetch_hn_offers_merges_both_threads_newest_first():
    session = json_session(
        load("hn_whoishiring_search.json"),
        load("hn_whoishiring_items.json"),
        load("hn_freelancer_search.json"),
        load("hn_freelancer_items.json"),
    )

    offers = fetch_hn_offers(session)
    ids = [int(o["link"].rsplit("=", 1)[1]) for o in offers]

    assert ids[:2] == [305, 302]
    assert ids == sorted(ids, reverse=True)
    assert len(ids) == len(set(ids)) > 2


def test_fetch_hn_offers_keeps_one_thread_when_the_other_fails(caplog):
    session = MagicMock()
    failing = MagicMock()
    failing.raise_for_status.side_effect = hn_radar.requests.HTTPError("503")
    session.get.side_effect = [
        failing,
        MagicMock(json=MagicMock(return_value=load("hn_freelancer_search.json"))),
        MagicMock(json=MagicMock(return_value=load("hn_freelancer_items.json"))),
    ]

    offers = fetch_hn_offers(session)

    assert len(offers) == 2
    assert "503" in caplog.text


def test_offer_message_carries_the_raw_post():
    offer = {
        "empresa": "Acme",
        "rol": "Python dev",
        "tipo": "freelance",
        "remoto": "Remote",
        "presupuesto": "$60/hr",
        "texto": "Need a scraping pipeline.",
    }

    message = offer_message(offer)

    for part in ("Acme", "Python dev", "freelance", "$60/hr", "scraping pipeline"):
        assert part in message
