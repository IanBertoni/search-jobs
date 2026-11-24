"""Companies already applied to, rejected or not eligible: never sent to Groq again.

File: 03_tracking/applied.txt (git-ignored, personal), one "empresa | estado" per
line, "#" starts a comment. A company matches as a whole word, ignoring case, so
"Samplesoft" also drops "Samplesoft Labs" but not "Samplesoftwood".
Used by cold_outreach.py, radar_watch.py and outbox.py before any Groq call.
"""

import logging
import re
from pathlib import Path

from config import TRACKING_DIR

APPLIED = TRACKING_DIR / "applied.txt"

logger = logging.getLogger(__name__)


def load_applied(path: Path = APPLIED) -> dict[str, str]:
    """{lowercase company: status}; empty if the file does not exist."""
    if not path.exists():
        return {}
    applied = {}
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        if "|" not in line:
            logger.warning("%s:%d sin separador '|', ignorada", path.name, number)
            continue
        company, status = (part.strip() for part in line.split("|", 1))
        applied[company.lower()] = status
    return applied


def _status(empresa: str, patterns: dict[str, re.Pattern]) -> str | None:
    return next((s for s, p in patterns.items() if p.search(empresa)), None)


def drop_applied(offers: list[dict], applied: dict[str, str]) -> list[dict]:
    """Offers whose company is not in applied.txt (each drop is logged)."""
    patterns = {
        f"{company} ({status})": re.compile(
            rf"(?<!\w){re.escape(company)}(?!\w)", re.IGNORECASE
        )
        for company, status in applied.items()
    }
    kept = []
    for offer in offers:
        status = _status(offer["empresa"], patterns)
        if status:
            logger.info("applied.txt: %s ignorada, %s", offer["link"], status)
            continue
        kept.append(offer)
    return kept
