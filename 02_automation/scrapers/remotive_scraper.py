"""Remotive "Artificial Intelligence" RSS -> rows shaped like ``clean_jobs``.

robots.txt disallows remotive.com/api/*, so this reads the public RSS feed.
Remotive asks for at most ~4 requests a day and a link back to the posting:
the radar runs once a day and every lead keeps its Remotive link.
Also holds the RSS/feed helpers the other feed sources reuse.
"""

import logging
import xml.etree.ElementTree as ET

import pandas as pd
import requests

from scrapers.getonboard_scraper import (
    TIMEOUT_S,
    UNKNOWN,
    _text_or_unknown,
    clean_jobs,
    html_to_text,
)

REMOTIVE_FEED = "https://remotive.com/remote-jobs/feed/artificial-intelligence"
PUB_DATE_FORMAT = "%a, %d %b %Y %H:%M:%S %Z"  # RFC 822: "Thu, 22 Oct 2026 18:40:19 GMT"
RSS_TAGS = ["title", "company", "link", "pubDate", "description", "location"]

logger = logging.getLogger(__name__)


def read_rss(session: requests.Session, url: str, source: str) -> pd.DataFrame | None:
    """Fetch an RSS feed: one row per <item>, one column per tag in ``RSS_TAGS``.

    Returns None (logged as ERROR) if the request fails or the XML is broken.
    """
    # shortcut: one attempt, no retry; the next daily run picks the feed up again
    try:
        response = session.get(url, timeout=TIMEOUT_S)
        response.raise_for_status()
        items = ET.fromstring(response.content).iter("item")
    except (requests.RequestException, ET.ParseError) as error:
        logger.error("%s: feed skipped: %s", source, error)
        return None
    return pd.DataFrame(
        [{tag: item.findtext(tag) for tag in RSS_TAGS} for item in items],
        columns=RSS_TAGS,
    )


def feed_jobs(rows: pd.DataFrame) -> pd.DataFrame:
    """Shape feed rows like ``clean_jobs`` output.

    Args:
        rows: Columns titulo, empresa, link, description (HTML or text),
            fecha (aware datetime) and pais (None if unknown); min_salary and
            max_salary are optional.
    """
    rows = rows.reset_index(drop=True)
    description = rows["description"].map(html_to_text)
    return (
        clean_jobs([])
        .reindex(rows.index)
        .assign(
            titulo=_text_or_unknown(rows["titulo"], "title"),
            empresa=_text_or_unknown(rows["empresa"], "company"),
            link=_text_or_unknown(rows["link"], "link"),
            descripcion=description,
            # no separate project field: the description is the posting's whole context
            proyecto=description,
            fecha=rows["fecha"],
            min_salary=rows.get("min_salary"),
            max_salary=rows.get("max_salary"),
            pais=rows["pais"].fillna(UNKNOWN),
        )
    )


def fetch_remotive(session: requests.Session | None = None) -> pd.DataFrame | None:
    """Download the Remotive AI feed.

    Args:
        session: HTTP session to use. Defaults to a new ``requests.Session``.

    Returns:
        One row per posting with the ``clean_jobs`` columns (no salary or
        seniority in the feed), or None if the feed failed or isn't valid XML.
    """
    feed = read_rss(session or requests.Session(), REMOTIVE_FEED, "remotive")
    if feed is None:
        return None
    return feed_jobs(
        pd.DataFrame(
            {
                "titulo": feed["title"],
                "empresa": feed["company"],
                "link": feed["link"],
                "description": feed["description"],
                "fecha": pd.to_datetime(
                    feed["pubDate"], format=PUB_DATE_FORMAT, utc=True, errors="coerce"
                ),
                "pais": feed["location"],
            }
        )
    )
