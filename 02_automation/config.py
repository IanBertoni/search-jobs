"""Settings from 02_automation/.env (see .env.example) and logging setup.

Every setting has a default, so the radar also runs without a .env file.
"""

import logging
import os
from collections.abc import Callable
from datetime import date
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

load_dotenv(Path(__file__).with_name(".env"))


def _keywords(raw: str) -> list[str]:
    return [word.strip() for word in raw.split(",") if word.strip()]


def _env(name: str, default: str, cast: Callable[[str], Any] = str) -> Any:
    raw = os.getenv(name, default)
    try:
        return cast(raw)
    except ValueError as error:
        raise ValueError(f"invalid {name}={raw!r} in .env") from error


API_URL = _env("GETONBOARD_API_URL", "https://www.getonbrd.com/api/v0").rstrip("/")
REQUEST_DELAY_S = _env("REQUEST_DELAY_S", "1.0", float)
MIN_SCORE = _env("MIN_SCORE", "4", int)
# Get on Board /api/v0/seniorities: 1 Sin experiencia, 2 Junior, 3 Semi Senior (3-5 years)
SENIORITY_IDS = _env(
    "SENIORITY_IDS", "1,2", lambda raw: {int(i) for i in _keywords(raw)}
)
PROFILE_KEYWORDS = _env(
    "PROFILE_KEYWORDS",
    "data,datos,ai,ia,ml,python,etl,analytics,machine learning"
    ",inteligencia artificial,rag,llm",
    _keywords,
)
BLACKLIST_KEYWORDS = _env(
    "BLACKLIST_KEYWORDS", "sales,docente,soporte,support", _keywords
)
# a posting without seniority or level keyword is kept if it names this stack
STACK_KEYWORDS = _env(
    "STACK_KEYWORDS", "python,fastapi,llm,rag,sql,postgresql", _keywords
)
# generic engineering titles count as data_ai when requisitos mention AI context
BROAD_TITLE_KEYWORDS = _env(
    "BROAD_TITLE_KEYWORDS",
    "software engineer,backend engineer,python developer",
    _keywords,
)
AI_CONTEXT_KEYWORDS = _env(
    "AI_CONTEXT_KEYWORDS", "llm,rag,data pipeline,data pipelines,ai", _keywords
)
AI_TRAINING_MIN_SCORE = _env("AI_TRAINING_MIN_SCORE", "2", int)
# second vertical: AI training / evaluation roles (Anyone AI, Mindrift...)
AI_TRAINING_KEYWORDS = _env(
    "AI_TRAINING_KEYWORDS",
    "evaluator,evaluación,ai trainer,coding trainer,software engineering ai trainer"
    ",quality evaluator,agent evaluator,audio evaluator,prompt evaluator"
    ",code evaluator,ai rater,rlhf,annotation,swe-bench,coding specialist",
    _keywords,
)

GROQ_API_KEY = _env("GROQ_API_KEY", "")
GROQ_MODEL = _env("GROQ_MODEL", "openai/gpt-oss-120b")
GROQ_BASE_URL = _env("GROQ_BASE_URL", "https://api.groq.com/openai/v1").rstrip("/")

TRACKING_DIR = Path(__file__).resolve().parents[1] / "03_tracking"
LOG_FORMAT = "%(asctime)s - %(levelname)s - %(name)s - %(message)s"


def setup_logging(verbose: bool = False, log_dir: Path | None = None) -> None:
    """Send logs to stderr and to ``scraper_YYYY-MM-DD.log``.

    Args:
        verbose: Log INFO and above instead of only WARNING and ERROR.
        log_dir: Folder for the log file. Defaults to ``03_tracking/logs``.
    """
    log_dir = log_dir or TRACKING_DIR / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / f"scraper_{date.today().isoformat()}.log"
    logging.basicConfig(
        level=logging.INFO if verbose else logging.WARNING,
        format=LOG_FORMAT,
        # delay: a clean run with nothing to report leaves no empty file behind
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler(log_file, encoding="utf-8", delay=True),
        ],
        force=True,
    )
