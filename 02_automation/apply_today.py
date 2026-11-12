"""Shortlist from leads_consolidated.csv -> console + 03_tracking/to_apply.md.

Read-only: applying stays manual (see CLAUDE.md).
Usage (from 02_automation/): python apply_today.py [--verbose]
"""

import logging
import re
import sys
from collections.abc import Iterable
from pathlib import Path

import pandas as pd

from config import (
    BLACKLIST_KEYWORDS,
    MIN_SCORE,
    PROFILE_KEYWORDS,
    TRACKING_DIR,
    setup_logging,
)
from scrapers.getonboard_scraper import (
    CONSOLIDATED_CSV,
    consolidate,
    parse_args,
    read_leads,
)

TO_APPLY_MD = TRACKING_DIR / "to_apply.md"
# the 3 jd-decoder checks a script can't do
MANUAL_CHECKS = "careers page, layoffs, actividad del recruiter"

logger = logging.getLogger(__name__)


def _whole_words(words: Iterable[str]) -> re.Pattern[str]:
    # whole words so "ai" doesn't hit "maintain"; an empty list matches nothing
    alternatives = "|".join(map(re.escape, words))
    pattern = rf"\b(?:{alternatives})\b" if alternatives else r"(?!)"
    return re.compile(pattern, re.IGNORECASE)


PROFILE = _whole_words(PROFILE_KEYWORDS)
BLACKLIST = _whole_words(BLACKLIST_KEYWORDS)


def matches_profile(leads: pd.DataFrame) -> pd.Series:
    # title only: QA/Full-Stack requisitos also list Python or "bases de datos"
    titles = leads["titulo"]
    return titles.str.contains(PROFILE) & ~titles.str.contains(BLACKLIST)


def select_top(leads: pd.DataFrame, min_score: int = MIN_SCORE) -> pd.DataFrame:
    keep = (leads["score_jd"] >= min_score) & matches_profile(leads)
    return leads[keep].sort_values("score_jd", ascending=False, kind="stable")


def render_markdown(top: pd.DataFrame) -> str:
    sections = [f"# Ofertas para postular ({len(top)})\n"]
    for lead in top.itertuples():
        reqs = "\n".join(f"- {r}" for r in str(lead.requisitos).split(" | "))
        sections.append(
            f"## {lead.titulo} — {lead.empresa}\n\n"
            f"- Score JD: {lead.score_jd}/6\n- Link: {lead.link}\n"
            f"- Revisión manual: {lead.manual_review} ({MANUAL_CHECKS})\n\n"
            f"**Requisitos clave:**\n\n{reqs}\n"
        )
    return "\n".join(sections)


def load_leads(folder: Path) -> pd.DataFrame:
    """Read the consolidated CSV, rebuilding it from the dated snapshots if it's corrupt."""
    try:
        return read_leads(folder / CONSOLIDATED_CSV)
    except (OSError, ValueError) as error:
        logger.warning(
            "%s unreadable (%s); rebuilding it from the dated snapshots",
            CONSOLIDATED_CSV,
            error,
        )
        return read_leads(consolidate(folder))


def main() -> None:
    setup_logging(parse_args(__doc__).verbose)
    top = select_top(load_leads(TRACKING_DIR))
    TO_APPLY_MD.write_text(render_markdown(top), encoding="utf-8")
    sys.stdout.reconfigure(
        errors="replace"
    )  # Windows cp1252 console can't print every char
    # the shortlist is this script's output, not a log: it stays on stdout
    for lead in top.itertuples():
        print(f"[{lead.score_jd}] {lead.empresa} | {lead.titulo}\n    {lead.link}")
        print(f"    {lead.requisitos}\n")
    logger.info("%d ofertas -> %s", len(top), TO_APPLY_MD)


if __name__ == "__main__":
    main()
