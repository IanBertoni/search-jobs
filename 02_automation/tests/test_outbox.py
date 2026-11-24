import json
from unittest.mock import MagicMock

import outbox
from outbox import job_message, load_state, mark_sent, render, save_state, update

EMPTY = {"drafts": {}, "skipped": [], "sent": [], "order": []}


def job(slug: str, **extra) -> dict:
    return {
        "empresa": slug,
        "titulo": "Data Engineer",
        "ubicacion": "Remote - LATAM",
        "remoto": True,
        "link": f"https://jobs.ashbyhq.com/{slug}/1",
        "texto": "Python, SQL and Airflow pipelines.",
        "publicado": "2026-11-20",
        **extra,
    }


def fake_screen(verdicts: dict):
    """ats_radar.screen stand-in: drops "skip", merges the verdict like the real one."""

    def screen(jobs, _session):
        return [
            {**j, "verdict": verdicts[j["empresa"]], "min_years": 1, "reason": "ok"}
            for j in jobs
            if verdicts[j["empresa"]] != "skip"
        ]

    return screen


def test_job_message_carries_the_raw_posting():
    message = job_message(job("posthog"))

    for part in ("posthog", "Data Engineer", "Remote - LATAM", "Airflow"):
        assert part in message


def test_load_state_returns_an_empty_state_when_missing_or_corrupt(tmp_path, caplog):
    corrupt = tmp_path / "bad.json"
    corrupt.write_text("{not json", encoding="utf-8")

    assert load_state(tmp_path / "nope.json") == EMPTY
    assert load_state(corrupt) == EMPTY
    assert "corrupt" in caplog.text


def test_state_round_trips_through_disk(tmp_path):
    path = tmp_path / "state.json"
    state = {**EMPTY, "sent": ["https://x/1"]}

    save_state(path, state)

    assert load_state(path) == state


def test_update_screens_new_jobs_and_drafts_a_pitch_for_the_ones_that_fit(
    monkeypatch,
):
    monkeypatch.setattr(outbox, "screen", fake_screen({"a": "apply", "b": "skip"}))
    calls = []
    monkeypatch.setattr(
        outbox, "draft", lambda system, user, _s: calls.append(system) or "PITCH A"
    )

    state = update([job("a"), job("b")], EMPTY, MagicMock(), "SYS")

    assert list(state["drafts"]) == [job("a")["link"]]
    assert state["drafts"][job("a")["link"]]["pitch"] == "PITCH A"
    assert state["drafts"][job("a")["link"]]["job"]["verdict"] == "apply"
    assert state["skipped"] == [job("b")["link"]]
    assert state["order"] == [job("a")["link"]]
    assert calls == ["SYS"]
    assert EMPTY == {"drafts": {}, "skipped": [], "sent": [], "order": []}


def test_update_never_rescreens_drafted_skipped_or_sent_jobs(monkeypatch):
    screened = []

    def screen(jobs, _session):
        screened.extend(j["empresa"] for j in jobs)
        return [{**j, "verdict": "apply", "reason": "ok"} for j in jobs]

    monkeypatch.setattr(outbox, "screen", screen)
    monkeypatch.setattr(outbox, "draft", lambda *_a: "P")
    state = {
        **EMPTY,
        "drafts": {job("a")["link"]: {"job": job("a"), "pitch": "old"}},
        "skipped": [job("b")["link"]],
        "sent": [job("c")["link"]],
    }

    new = update([job(s) for s in "abcd"], state, MagicMock(), "S", limit=5)

    assert screened == ["d"]
    assert new["drafts"][job("a")["link"]]["pitch"] == "old"


def test_update_respects_the_limit(monkeypatch):
    monkeypatch.setattr(outbox, "screen", fake_screen(dict.fromkeys("abc", "apply")))
    monkeypatch.setattr(outbox, "draft", lambda *_a: "P")

    state = update([job(s) for s in "abc"], EMPTY, MagicMock(), "S", limit=2)

    assert len(state["drafts"]) == 2


def test_update_retries_a_failed_pitch_next_run(monkeypatch):
    monkeypatch.setattr(outbox, "screen", fake_screen({"a": "apply"}))
    monkeypatch.setattr(outbox, "draft", lambda *_a: None)

    state = update([job("a")], EMPTY, MagicMock(), "S")

    assert state["drafts"] == {}
    assert state["skipped"] == []


def test_mark_sent_adds_links_without_duplicates_or_mutation():
    state = {**EMPTY, "sent": ["x"]}

    assert mark_sent(state, ["x", "y"])["sent"] == ["x", "y"]
    assert state["sent"] == ["x"]


def test_render_lists_unsent_drafts_apply_first_with_a_checkbox():
    def entry(slug, verdict):
        return {"job": {**job(slug), "verdict": verdict, "reason": "r"}, "pitch": slug}

    links = [job(s)["link"] for s in "abc"]
    state = {
        **EMPTY,
        "drafts": {
            links[0]: entry("a", "stretch"),
            links[1]: entry("b", "apply"),
            links[2]: entry("c", "apply"),
        },
        "sent": [links[2]],
        "order": links,
    }

    text = render(state)

    assert text.index("**b — Data Engineer**") < text.index("**a — Data Engineer**")
    assert "- [ ]" in text
    assert "**c — Data Engineer**" not in text
    assert f"python outbox.py --sent {links[1]}" in text


def test_render_says_so_when_the_outbox_is_empty():
    assert "vacío" in render(EMPTY)


def test_build_runs_the_ats_radar_saves_state_and_writes_the_outbox(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(outbox, "STATE", tmp_path / "state.json")
    monkeypatch.setattr(outbox, "OUTPUT", tmp_path / "OUTBOX.md")
    monkeypatch.setattr(outbox, "fetch_all", lambda _s: [job("a"), job("x")])
    monkeypatch.setattr(outbox, "candidates", lambda jobs: jobs[:1])
    monkeypatch.setattr(outbox, "load_system_prompt", lambda: "SYS")
    monkeypatch.setattr(outbox, "screen", fake_screen({"a": "apply"}))
    monkeypatch.setattr(outbox, "draft", lambda *_a: "THE PITCH")
    monkeypatch.setattr(outbox, "load_applied", dict)

    state = outbox.build(MagicMock())

    assert list(state["drafts"]) == [job("a")["link"]]
    saved = json.loads((tmp_path / "state.json").read_text("utf-8"))
    assert saved["order"] == [job("a")["link"]]
    assert "THE PITCH" in (tmp_path / "OUTBOX.md").read_text("utf-8")


def test_build_skips_companies_in_applied_txt_before_groq(tmp_path, monkeypatch):
    monkeypatch.setattr(outbox, "STATE", tmp_path / "state.json")
    monkeypatch.setattr(outbox, "OUTPUT", tmp_path / "OUTBOX.md")
    monkeypatch.setattr(outbox, "fetch_all", lambda _s: [job("a"), job("posthog")])
    monkeypatch.setattr(outbox, "candidates", lambda jobs: jobs)
    monkeypatch.setattr(outbox, "load_system_prompt", lambda: "SYS")
    monkeypatch.setattr(outbox, "load_applied", lambda: {"posthog": "aplicado"})
    screened = []

    def screen(jobs, _session):
        screened.extend(j["empresa"] for j in jobs)
        return [{**j, "verdict": "apply", "reason": "ok"} for j in jobs]

    monkeypatch.setattr(outbox, "screen", screen)
    monkeypatch.setattr(outbox, "draft", lambda *_a: "P")

    outbox.build(MagicMock())

    assert screened == ["a"]
