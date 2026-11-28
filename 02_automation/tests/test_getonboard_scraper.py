import json
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd
import pytest
import requests

from scrapers import ai_training_scraper, getonboard_scraper, remotive_scraper
from scrapers.getonboard_scraper import (
    LEAD_COLUMNS,
    RETRY_BACKOFF_S,
    SOURCES,
    TIMEOUT_S,
    build_leads,
    clean_jobs,
    consolidate,
    fetch_jobs,
    html_to_text,
    keep_target_levels,
    save_snapshot,
)

FIXTURE = Path(__file__).parent / "fixtures" / "getonboard_jobs.json"
NOW = datetime(2026, 11, 12, tzinfo=UTC)


@pytest.fixture
def raw_jobs():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))["data"]


@pytest.fixture(autouse=True)
def remotive_offline(monkeypatch):
    # main() also reads the feed sources; keep these tests off the network, 0 jobs
    monkeypatch.setattr(remotive_scraper, "fetch_remotive", lambda: clean_jobs([]))
    for fetch in ("fetch_aitrainer_work", "fetch_aitraining_jobs"):
        monkeypatch.setattr(ai_training_scraper, fetch, lambda: clean_jobs([]))


NOT_JSON = object()


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        if isinstance(self.payload, int):  # an HTTP error status
            raise requests.HTTPError(f"{self.payload} Error", response=self)

    @property
    def status_code(self):
        return self.payload

    def json(self):
        if self.payload is NOT_JSON:
            raise requests.JSONDecodeError("Expecting value", "<html>", 0)
        return self.payload


class FakeSession:
    def __init__(self, payload, failures=0, broken_path=None):
        self.payload = payload
        self.failures = failures  # first N calls raise
        self.broken_path = broken_path  # this source always raises
        self.urls = []
        self.timeouts = []

    def get(self, url, params=None, timeout=None):
        self.urls.append(url)
        self.timeouts.append(timeout)
        if len(self.urls) <= self.failures or (
            self.broken_path and self.broken_path in url
        ):
            raise requests.ConnectionError("boom")
        return FakeResponse(self.payload)


def test_fetch_jobs_queries_every_source(raw_jobs):
    session = FakeSession({"data": raw_jobs})

    jobs, failed = fetch_jobs(session=session, delay=0)

    assert len(session.urls) == len(SOURCES)
    assert failed == []
    assert len(jobs) == len(raw_jobs) * len(SOURCES)


def test_fetch_jobs_retries_with_exponential_backoff(raw_jobs):
    session = FakeSession({"data": raw_jobs}, failures=2)
    sleeps = []

    jobs, failed = fetch_jobs(session=session, delay=0, sleep=sleeps.append)

    assert sleeps[:2] == [1, 2]
    assert failed == []
    assert len(jobs) == len(raw_jobs) * len(SOURCES)
    assert set(session.timeouts) == {TIMEOUT_S} == {10}


def test_fetch_jobs_skips_failing_source_and_continues(raw_jobs, caplog):
    session = FakeSession({"data": raw_jobs}, broken_path=SOURCES[0][0])
    sleeps = []

    jobs, failed = fetch_jobs(session=session, delay=0, sleep=sleeps.append)

    assert sleeps[:3] == list(RETRY_BACKOFF_S) == [1, 2, 4]
    assert len(jobs) == len(raw_jobs) * (len(SOURCES) - 1)
    assert failed == [SOURCES[0][0]]
    assert "skipped after 4 attempts" in caplog.text


def test_fetch_jobs_fails_at_once_on_4xx():
    session = FakeSession(404)
    sleeps = []

    jobs, failed = fetch_jobs(session=session, delay=0, sleep=sleeps.append)

    assert jobs == [] and len(failed) == len(SOURCES)
    assert len(session.urls) == len(SOURCES)  # one attempt each
    assert set(sleeps) == {0}


@pytest.mark.parametrize("status", [429, 503])
def test_fetch_jobs_retries_rate_limit_and_5xx(status):
    session = FakeSession(status)

    fetch_jobs(session=session, delay=0, sleep=lambda _: None)

    assert len(session.urls) == len(SOURCES) * (len(RETRY_BACKOFF_S) + 1)


def test_clean_jobs_extracts_fields(raw_jobs):
    df = clean_jobs(raw_jobs)

    first = df.iloc[0]
    assert len(df) == 3
    assert first["titulo"] == "Systems Analyst (Fluent English)"
    assert first["empresa"] == "Institute for Portfolio Alternative"
    assert first["link"].startswith("https://www.getonbrd.com/jobs/")
    assert "<" not in first["descripcion"]
    assert "Meticulous and systematic" in first["descripcion"]
    assert first["fecha"].year == 2026


def test_clean_jobs_drops_duplicates_across_sources(raw_jobs):
    assert len(clean_jobs(raw_jobs + raw_jobs)) == 3


def test_build_leads_has_tracking_columns(raw_jobs):
    leads = build_leads(clean_jobs(raw_jobs), NOW)

    assert list(leads.columns) == LEAD_COLUMNS
    assert leads["score_jd"].between(0, 6).all()
    assert leads["requisitos"].str.len().gt(0).all()
    assert (leads["fecha_scrapeo"] == "2026-11-12").all()
    assert (leads["manual_review"] == "PENDING").all()


def test_build_leads_sorts_most_real_first(raw_jobs):
    leads = build_leads(clean_jobs(raw_jobs), NOW)

    assert leads["score_jd"].is_monotonic_decreasing


def test_build_leads_roundtrips_csv(raw_jobs, tmp_path):
    out = tmp_path / "leads.csv"

    build_leads(clean_jobs(raw_jobs), NOW).to_csv(out, index=False)

    assert list(pd.read_csv(out).columns) == LEAD_COLUMNS


def write_snapshot(folder, day, rows):
    # legacy layout: before manual_review, vertical, pais and priority existed
    pd.DataFrame(rows, columns=LEAD_COLUMNS[:6]).to_csv(
        folder / f"leads_{day}.csv", index=False
    )


def test_save_snapshot_writes_dated_file(raw_jobs, tmp_path):
    leads = build_leads(clean_jobs(raw_jobs), NOW)

    path = save_snapshot(leads, tmp_path, NOW)

    assert path == tmp_path / "leads_2026-11-12.csv"
    assert len(pd.read_csv(path)) == 3


def test_consolidate_dedupes_by_link_keeping_latest(tmp_path):
    write_snapshot(
        tmp_path, "2026-11-10", [["Old", "A", "https://x/1", 4, "SQL", "2026-11-10"]]
    )
    write_snapshot(
        tmp_path,
        "2026-11-12",
        [
            ["New", "A", "https://x/1", 5, "SQL", "2026-11-12"],
            ["Other", "B", "https://x/2", 6, "Python", "2026-11-12"],
        ],
    )

    merged = pd.read_csv(consolidate(tmp_path))

    assert sorted(merged["link"]) == ["https://x/1", "https://x/2"]
    assert merged.set_index("link").loc["https://x/1", "titulo"] == "New"
    assert (merged["manual_review"] == "PENDING").all()  # added to legacy snapshots
    assert (merged["vertical"] == "data_ai").all()  # classified from the title
    assert (merged["priority"] == "P2").all()  # country unknown


def test_consolidate_ignores_its_own_output(tmp_path):
    write_snapshot(
        tmp_path, "2026-11-12", [["T", "A", "https://x/1", 5, "SQL", "2026-11-12"]]
    )

    consolidate(tmp_path)
    merged = pd.read_csv(consolidate(tmp_path))

    assert len(merged) == 1


def test_keep_target_levels_uses_api_seniority_as_hard_filter(raw_jobs):
    kept = keep_target_levels(clean_jobs(raw_jobs))

    # the fixture only has Semi Senior (id 3, 3-5 years) and Lead (id 5) roles
    assert kept.empty


@pytest.mark.parametrize(("seniority_id", "kept"), [(1, True), (2, True), (3, False)])
def test_keep_target_levels_excludes_semi_senior(seniority_id, kept):
    jobs = pd.DataFrame(
        {
            "titulo": ["Data Engineer"],
            "descripcion": [""],
            "seniority_id": [seniority_id],
        }
    )

    assert len(keep_target_levels(jobs)) == int(kept)


@pytest.mark.parametrize(
    ("titulo", "descripcion", "kept"),
    [
        ("Data Engineer Jr", "", True),
        ("Practicante Data Science", "", True),
        # Semi Senior means 3-5 years in LATAM: not a target level keyword
        ("Data Engineer", "Buscamos perfil semi-senior", False),
        ("Data Engineer SSR", "", False),
        ("Data Engineer", "Ideal sin experiencia previa", True),
        ("Data Engineer", "5+ years required", False),
        # no seniority and no level keyword: the stack decides
        ("Data Engineer", "Stack: FastAPI y PostgreSQL", True),
        ("Backend Engineer", "We build LLM apps with RAG", True),
        ("Backend Engineer", "Java and Spring Boot", False),
        ("Majority analyst", "", False),
    ],
)
def test_keep_target_levels_falls_back_to_keywords(titulo, descripcion, kept):
    jobs = pd.DataFrame(
        {"titulo": [titulo], "descripcion": [descripcion], "seniority_id": [None]}
    )

    assert len(keep_target_levels(jobs)) == int(kept)


@pytest.mark.parametrize("payload", [{}, {"data": None}, [], None])
def test_fetch_jobs_warns_and_returns_empty_on_malformed_payload(payload, caplog):
    sleeps = []

    jobs, failed = fetch_jobs(
        session=FakeSession(payload), delay=0, sleep=sleeps.append
    )

    assert jobs == []
    assert "malformed JSON payload" in caplog.text
    assert failed == []  # a well-formed HTTP answer is not a failed source
    assert set(sleeps) == {0}  # a well-formed HTTP answer is not retried


def test_fetch_jobs_logs_a_search_without_results_as_info_not_warning(caplog):
    # "evaluator" or "trainer" often have 0 jobs: the API works, it's just empty
    caplog.set_level("INFO")

    jobs, failed = fetch_jobs(
        session=FakeSession({"data": [], "meta": {"total_pages": 1}}),
        delay=0,
        sleep=lambda _: None,
    )

    assert jobs == [] and failed == []
    assert "0 jobs" in caplog.text
    assert not [r for r in caplog.records if r.levelname == "WARNING"]


def test_fetch_jobs_returns_empty_when_body_is_not_json(caplog):
    jobs, failed = fetch_jobs(
        session=FakeSession(NOT_JSON), delay=0, sleep=lambda _: None
    )

    assert jobs == []
    assert "skipped after 4 attempts" in caplog.text
    assert len(failed) == len(SOURCES)


def test_clean_jobs_uses_unknown_for_missing_fields_and_warns(raw_jobs, caplog):
    broken = {
        "id": "x-1",
        "attributes": {"title": None, "company": {"data": None}},
        "links": {},
    }

    df = clean_jobs([*raw_jobs, broken, "not a job"])

    last = df.iloc[-1]
    assert len(df) == 4
    assert (last["titulo"], last["empresa"], last["link"]) == ("Unknown",) * 3
    assert last["descripcion"] == "" and pd.isna(last["fecha"])
    assert "1 jobs without title" in caplog.text
    assert list(build_leads(df, NOW).columns) == LEAD_COLUMNS  # still scoreable


def test_pipeline_survives_no_jobs():
    leads = build_leads(keep_target_levels(clean_jobs([])), NOW)

    assert leads.empty and list(leads.columns) == LEAD_COLUMNS


@pytest.mark.parametrize("raw", [None, float("nan"), 3])
def test_html_to_text_treats_non_strings_as_empty(raw):
    assert html_to_text(raw) == ""


def test_consolidate_skips_corrupt_snapshot(tmp_path, caplog):
    write_snapshot(
        tmp_path, "2026-11-11", [["T", "A", "https://x/1", 5, "SQL", "2026-11-11"]]
    )
    (tmp_path / "leads_2026-11-12.csv").write_bytes(b"\xff\xfe\x00garbage")

    merged = pd.read_csv(consolidate(tmp_path))

    assert list(merged["link"]) == ["https://x/1"]
    assert "skipping corrupt snapshot leads_2026-11-12.csv" in caplog.text


def test_consolidate_without_snapshots_writes_empty_file(tmp_path):
    assert list(pd.read_csv(consolidate(tmp_path)).columns) == LEAD_COLUMNS


def test_main_writes_nothing_when_every_source_fails(tmp_path, monkeypatch):
    monkeypatch.setattr(getonboard_scraper, "fetch_jobs", lambda: ([], []))
    monkeypatch.setattr(getonboard_scraper, "setup_logging", lambda verbose: None)
    monkeypatch.setattr(getonboard_scraper, "TRACKING_DIR", tmp_path)
    monkeypatch.setattr("sys.argv", ["getonboard_scraper"])

    with pytest.raises(SystemExit):
        getonboard_scraper.main()

    assert list(tmp_path.iterdir()) == []


def test_main_writes_snapshot_and_consolidated(raw_jobs, tmp_path, monkeypatch):
    monkeypatch.setattr(getonboard_scraper, "fetch_jobs", lambda: (raw_jobs, []))
    monkeypatch.setattr(getonboard_scraper, "setup_logging", lambda verbose: None)
    monkeypatch.setattr(getonboard_scraper, "TRACKING_DIR", tmp_path)
    monkeypatch.setattr("sys.argv", ["getonboard_scraper"])

    getonboard_scraper.main()

    assert len(list(tmp_path.glob("leads_*.csv"))) == 2


def make_jobs(prefix, count):
    return [
        {
            "id": f"{prefix}-{i}",
            "attributes": {
                "title": f"Data Engineer {prefix}{i}",
                "description": "<p>Python</p>",
                "seniority": {"data": {"id": 2}},
                "company": {"data": {"attributes": {"name": "Acme"}}},
            },
            "links": {"public_url": f"https://x/{prefix}-{i}"},
        }
        for i in range(count)
    ]


class SourceSession:
    """Answers each source path with its own jobs; a path left out is down."""

    def __init__(self, jobs_by_path):
        self.jobs_by_path = jobs_by_path

    def get(self, url, params=None, timeout=None):
        for path, jobs in self.jobs_by_path.items():
            if path in url:
                return FakeResponse({"data": jobs})
        raise requests.ConnectionError("boom")


@pytest.fixture
def run_main(tmp_path, monkeypatch):
    def run(jobs_by_path):
        session = SourceSession(jobs_by_path)
        monkeypatch.setattr(
            getonboard_scraper,
            "fetch_jobs",
            lambda: fetch_jobs(session=session, delay=0, sleep=lambda _: None),
        )
        monkeypatch.setattr(getonboard_scraper, "setup_logging", lambda verbose: None)
        monkeypatch.setattr(getonboard_scraper, "TRACKING_DIR", tmp_path)
        monkeypatch.setattr("sys.argv", ["getonboard_scraper"])
        getonboard_scraper.main()
        (snapshot,) = tmp_path.glob("leads_????-??-??.csv")
        return pd.read_csv(snapshot)

    return run


def test_main_keeps_both_healthy_sources_when_one_fails(run_main):
    source_a, _, source_c, *_ = (path for path, _ in SOURCES)

    snapshot = run_main({source_a: make_jobs("a", 10), source_c: make_jobs("c", 8)})

    assert len(snapshot) == 18


def test_main_partial_failure_merges_into_same_day_snapshot(run_main):
    source_a, source_b, source_c, *_ = (path for path, _ in SOURCES)
    healthy = {source_a: make_jobs("a", 10), source_c: make_jobs("c", 8)}
    run_main({**healthy, source_b: make_jobs("b", 5)})

    snapshot = run_main(healthy)  # source B is down on the second run

    assert len(snapshot) == 23  # the 5 leads only B had found earlier survive
    assert snapshot["link"].is_unique


def test_main_full_success_replaces_same_day_snapshot(run_main):
    source_a, source_b, source_c, *_ = (path for path, _ in SOURCES)
    healthy_rest = {path: [] for path, _ in SOURCES}  # answer, with 0 jobs
    run_main(
        {
            **healthy_rest,
            source_a: make_jobs("a", 10),
            source_b: make_jobs("b", 5),
            source_c: [{}],
        }
    )

    snapshot = run_main(
        {
            **healthy_rest,
            source_a: make_jobs("a", 10),
            source_b: make_jobs("b", 2),
            source_c: [{}],
        }
    )

    assert len(snapshot) == 12
