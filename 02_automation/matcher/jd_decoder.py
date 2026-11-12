"""Automatable subset of the jd-decoder ghost-risk checklist.

Only 3 of the 6 checklist items can be read from the posting itself:
stale (45+ days), no concrete project/team, missing or very wide comp band.
The rest (careers page, layoffs, recruiter activity) need manual checks, so
they count as 0 risk: most postings are real (jd-decoder base rates).
"""

from collections.abc import Mapping
from datetime import datetime
from typing import Any

import pandas as pd

STALE_DAYS = 45
MIN_PROJECT_CHARS = (
    200  # shorter than this reads as boilerplate, not a concrete problem
)
MAX_BAND_RATIO = 2.0  # max/min salary above this spans several levels or geos
MAX_SCORE = 6
MAX_REQUIREMENTS = 5
MANUAL_REVIEW_PENDING = "PENDING"  # the 3 manual checks are still to do


def ghost_risk(job: Mapping[str, Any], now: datetime) -> int:
    """Count the ghost-job signals readable from the posting itself.

    Args:
        job: Posting with keys fecha (aware datetime or NaT), proyecto (str or
            None), min_salary and max_salary (number, None or NaN).
        now: Timezone-aware reference time used to age the posting.

    Returns:
        0 to 3: one point each for stale, vague project and missing/wide band.
        An unknown fecha (NaT) is not counted as stale.

    Raises:
        KeyError: If ``job`` lacks one of the keys above.
    """
    stale = (now - job["fecha"]).days >= STALE_DAYS
    vague = len(job["proyecto"] or "") < MIN_PROJECT_CHARS
    low, high = job["min_salary"], job["max_salary"]
    bad_band = pd.isna(low) or pd.isna(high) or not low or high / low > MAX_BAND_RATIO
    return int(stale) + int(vague) + int(bad_band)


def score_jd(job: Mapping[str, Any], now: datetime) -> int:
    """Likelihood the posting is real: 6 = no ghost signals found.

    Args:
        job: Posting, as described in ``ghost_risk``.
        now: Timezone-aware reference time.

    Returns:
        3 to 6. The 3 manual checklist items are assumed clean.

    Raises:
        KeyError: If ``job`` lacks a key ``ghost_risk`` needs.
    """
    return MAX_SCORE - ghost_risk(job, now)


def extract_requirements(text: str) -> list[str]:
    """Take the first non-empty lines of a description as its must-haves.

    Args:
        text: Plain-text job description.

    Returns:
        Up to ``MAX_REQUIREMENTS`` stripped lines; empty for empty text.
    """
    # jd-decoder: the earliest requirements are the must-haves
    return [line.strip() for line in text.splitlines() if line.strip()][
        :MAX_REQUIREMENTS
    ]
