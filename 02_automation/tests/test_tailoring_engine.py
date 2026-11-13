import json
import logging
from unittest.mock import MagicMock

import pandas as pd
import pytest

from tailoring_engine import (
    AI_TRAINING_RULES,
    STRICT_RULES,
    _validate_pitch,
    tailor_all,
)

LEAD = pd.DataFrame(
    {
        "titulo": ["Data Engineer Sénior"],
        "empresa": ["Acme Ñandú"],
        "link": ["https://x/1"],
        "score_jd": [6],
        "requisitos": ["Python | SQL | Airflow"],
        "fecha_scrapeo": ["2026-11-12"],
        "manual_review": ["PENDING"],
        "vertical": ["data_ai"],
    }
)

TAILORED = {
    "pitch": "Línea uno\nLínea dos\nLínea tres",
    "cv": "# Ian Bertoni\n\n## Proyectos\n- RAG pipeline",
    "preguntas": ["¿Airflow?", "¿SQL?", "¿Python?"],
    "cubiertos": ["Python", "SQL"],
    "no_cubiertos": ["Airflow"],
}


def groq_session(status: int = 200, content: str = json.dumps(TAILORED)):
    response = MagicMock(status_code=status, ok=status < 400, text="server error")
    response.json.return_value = {"choices": [{"message": {"content": content}}]}
    session = MagicMock()
    session.post.return_value = response
    return session


def test_writes_one_markdown_with_the_three_blocks(tmp_path):
    session = groq_session()

    written = tailor_all(LEAD, "CV de Ian", tmp_path, session)

    out = tmp_path / "acme-nandu_data-engineer-senior.md"
    assert written == [out]
    text = out.read_text(encoding="utf-8")
    for block in (
        "## PITCH",
        "## CV ADAPTADO",
        "## NOTAS",
        "Línea tres",
        "RAG pipeline",
        "- ¿SQL?",
        "Cobertura: 2/3 requisitos. No cubiertos: Airflow",
    ):
        assert block in text
    body = session.post.call_args.kwargs["json"]
    assert body["response_format"] == {"type": "json_object"}
    prompt = json.dumps(body["messages"], ensure_ascii=False)
    assert "CV de Ian" in prompt and "Airflow" in prompt and "inventar" in prompt


@pytest.mark.parametrize(
    "session",
    [
        groq_session(status=500),
        groq_session(content="{no es json"),
        groq_session(content="{}"),
    ],
    ids=["http-500", "malformed-json", "missing-keys"],
)
def test_groq_failure_logs_error_and_keeps_going(tmp_path, caplog, session):
    with caplog.at_level(logging.ERROR):
        written = tailor_all(LEAD, "CV", tmp_path, session)

    assert written == []
    assert list(tmp_path.iterdir()) == []
    assert any(
        r.levelno == logging.ERROR and "Acme" in r.message for r in caplog.records
    )


def test_less_than_two_covered_requirements_writes_skip_without_pitch(tmp_path):
    reply = {**TAILORED, "cubiertos": ["Python"], "no_cubiertos": ["SQL", "Airflow"]}

    [out] = tailor_all(LEAD, "CV", tmp_path, groq_session(content=json.dumps(reply)))

    text = out.read_text(encoding="utf-8")
    assert "SKIP: 1/3 requisitos cubiertos. No cubiertos: SQL, Airflow" in text
    assert "## PITCH" not in text and "Línea uno" not in text


CV = "Built a RAG pipeline. Implemented over 1,200 automated tests (pytest). PostgreSQL, Flask."


def test_invented_years_are_retried_once_then_failed_validation(tmp_path, caplog):
    reply = {**TAILORED, "pitch": "Tengo 5 años de Python.\nRAG.\nQA."}
    session = groq_session(content=json.dumps(reply))

    with caplog.at_level(logging.WARNING):
        [out] = tailor_all(LEAD, CV, tmp_path, session)

    assert session.post.call_count == 2
    retry_prompt = session.post.call_args.kwargs["json"]["messages"][0]["content"]
    assert STRICT_RULES in retry_prompt
    text = out.read_text(encoding="utf-8")
    assert "FAILED_VALIDATION" in text and "5 años" not in text
    assert "## PITCH" not in text and "## CV ADAPTADO" in text and "## NOTAS" in text
    assert any(
        r.levelno == logging.WARNING and "años" in r.message for r in caplog.records
    )


def test_pitch_with_cv_figures_is_accepted():
    assert _validate_pitch("Implementé más de 1200 tests con pytest.", CV) is None
    assert _validate_pitch("Over 1,200 automated tests.", CV) is None


@pytest.mark.parametrize(
    "pitch",
    [
        "Gestioné PostgreSQL a gran escala.",
        "APIs con Flask en producción.",
        "Más de 3 proyectos con pytest.",
        "Con más de cinco años de desarrollo Python.",
    ],
)
def test_pitch_with_inflated_claims_is_rejected(pitch):
    assert _validate_pitch(pitch, CV)


def test_ai_training_pitch_is_short_and_skips_the_cv(tmp_path):
    lead = LEAD.assign(titulo="AI Coding Evaluator", vertical="ai_training")
    session = groq_session()

    [out] = tailor_all(lead, "CV de Ian", tmp_path, session)

    system = session.post.call_args.kwargs["json"]["messages"][0]["content"]
    assert AI_TRAINING_RULES in system
    text = out.read_text(encoding="utf-8")
    assert "## PITCH" in text and "## NOTAS" in text
    assert "## CV ADAPTADO" not in text and "RAG pipeline" not in text
