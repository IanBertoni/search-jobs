"""Outbox: ATS radar leads -> pitches drafted by Groq with the giga prompt.

Runs the ATS radar (Greenhouse/Lever/Ashby careers pages, see ats_radar.py), lets
Groq judge only postings it has never seen (apply/stretch/skip), and drafts a
pitch with 01_assets/pitch/giga_prompt_private.txt for each one that fits.
Only drafts: you copy the pitch, apply by hand and mark it sent (CLAUDE.md).
State (drafts, skipped, sent, last order): 03_tracking/ats_outbox_state.json,
so a posting is judged and drafted once and a sent one never comes back.
Output: 03_tracking/OUTBOX.md
Usage (from 02_automation/): python outbox.py [--verbose]
                             python outbox.py --sent URL [URL ...]
"""

import argparse
import json
import logging
import sys
from datetime import UTC, datetime
from pathlib import Path

import requests

from applied import drop_applied, load_applied
from ats_radar import VERDICT_ORDER, candidates, fetch_all, screen
from config import GROQ_API_KEY, TRACKING_DIR, setup_logging
from pitch import OFFER_CHARS, draft, load_system_prompt

STATE = TRACKING_DIR / "ats_outbox_state.json"
OUTPUT = TRACKING_DIR / "OUTBOX.md"
MAX_NEW = 15  # postings judged per run: two Groq calls each (verdict + pitch)

logger = logging.getLogger(__name__)


def job_message(job: dict) -> str:
    return (
        f"Empresa: {job['empresa']}\n"
        f"Rol: {job['titulo']}\n"
        f"Ubicación: {job['ubicacion']}\n\n"
        f"{job['texto'][:OFFER_CHARS]}"
    )


def _empty() -> dict:
    return {"drafts": {}, "skipped": [], "sent": [], "order": []}


def load_state(path: Path) -> dict:
    """Empty state if the file is missing or corrupt (logged)."""
    if not path.exists():
        return _empty()
    try:
        return {**_empty(), **json.loads(path.read_text(encoding="utf-8"))}
    except (json.JSONDecodeError, TypeError) as error:
        logger.error("corrupt %s, starting empty: %s", path, error)
        return _empty()


def save_state(path: Path, state: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")


def update(
    jobs: list[dict],
    state: dict,
    session: requests.Session,
    system: str,
    limit: int = MAX_NEW,
) -> dict:
    """New state: unseen jobs judged, a pitch for each that fits (none mutated)."""
    seen = set(state["drafts"]) | set(state["skipped"]) | set(state["sent"])
    new = [j for j in jobs if j["link"] not in seen][:limit]
    fits = screen(new, session)
    fit_links = {j["link"] for j in fits}
    drafts = dict(state["drafts"])
    for job in fits:
        pitch = draft(system, job_message(job), session)
        if pitch is None:
            continue  # neither drafted nor skipped: retried next run
        drafts[job["link"]] = {
            "job": job,
            "pitch": pitch,
            "creado": datetime.now(UTC).isoformat(timespec="minutes"),
        }
    skipped = [j["link"] for j in new if j["link"] not in fit_links]
    return {
        **state,
        "drafts": drafts,
        "skipped": list(state["skipped"]) + skipped,
        "order": [j["link"] for j in jobs if j["link"] in drafts],
    }


def mark_sent(state: dict, links: list[str]) -> dict:
    sent = list(state["sent"]) + [x for x in links if x not in state["sent"]]
    return {**state, "sent": sent}


def render(state: dict) -> str:
    """Unsent drafts still on the boards: apply before stretch, newest first."""
    sent = set(state["sent"])
    ready = [
        state["drafts"][link]
        for link in state["order"]
        if link in state["drafts"] and link not in sent
    ]
    newest = sorted(ready, key=lambda d: d["job"]["publicado"], reverse=True)
    ordered = sorted(newest, key=lambda d: VERDICT_ORDER.get(d["job"]["verdict"], 9))
    header = (
        "# OUTBOX — careers pages de startups (ATS)\n\n"
        f"Generado: {datetime.now(UTC):%Y-%m-%d %H:%M} UTC. Revisá, copiá el pitch, "
        "aplicá a mano y marcalo: `python outbox.py --sent <link>`.\n"
    )
    if not ordered:
        return header + "\nOutbox vacío: no hay pitches nuevos.\n"
    blocks = []
    for d in ordered:
        j = d["job"]
        blocks.append(
            f"## - [ ] **{j['empresa']} — {j['titulo']}**\n\n"
            f"- Veredicto: {j['verdict']} · {j.get('reason') or '?'}\n"
            f"- Ubicación: {j['ubicacion']} · Publicado: {j['publicado']}\n"
            f"- Link: {j['link']}\n"
            f"- Marcar enviado: `python outbox.py --sent {j['link']}`\n\n"
            f"{d['pitch']}\n"
        )
    return header + "\n" + "\n---\n\n".join(blocks)


def build(session: requests.Session) -> dict:
    """ATS radar -> new pitches, then save the state and rewrite OUTBOX.md."""
    system = load_system_prompt()
    jobs = drop_applied(candidates(fetch_all(session)), load_applied())
    state = update(jobs, load_state(STATE), session, system)
    save_state(STATE, state)
    OUTPUT.write_text(render(state), encoding="utf-8")
    return state


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sent", nargs="+", metavar="URL", help="mark applied")
    parser.add_argument("--verbose", action="store_true", help="log INFO too")
    args = parser.parse_args()
    setup_logging(args.verbose)
    if args.sent:
        state = mark_sent(load_state(STATE), args.sent)
        save_state(STATE, state)
        OUTPUT.write_text(render(state), encoding="utf-8")
        print(f"enviados: {len(args.sent)} -> {OUTPUT}")
        return
    if not GROQ_API_KEY:
        sys.exit("GROQ_API_KEY missing in 02_automation/.env")
    state = build(requests.Session())
    print(f"{len(state['order'])} pitches en el outbox -> {OUTPUT}")


if __name__ == "__main__":
    main()
