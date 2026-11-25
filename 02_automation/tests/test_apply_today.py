import pandas as pd
import pytest

from apply_today import (
    drop_applied_links,
    load_applied_links,
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
        "pais": ["Remote", "Unknown", "Remote", "Hybrid: Chile"],
        "priority": ["P1", "P2", "P1", "P3"],
    }
)


def test_select_top_filters_by_score_best_first():
    top = select_top(LEADS)

    assert list(top["empresa"]) == ["Beta", "Acme"]


def test_select_top_shows_score_six_first_then_five_and_four():
    leads = pd.DataFrame(
        {
            "titulo": ["Data Engineer"] * 5,
            "empresa": ["Acme"] * 5,
            "score_jd": [4, 6, 3, 5, 6],
            "vertical": ["data_ai"] * 5,
            "priority": ["P1"] * 5,
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
    assert "- Prioridad: P1 (Remote)" in md


def test_select_top_lowers_the_bar_to_two_for_ai_training():
    leads = pd.DataFrame(
        {
            "titulo": ["AI Coding Evaluator", "Data Engineer"],
            "empresa": ["mindrift", "Acme"],
            "score_jd": [2, 3],
            "vertical": ["ai_training", "data_ai"],
            "priority": ["P1", "P1"],
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


def test_select_top_orders_by_fit_anyone_ai_then_coding_titles():
    leads = pd.DataFrame(
        {
            "titulo": [
                "Audio Evaluator",
                "Python Coding Specialist - AI Trainer",
                "Data Analyst - AI Trainer",
                "Data Engineer",
                "Backend Developer - AI Trainer",
            ],
            "empresa": ["meridial", "mindrift", "anyoneai", "Acme", "Anyone AI"],
            "score_jd": [6, 4, 3, 6, 5],
            "vertical": ["ai_training"] * 3 + ["data_ai", "ai_training"],
            "priority": ["P1"] * 5,
            "requisitos": [""] * 5,
        }
    )

    order = list(select_top(leads)["titulo"])

    assert order == [
        "Backend Developer - AI Trainer",  # Anyone AI, higher score first
        "Data Analyst - AI Trainer",  # Anyone AI
        "Python Coding Specialist - AI Trainer",  # coding title
        "Audio Evaluator",  # the rest, by score
        "Data Engineer",
    ]


def test_select_top_ranks_by_priority_before_fit():
    leads = pd.DataFrame(
        {
            "titulo": ["Backend Developer - AI Trainer"] * 2 + ["AI Coding Evaluator"],
            "empresa": ["anyoneai", "anyoneai", "mindrift"],
            "score_jd": [6, 6, 3],
            "vertical": ["ai_training"] * 3,
            "requisitos": [""] * 3,
            "pais": ["Unknown", "Remote", "Remote"],
            "priority": ["P2", "P1", "P1"],
        }
    )

    top = select_top(leads)

    # a P1 from any platform beats Anyone AI in P2
    assert list(top["priority"]) == ["P1", "P1", "P2"]
    assert list(top["empresa"])[:2] == ["anyoneai", "mindrift"]


def evaluator(**fields) -> pd.DataFrame:
    lead = {
        "titulo": "AI Coding Evaluator",
        "empresa": "micro1",
        "score_jd": 5,
        "vertical": "ai_training",
        "requisitos": "Review Python code",
        "pais": "Remote",
        "priority": "P1",
    }
    return pd.DataFrame([{**lead, **fields}])


@pytest.mark.parametrize(
    "where",
    [
        {"pais": "Hybrid: Chile", "priority": "P3"},
        {"pais": "On-site: Chile", "priority": "P3"},
        {"pais": "Remote, United States", "priority": "P2"},
        {"pais": "Germany", "priority": "P2"},
    ],
)
def test_select_top_drops_on_site_hybrid_and_other_countries(where):
    # only 100% remote offers that include the target regions
    assert select_top(evaluator(**where)).empty


@pytest.mark.parametrize(
    "where",
    [
        {"pais": "Remote", "priority": "P1"},
        {"pais": "World Wide - Remote", "priority": "P1"},
        {"pais": "Argentina, Brazil", "priority": "P1"},
        {"pais": "Unknown", "priority": "P2"},  # can't tell: Ian checks it
    ],
)
def test_select_top_keeps_remote_anywhere_and_unknown(where):
    assert len(select_top(evaluator(**where))) == 1


@pytest.mark.parametrize(
    "lead",
    [
        {"idiomas": "German, English"},
        {"idiomas": "Hindi"},
        {"titulo": "Hindi Audio Evaluator"},
        {"requisitos": "Native Japanese speaker | Python"},
    ],
)
def test_select_top_drops_offers_in_other_languages(lead):
    assert select_top(evaluator(**lead)).empty


@pytest.mark.parametrize(
    "lead",
    [
        {"idiomas": "English, Spanish"},
        {"idiomas": "Latin American Spanish"},
        {"idiomas": "American English"},
        {"requisitos": "Polish the UI | Python"},  # the verb, not the language
    ],
)
def test_select_top_keeps_english_and_spanish(lead):
    assert len(select_top(evaluator(**lead))) == 1


@pytest.mark.parametrize(
    "lead",
    [
        {"titulo": "Senior AI Coding Evaluator"},
        {"titulo": "Lead Code Evaluator"},
        {"requisitos": "3–7 years of professional software engineering experience"},
        {"requisitos": "5+ years with Python"},
        {"requisitos": "At least 3 years in backend"},
    ],
)
def test_select_top_drops_senior_and_three_plus_years(lead):
    assert select_top(evaluator(**lead)).empty


@pytest.mark.parametrize(
    "titulo",
    [
        "Physics Expert - AI Trainer",
        "Biology Expert - AI Trainer",
        "AI Trainer (Life Sciences)",
        "CFD Engineer - AI Trainer",
        "CAD Engineer - AI Trainer",
        "Freelance Mechanical CAD&Simulation Engineer - AI Trainer",
        "Nuclear Engineer - AI Trainer (Contract)",
        "Certified Chef Consultant – AI Research & Annotation Project",
        "Law Model Prompt Evaluator",
        "AI Trainer (Mathematics & Statistics)",
        "AI Trainer (Earth & Environmental Sciences)",
    ],
)
def test_select_top_drops_experts_of_other_fields(titulo):
    assert select_top(evaluator(titulo=titulo)).empty


@pytest.mark.parametrize(
    "titulo",
    [
        "AI Trainer (ML Engineering)",
        "Java Coding Specialist - Freelance AI Trainer Project",
        "Search Engine Evaluator - Spanish (Argentina)",
    ],
)
def test_select_top_keeps_software_and_generalist_roles(titulo):
    assert len(select_top(evaluator(titulo=titulo))) == 1


def test_select_top_keeps_up_to_two_years():
    assert len(select_top(evaluator(requisitos="1+ years | Python"))) == 1


def test_select_top_drops_unpaid_unless_it_leads_to_a_hire():
    unpaid = evaluator(requisitos="This is an unpaid internship | Python")
    to_hire = evaluator(requisitos="Unpaid for 2 months, then conversion to full-time")

    assert select_top(unpaid).empty
    assert len(select_top(to_hire)) == 1


def test_render_uses_the_direct_link_and_credits_the_portal():
    lead = evaluator(
        link="https://aitrainer.work/jobs/micro1/x",
        apply_link="https://jobs.micro1.ai/post/1",
        manual_review="PENDING",
    )

    md = render_markdown(select_top(lead))

    assert "- Link: https://jobs.micro1.ai/post/1" in md
    assert "- Fuente: https://aitrainer.work/jobs/micro1/x" in md


@pytest.mark.parametrize(
    ("link", "line"),
    [
        ("https://aitrainer.work/jobs/anyoneai/x", "- Link del portal: "),
        ("https://aitraining.jobs/jobs/y", "- Link del portal: "),
        ("https://www.getonbrd.com/jobs/z", "- Link: "),  # the form is on that page
    ],
)
def test_render_flags_aggregator_links_without_a_direct_one(link, line):
    md = render_markdown(select_top(evaluator(link=link, manual_review="PENDING")))

    assert f"{line}{link}" in md


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


def test_load_applied_links_reads_one_link_per_line_with_notes(tmp_path):
    path = tmp_path / "applied_links.txt"
    path.write_text(
        "﻿# comment\n\n"
        "https://job-boards.eu.greenhouse.io/agency/jobs/4778246101/confirmation x\n"
        "https://aitrainer.work/jobs/micro1/ai-trainer-swe-7 cerrado por ahora\n"
        "not a link\n",
        encoding="utf-8",
    )

    assert load_applied_links(path) == {"4778246101", "ai-trainer-swe-7"}


def test_load_applied_links_is_empty_without_the_file(tmp_path):
    assert load_applied_links(tmp_path / "missing.txt") == set()


def test_drop_applied_links_matches_the_posting_id_in_link_or_apply_link():
    leads = pd.DataFrame(
        {
            "titulo": ["Spanish Contributor", "QA Specialist", "AI Trainer", "Other"],
            "link": [
                "https://aitrainer.work/jobs/meridial/spanish",
                "https://www.getonbrd.com/jobs/conversational-ai-qa-specialist-x",
                "https://aitrainer.work/jobs/micro1/ai-trainer-swe-7",
                "https://aitrainer.work/jobs/micro1/ai-trainer-swe-70",
            ],
            "apply_link": [
                "https://job-boards.eu.greenhouse.io/agency/jobs/4778246101",
                None,
                None,
                None,
            ],
        }
    )
    applied = {
        "4778246101",  # applied through the form: its confirmation link
        "conversational-ai-qa-specialist-x",  # /empleos/<category>/<slug> on getonbrd
        "ai-trainer-swe-7",
    }

    assert list(drop_applied_links(leads, applied)["titulo"]) == ["Other"]


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
