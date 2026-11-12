"""Tailor pitch + CV + interview notes per lead with Groq -> 03_tracking/tailored/.

Read-only: applying stays manual (see CLAUDE.md).
Usage (from 02_automation/): python tailoring_engine.py [--all] [--verbose]
  no flag: the top lead only; --all: every score-6 lead.
"""

import argparse
import json
import logging
import re
import sys
import unicodedata
from pathlib import Path

import pandas as pd
import requests

from apply_today import load_leads, select_top
from config import (
    GROQ_API_KEY,
    GROQ_BASE_URL,
    GROQ_MODEL,
    TRACKING_DIR,
    setup_logging,
)
from scrapers.getonboard_scraper import html_to_text

CV_HTML = Path(__file__).resolve().parents[1] / "cv-ian-no-photo.html"
TAILORED_DIR = TRACKING_DIR / "tailored"
TOP_SCORE = 6
TIMEOUT_S = 120
MIN_COVERED = 2  # fewer covered requirements than this: no pitch, SKIP file
logger = logging.getLogger(__name__)
STYLE = re.compile(r"<style.*?</style>", re.IGNORECASE | re.DOTALL)

SYSTEM_PROMPT = """Sos un career coach para un Data & AI Engineer.
Regla estricta: está PROHIBIDO inventar datos. Usá solo experiencia, proyectos,
tecnologías, cifras, títulos y fechas que aparecen literalmente en el CV. Si la
oferta pide algo que el CV no tiene, no lo agregues ni lo insinúes.
Pitch: SOLO puede afirmar cosas que estén literalmente en el CV. Nunca uses
vocabulario de la oferta presentado como experiencia propia; un requisito que
el CV no cubre se omite del pitch.
Respondé SOLO un objeto JSON con estas claves:
- "cubiertos": lista de los requisitos de la oferta que el CV cubre de forma
  explícita (texto corto de cada requisito).
- "no_cubiertos": lista de los requisitos de la oferta que el CV no cubre.
- "pitch": string, exactamente 3 líneas separadas por \\n, para esta oferta;
  string vacío si "cubiertos" tiene menos de 2 elementos.
- "cv": string, el CV en Markdown con los proyectos/experiencia reordenados
  según relevancia para la oferta (mismo contenido, sin agregar nada).
- "preguntas": lista de 3 strings, preguntas probables de entrevista para esta oferta."""


def slug(text: str) -> str:
    ascii_text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", ascii_text.lower()).strip("-")


def ask_groq(lead, cv_text: str, session: requests.Session) -> dict:
    offer = (
        f"Puesto: {lead.titulo}\nEmpresa: {lead.empresa}\nRequisitos: {lead.requisitos}"
    )
    response = session.post(
        f"{GROQ_BASE_URL}/chat/completions",
        headers={"Authorization": f"Bearer {GROQ_API_KEY}"},
        json={
            "model": GROQ_MODEL,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": f"CV:\n{cv_text}\n\nOFERTA:\n{offer}"},
            ],
        },
        timeout=TIMEOUT_S,
    )
    if not response.ok:  # the body says why (e.g. json_validate_failed)
        raise requests.HTTPError(f"{response.status_code}: {response.text[:300]}")
    return json.loads(response.json()["choices"][0]["message"]["content"])


def render(lead, tailored: dict) -> str:
    covered, missing = tailored["cubiertos"], tailored["no_cubiertos"]
    ratio = f"{len(covered)}/{len(covered) + len(missing)} requisitos"
    not_covered = f"No cubiertos: {', '.join(missing) or '-'}"
    header = f"# {lead.titulo} — {lead.empresa}\n\n{lead.link}\n\n"
    # decided here, not trusted to the model: it may still return a pitch
    if len(covered) < MIN_COVERED:
        return f"{header}SKIP: {ratio} cubiertos. {not_covered}\n"
    questions = "\n".join(f"- {q}" for q in tailored["preguntas"])
    return (
        f"{header}## PITCH\n\n{tailored['pitch']}\n\n"
        f"## CV ADAPTADO\n\n{tailored['cv']}\n\n"
        f"## NOTAS\n\n{questions}\n\nCobertura: {ratio}. {not_covered}\n"
    )


def tailor_all(
    leads: pd.DataFrame, cv_text: str, out_dir: Path, session: requests.Session
) -> list[Path]:
    """One Groq call per lead; a failed lead is logged and skipped."""
    out_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for lead in leads.itertuples():
        try:
            text = render(lead, ask_groq(lead, cv_text, session))
        # ValueError covers malformed JSON; Key/TypeError a reply missing the keys
        except (requests.RequestException, ValueError, KeyError, TypeError) as error:
            logger.error("%s | %s: %s", lead.empresa, lead.titulo, error)
            continue
        path = out_dir / f"{slug(lead.empresa)}_{slug(lead.titulo)}.md"
        path.write_text(text, encoding="utf-8")
        written.append(path)
    return written


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--all", action="store_true", help="every score-6 lead")
    parser.add_argument("--verbose", action="store_true", help="log INFO too")
    args = parser.parse_args()
    setup_logging(args.verbose)
    if not GROQ_API_KEY:
        sys.exit("GROQ_API_KEY missing in 02_automation/.env")
    leads = load_leads(TRACKING_DIR)
    top = select_top(leads, TOP_SCORE) if args.all else select_top(leads).head(1)
    cv_text = html_to_text(STYLE.sub("", CV_HTML.read_text(encoding="utf-8")))
    written = tailor_all(top, cv_text, TAILORED_DIR, requests.Session())
    print(f"{len(written)}/{len(top)} archivos -> {TAILORED_DIR}")


if __name__ == "__main__":
    main()
