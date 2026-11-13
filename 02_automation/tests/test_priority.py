from pathlib import Path

import pandas as pd
import pytest

from scrapers.getonboard_scraper import clean_jobs, priority_of
from tailoring_engine import pick_batch

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.mark.parametrize(
    ("pais", "descripcion", "priority"),
    [
        ("Argentina", "", "P1"),
        ("Remote, LATAM", "", "P1"),
        ("World Wide - Remote", "", "P1"),
        ("Worldwide", "", "P1"),
        ("Remote", "", "P1"),
        ("Remote (most countries)", "", "P1"),
        ("Remote in Mexico", "", "P2"),
        ("Remote, United States", "", "P2"),
        ("United Kingdom", "", "P2"),
        ("Unknown", "", "P2"),
        ("Remote (local)", "", "P2"),
        ("Remote", "Candidates must be based in the US.", "P3"),
        ("Remote", "This role is UK-only.", "P3"),
        ("United States only, excluding Illinois", "", "P3"),
        ("USA Only", "", "P3"),
        ("Hybrid: Chile", "", "P3"),
        ("On-site: Peru", "", "P3"),
    ],
)
def test_priority_ranks_country_without_dropping(pais, descripcion, priority):
    result = priority_of(pd.Series([pais]), pd.Series([descripcion]))

    assert result.iloc[0] == priority


@pytest.mark.parametrize(
    ("modality", "countries", "pais"),
    [
        ("fully_remote", ["Remote"], "Remote"),
        ("remote_local", ["Remote"], "Remote (local)"),
        ("hybrid", ["Peru"], "Hybrid: Peru"),
        ("no_remote", ["Chile"], "On-site: Chile"),
        (None, None, "Unknown"),
    ],
)
def test_getonboard_pais_comes_from_remote_modality(modality, countries, pais):
    job = {
        "id": "x",
        "attributes": {
            "title": "Data Engineer",
            "remote_modality": modality,
            "countries": countries,
        },
    }

    assert clean_jobs([job])["pais"].iloc[0] == pais


def test_feed_sources_fill_pais():
    from scrapers.ai_training_scraper import fetch_aitrainer_work, fetch_aitraining_jobs
    from scrapers.remotive_scraper import fetch_remotive
    from tests.test_ai_training_sources import NOW, Response, Session
    from tests.test_ai_training_vertical import FeedResponse, FeedSession

    aitrainer = fetch_aitrainer_work(
        Session(Response((FIXTURES / "aitrainer_work_jobs.json").read_bytes())), now=NOW
    )
    aitraining = fetch_aitraining_jobs(
        Session(Response((FIXTURES / "aitraining_jobs_feed.xml").read_bytes()))
    )
    remotive = fetch_remotive(FeedSession(FeedResponse()))

    assert list(aitrainer["pais"]) == ["Remote, LATAM", "Remote"]
    assert list(aitraining["pais"]) == ["Remote, worldwide"]
    assert remotive["pais"].iloc[0] == "Worldwide"


def test_pick_batch_takes_the_top_n_and_never_p3():
    shortlist = pd.DataFrame(
        {"titulo": ["a", "b", "c", "d"], "priority": ["P1", "P3", "P2", "P2"]}
    )

    assert list(pick_batch(shortlist, 2)["titulo"]) == ["a", "c"]
    assert list(pick_batch(shortlist, 15)["titulo"]) == ["a", "c", "d"]
