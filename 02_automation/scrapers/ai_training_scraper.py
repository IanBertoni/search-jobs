"""AI training / evaluation aggregators -> rows shaped like ``clean_jobs``.

Both sites disallow /api/ in robots.txt (that's where the aitraining.jobs MCP
and the aitrainer.work API live), so this reads their public feeds instead:
- aitrainer.work /data/jobs.json: every listing of 30+ platforms (Anyone AI,
  Mindrift, micro1...), CC BY 4.0 with a link back, which each lead keeps.
- aitraining.jobs /feeds/rss.xml: its newest 100 roles.
Only titles of the AI training vertical are kept: these feeds also carry
thousands of expert gigs (Chemistry Expert, Accountant) that aren't the target.
"""

import logging
import re
from datetime import UTC, datetime

import pandas as pd
import requests

from scrapers.getonboard_scraper import AI_TRAINING_TITLE, TIMEOUT_S
from scrapers.remotive_scraper import PUB_DATE_FORMAT, feed_jobs, read_rss

AITRAINER_WORK_JOBS = "https://aitrainer.work/data/jobs.json"
AITRAINER_WORK_PAGE = "https://aitrainer.work/jobs/"  # + platform/slug
AITRAINER_WORK_FIELDS = [
    "title",
    "platform",
    "slug",
    "description",
    "first_seen",
    "last_seen",
    "pay_min",
    "pay_max",
    "geographic_restrictions",
    "eligible_countries",
    "languages",
    "apply_url",
]
# the feed keeps closed listings too; a platform is re-read every few days
FRESH_DAYS = 14
AITRAINING_JOBS_FEED = "https://aitraining.jobs/feeds/rss.xml"
# "AI Conversation Evaluator at micro1 ($40-$70/hr)"
TITLE_AT_PLATFORM = re.compile(
    r"^(?P<titulo>.+) at (?P<empresa>[^()]+?)(?: \((?P<pay>[^)]*)\))?$"
)
PAY_RANGE = re.compile(r"\$(\d+(?:\.\d+)?)\s*-\s*\$(\d+(?:\.\d+)?)")

logger = logging.getLogger(__name__)


def _is_training(titles: pd.Series) -> pd.Series:
    return titles.fillna("").str.contains(AI_TRAINING_TITLE).astype(bool)


def _joined(values: object) -> str | None:
    return ", ".join(values) if isinstance(values, list) and values else None


def fetch_aitrainer_work(
    session: requests.Session | None = None, now: datetime | None = None
) -> pd.DataFrame | None:
    """Download aitrainer.work listings seen in the last ``FRESH_DAYS``.

    Args:
        session: HTTP session to use. Defaults to a new ``requests.Session``.
        now: Timezone-aware reference time for freshness. Defaults to now.

    Returns:
        AI training listings with the ``clean_jobs`` columns (pay band as
        salary), or None if the download failed or the JSON isn't a job list.
    """
    session = session or requests.Session()
    now = pd.Timestamp(now or datetime.now(UTC))
    try:
        response = session.get(AITRAINER_WORK_JOBS, timeout=TIMEOUT_S)
        response.raise_for_status()
        listings = pd.DataFrame(response.json()["jobs"])
    except (requests.RequestException, ValueError, KeyError, TypeError) as error:
        logger.error("aitrainer.work: feed skipped: %s", error)
        return None
    listings = listings.reindex(columns=AITRAINER_WORK_FIELDS)
    # some platforms (Anyone AI) share one referral link for every role: that's
    # their landing page, not the posting's form, so only unique links are kept
    shared = listings["apply_url"].duplicated(keep=False)
    listings = listings.assign(apply_url=listings["apply_url"].mask(shared))
    last_seen = pd.to_datetime(
        listings["last_seen"], format="ISO8601", utc=True, errors="coerce"
    )
    fresh = now - last_seen <= pd.Timedelta(days=FRESH_DAYS)
    listings = listings[fresh & _is_training(listings["title"])]
    # eligible_countries is the stricter list ("United States, Canada")
    where = listings["eligible_countries"].map(_joined)
    where = where.fillna(listings["geographic_restrictions"].map(_joined))
    return feed_jobs(
        pd.DataFrame(
            {
                "titulo": listings["title"],
                "empresa": listings["platform"],
                # the aitrainer.work page stays the key and the CC BY link back
                "link": AITRAINER_WORK_PAGE
                + listings["platform"]
                + "/"
                + listings["slug"],
                "description": listings["description"],
                "fecha": pd.to_datetime(
                    listings["first_seen"], format="ISO8601", utc=True, errors="coerce"
                ),
                "min_salary": listings["pay_min"],
                "max_salary": listings["pay_max"],
                "pais": where,
                "apply_link": listings["apply_url"],
                "idiomas": listings["languages"].map(_joined),
            }
        )
    )


def fetch_aitraining_jobs(
    session: requests.Session | None = None,
) -> pd.DataFrame | None:
    """Download the aitraining.jobs RSS feed (newest 100 roles).

    Args:
        session: HTTP session to use. Defaults to a new ``requests.Session``.

    Returns:
        AI training roles with the ``clean_jobs`` columns (platform as
        empresa, hourly pay from the title as salary), or None if it failed.
    """
    feed = read_rss(
        session or requests.Session(), AITRAINING_JOBS_FEED, "aitraining.jobs"
    )
    if feed is None:
        return None
    parts = feed["title"].fillna("").str.extract(TITLE_AT_PLATFORM)
    pay = parts["pay"].fillna("").str.extract(PAY_RANGE).astype(float)
    rows = pd.DataFrame(
        {
            "titulo": parts["titulo"],
            "empresa": parts["empresa"],
            "link": feed["link"],
            "description": feed["description"],
            "fecha": pd.to_datetime(
                feed["pubDate"], format=PUB_DATE_FORMAT, utc=True, errors="coerce"
            ),
            "min_salary": pay[0],
            "max_salary": pay[1],
            # "<p>Location: Remote, worldwide</p>"; about half the roles omit it
            "pais": feed["description"]
            .fillna("")
            .str.extract(r"Location:\s*([^<]+)")[0]
            .str.strip(),
        }
    )
    return feed_jobs(rows[_is_training(rows["titulo"])])
