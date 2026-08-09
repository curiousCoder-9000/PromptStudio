"""Run journal — append-only history of background jobs.

Job status is a live dict overwritten by the next run, so "why did last night's
sync stop at account 12" is unanswerable without this. The properties that
matter: a run is always closed out, a crash costs one line rather than the file,
and journalling never breaks the job it is observing.
"""

import json
import os

import pytest

from promptstudio.storage.journal import RunJournal, list_kinds


@pytest.fixture
def journal(tmp_path):
    return RunJournal("testkind", directory=str(tmp_path))


def _events(journal):
    return [r["event"] for r in journal.read_records()]


def test_run_brackets_start_and_end(journal):
    with journal.run(creator="someone", total=3):
        pass
    assert _events(journal) == ["run_start", "run_end"]


def test_start_record_carries_run_metadata(journal):
    with journal.run(creator="someone", total=3):
        pass
    start = journal.read_records()[0]
    assert start["creator"] == "someone"
    assert start["total"] == 3
    assert start["kind"] == "testkind"
    assert start["ts"]


def test_items_and_events_share_the_run_id(journal):
    with journal.run() as run:
        run.item(path="a.jpg", ok=True)
        run.event("rate_limit", backoff_sec=60)
    ids = {r["run_id"] for r in journal.read_records()}
    assert len(ids) == 1


def test_run_end_counts_items_and_failures(journal):
    with journal.run() as run:
        run.item(path="a", ok=True)
        run.item(path="b", ok=False)
        run.item(path="c", ok=True)
    end = journal.read_records()[-1]
    assert end["items"] == 3
    assert end["failures"] == 1
    assert end["outcome"] == "ok"
    assert end["duration_sec"] >= 0


def test_summary_is_merged_into_run_end(journal):
    with journal.run() as run:
        run.summary(top_score_share=0.85)
        run.summary(unscored_rate=0.02)
    end = journal.read_records()[-1]
    assert end["top_score_share"] == 0.85
    assert end["unscored_rate"] == 0.02


def test_exception_is_recorded_and_re_raised(journal):
    """The journal observes; it must not swallow the failure."""
    with pytest.raises(ValueError, match="boom"):
        with journal.run() as run:
            run.item(path="a", ok=True)
            raise ValueError("boom")

    end = journal.read_records()[-1]
    assert end["event"] == "run_end"
    assert end["outcome"] == "error"
    assert "ValueError: boom" in end["error"]
    assert end["items"] == 1


def test_keyboard_interrupt_is_recorded_as_cancelled(journal):
    with pytest.raises(KeyboardInterrupt):
        with journal.run():
            raise KeyboardInterrupt
    assert journal.read_records()[-1]["outcome"] == "cancelled"


def test_exotic_values_are_stringified_rather_than_dropped(journal):
    """`default=str` keeps the record; losing history to a stray type is worse."""
    with journal.run() as run:
        run.item(path="a", ok=True, weird=object())
    item = [r for r in journal.read_records() if r["event"] == "item"][0]
    assert item["path"] == "a"
    assert isinstance(item["weird"], str)


def test_circular_reference_is_dropped_without_breaking_the_job(journal):
    """Diagnostics must never take down the work they are observing."""
    loop: dict = {}
    loop["self"] = loop

    with journal.run() as run:
        run.item(path="a", ok=True, loop=loop)  # unserializable even with default=str
        run.item(path="b", ok=True)

    paths = [r.get("path") for r in journal.read_records() if r["event"] == "item"]
    assert paths == ["b"]


def test_unwritable_directory_does_not_raise(tmp_path):
    blocked = tmp_path / "afile"
    blocked.write_text("not a directory")
    journal = RunJournal("k", directory=str(blocked / "sub"))
    with journal.run() as run:
        run.item(path="a", ok=True)
    assert journal.read_records() == []


def test_truncated_line_costs_one_record_not_the_file(journal):
    with journal.run() as run:
        run.item(path="a", ok=True)
    with open(journal.path, "a", encoding="utf-8") as f:
        f.write('{"ts": "2026-01-01", "event": "it')  # crash mid-write

    records = journal.read_records()
    assert [r["event"] for r in records] == ["run_start", "item", "run_end"]


def test_each_line_is_independently_valid_json(journal):
    with journal.run() as run:
        run.item(path="a", ok=True)
    for line in open(journal.path, encoding="utf-8"):
        assert isinstance(json.loads(line), dict)


def test_runs_are_grouped_newest_first(journal):
    for i in range(3):
        with journal.run(index=i) as run:
            run.item(path=f"{i}.jpg", ok=True)
    runs = journal.read_runs()
    assert [r["index"] for r in runs] == [2, 1, 0]


def test_read_runs_counts_items_rather_than_returning_them(journal):
    """A 4000-photo run must not become 4000 objects in an API response."""
    with journal.run() as run:
        for i in range(50):
            run.item(path=f"{i}.jpg", ok=True)
    run_summary = journal.read_runs()[0]
    assert run_summary["item_count"] == 50
    assert "items" in run_summary  # from run_end
    assert not any(isinstance(v, list) and len(v) > 10 for v in run_summary.values())


def test_events_are_kept_on_the_run_summary(journal):
    with journal.run() as run:
        run.event("rate_limit", consecutive=2, backoff_sec=60)
    events = journal.read_runs()[0]["events"]
    assert len(events) == 1
    assert events[0]["name"] == "rate_limit"
    assert events[0]["backoff_sec"] == 60


def test_in_flight_run_is_visible_before_it_finishes(journal):
    with journal.run(creator="x") as run:
        run.item(path="a", ok=True)
        snapshot = journal.read_runs()[0]
        assert snapshot["started_at"]
        assert snapshot["finished_at"] is None


def test_rotation_preserves_recent_history(journal, monkeypatch):
    monkeypatch.setattr("promptstudio.storage.journal.JOURNAL_MAX_BYTES", 400)
    for i in range(40):
        with journal.run(index=i):
            pass
    assert os.path.isfile(journal.path + ".1")
    assert os.path.getsize(journal.path) < 4000


def test_disabled_journal_writes_nothing(journal, monkeypatch):
    monkeypatch.setattr("promptstudio.storage.journal.JOURNAL_ENABLED", False)
    with journal.run() as run:
        run.item(path="a", ok=True)
    assert not os.path.exists(journal.path)


def test_for_kind_returns_one_instance_per_kind():
    assert RunJournal.for_kind("alpha") is RunJournal.for_kind("alpha")
    assert RunJournal.for_kind("alpha") is not RunJournal.for_kind("beta")


def test_list_kinds_reads_the_journal_directory(tmp_path, monkeypatch):
    monkeypatch.setattr("promptstudio.storage.journal.JOURNAL_DIR", str(tmp_path))
    (tmp_path / "classify.jsonl").write_text("")
    (tmp_path / "sync.jsonl").write_text("")
    (tmp_path / "notes.txt").write_text("")
    assert list_kinds() == ["classify", "sync"]


def test_list_kinds_on_missing_directory(tmp_path, monkeypatch):
    monkeypatch.setattr("promptstudio.storage.journal.JOURNAL_DIR", str(tmp_path / "nope"))
    assert list_kinds() == []
