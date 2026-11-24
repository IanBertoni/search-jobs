"""Freelancer.com watcher for Windows Task Scheduler (one run every 30 minutes).

Each run: radar -> 03_tracking/freelancer_leads.md, outbox (Groq pitches with the
giga prompt) -> 03_tracking/OUTBOX.md, then a Telegram alert, with the pitch, for
each new project under FEW_BIDS bids. Bidding stays manual (see CLAUDE.md).
Alerted ids: 03_tracking/notified.json (a failed send is retried next run).
Usage (from 02_automation/): python radar_watch.py [--verbose] [--test-telegram]
"""

import argparse
import json
import logging

import requests

import outbox
from config import (
    GROQ_API_KEY,
    TELEGRAM_BOT_TOKEN,
    TELEGRAM_CHAT_ID,
    TRACKING_DIR,
    setup_logging,
)
from freelancer_radar import FEW_BIDS, budget_label, candidates, fetch_all, render
from freelancer_radar import OUTPUT as LEADS_OUTPUT

TELEGRAM_URL = "https://api.telegram.org/bot{token}/sendMessage"
TELEGRAM_MAX_CHARS = 4096
TIMEOUT_S = 30
NOTIFIED = TRACKING_DIR / "notified.json"

logger = logging.getLogger(__name__)


def hot(projects: list[dict], notified: set[int]) -> list[dict]:
    return [p for p in projects if p["bids"] < FEW_BIDS and p["id"] not in notified]


def message(project: dict, pitch: str | None) -> str:
    head = (
        f"🔥 {project['titulo']}\n"
        f"{budget_label(project)} · {project['bids']} bids\n"
        f"{project['link']}\n\n"
    )
    body = pitch or "Sin pitch: pegá la descripción en el giga prompt."
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


def notify(projects: list[dict], drafts: dict, session: requests.Session) -> list[int]:
    """Ids actually alerted; a failed send is logged and left for the next run."""
    sent = []
    for project in projects:
        pitch = drafts.get(str(project["id"]), {}).get("pitch")
        try:
            send_telegram(message(project, pitch), session)
        except (requests.RequestException, RuntimeError) as error:
            logger.error("alert %s: %s", project["id"], error)
            continue
        sent.append(project["id"])
    return sent


def _load_notified() -> list[int]:
    try:
        return json.loads(NOTIFIED.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return []
    except json.JSONDecodeError as error:
        logger.error("corrupt %s, starting empty: %s", NOTIFIED, error)
        return []


def run(session: requests.Session) -> dict:
    projects = fetch_all(session)
    shortlist = candidates(projects)
    LEADS_OUTPUT.write_text(render(shortlist), encoding="utf-8")
    drafts: dict = {}
    if GROQ_API_KEY:
        try:
            drafts = outbox.build(shortlist, session)["drafts"]
        except (OSError, ValueError) as error:
            logger.error("outbox skipped: %s", error)
    else:
        logger.warning("GROQ_API_KEY missing: alerts go out without a pitch")
    notified = _load_notified()
    alerted = notify(hot(shortlist, set(notified)), drafts, session)
    NOTIFIED.write_text(json.dumps(notified + alerted), encoding="utf-8")
    return {
        "proyectos": len(projects),
        "candidatos": len(shortlist),
        "avisados": len(alerted),
    }


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
