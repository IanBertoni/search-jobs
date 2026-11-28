from datetime import date

import pytest

from analytics import (
    Application,
    append_applications,
    load_applications,
    render,
    summarize,
)

HEADER = "fecha_envio,empresa,puesto,link,canal,estado\n"
TODAY = date(2026, 12, 15)


def app(fecha: str | None, estado: str = "pending", canal: str = "ats") -> Application:
    sent = date.fromisoformat(fecha) if fecha else None
    return Application(sent, "Acme", "Python Engineer", "https://x/1", canal, estado)


def test_load_applications_reads_every_row(tmp_path):
    csv = tmp_path / "applications.csv"
    csv.write_text(
        HEADER
        + "2026-11-25,Supabase,SDK Engineer,https://x/1,ats,pending\n"
        + ",Sampleai,AI Trainer,https://x/2,plataforma,rejected\n",
        encoding="utf-8",
    )

    apps = load_applications(csv)

    assert apps[0] == Application(
        date(2026, 11, 25), "Supabase", "SDK Engineer", "https://x/1", "ats", "pending"
    )
    assert apps[1].fecha_envio is None  # unknown date: counted, never mature


def test_load_applications_is_empty_without_the_file(tmp_path):
    assert load_applications(tmp_path / "missing.csv") == []


@pytest.mark.parametrize(
    "row, error",
    [
        ("2026-11-25,Acme,Dev,https://x/1,ats,ghosted\n", "estado"),
        ("25/11/2026,Acme,Dev,https://x/1,ats,pending\n", "fecha_envio"),
    ],
)
def test_load_applications_rejects_bad_rows_with_the_line_number(tmp_path, row, error):
    csv = tmp_path / "applications.csv"
    csv.write_text(HEADER + row, encoding="utf-8")

    with pytest.raises(ValueError, match=rf"line 2: .*{error}"):
        load_applications(csv)


def test_summarize_counts_only_applications_older_than_14_days_as_mature():
    apps = [
        app("2026-11-30"),  # 15 days: mature
        app("2026-12-01"),  # 14 days: not yet
        app(None),
    ]

    summary = summarize(apps, TODAY)

    assert (summary.total, summary.mature) == (3, 1)


def test_summarize_response_rate_is_any_answer_over_mature_applications():
    apps = [
        app("2026-11-01", "rejected"),
        app("2026-11-01", "interview"),
        app("2026-11-01", "offer"),
        app("2026-11-01"),
        app("2026-12-10", "rejected"),  # answered fast, but not mature yet
    ]

    summary = summarize(apps, TODAY)

    assert (summary.mature, summary.responses, summary.interviews) == (4, 3, 2)
    assert summary.response_rate == pytest.approx(0.75)


def test_summarize_has_no_rate_without_mature_applications():
    assert summarize([app("2026-12-10")], TODAY).response_rate is None


def test_render_breaks_the_rate_down_by_channel():
    apps = [app("2026-11-01", "rejected", "email"), app("2026-11-01", canal="ats")]

    report = render(apps, TODAY)

    assert "Total: 2" in report
    assert "Tasa de respuesta: 1/2 (50%)" in report
    assert "ats: 0/1 (0%)" in report
    assert "email: 1/1 (100%)" in report


def test_render_says_when_there_is_no_mature_data_yet():
    assert "sin datos maduros" in render([app("2026-12-10")], TODAY)


def test_append_applications_creates_the_file_and_skips_known_links(tmp_path):
    csv = tmp_path / "applications.csv"
    first = app("2026-11-28")

    assert append_applications([first], csv) == 1
    assert append_applications([first, app("2026-11-28", canal="email")], csv) == 0

    assert load_applications(csv) == [first]


def test_append_applications_keeps_rows_without_a_link(tmp_path):
    csv = tmp_path / "applications.csv"
    no_link = Application(None, "Canonical", "Python", "", "ats", "pending")

    assert append_applications([no_link, no_link], csv) == 2
