import pandas as pd
import pytest

from apply_today import matches_profile, render_markdown, select_top

LEADS = pd.DataFrame(
    {
        "titulo": ["Data Engineer", "Ghost Analyst", "ML Engineer", "QA Engineer"],
        "empresa": ["Acme", "Nadie", "Beta", "Gamma"],
        "link": ["https://x/1", "https://x/2", "https://x/3", "https://x/4"],
        "score_jd": [5, 3, 6, 6],
        "requisitos": ["Python | SQL", "Excel", "PyTorch", "Cypress | Selenium"],
        "fecha_scrapeo": ["2026-11-12"] * 4,
    }
)


def test_select_top_filters_by_score_best_first():
    top = select_top(LEADS)

    assert list(top["empresa"]) == ["Beta", "Acme"]


def test_render_markdown_lists_each_offer():
    md = render_markdown(select_top(LEADS))

    assert md.startswith("# Ofertas para postular")
    assert "## ML Engineer — Beta" in md
    assert "https://x/1" in md
    assert "- Python\n- SQL" in md
    assert "Ghost Analyst" not in md


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
    ],
)
def test_matches_profile_on_title_whole_words(titulo, matches):
    # requisitos mention Python/data even in QA and Full-Stack posts, so only title counts
    lead = pd.DataFrame({"titulo": [titulo], "requisitos": ["Python | bases de datos"]})

    assert matches_profile(lead).iloc[0] == matches
