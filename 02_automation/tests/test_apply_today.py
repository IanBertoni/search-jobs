import pandas as pd

from apply_today import render_markdown, select_top

LEADS = pd.DataFrame(
    {
        "titulo": ["Data Engineer", "Ghost Analyst", "ML Engineer"],
        "empresa": ["Acme", "Nadie", "Beta"],
        "link": ["https://x/1", "https://x/2", "https://x/3"],
        "score_jd": [5, 3, 6],
        "requisitos": ["Python | SQL", "Excel", "PyTorch"],
        "fecha_scrapeo": ["2026-11-12"] * 3,
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
