"""Automatable subset of the jd-decoder ghost-risk checklist.

Only 3 of the 6 checklist items can be read from the posting itself:
stale (45+ days), no concrete project/team, missing or very wide comp band.
The rest (careers page, layoffs, recruiter activity) need manual checks, so
they count as 0 risk: most postings are real (jd-decoder base rates).
"""

from collections.abc import Mapping
from datetime import datetime

import pandas as pd

STALE_DAYS = 45
MIN_PROJECT_CHARS = (
    200  # shorter than this reads as boilerplate, not a concrete problem
)
MAX_BAND_RATIO = 2.0  # max/min salary above this spans several levels or geos
MAX_SCORE = 6
MAX_REQUIREMENTS = 5


def ghost_risk(job: Mapping, now: datetime) -> int:
    stale = (now - job["fecha"]).days >= STALE_DAYS
    vague = len(job["proyecto"] or "") < MIN_PROJECT_CHARS
    low, high = job["min_salary"], job["max_salary"]
    bad_band = pd.isna(low) or pd.isna(high) or not low or high / low > MAX_BAND_RATIO
    return int(stale) + int(vague) + int(bad_band)


def score_jd(job: Mapping, now: datetime) -> int:
    """Likelihood the posting is real: 6 = no ghost signals found."""
    return MAX_SCORE - ghost_risk(job, now)


def extract_requirements(text: str) -> list[str]:
    # jd-decoder: the earliest requirements are the must-haves
    return [line.strip() for line in text.splitlines() if line.strip()][
        :MAX_REQUIREMENTS
    ]
