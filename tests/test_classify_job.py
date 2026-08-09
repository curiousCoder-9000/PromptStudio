"""Per-creator classify job control.

The vision call is mocked: these tests are about job control, the OLLAMA lease,
and the distribution guard — not about Ollama.

The distribution guard is the reason `tier_hist` and `top_tier_share` exist at
all. The previous classifier shipped with 85% of the archive on a single value,
which makes a filter a no-op, and nothing surfaced it because nothing measured
it. Per-run, in the journal, for free.
"""

import time
from unittest.mock import patch

import pytest

from promptstudio.jobs import LEASES, OLLAMA
from promptstudio.scraping import media_classifier as mc
from promptstudio.scraping.classify_job import ClassifyJobManager
from promptstudio.storage.db import ArchiveIndex


@pytest.fixture
def manager():
    """A fresh manager (bypassing the singleton) per test."""
    LEASES.reset()
    yield ClassifyJobManager()
    LEASES.reset()


def wait_until(predicate, timeout=5.0, interval=0.02):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return False


def _verdict(tier, ok=True, error=""):
    return mc.MediaVerdict(
        path="x",
        ok=ok,
        has_woman=tier > 0,
        exposure_tier=tier if ok else -1,
        confidence=0.8,
        brief_reason=f"tier {tier}",
        error=error,
        prompt_version=mc.CLASSIFY_FRAME_VERSION,
    )


def run_job(manager, tiers, make_photo, **kwargs):
    """Classify N photos, returning the given tiers in order. Blocks until done."""
    for i in range(len(tiers)):
        make_photo(name=f"p{i:02d}.jpg")
    calls = iter(tiers)

    def fake_classify(path, *, rel_path=""):
        return _verdict(next(calls))

    with patch.object(mc, "ollama_reachable", return_value=True), \
         patch("promptstudio.scraping.classify_job.ollama_reachable", return_value=True), \
         patch("promptstudio.scraping.classify_job.classify_media", side_effect=fake_classify):
        result = manager.start("test_creator", **kwargs)
        assert wait_until(lambda: not manager.is_running(), timeout=10), "job never finished"
    return result


# ── idle shape ───────────────────────────────────────────────────────

def test_idle_status_has_the_full_shape(manager):
    status = manager.get_status()
    for key in (
        "running", "creator", "total", "completed", "failed", "kept", "rejected",
        "current", "cancelled", "cancel_requested", "tier_hist", "top_tier_share",
        "error_rate", "model",
    ):
        assert key in status, key
    assert status["running"] is False
    assert status["tier_hist"] == {"-1": 0, "0": 0, "1": 0, "2": 0, "3": 0, "4": 0}


def test_histogram_snapshot_is_a_copy(manager):
    """Callers must not observe the histogram mutating under them mid-run."""
    a = manager.get_status()["tier_hist"]
    a["0"] = 999
    assert manager.get_status()["tier_hist"]["0"] == 0


# ── guards before any work starts ────────────────────────────────────

def test_empty_creator_now_means_the_whole_archive(manager):
    """Contract change (F2): "" used to be rejected, and is now archive-wide.

    With an empty archive there is nothing to visit, so the first real answer
    is `nothing_to_do` — the point is that it is no longer `bad_creator`.
    Full coverage in tests/test_classify_all_creators.py.
    """
    with patch("promptstudio.scraping.classify_job.ollama_reachable", return_value=True):
        assert manager.start("")["status"] == "nothing_to_do"


def test_a_non_creator_folder_is_still_rejected(manager):
    """Scope is either one real creator or everything — never `_trash`."""
    assert manager.start("_thumbs")["status"] == "bad_creator"


def test_unreachable_ollama_is_reported_before_scanning(manager):
    with patch("promptstudio.scraping.classify_job.ollama_reachable", return_value=False):
        assert manager.start("test_creator")["status"] == "ollama_down"


def test_nothing_to_do_when_everything_is_classified(manager, make_photo):
    rel, _full = make_photo(name="a.jpg")
    ArchiveIndex.get().set_verdict(rel, creator="test_creator", tier=2)
    with patch("promptstudio.scraping.classify_job.ollama_reachable", return_value=True):
        result = manager.start("test_creator")
    assert result["status"] == "nothing_to_do"
    assert result["pending"] == 0


def test_nothing_to_do_does_not_take_the_lease(manager, make_photo):
    """The lease is acquired only once there is real work — otherwise a no-op
    start would lock out a batch analyze."""
    with patch("promptstudio.scraping.classify_job.ollama_reachable", return_value=True):
        manager.start("test_creator")
    assert LEASES.holder(OLLAMA) is None


# ── lease contention ─────────────────────────────────────────────────

def test_a_held_ollama_lease_blocks_the_job(manager, make_photo):
    make_photo(name="a.jpg")
    LEASES.acquire([OLLAMA], "batch")
    with patch("promptstudio.scraping.classify_job.ollama_reachable", return_value=True):
        result = manager.start("test_creator")
    assert result["status"] == "busy"
    assert "batch" in result["message"]
    assert manager.is_running() is False


def test_the_lease_is_released_when_the_job_finishes(manager, make_photo):
    run_job(manager, [2], make_photo)
    assert LEASES.holder(OLLAMA) is None


def test_the_lease_is_released_even_when_every_item_fails(manager, make_photo):
    make_photo(name="a.jpg")
    with patch("promptstudio.scraping.classify_job.ollama_reachable", return_value=True), \
         patch("promptstudio.scraping.classify_job.classify_media",
               side_effect=RuntimeError("boom")):
        manager.start("test_creator")
        assert wait_until(lambda: not manager.is_running(), timeout=10)
    assert LEASES.holder(OLLAMA) is None
    assert manager.get_status()["failed"] == 1


def test_the_job_takes_the_lease_while_running(manager, make_photo):
    make_photo(name="a.jpg")
    seen = {}

    def slow(path, *, rel_path=""):
        seen["holder"] = LEASES.holder(OLLAMA)
        return _verdict(2)

    with patch("promptstudio.scraping.classify_job.ollama_reachable", return_value=True), \
         patch("promptstudio.scraping.classify_job.classify_media", side_effect=slow):
        manager.start("test_creator")
        assert wait_until(lambda: not manager.is_running(), timeout=10)
    assert seen["holder"] == "classify"


# ── counters ─────────────────────────────────────────────────────────

def test_keep_and_reject_counters_follow_the_threshold(manager, make_photo):
    run_job(manager, [0, 1, 2, 3, 4], make_photo)
    status = manager.get_status()
    assert status["completed"] == 5
    # Default cut is 1, so tiers 0 and 1 are rejects.
    assert status["rejected"] == 2
    assert status["kept"] == 3
    assert status["failed"] == 0


def test_the_histogram_records_every_tier(manager, make_photo):
    run_job(manager, [0, 0, 3, 4], make_photo)
    hist = manager.get_status()["tier_hist"]
    assert hist["0"] == 2 and hist["3"] == 1 and hist["4"] == 1
    assert hist["-1"] == 0


def test_top_tier_share_exposes_a_saturated_classifier(manager, make_photo):
    """The metric that would have caught the previous classifier on day one."""
    run_job(manager, [3, 3, 3, 3, 1], make_photo)
    status = manager.get_status()
    assert status["top_tier_share"] == pytest.approx(0.8)
    assert status["error_rate"] == 0.0


def test_failures_land_in_the_error_bucket_not_a_tier(manager, make_photo):
    for i in range(3):
        make_photo(name=f"p{i}.jpg")
    replies = iter([_verdict(3), _verdict(0, ok=False, error="timeout"), _verdict(4)])
    with patch("promptstudio.scraping.classify_job.ollama_reachable", return_value=True), \
         patch("promptstudio.scraping.classify_job.classify_media",
               side_effect=lambda path, rel_path="": next(replies)):
        manager.start("test_creator")
        assert wait_until(lambda: not manager.is_running(), timeout=10)
    status = manager.get_status()
    assert status["failed"] == 1
    assert status["tier_hist"]["-1"] == 1
    # Rates are rounded to 4dp at the source so the status payload stays small.
    assert status["error_rate"] == pytest.approx(1 / 3, abs=1e-4)
    # A failed item is neither kept nor rejected.
    assert status["kept"] + status["rejected"] == 2


def test_verdicts_reach_the_index(manager, make_photo):
    run_job(manager, [0, 4], make_photo)
    index = ArchiveIndex.get()
    counts = index.creator_verdict_counts()["test_creator"]
    assert counts["reject_count"] == 1
    assert counts["keep_count"] == 1


# ── cancel ───────────────────────────────────────────────────────────

def test_cancel_on_an_idle_manager_is_a_no_op(manager):
    assert manager.cancel() is False


def test_cancel_stops_after_the_current_item(manager, make_photo):
    for i in range(20):
        make_photo(name=f"p{i:02d}.jpg")
    started = []

    def slow(path, *, rel_path=""):
        started.append(path)
        if len(started) == 2:
            manager.cancel()
        time.sleep(0.01)
        return _verdict(2)

    with patch("promptstudio.scraping.classify_job.ollama_reachable", return_value=True), \
         patch("promptstudio.scraping.classify_job.classify_media", side_effect=slow):
        result = manager.start("test_creator")
        assert result["status"] == "started"
        assert wait_until(lambda: not manager.is_running(), timeout=10)

    status = manager.get_status()
    assert status["cancelled"] is True
    assert status["completed"] < 20
    # cancel_requested is a transient signal for the UI; it clears on exit so a
    # later run does not start out looking like it is already stopping.
    assert status["cancel_requested"] is False
    assert LEASES.holder(OLLAMA) is None


# ── pending selection ────────────────────────────────────────────────

def test_only_unclassified_media_is_queued(manager, make_photo):
    done, _full = make_photo(name="done.jpg")
    ArchiveIndex.get().set_verdict(done, creator="test_creator", tier=2)
    make_photo(name="todo.jpg")
    pending = manager.list_pending("test_creator")
    assert [p["filename"] for p in pending] == ["todo.jpg"]


def test_rescore_stale_picks_up_superseded_versions(manager, make_photo):
    rel, _full = make_photo(name="old.jpg")
    ArchiveIndex.get().set_verdict(
        rel, creator="test_creator", tier=2, prompt_version="v1-ancient"
    )
    assert manager.list_pending("test_creator") == []
    stale = manager.list_pending("test_creator", rescore_stale=True)
    assert [p["filename"] for p in stale] == ["old.jpg"]


def test_force_requeues_everything(manager, make_photo):
    rel, _full = make_photo(name="a.jpg")
    ArchiveIndex.get().set_verdict(rel, creator="test_creator", tier=2)
    assert len(manager.list_pending("test_creator", force=True)) == 1


def test_only_unclassified_false_behaves_like_force(manager, make_photo):
    rel, _full = make_photo(name="a.jpg")
    ArchiveIndex.get().set_verdict(rel, creator="test_creator", tier=2)
    result = run_job(manager, [3], make_photo, only_unclassified=False)
    assert result["status"] == "started"
    assert ArchiveIndex.get().get_verdict(rel)["tier"] == 3


# ── journal ──────────────────────────────────────────────────────────

def test_the_run_is_journalled_with_its_distribution(manager, make_photo):
    from promptstudio.storage.journal import read_runs

    run_job(manager, [0, 3, 3], make_photo)
    runs = read_runs("classify", limit=5)
    assert runs, "no classify run journalled"
    # summary() fields are merged flat into the run_end record.
    latest = runs[0]
    assert latest.get("creator") == "test_creator"
    assert latest.get("outcome") == "ok"
    assert latest.get("item_count") == 3
    assert latest.get("kept") == 2
    assert latest.get("rejected") == 1
    assert latest.get("top_tier_share") == pytest.approx(2 / 3, abs=1e-4)
    assert latest.get("tier_hist", {}).get("3") == 2
