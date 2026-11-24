"""HN watcher for Windows Task Scheduler (one run every 30 minutes).

Each run: hn_radar ("Who is hiring?" + "Seeking freelancer"), then for each post
not alerted yet (newest first, MAX_ALERTS per run) a Groq pitch with the giga
prompt and an immediate Telegram alert with the link and the pitch.
Applying stays manual (see CLAUDE.md). Freelancer.com is no longer watched:
freelancer_radar.py still runs by hand.
Alerted links: 03_tracking/hn_notified.json (a failed send is retried next run).
Usage (from 02_automation/): python radar_watch.py [--verbose] [--test-telegram]
"""

import argparse
import json
import logging

import requests

from config import (
    GROQ_API_KEY,
    TELEGRAM_BOT_TOKEN,
    TELEGRAM_CHAT_ID,
    TRACKING_DIR,
    setup_logging,
)
from hn_radar import fetch_hn_offers, offer_message
from pitch import draft, load_system_prompt

TELEGRAM_URL = "https://api.telegram.org/bot{token}/sendMessage"
TELEGRAM_MAX_CHARS = 4096
TIMEOUT_S = 30
# the first run finds the whole month's backlog: drip it, 5 alerts per run
MAX_ALERTS = 5
NOTIFIED = TRACKING_DIR / "hn_notified.json"

logger = logging.getLogger(__name__)


def fresh(offers: list[dict], notified: set[str], limit: int = MAX_ALERTS) -> list:
    return [o for o in offers if o["link"] not in notified][:limit]


def message(offer: dict, pitch: str | None) -> str:
    head = (
        f"🔥 HN {offer['tipo']}: {offer['empresa']} — {offer['rol']}\n"
        f"{offer['presupuesto']} · {offer['remoto']}\n"
        f"{offer['link']}\n\n"
    )
    body = pitch or "Sin pitch: pegá el post en el giga prompt."
    room = TELEGRAM_MAX_CHARS - len(head)
    return head + (body if len(body) <= room else body[: room - 1] + "…")


def send_telegram(text: str, session: requests.Session) -> None:
    """RuntimeError if the bot is not configured or Telegram answers an error."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        raise RuntimeError("TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID missing in .env")
    try:
        response = session.post(
            TELEGRAM_URL.format(token=TELEGRAM_BOT_TOKEN),
            json={
                "chat_id": TELEGRAM_CHAT_ID,
                "text": text,
                "disable_web_page_preview": True,
            },
            timeout=TIMEOUT_S,
        )
    except requests.RequestException as error:
        # the request URL carries the bot token: never let it reach the log
        raise RuntimeError(f"telegram request failed: {type(error).__name__}") from None
    if not response.ok:
        raise RuntimeError(f"telegram {response.status_code}: {response.text[:200]}")


def notify(
    offers: list[dict], system: str | None, session: requests.Session
) -> list[str]:
    """Links actually alerted; a failed send is logged and left for the next run."""
    alerted = []
    for offer in offers:
        pitch = draft(system, offer_message(offer), session) if system else None
        try:
            send_telegram(message(offer, pitch), session)
        except RuntimeError as error:
            logger.error("alert %s: %s", offer["link"], error)
            continue
        alerted.append(offer["link"])
    return alerted


def _load_notified() -> list[str]:
    try:
        return json.loads(NOTIFIED.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return []
    except json.JSONDecodeError as error:
        logger.error("corrupt %s, starting empty: %s", NOTIFIED, error)
        return []


def _system_prompt() -> str | None:
    if not GROQ_API_KEY:
        logger.warning("GROQ_API_KEY missing: alerts go out without a pitch")
        return None
    try:
        return load_system_prompt()
    except (OSError, ValueError) as error:
        logger.error("giga prompt unavailable, alerts without a pitch: %s", error)
        return None


def run(session: requests.Session) -> dict:
    offers = fetch_hn_offers(session)
    notified = _load_notified()
    new = fresh(offers, set(notified))
    alerted = notify(new, _system_prompt() if new else None, session)
    NOTIFIED.parent.mkdir(parents=True, exist_ok=True)
    NOTIFIED.write_text(json.dumps(notified + alerted), encoding="utf-8")
    return {"posts": len(offers), "nuevos": len(new), "avisados": len(alerted)}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verbose", action="store_true", help="log INFO too")
    parser.add_argument(
        "--test-telegram", action="store_true", help="send a test alert and exit"
    )
    args = parser.parse_args()
    setup_logging(args.verbose)
    session = requests.Session()
    if args.test_telegram:
        send_telegram("✅ radar_watch conectado.", session)
        print("mensaje de prueba enviado")
        return
    summary = run(session)
    logger.info("radar_watch: %s", summary)
    print(summary)


if __name__ == "__main__":
    main()
