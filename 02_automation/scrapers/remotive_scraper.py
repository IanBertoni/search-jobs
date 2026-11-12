"""Remotive "Artificial Intelligence" RSS -> rows shaped like ``clean_jobs``.

robots.txt disallows remotive.com/api/*, so this reads the public RSS feed.
Remotive asks for at most ~4 requests a day and a link back to the posting:
the radar runs once a day and every lead keeps its Remotive link.
"""

import logging
import xml.etree.ElementTree as ET

import pandas as pd
import requests

from scrapers.getonboard_scraper import (
    TIMEOUT_S,
    _text_or_unknown,
    clean_jobs,
    html_to_text,
)

REMOTIVE_FEED = "https://remotive.com/remote-jobs/feed/artificial-intelligence"
PUB_DATE_FORMAT = "%a, %d %b %Y %H:%M:%S %Z"  # RFC 822: "Thu, 22 Oct 2026 18:40:19 GMT"

logger = logging.getLogger(__name__)


def fetch_remotive(session: requests.Session | None = None) -> pd.DataFrame | None:
    """Download the Remotive AI feed.

    Args:
        session: HTTP session to use. Defaults to a new ``requests.Session``.

    Returns:
        One row per posting with the ``clean_jobs`` columns (no salary or
        seniority in the feed), or None if the feed failed or isn't valid XML.
    """
    # shortcut: one attempt, no retry; the next daily run picks the feed up again
    session = session or requests.Session()
    try:
        response = session.get(REMOTIVE_FEED, timeout=TIMEOUT_S)
        response.raise_for_status()
        items = ET.fromstring(response.content).iter("item")
    except (requests.RequestException, ET.ParseError) as error:
        logger.error("remotive: feed skipped: %s", error)
        return None
    feed = pd.DataFrame(
        [
            {
                tag: item.findtext(tag)
                for tag in ("title", "company", "link", "pubDate", "description")
            }
            for item in items
        ],
        columns=["title", "company", "link", "pubDate", "description"],
    )
    description = feed["description"].map(html_to_text)
    return (
        clean_jobs([])
        .reindex(range(len(feed)))
        .assign(
            titulo=_text_or_unknown(feed["title"], "title"),
            empresa=_text_or_unknown(feed["company"], "company"),
            link=_text_or_unknown(feed["link"], "link"),
            descripcion=description,
            # no separate project field: the description is the posting's whole context
            proyecto=description,
            fecha=pd.to_datetime(
                feed["pubDate"], format=PUB_DATE_FORMAT, utc=True, errors="coerce"
            ),
        )
    )
