"""Shortlist from leads_consolidated.csv -> console + 03_tracking/to_apply.md.

Read-only: applying stays manual (see CLAUDE.md).
Usage (from 02_automation/): python apply_today.py [--verbose]
"""

import logging
import re
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
    UNKNOWN,
    consolidate,
    parse_args,
    read_leads,
    whole_words,
)

TO_APPLY_MD = TRACKING_DIR / "to_apply.md"
# postings already applied to or closed: one link per line, then an optional note
APPLIED_LINKS = TRACKING_DIR / "applied_links.txt"
# the form's own pages end the link: ".../jobs/1234567890/confirmation"
FORM_PAGES = {"confirmation", "apply", "application"}
# the 3 jd-decoder checks a script can't do
MANUAL_CHECKS = "careers page, layoffs, actividad del recruiter"

logger = logging.getLogger(__name__)


PROFILE = whole_words(PROFILE_KEYWORDS)
BLACKLIST = whole_words(BLACKLIST_KEYWORDS)
BROAD_TITLE = whole_words(BROAD_TITLE_KEYWORDS)
AI_CONTEXT = whole_words(AI_CONTEXT_KEYWORDS)
FIT_TITLE = whole_words(FIT_TITLE_KEYWORDS)
TOP_PLATFORM = "anyoneai"  # aitrainer.work key; "Anyone AI" elsewhere

# ===== WHERE IAN CAN APPLY: 100% remote, English or Spanish, entry level, paid =====
# Ian speaks English and Spanish; any other required language drops the offer
OK_LANGUAGE = re.compile(r"english|spanish|español|inglés|latin americ", re.IGNORECASE)
# languages named in the title or requirements; no "Polish" (it's also a verb)
OTHER_LANGUAGE = whole_words(
    [
        "german",
        "french",
        "portuguese",
        "japanese",
        "korean",
        "chinese",
        "mandarin",
        "cantonese",
        "dutch",
        "arabic",
        "italian",
        "russian",
        "hindi",
        "danish",
        "finnish",
        "indonesian",
        "vietnamese",
        "swedish",
        "hebrew",
        "thai",
        "turkish",
        "tamil",
        "malay",
        "norwegian",
        "telugu",
        "bengali",
        "tagalog",
        "ukrainian",
        "czech",
        "marathi",
        "hungarian",
        "gujarati",
        "punjabi",
        "romanian",
        "greek",
        "urdu",
    ]
)
SENIOR_TITLE = whole_words(["senior", "sr", "lead", "staff", "principal"])
# AI training roles for experts of other fields: they want a degree in that field
OTHER_FIELD_TITLE = whole_words(
    [
        "physics",
        "biology",
        "chemistry",
        "life sciences",
        "physical sciences",
        "environmental sciences",
        "cfd",
        "cad",
        "mechanical",
        "nuclear",
        "chef",
        "law",
        "legal",
        "medical",
        "mathematics",
    ]
)
# "3+ years", "3–7 years", "at least 3 years", "5 años": the first number counts
YEARS = re.compile(r"(\d+)\s*(?:\+|[-–]\s*\d+)?\s*(?:years|yrs|años)", re.IGNORECASE)
MAX_YEARS = 3  # 3 or more is out of reach for Ian
UNPAID = whole_words(["unpaid", "ad honorem", "no remunerada", "no remunerado"])
# unpaid is fine only with a concrete path to a paid role (progress.md)
HIRE_PATH = re.compile(
    r"conversion to full[- ]time|path to (?:hire|full[- ]time)|full[- ]time offer"
    r"|return offer|contrataci[oó]n",
    re.IGNORECASE,
)
# aggregators whose page isn't the application form
AGGREGATOR = re.compile(
    r"^https?://(?:www\.)?(?:aitrainer\.work|aitraining\.jobs|remotive\.com)/"
)


def _column(leads: pd.DataFrame, name: str, default: str = "") -> pd.Series:
    if name not in leads:
        return pd.Series(default, index=leads.index, dtype=object)
    return leads[name].fillna(default).astype(str)


def _needs_other_language(idiomas: str) -> bool:
    return any(
        part.strip() and not OK_LANGUAGE.search(part) for part in idiomas.split(",")
    )


def _asks_three_plus_years(text: str) -> bool:
    years = YEARS.search(text)
    return bool(years) and int(years.group(1)) >= MAX_YEARS


def reachable(leads: pd.DataFrame) -> pd.Series:
    """True for offers Ian can take from home today.

    Drops on-site, hybrid or country-locked offers (keeps P1 and unknown
    locations), other required languages, Senior titles or 3+ years, experts
    of other fields (physics, law...) and unpaid offers without a path to a
    paid role.
    """
    titles = _column(leads, "titulo")
    text = titles + " " + _column(leads, "requisitos")
    pais = _column(leads, "pais", UNKNOWN)
    remote = (_column(leads, "priority") == "P1") | (pais.str.strip() == UNKNOWN)
    language = _column(leads, "idiomas").map(_needs_other_language) | text.str.contains(
        OTHER_LANGUAGE
    )
    senior = titles.str.contains(SENIOR_TITLE) | text.map(_asks_three_plus_years)
    other_field = titles.str.contains(OTHER_FIELD_TITLE)
    unpaid = text.str.contains(UNPAID) & ~text.str.contains(HIRE_PATH)
    return remote & ~language & ~senior & ~other_field & ~unpaid


def link_lines(lead: object) -> list[str]:
    """Markdown lines for where to apply: the form first, the portal flagged."""
    link = str(lead.link)
    apply_link = getattr(lead, "apply_link", None)
    if isinstance(apply_link, str) and apply_link:
        return [f"- Link: {apply_link}", f"- Fuente: {link}"]
    if AGGREGATOR.match(link):
        return [f"- Link del portal: {link}"]
    return [f"- Link: {link}"]


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
    keep = (
        (leads["score_jd"] >= bar.fillna(min_score))
        & matches_profile(leads)
        & reachable(leads)
    )
    top = leads[keep]
    # by fit, then score: score_jd is ghost risk and barely varies in ai_training
    company = top["empresa"].astype(str).str.casefold()
    is_top_platform = company.str.replace(r"[^a-z0-9]", "", regex=True) == TOP_PLATFORM
    fit = (~is_top_platform).astype(int) + (
        ~is_top_platform & ~top["titulo"].str.contains(FIT_TITLE)
    ).astype(int)
    return (
        top.assign(_fit=fit)
        .sort_values(
            ["priority", "_fit", "score_jd"],
            ascending=[True, True, False],
            kind="stable",
        )
        .drop(columns="_fit")
    )


def render_markdown(top: pd.DataFrame) -> str:
    sections = [f"# Ofertas para postular ({len(top)})\n"]
    for lead in top.itertuples():
        reqs = "\n".join(f"- {r}" for r in str(lead.requisitos).split(" | "))
        links = "\n".join(link_lines(lead))
        sections.append(
            f"## {lead.titulo} — {lead.empresa}\n\n"
            f"- Score JD: {lead.score_jd}/6\n- Vertical: {lead.vertical}\n"
            f"- Prioridad: {lead.priority} ({lead.pais})\n"
            f"{links}\n"
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


def posting_id(link: object) -> str | None:
    """Last path segment of a posting URL: the same on every link to that posting.

    Greenhouse adds "/confirmation" after applying and getonbrd shows a job
    as /jobs/<slug> or /empleos/<category>/<slug>, so whole URLs don't match.
    """
    if not isinstance(link, str) or not link.startswith("http"):
        return None
    path = link.split("?", 1)[0].split("#", 1)[0].rstrip("/")
    segments = [s for s in path.split("/")[3:] if s]
    while segments and segments[-1].lower() in FORM_PAGES:
        segments.pop()
    return segments[-1].lower() if segments else None


def load_applied_links(path: Path = APPLIED_LINKS) -> set[str]:
    """Posting ids from applied_links.txt; empty if the file doesn't exist."""
    if not path.exists():
        return set()
    lines = path.read_text(encoding="utf-8-sig").splitlines()
    first_words = (line.split("#", 1)[0].split(maxsplit=1) for line in lines)
    return {
        posting for words in first_words if words and (posting := posting_id(words[0]))
    }


def drop_applied_links(leads: pd.DataFrame, applied: set[str]) -> pd.DataFrame:
    """Leads whose link or apply_link isn't an applied or closed posting."""
    seen = leads["link"].map(posting_id).isin(applied)
    if "apply_link" in leads:
        seen |= leads["apply_link"].map(posting_id).isin(applied)
    return leads[~seen]


def main() -> None:
    setup_logging(parse_args(__doc__).verbose)
    leads = drop_applied_links(load_leads(TRACKING_DIR), load_applied_links())
    top = select_top(leads)
    TO_APPLY_MD.write_text(render_markdown(top), encoding="utf-8")
    sys.stdout.reconfigure(
        errors="replace"
    )  # Windows cp1252 console can't print every char
    # the shortlist is this script's output, not a log: it stays on stdout
    for lead in top.itertuples():
        where = link_lines(lead)[0].removeprefix("- ")
        print(f"[{lead.score_jd}] {lead.empresa} | {lead.titulo}\n    {where}")
        print(f"    {lead.requisitos}\n")
    logger.info("%d ofertas -> %s", len(top), TO_APPLY_MD)


if __name__ == "__main__":
    main()
