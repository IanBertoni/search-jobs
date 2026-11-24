"""Outbox: Freelancer.com candidates -> pitches drafted by Groq with the giga prompt.

The system prompt is 01_assets/pitch/giga_prompt_private.txt, verbatim (no fixed
stack forced into the pitch): Groq does what you used to do by pasting by hand.
Only drafts: you copy the pitch, bid on Freelancer.com and mark it sent (CLAUDE.md).
State (drafts, sent ids, last order): 03_tracking/outbox_state.json, so a project
is drafted once and a sent one never comes back.
Output: 03_tracking/OUTBOX.md
Usage (from 02_automation/): python outbox.py [--verbose]
                             python outbox.py --sent ID [ID ...]
"""

import argparse
import json
import logging
import sys
from datetime import UTC, datetime
from pathlib import Path

import requests

from cold_outreach import groq_chat
from config import GROQ_API_KEY, TRACKING_DIR, setup_logging
from freelancer_radar import budget_label, candidates, fetch_all

GIGA_PROMPT = (
    Path(__file__).resolve().parents[1] / "01_assets/pitch/giga_prompt_private.txt"
)
PLACEHOLDER = "[PEGAR AQUÍ LA DESCRIPCIÓN DE LA OFERTA]"
TEMPLATE_MARK = "[TU_NOMBRE]"  # still in the file: the public template, not filled
STATE = TRACKING_DIR / "outbox_state.json"
OUTPUT = TRACKING_DIR / "OUTBOX.md"
MAX_DRAFTS = 8  # Groq calls per run; candidates come under-5-bids first
DESCRIPTION_CHARS = 4000

logger = logging.getLogger(__name__)


def system_prompt(giga: str) -> str:
    """The giga prompt, with the offer moved to the user message."""
    if TEMPLATE_MARK in giga:
        raise ValueError("fill 01_assets/pitch/giga_prompt_private.txt first")
    return giga.replace(
        PLACEHOLDER, "(La descripción de la oferta viene en el mensaje del usuario.)"
    ).strip()


def offer_message(project: dict) -> str:
    return (
        f"Título: {project['titulo']}\n"
        f"Presupuesto: {budget_label(project)}\n"
        f"Bids: {project['bids']}\n"
        f"Skills: {', '.join(project['skills'])}\n\n"
        f"{project['descripcion'][:DESCRIPTION_CHARS]}"
    )


def _empty() -> dict:
    return {"drafts": {}, "sent": [], "order": []}


def load_state(path: Path | None) -> dict:
    """Empty state if the file is missing or corrupt (logged)."""
    if path is None or not path.exists():
        return _empty()
    try:
        return {**_empty(), **json.loads(path.read_text(encoding="utf-8"))}
    except (json.JSONDecodeError, TypeError) as error:
        logger.error("corrupt %s, starting empty: %s", path, error)
        return _empty()


def save_state(path: Path, state: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")


def draft_new(
    projects: list[dict],
    state: dict,
    session: requests.Session,
    system: str,
    limit: int = MAX_DRAFTS,
) -> dict:
    """New state with a Groq pitch for up to `limit` undrafted, unsent projects."""
    drafts = dict(state["drafts"])
    done = set(drafts) | {str(i) for i in state["sent"]}
    pending = [p for p in projects if str(p["id"]) not in done][:limit]
    for project in pending:
        try:
            pitch = groq_chat(system, offer_message(project), session)
        except (requests.RequestException, KeyError, TypeError, ValueError) as error:
            logger.error("outbox %s: groq failed: %s", project["id"], error)
            continue
        drafts[str(project["id"])] = {
            "project": project,
            "pitch": pitch,
            "creado": datetime.now(UTC).isoformat(timespec="minutes"),
        }
    return {**state, "drafts": drafts, "order": [p["id"] for p in projects]}


def mark_sent(state: dict, ids: list[int]) -> dict:
    sent = list(state["sent"]) + [i for i in ids if i not in state["sent"]]
    return {**state, "sent": sent}


def render(state: dict) -> str:
    """Unsent drafts of the last candidate list, in its order (fewest bids first)."""
    sent = set(state["sent"])
    ready = [
        state["drafts"][str(pid)]
        for pid in state["order"]
        if str(pid) in state["drafts"] and pid not in sent
    ]
    header = (
        "# OUTBOX — Freelancer.com\n\n"
        f"Generado: {datetime.now(UTC):%Y-%m-%d %H:%M} UTC. Revisá, copiá el pitch, "
        "bideá a mano y marcalo: `python outbox.py --sent <id>`.\n"
    )
    if not ready:
        return header + "\nOutbox vacío: no hay pitches nuevos.\n"
    blocks = []
    for draft in ready:
        p = draft["project"]
        blocks.append(
            f"## - [ ] **{p['titulo']}**\n\n"
            f"- Link: {p['link']}\n- Presupuesto: {budget_label(p)}\n"
            f"- Bids (al redactar): {p['bids']} · Publicado: {p['publicado']}\n"
            f"- Marcar enviado: `python outbox.py --sent {p['id']}`\n\n"
            f"{draft['pitch']}\n"
        )
    return header + "\n" + "\n---\n\n".join(blocks)


def build(projects: list[dict], session: requests.Session) -> dict:
    """Draft new pitches, save the state and rewrite OUTBOX.md."""
    system = system_prompt(GIGA_PROMPT.read_text(encoding="utf-8"))
    state = draft_new(projects, load_state(STATE), session, system)
    save_state(STATE, state)
    OUTPUT.write_text(render(state), encoding="utf-8")
    return state


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sent", type=int, nargs="+", metavar="ID", help="mark sent")
    parser.add_argument("--verbose", action="store_true", help="log INFO too")
    args = parser.parse_args()
    setup_logging(args.verbose)
    if args.sent:
        state = mark_sent(load_state(STATE), args.sent)
        save_state(STATE, state)
        OUTPUT.write_text(render(state), encoding="utf-8")
        print(f"enviados: {args.sent} -> {OUTPUT}")
        return
    if not GROQ_API_KEY:
        sys.exit("GROQ_API_KEY missing in 02_automation/.env")
    session = requests.Session()
    state = build(candidates(fetch_all(session)), session)
    print(f"{len(state['drafts'])} pitches en total -> {OUTPUT}")


if __name__ == "__main__":
    main()
