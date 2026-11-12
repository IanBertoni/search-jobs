"""Shortlist from leads_consolidated.csv -> console + 03_tracking/to_apply.md.

Read-only: applying stays manual (see CLAUDE.md).
Usage (from 02_automation/): python apply_today.py
"""

import re
import sys

import pandas as pd

from scrapers.getonboard_scraper import CONSOLIDATED_CSV, CSV_ENCODING, TRACKING_DIR

MIN_SCORE = 4
TO_APPLY_MD = TRACKING_DIR / "to_apply.md"
# whole words so "ai" doesn't hit "maintain"; datos/ia/inteligencia artificial cover Spanish posts
PROFILE = re.compile(
    r"\b(?:data|datos|ai|ia|ml|python|etl|analytics|machine learning"
    r"|inteligencia artificial|rag|llm)\b",
    re.IGNORECASE,
)


def matches_profile(leads: pd.DataFrame) -> pd.Series:
    # title only: QA/Full-Stack requisitos also list Python or "bases de datos"
    return leads["titulo"].str.contains(PROFILE)


def select_top(leads: pd.DataFrame, min_score: int = MIN_SCORE) -> pd.DataFrame:
    keep = (leads["score_jd"] >= min_score) & matches_profile(leads)
    return leads[keep].sort_values("score_jd", ascending=False, kind="stable")


def render_markdown(top: pd.DataFrame) -> str:
    sections = [f"# Ofertas para postular ({len(top)})\n"]
    for lead in top.itertuples():
        reqs = "\n".join(f"- {r}" for r in str(lead.requisitos).split(" | "))
        sections.append(
            f"## {lead.titulo} — {lead.empresa}\n\n"
            f"- Score JD: {lead.score_jd}/6\n- Link: {lead.link}\n\n"
            f"**Requisitos clave:**\n\n{reqs}\n"
        )
    return "\n".join(sections)


def main() -> None:
    leads = pd.read_csv(TRACKING_DIR / CONSOLIDATED_CSV, encoding=CSV_ENCODING)
    top = select_top(leads)
    TO_APPLY_MD.write_text(render_markdown(top), encoding="utf-8")
    sys.stdout.reconfigure(
        errors="replace"
    )  # Windows cp1252 console can't print every char
    for lead in top.itertuples():
        print(f"[{lead.score_jd}] {lead.empresa} | {lead.titulo}\n    {lead.link}")
        print(f"    {lead.requisitos}\n")
    print(f"{len(top)} ofertas -> {TO_APPLY_MD}")


if __name__ == "__main__":
    main()
