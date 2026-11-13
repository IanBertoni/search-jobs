import pandas as pd
import pytest

from apply_today import (
    load_leads,
    matches_profile,
    render_markdown,
    select_top,
)
from scrapers.getonboard_scraper import CONSOLIDATED_CSV, CSV_ENCODING, whole_words

LEADS = pd.DataFrame(
    {
        "titulo": ["Data Engineer", "Ghost Analyst", "ML Engineer", "QA Engineer"],
        "empresa": ["Acme", "Nadie", "Beta", "Gamma"],
        "link": ["https://x/1", "https://x/2", "https://x/3", "https://x/4"],
        "score_jd": [5, 3, 6, 6],
        "requisitos": ["Python | SQL", "Excel", "PyTorch", "Cypress | Selenium"],
        "fecha_scrapeo": ["2026-11-12"] * 4,
        "manual_review": ["PENDING"] * 4,
        "vertical": ["data_ai"] * 4,
    }
)


def test_select_top_filters_by_score_best_first():
    top = select_top(LEADS)

    assert list(top["empresa"]) == ["Beta", "Acme"]


def test_select_top_shows_score_six_first_then_five_and_four():
    leads = pd.DataFrame(
        {
            "titulo": ["Data Engineer"] * 5,
            "score_jd": [4, 6, 3, 5, 6],
            "vertical": ["data_ai"] * 5,
            "requisitos": [""] * 5,
        }
    )

    assert list(select_top(leads)["score_jd"]) == [6, 6, 5, 4]


def test_render_markdown_lists_each_offer():
    md = render_markdown(select_top(LEADS))

    assert md.startswith("# Ofertas para postular")
    assert "## ML Engineer — Beta" in md
    assert "https://x/1" in md
    assert "- Python\n- SQL" in md
    assert "Ghost Analyst" not in md
    assert "- Revisión manual: PENDING (careers page" in md
    assert "- Vertical: data_ai" in md


def test_select_top_lowers_the_bar_to_two_for_ai_training():
    leads = pd.DataFrame(
        {
            "titulo": ["AI Coding Evaluator", "Data Engineer"],
            "score_jd": [2, 3],
            "vertical": ["ai_training", "data_ai"],
            "requisitos": ["", ""],
        }
    )

    assert list(select_top(leads)["titulo"]) == ["AI Coding Evaluator"]


@pytest.mark.parametrize(
    ("titulo", "requisitos", "matches"),
    [
        ("Software Engineer", "Build LLM agents | Python", True),
        ("Backend Engineer", "Design data pipelines on GCP", True),
        ("Software Engineer", "RAG over legal documents", True),
        ("Backend Engineer", "Java | Spring Boot", False),
        ("Frontend Engineer", "LLM chat UI", False),
    ],
)
def test_broad_engineering_titles_need_ai_in_the_requirements(
    titulo, requisitos, matches
):
    lead = pd.DataFrame({"titulo": [titulo], "requisitos": [requisitos]})

    assert matches_profile(lead).iloc[0] == matches


def test_select_top_drops_offers_outside_data_ai_profile():
    assert "QA Engineer" not in set(select_top(LEADS)["titulo"])


@pytest.mark.parametrize(
    ("titulo", "matches"),
    [
        ("Analista de Datos", True),
        ("Ingeniero de IA", True),
        ("Ingeniero LLM / RAG / Agentes", True),
        ("Maintainer Storage HTML", False),
        ("QA Automation Engineer", False),
        ("AI Sales Specialist", False),
        ("Docente Big Data", False),
        ("Especialista en Soporte IA", False),
        ("Data Support Engineer", False),
        ("AI Coding Evaluator", True),
        ("Python Developer - AI Trainer", True),
        ("Analista de Evaluación de Modelos", True),
        ("Video Annotation Specialist", True),
        ("Software Engineering AI Trainer", True),
        ("AI Rater", True),
        ("RLHF Specialist", True),
        ("SWE-Bench Task Writer", True),
        ("Audio Evaluator", True),
        ("Coding Specialist", True),
        ("Quality Analyst", False),
        ("Prompt Specialist", False),
        ("Sales Trainer", False),
        ("Trainee Account Executive", False),
    ],
)
def test_matches_profile_on_titlewhole_words(titulo, matches):
    # requisitos mention Python/data even in QA and Full-Stack posts, so only title counts
    lead = pd.DataFrame({"titulo": [titulo], "requisitos": ["Python | bases de datos"]})

    assert matches_profile(lead).iloc[0] == matches


def test_whole_words_escapes_keywords_and_empty_list_matches_nothing():
    assert whole_words(["node.js"]).search("nodexjs") is None
    assert whole_words([]).search("Data Engineer") is None


@pytest.mark.parametrize(
    "corrupt",
    [b"", b"\xff\xfe\x00garbage", b"not,a,leads\nfile,at,all", b'titulo,empresa\n"cut'],
)
def test_load_leads_rebuilds_corrupt_consolidated_from_snapshots(
    tmp_path, corrupt, caplog
):
    LEADS.to_csv(tmp_path / "leads_2026-11-12.csv", index=False, encoding=CSV_ENCODING)
    (tmp_path / CONSOLIDATED_CSV).write_bytes(corrupt)

    leads = load_leads(tmp_path)

    assert sorted(leads["empresa"]) == sorted(LEADS["empresa"])
    assert "rebuilding" in caplog.text
    assert len(load_leads(tmp_path)) == len(LEADS)  # the file itself was repaired


def test_load_leads_rebuilds_missing_consolidated(tmp_path):
    LEADS.to_csv(tmp_path / "leads_2026-11-12.csv", index=False, encoding=CSV_ENCODING)

    assert len(load_leads(tmp_path)) == len(LEADS)
