"""Giga prompt as Groq system prompt: one pitch per offer, shared by every flow.

01_assets/pitch/giga_prompt_private.txt is used verbatim (no fixed stack forced
into the pitch, see commit 7c5334f); only the paste placeholder moves out, since
the offer goes in the user message. Drafts only: sending stays manual (CLAUDE.md).
"""

import logging
from pathlib import Path

import requests

from cold_outreach import groq_chat

GIGA_PROMPT = (
    Path(__file__).resolve().parents[1] / "01_assets/pitch/giga_prompt_private.txt"
)
PLACEHOLDER = "[PEGAR AQUÍ LA DESCRIPCIÓN DE LA OFERTA]"
TEMPLATE_MARK = "[TU_NOMBRE]"  # still in the file: the public template, not filled
OFFER_CHARS = 4000  # the post or posting sent to Groq

logger = logging.getLogger(__name__)


def system_prompt(giga: str) -> str:
    """The giga prompt, with the offer moved to the user message."""
    if TEMPLATE_MARK in giga:
        raise ValueError("fill 01_assets/pitch/giga_prompt_private.txt first")
    return giga.replace(
        PLACEHOLDER, "(La descripción de la oferta viene en el mensaje del usuario.)"
    ).strip()


def load_system_prompt() -> str:
    """OSError if the private giga prompt is missing, ValueError if unfilled."""
    return system_prompt(GIGA_PROMPT.read_text(encoding="utf-8"))


def draft(system: str, offer: str, session: requests.Session) -> str | None:
    """One Groq pitch; None (logged) if the call fails, so the caller moves on."""
    try:
        return groq_chat(system, offer, session)
    except (requests.RequestException, KeyError, TypeError, ValueError) as error:
        logger.error("groq pitch failed: %s", error)
        return None
