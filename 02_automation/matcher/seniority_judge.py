"""Groq reads a posting's requirements and says if Ian can realistically apply.

Keyword filters only see "junior" in a title; most roles a self-taught dev can
land never say it, and many "Data Engineer" titles ask 1-2 years. The judge
reads the requirements instead: years, degree, location, kind of role.
"""

import json
import re

import requests

from cold_outreach import groq_chat

VERDICTS = {"apply", "stretch", "skip"}
JSON_OBJECT = re.compile(r"\{.*\}", re.DOTALL)

JUDGE_PROMPT = """You screen job postings for one candidate:
a self-taught Data & AI Engineer in Argentina with no university degree and
under 1 year of paid experience, but a strong public portfolio: a production RAG
system with anti-hallucination guardrails, 1,200+ automated tests, DanAgro
Precision (agtech data pipelines). Stack: Python, FastAPI, SQL, LLMs, RAG, ETL.

Judge the requirements in the text, not the title: a title without "junior"
can still fit, and "Data Engineer" often asks only 1-2 years.
- apply: asks 2 years or less (or states none); a degree is not mandatory
  (absent, "preferred", or "or equivalent experience"); doable from Argentina
  (remote LatAm/global, or located in Argentina); an engineering, data or AI role.
- stretch: everything fits except one soft gap: 3 years asked, a degree
  "required" without saying equivalent experience counts, or a mostly different stack.
- skip: 4+ years, a mandatory degree with no alternative, residency or work
  authorization Argentina can't meet, or not an engineering/data/AI role.
Reply with ONLY this JSON, no other text:
{"verdict": "apply|stretch|skip", "min_years": <int or null>,
 "degree_required": <true|false>, "from_argentina": <true|false>,
 "reason": "<15 words max, in English>"}"""


def parse_verdict(reply: str) -> dict:
    """The JSON object in Groq's reply; ValueError if missing or unknown verdict."""
    match = JSON_OBJECT.search(reply)
    if not match:
        raise ValueError(f"no JSON in reply: {reply[:100]!r}")
    data = json.loads(match.group(0))
    if data.get("verdict") not in VERDICTS:
        raise ValueError(f"unknown verdict: {data.get('verdict')!r}")
    return {
        "verdict": data["verdict"],
        "min_years": data.get("min_years"),
        "degree_required": data.get("degree_required"),
        "from_argentina": data.get("from_argentina"),
        "reason": str(data.get("reason") or "").strip(),
    }


def judge_fit(
    titulo: str, ubicacion: str, texto: str, session: requests.Session
) -> dict:
    posting = f"Title: {titulo}\nLocation: {ubicacion}\n\n{texto}"
    return parse_verdict(groq_chat(JUDGE_PROMPT, posting, session))
