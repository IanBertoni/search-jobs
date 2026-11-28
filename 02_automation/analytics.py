"""Real response rate of the applications in 03_tracking/applications.csv.

File (git-ignored, personal): fecha_envio,empresa,puesto,link,canal,estado.
fecha_envio is YYYY-MM-DD (empty if unknown: counted, never mature); estado is
pending, rejected, interview or offer. Update estado when a company answers.
Separate from applied_links.txt, which also lists closed and ineligible offers.
Usage (from 02_automation/): python analytics.py
"""

import csv
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from config import TRACKING_DIR

APPLICATIONS_CSV = TRACKING_DIR / "applications.csv"
STATES = {"pending", "rejected", "interview", "offer"}
ANSWERED = STATES - {"pending"}
INTERVIEWED = {"interview", "offer"}
# companies take 1-3 weeks to answer: younger applications would bias the rate
MATURE_AFTER_DAYS = 14


@dataclass(frozen=True)
class Application:
    fecha_envio: date | None
    empresa: str
    puesto: str
    link: str
    canal: str
    estado: str


@dataclass(frozen=True)
class Summary:
    total: int
    mature: int
    responses: int
    interviews: int

    @property
    def response_rate(self) -> float | None:
        return self.responses / self.mature if self.mature else None


def _parse(row: dict[str, str], line: int) -> Application:
    estado = row["estado"].strip().lower()
    if estado not in STATES:
        raise ValueError(f"line {line}: estado {estado!r} not in {sorted(STATES)}")
    raw_date = row["fecha_envio"].strip()
    try:
        sent = date.fromisoformat(raw_date) if raw_date else None
    except ValueError as error:
        raise ValueError(
            f"line {line}: fecha_envio {raw_date!r} is not YYYY-MM-DD"
        ) from error
    return Application(
        sent, row["empresa"], row["puesto"], row["link"], row["canal"], estado
    )


def load_applications(path: Path = APPLICATIONS_CSV) -> list[Application]:
    """Every row of the file; empty if it doesn't exist. ValueError on a bad row."""
    if not path.exists():
        return []
    with path.open(encoding="utf-8-sig", newline="") as file:
        # line 1 is the header
        return [_parse(row, line) for line, row in enumerate(csv.DictReader(file), 2)]


def summarize(apps: list[Application], today: date) -> Summary:
    """Counts over all applications; answers only over the mature ones."""
    mature = [
        a
        for a in apps
        if a.fecha_envio and (today - a.fecha_envio).days > MATURE_AFTER_DAYS
    ]
    return Summary(
        total=len(apps),
        mature=len(mature),
        responses=sum(a.estado in ANSWERED for a in mature),
        interviews=sum(a.estado in INTERVIEWED for a in mature),
    )


def _rate_line(label: str, summary: Summary) -> str:
    rate = summary.response_rate
    if rate is None:
        return f"{label}: sin datos maduros"
    return f"{label}: {summary.responses}/{summary.mature} ({rate:.0%})"


def render(apps: list[Application], today: date) -> str:
    """Plain-text report: totals, the overall rate and the rate per channel."""
    summary = summarize(apps, today)
    lines = [
        f"Total: {summary.total}",
        f"Maduras (más de {MATURE_AFTER_DAYS} días): {summary.mature}",
        f"Respuestas: {summary.responses} (entrevistas u ofertas: {summary.interviews})",
        _rate_line("Tasa de respuesta", summary),
        "Por canal:",
    ]
    for canal in sorted({a.canal for a in apps}):
        channel = summarize([a for a in apps if a.canal == canal], today)
        lines.append("  " + _rate_line(canal, channel))
    return "\n".join(lines)


def main() -> None:
    print(render(load_applications(), date.today()))


if __name__ == "__main__":
    main()
