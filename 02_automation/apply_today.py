"""Shortlist from leads_consolidated.csv -> console + 03_tracking/to_apply.md.

Read-only: applying stays manual (see CLAUDE.md).
Usage (from 02_automation/): python apply_today.py [--verbose]
"""

import logging
import sys
from pathlib import Path

import pandas as pd

from config import (
    AI_CONTEXT_KEYWORDS,
    AI_TRAINING_MIN_SCORE,
    BLACKLIST_KEYWORDS,
    BROAD_TITLE_KEYWORDS,
    FIT_TITLE_KEYWORDS,
    MIN_SCORE,
    PROFILE_KEYWORDS,
    TRACKING_DIR,
    setup_logging,
)
from scrapers.getonboard_scraper import (
    AI_TRAINING_TITLE,
    CONSOLIDATED_CSV,
    consolidate,
    parse_args,
    read_leads,
    whole_words,
)

TO_APPLY_MD = TRACKING_DIR / "to_apply.md"
# the 3 jd-decoder checks a script can't do
MANUAL_CHECKS = "careers page, layoffs, actividad del recruiter"

logger = logging.getLogger(__name__)


PROFILE = whole_words(PROFILE_KEYWORDS)
BLACKLIST = whole_words(BLACKLIST_KEYWORDS)
BROAD_TITLE = whole_words(BROAD_TITLE_KEYWORDS)
AI_CONTEXT = whole_words(AI_CONTEXT_KEYWORDS)
FIT_TITLE = whole_words(FIT_TITLE_KEYWORDS)
TOP_PLATFORM = "anyoneai"  # aitrainer.work key; "Anyone AI" elsewhere


def matches_profile(leads: pd.DataFrame) -> pd.Series:
    # title only: QA/Full-Stack requisitos also list Python or "bases de datos"
    # either vertical: Data & AI or AI training / evaluation
    titles = leads["titulo"]
    # shortcut: requisitos is only the description's first lines; read the whole
    # description at scrape time if broad titles with AI further down get missed
    broad = titles.str.contains(BROAD_TITLE) & leads["requisitos"].astype(
        str
    ).str.contains(AI_CONTEXT)
    wanted = (
        titles.str.contains(PROFILE) | titles.str.contains(AI_TRAINING_TITLE) | broad
    )
    return wanted & ~titles.str.contains(BLACKLIST)


def select_top(leads: pd.DataFrame, min_score: int = MIN_SCORE) -> pd.DataFrame:
    # ai_training gets a lower bar: ghost risk matters less for per-project gigs
    bar = leads["vertical"].map({"ai_training": AI_TRAINING_MIN_SCORE})
    keep = (leads["score_jd"] >= bar.fillna(min_score)) & matches_profile(leads)
    top = leads[keep]
    # by fit, then score: score_jd is ghost risk and barely varies in ai_training
    company = top["empresa"].astype(str).str.casefold()
    is_top_platform = company.str.replace(r"[^a-z0-9]", "", regex=True) == TOP_PLATFORM
    fit = (~is_top_platform).astype(int) + (
        ~is_top_platform & ~top["titulo"].str.contains(FIT_TITLE)
    ).astype(int)
    return (
        top.assign(_fit=fit)
        .sort_values(["_fit", "score_jd"], ascending=[True, False], kind="stable")
        .drop(columns="_fit")
    )


def render_markdown(top: pd.DataFrame) -> str:
    sections = [f"# Ofertas para postular ({len(top)})\n"]
    for lead in top.itertuples():
        reqs = "\n".join(f"- {r}" for r in str(lead.requisitos).split(" | "))
        sections.append(
            f"## {lead.titulo} — {lead.empresa}\n\n"
            f"- Score JD: {lead.score_jd}/6\n- Vertical: {lead.vertical}\n"
            f"- Link: {lead.link}\n"
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
