from datetime import UTC, datetime, timedelta

import pytest

from matcher.jd_decoder import extract_requirements, ghost_risk, score_jd

NOW = datetime(2026, 11, 12, tzinfo=UTC)
CONCRETE_PROJECT = "Migramos nuestro data lake a Databricks y necesitamos " * 10


def job(days_old=1, projects=CONCRETE_PROJECT, min_salary=3000, max_salary=4000):
    return {
        "fecha": NOW - timedelta(days=days_old),
        "proyecto": projects,
        "min_salary": min_salary,
        "max_salary": max_salary,
    }


@pytest.mark.parametrize(
    ("posting", "expected_risk"),
    [
        (job(), 0),
        (job(days_old=60), 1),
        (job(projects="Somos una gran empresa."), 1),
        (job(min_salary=None, max_salary=None), 1),
        (job(min_salary=1000, max_salary=5000), 1),
        (job(days_old=90, projects="", min_salary=None, max_salary=None), 3),
    ],
)
def test_ghost_risk_counts_automatable_signals(posting, expected_risk):
    assert ghost_risk(posting, NOW) == expected_risk


def test_score_jd_is_six_minus_ghost_risk():
    assert score_jd(job(), NOW) == 6
    assert score_jd(job(days_old=90, projects="", max_salary=None), NOW) == 3


def test_extract_requirements_takes_first_lines_as_must_haves():
    text = "Python avanzado\n\nSQL\nAirflow\nDocker\nAWS\nInglés"
    assert extract_requirements(text) == [
        "Python avanzado",
        "SQL",
        "Airflow",
        "Docker",
        "AWS",
    ]


def test_extract_requirements_empty_text():
    assert extract_requirements("") == []


def test_extract_requirements_skips_logistics_lines():
    # micro1 "AI Trainer (SWE)": pay/location/hours bullets came first
    text = (
        "Pay: $60–$120/hour equivalent, paid per accepted task\n"
        "Location: Global, fully remote\n"
        "Job Type: Contractor\n"
        "Time Commitment: 15+ hours per week\n"
        "Rate: 45 USD per hour\n"
        "Hybrid or on-site in Madrid\n"
        "Part-time, 6 months\n"
        "Freelance engagement\n"
        "Strong Python and software engineering skills\n"
        "Experience reviewing code and writing unit tests\n"
        "Accurate written English\n"
    )

    assert extract_requirements(text) == [
        "Strong Python and software engineering skills",
        "Experience reviewing code and writing unit tests",
        "Accurate written English",  # "accurate" is not the word "rate"
    ]
