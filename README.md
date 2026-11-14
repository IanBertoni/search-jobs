# Agentic Job Search Automation (Python + LLMs)

A small agent pipeline that finds remote Data & AI roles, decides which ones are
worth my time, and drafts the application text with an LLM, so the only
step I do by hand is hitting "send".

Built test-first. Every LLM output goes through a guardrail before it's saved.
Sending stays manual on purpose: the agent never submits an application or
an email on its own.

## The agentic loop

Each module follows the same loop: **observe → evaluate → act → check the output**.

```text
[ Hacker News Post ]
       │
       ▼
┌──────────────────────────────┐
│ 1. Hardcoded Filter          │  <-- Drops H1B, US-Only, Senior roles
└──────────────────────────────┘
       │
       ▼
┌──────────────────────────────┐
│ 2. Agentic Evaluator (Groq)  │  <-- Reads context, decides to SKIP or Write
└──────────────────────────────┘
       │
       ▼
┌──────────────────────────────┐
│ 3. Software Guardrails       │  <-- Regex fixes, AI-tone detection
└──────────────────────────────┘
       │
       ▼
[ Clean Markdown Output ] 
```

| Step | Cold outreach (`cold_outreach.py`) | Applications (`tailoring_engine.py`) |
|---|---|---|
| Scraper | Latest HN "Who is hiring?" thread (Algolia API) | Get on Board API, Remotive and AI-training RSS feeds |
| Filter | Dealbreakers: closed posts, visa/citizenship, on-site UK/EU, PhD/PyTorch, 5+ years, senior titles | Profile keywords, seniority, ghost-job risk score (`matcher/jd_decoder.py`) |
| LLM | One Groq call per post → a cold email of 5 lines or fewer | One Groq call per lead → JSON with pitch, CV and interview questions |
| Guardrail | `SKIP` veto, company-name fix, "sounds like AI" flag | Rejects pitches with invented numbers, years or "at scale"; one stricter retry |
| Output | `03_tracking/outreach/founders_outreach.md` | `03_tracking/tailored/*.md` |

## Evaluations & Guardrails

LLMs are confidently wrong, so the agent never trusts a reply as is.

- **192 automated tests** (pytest) cover the scrapers, filters, matcher and
  guardrails. Scrapers run on saved API/RSS fixtures, and the LLM is mocked, so the suite
  runs offline in about 3 seconds.
- **Deterministic filter before the LLM.** Hard rules drop posts that
  can't work (closed roles, "US citizenship required", "Hybrid UK", PhD/PyTorch
  research roles, 5+ years) before any LLM call. That costs nothing and gives
  the same result every run.
- **SKIP rule.** The prompt makes the model answer with exactly `SKIP` when a
  post breaks the requirements in a way the filter missed (for example "REMOTE (US)").
  The code drops those offers instead of saving a weak email.
- **Output validation.** The tailoring guardrail rejects any pitch that
  mentions years of experience, numbers the CV doesn't have, or "at scale"
  claims. It retries once with stricter rules, then marks the lead
  `FAILED_VALIDATION` instead of shipping an invented claim.
- **Data validation at the boundaries.** HTML is stripped from API text,
  malformed JSON and HTTP errors are logged and skipped per lead (one bad reply
  doesn't kill the batch), and settings come from `.env` with checked types.

## Stack

Python 3 · requests · pandas · Groq (`openai/gpt-oss-120b`) · pytest + pytest-cov · ruff

## Run it

```bash
pip install -r requirements.txt
cd 02_automation
cp .env.example .env                      # add GROQ_API_KEY
python -m pytest -q                       # 192 tests, offline
python -m scrapers.getonboard_scraper     # collect leads
python tailoring_engine.py --top 5        # pitch + CV per lead
python cold_outreach.py --top 5           # founder emails from HN
```

## Layout

```
01_assets/      CV and base pitches
02_automation/  scrapers/, matcher/, cold_outreach.py, tailoring_engine.py, tests/
03_tracking/    leads, tailored applications, outreach drafts
```

Scraping follows robots.txt and rate limits, and uses public APIs or RSS
feeds where they exist.
