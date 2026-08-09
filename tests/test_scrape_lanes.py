"""Per-source scrape lanes — see docs/design_scrape_lanes.md.

Every test here is a behaviour that was wrong while there was one global
scrape worker. The headline one is `test_cancelling_one_lane_leaves_others`:
`SourceContext` used to get `should_cancel=self.is_cancel_requested`, a single
process-wide Event, so cancelling X killed a running Reddit job too.
"""

import json
import threading
import time

import pytest

from promptstudio.jobs import LEASES, scrape_resource
from promptstudio.scraping.creator_queue import CreatorScrapeQueue
from promptstudio.scraping.results import SyncResult
from promptstudio.scraping.sync_manager import SyncManager

SOURCES = ("instagram", "x", "reddit")


@pytest.fixture(autouse=True)
def clean_leases():
    LEASES.reset()
    yield
    LEASES.reset()


@pytest.fixture
def queue(tmp_path):
    return CreatorScrapeQueue(path=str(tmp_path / "queue.json"))


@pytest.fixture
def no_pacing(monkeypatch):
    """Zero every lane's anti-ban waits.

    Instagram's real cooldown is 30-120s between jobs, so without this any test
    that lets a lane drain a second job would block for a minute. Pacing itself
    is covered separately, at the config level.
    """
    for name in SOURCES:
        monkeypatch.setenv(f"SCRAPE_ACCOUNT_PAUSE_MIN_{name.upper()}", "0")
        monkeypatch.setenv(f"SCRAPE_ACCOUNT_PAUSE_MAX_{name.upper()}", "0")
        monkeypatch.setenv(f"SCRAPE_BATCH_EVERY_{name.upper()}", "0")


@pytest.fixture
def manager(monkeypatch, queue, tmp_path, no_pacing):
    """A SyncManager whose lanes run against fake, controllable sources."""
    monkeypatch.setattr(
        "promptstudio.config.SYNC_STATUS_FILE", str(tmp_path / "sync_status.json")
    )
    monkeypatch.setattr(
        "promptstudio.scraping.sync_manager.SYNC_STATUS_FILE",
        str(tmp_path / "sync_status.json"),
    )
    monkeypatch.setattr(CreatorScrapeQueue, "get", classmethod(lambda cls: queue))
    mgr = SyncManager()
    monkeypatch.setattr(SyncManager, "get", classmethod(lambda cls: mgr))
    return mgr


class _Gate:
    """A source that blocks in run() until released, so lanes can overlap."""

    def __init__(self, name):
        self.name = name
        self.label = name
        self.entered = threading.Event()
        self.release = threading.Event()
        self.saw_cancel = None
        self.ran = False

    def parse_target(self, target, **kw):
        from promptstudio.scraping.sources.base import SourceTarget

        return SourceTarget(
            source=self.name,
            raw=target,
            url=f"https://example.test/{target}",
            folder=f"{target}__{self.name}" if self.name != "instagram" else target,
            handle=target,
        )

    def run(self, target, options, ctx):
        self.ran = True
        self.entered.set()
        # Poll the lane's cancel flag while blocked — this is the assertion
        # surface for "did MY lane get cancelled".
        while not self.release.wait(0.02):
            if ctx.cancelled():
                self.saw_cancel = True
                return SyncResult(
                    job_type="creator",
                    source=self.name,
                    aborted=True,
                    abort_reason="Cancelled by user",
                    stop_reason="cancel",
                )
        self.saw_cancel = bool(ctx.cancelled())
        return SyncResult(
            job_type="creator", source=self.name, downloaded=1,
            stop_reason="end_of_feed",
        )


@pytest.fixture
def gates(monkeypatch):
    import promptstudio.scraping.sources as sources_pkg

    made = {name: _Gate(name) for name in SOURCES}

    def fake_get_source(name):
        key = sources_pkg.normalize_source(name)
        if key not in made:
            raise ValueError(f"Unknown source '{name}'")
        return made[key]

    monkeypatch.setattr(sources_pkg, "get_source", fake_get_source)
    return made


def _wait(predicate, timeout=5.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if predicate():
            return True
        time.sleep(0.02)
    return False


# ── concurrency ─────────────────────────────────────────────────────────

def test_three_lanes_run_concurrently(manager, queue, gates):
    """The headline feature: one job per platform, all at once."""
    for name in SOURCES:
        queue.enqueue("someone", source=name)
    manager.try_drain_creator_queue()

    for name in SOURCES:
        assert _wait(gates[name].entered.is_set), f"{name} lane never started"

    assert all(manager.is_running(n) for n in SOURCES)
    assert sorted(manager.get_status()["running_lanes"]) == sorted(SOURCES)

    for gate in gates.values():
        gate.release.set()
    assert _wait(lambda: not manager.is_running())


def test_instagram_stays_pinned_to_one_job(manager, queue, gates):
    queue.enqueue("first", source="instagram")
    queue.enqueue("second", source="instagram")
    manager.try_drain_creator_queue()
    assert _wait(gates["instagram"].entered.is_set)

    # A second drain of the same lane must be refused while one is running.
    assert manager.try_drain_creator_queue("instagram") is False
    assert queue.pending_count("instagram") == 1

    gates["instagram"].release.set()
    assert _wait(lambda: not manager.is_running("instagram"))


def test_lease_is_per_source_not_global(manager, queue, gates):
    queue.enqueue("someone", source="x")
    manager.try_drain_creator_queue("x")
    assert _wait(gates["x"].entered.is_set)

    assert LEASES.holder(scrape_resource("x")) == "sync:x"
    assert LEASES.holder(scrape_resource("instagram")) is None, (
        "an X job must not hold the Instagram session"
    )
    gates["x"].release.set()


# ── cancellation isolation ──────────────────────────────────────────────

def test_cancelling_one_lane_leaves_others_running(manager, queue, gates):
    """The bug lanes exist to fix: one shared Event cancelled every source."""
    queue.enqueue("someone", source="x")
    queue.enqueue("someone", source="reddit")
    manager.try_drain_creator_queue()
    assert _wait(gates["x"].entered.is_set)
    assert _wait(gates["reddit"].entered.is_set)

    manager.request_cancel("x")
    assert _wait(lambda: not manager.is_running("x")), "X did not stop"

    assert gates["x"].saw_cancel is True
    assert manager.is_running("reddit"), "cancelling X must not stop Reddit"
    assert gates["reddit"].saw_cancel is None

    gates["reddit"].release.set()
    assert _wait(lambda: not manager.is_running("reddit"))
    assert gates["reddit"].saw_cancel is False


def test_cancel_with_no_source_stops_every_lane(manager, queue, gates):
    for name in ("x", "reddit"):
        queue.enqueue("someone", source=name)
    manager.try_drain_creator_queue()
    assert _wait(gates["x"].entered.is_set)
    assert _wait(gates["reddit"].entered.is_set)

    manager.request_cancel()
    assert _wait(lambda: not manager.is_running())
    assert gates["x"].saw_cancel is True
    assert gates["reddit"].saw_cancel is True


# ── failure isolation ───────────────────────────────────────────────────

def test_pause_is_lane_scoped(queue):
    queue.pause("cookies expired", source="x")
    assert queue.is_paused("x") is True
    assert queue.is_paused("instagram") is False
    assert queue.is_paused("reddit") is False


def test_global_pause_still_stops_everything(queue):
    for name in SOURCES:
        queue.enqueue("someone", source=name)
    queue.pause("user asked")
    assert all(queue.is_paused(n) for n in SOURCES)
    assert queue.is_paused() is True


def test_paused_lane_does_not_block_other_lanes(manager, queue, gates):
    queue.enqueue("someone", source="x")
    queue.enqueue("someone", source="reddit")
    queue.pause("cookies expired", source="x")

    manager.try_drain_creator_queue()
    assert _wait(gates["reddit"].entered.is_set)
    assert gates["x"].ran is False, "a paused lane must not start"
    assert queue.pending_count("x") == 1

    gates["reddit"].release.set()
    assert _wait(lambda: not manager.is_running("reddit"))


def test_global_pause_covers_a_lane_that_does_not_exist_yet(queue, gates):
    """Pause, then enqueue a source with no lane record — it must not start.

    Lanes are created lazily, so a global pause with an empty queue used to
    materialise only the Instagram lane. Enqueueing X afterwards found a fresh,
    unpaused lane and ran immediately, right after the user pressed Pause.
    """
    queue.pause("user pressed pause")
    assert queue.is_paused("x") is True, "a new lane must inherit the global pause"

    queue.enqueue("kaya", source="x")
    assert queue.is_paused("x") is True
    assert queue.is_paused() is True


def test_a_new_lane_after_a_targeted_pause_is_not_paused(queue):
    """The inverse: pausing only Instagram must not pause anything else."""
    queue.pause("rate limited", source="instagram")
    assert queue.is_paused("x") is False


def test_refused_start_does_not_release_the_running_lease(manager, queue, gates):
    """A same-lane refusal must not free the lease the live job is holding.

    Owners are per lane, and `LeaseRegistry.acquire` treats re-acquiring your
    own lease as success — so the refusal path fell through to `release(owner)`
    and dropped a lease belonging to a job that was still scraping.
    """
    queue.enqueue("someone", source="instagram")
    manager.try_drain_creator_queue("instagram")
    assert _wait(gates["instagram"].entered.is_set)
    assert LEASES.holder(scrape_resource("instagram")) == "sync:instagram"

    assert manager.start_job("saved", lambda log, orl=None: {}, source="instagram") is False
    assert manager.is_running("instagram") is True
    assert LEASES.holder(scrape_resource("instagram")) == "sync:instagram", (
        "the running job must still hold its lease"
    )

    gates["instagram"].release.set()
    assert _wait(lambda: not manager.is_running("instagram"))
    assert LEASES.holder(scrape_resource("instagram")) is None


def test_lease_is_freed_exactly_once_after_a_refusal(manager, queue, gates):
    """After the refusal, the lane must still be acquirable by the next job."""
    queue.enqueue("first", source="x")
    manager.try_drain_creator_queue("x")
    assert _wait(gates["x"].entered.is_set)
    manager.start_job("saved", lambda log, orl=None: {}, source="x")

    gates["x"].release.set()
    assert _wait(lambda: not manager.is_running("x"))
    assert LEASES.acquire([scrape_resource("x")], "someone_new") is None


def test_one_paused_lane_does_not_report_the_queue_as_paused(queue):
    """Flat `paused` means "nothing can run", so one paused lane must not set it.

    The union used to be computed over only the lanes that happened to exist in
    the file. Pausing X created exactly one lane, so `all(paused)` was trivially
    true — and a legacy client (and the modal Pause button) saw the whole queue
    as paused while Instagram and Reddit were idle and perfectly runnable.
    """
    queue.pause("cookies expired", source="x")

    assert queue.is_paused("x") is True
    assert queue.is_paused() is False, "only one of three lanes is paused"

    snap = queue.status_snapshot()
    assert snap["paused"] is False
    assert set(snap["lanes"]) >= {"instagram", "x", "reddit"}, (
        "status must describe every lane, not just the ones with state"
    )
    assert snap["lanes"]["instagram"]["paused"] is False
    assert snap["lanes"]["x"]["paused"] is True


def test_flat_paused_is_true_only_when_every_lane_is(queue):
    for name in SOURCES:
        queue.pause("stop", source=name)
    assert queue.is_paused() is True
    assert queue.status_snapshot()["paused"] is True


def test_status_snapshot_does_not_persist_lanes_as_a_side_effect(queue):
    """Reading status must not mutate the file it is reporting on."""
    import json

    queue.enqueue("nina", source="instagram")  # forces a first write
    queue.status_snapshot()
    with open(queue.path, "r", encoding="utf-8") as fh:
        on_disk = json.load(fh)
    assert set(on_disk.get("lanes") or {}) <= {"instagram"}, (
        f"a read created lane records: {sorted(on_disk.get('lanes') or {})}"
    )


def test_resume_is_lane_scoped(queue):
    queue.pause("stop everything")
    queue.resume("x")
    assert queue.is_paused("x") is False
    assert queue.is_paused("instagram") is True


# ── pacing (the Reddit-triggers-Instagram-pause bug) ────────────────────

def test_finished_counters_are_per_lane(queue):
    for name in SOURCES:
        job = queue.enqueue("someone", source=name)["job"]
        if name == "reddit":
            queue.mark_running(job["id"])
            queue.finalize_job(job["id"], status="done", stop_reason="end_of_feed")

    assert queue.lane_finished_count("reddit") == 1
    assert queue.lane_finished_count("instagram") == 0


def test_reddit_finishing_does_not_cool_down_instagram(queue):
    """A Reddit job used to increment the counter driving IG's batch pause."""
    job = queue.enqueue("someone", source="reddit")["job"]
    queue.mark_running(job["id"])
    queue.finalize_job(job["id"], status="done", stop_reason="end_of_feed")

    assert queue.should_account_pause_before("any", source="instagram") is False
    assert queue.should_batch_pause(source="instagram") is False
    # ...but the lane that actually did work does pace itself.
    assert queue.should_account_pause_before("any", source="reddit") is True


def test_gallery_dl_lanes_get_gentler_pacing_than_instagram():
    from promptstudio.config import (
        account_pause_range_for,
        batch_pause_every_for,
    )

    ig_lo, ig_hi = account_pause_range_for("instagram")
    x_lo, x_hi = account_pause_range_for("x")
    assert (ig_lo, ig_hi) == (30.0, 120.0), "Instagram pacing must not be relaxed"
    assert x_hi < ig_lo, "gallery-dl lanes should not inherit IG anti-ban waits"
    assert batch_pause_every_for("instagram") == 10
    assert batch_pause_every_for("reddit") == 0


def test_lane_pacing_is_overridable_per_source(monkeypatch):
    from promptstudio.config import account_pause_range_for

    monkeypatch.setenv("SCRAPE_ACCOUNT_PAUSE_MIN_X", "45")
    monkeypatch.setenv("SCRAPE_ACCOUNT_PAUSE_MAX_X", "90")
    assert account_pause_range_for("x") == (45.0, 90.0)
    assert account_pause_range_for("reddit")[0] < 45.0


# ── one-shot interaction ────────────────────────────────────────────────

def test_pending_reddit_does_not_block_an_instagram_oneshot(queue, monkeypatch):
    from promptstudio.server import handler

    monkeypatch.setattr(handler.CreatorScrapeQueue, "get", classmethod(lambda cls: queue))
    queue.enqueue("someone", source="reddit")

    assert handler._creator_queue_blocks_oneshot("instagram") is None
    blocked = handler._creator_queue_blocks_oneshot("reddit")
    assert blocked and blocked["creator_queue_depth"] == 1


# ── queue file migration ────────────────────────────────────────────────

def test_v1_queue_file_migrates_into_the_instagram_lane(tmp_path):
    """Everything in a pre-lane file was Instagram, so that is where it lands."""
    path = tmp_path / "queue.json"
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "paused": True,
                "pause_reason": "rate limited",
                "paused_at": "2026-01-01T00:00:00+00:00",
                "stats": {"completed_today": 4, "downloaded_today": 40, "errors_today": 1},
                "jobs": [
                    {
                        "id": "csq_old", "username": "nina", "status": "pending",
                        "mode": "full", "deep": True, "priority": 0,
                        "created_at": "2026-01-01T00:00:00+00:00",
                    }
                ],
                "history": [],
            }
        ),
        encoding="utf-8",
    )

    loaded = CreatorScrapeQueue(path=str(path))
    assert loaded.is_paused("instagram") is True
    assert loaded.is_paused("x") is False, "a v1 pause must not stop new lanes"

    snap = loaded.status_snapshot()
    assert snap["lanes"]["instagram"]["pause_reason"] == "rate limited"
    assert snap["lanes"]["instagram"]["stats"]["downloaded_today"] == 40
    assert snap["stats"]["downloaded_today"] == 40, "merged stats keep the total"
    # The job itself still back-fills to instagram, as before.
    assert loaded.peek_next()["source"] == "instagram"


def test_v2_queue_file_round_trips(tmp_path):
    path = tmp_path / "queue.json"
    first = CreatorScrapeQueue(path=str(path))
    first.pause("cookies expired", source="x")
    first.enqueue("nina", source="reddit")

    second = CreatorScrapeQueue(path=str(path))
    assert second.is_paused("x") is True
    assert second.is_paused("reddit") is False
    assert second.peek_next("reddit")["username"] == "nina"


# ── status file migration ───────────────────────────────────────────────

def test_flat_sync_status_migrates_into_the_instagram_lane(tmp_path, monkeypatch):
    path = tmp_path / "sync_status.json"
    path.write_text(
        json.dumps(
            {
                "running": False,
                "job_type": "creator",
                "progress": "Complete",
                "rate_limit_hits": 3,
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "promptstudio.scraping.sync_manager.SYNC_STATUS_FILE", str(path)
    )
    mgr = SyncManager()
    status = mgr.get_status()
    assert status["lanes"]["instagram"]["rate_limit_hits"] == 3
    # Flat keys are the Instagram lane, so pre-lane clients keep working.
    assert status["job_type"] == "creator"
    assert status["progress"] == "Complete"


def test_interrupted_lane_is_recovered_on_restart(tmp_path, monkeypatch):
    path = tmp_path / "sync_status.json"
    path.write_text(
        json.dumps(
            {"lanes": {"x": {"running": True, "job_type": "creator_queue",
                             "progress": "Downloading"}}}
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "promptstudio.scraping.sync_manager.SYNC_STATUS_FILE", str(path)
    )
    mgr = SyncManager()
    lane = mgr.get_status()["lanes"]["x"]
    assert lane["running"] is False
    assert lane["error"] == "Server restarted"


# ── status shape ────────────────────────────────────────────────────────

def test_status_exposes_every_lane(manager):
    lanes = manager.get_status()["lanes"]
    for name in SOURCES:
        assert name in lanes, f"{name} lane missing from status"


def test_queue_snapshot_reports_per_lane_depth(queue):
    queue.enqueue("a", source="x")
    queue.enqueue("b", source="x")
    queue.enqueue("c", source="reddit")

    snap = queue.status_snapshot()
    assert snap["lanes"]["x"]["pending_count"] == 2
    assert snap["lanes"]["reddit"]["pending_count"] == 1
    assert snap["pending_count"] == 3, "flat count stays the union"


def test_queue_position_is_scoped_to_its_own_lane(queue):
    """With lanes draining in parallel, a global position predicts nothing."""
    queue.enqueue("a", source="instagram")
    queue.enqueue("b", source="instagram")
    out = queue.enqueue("c", source="x")
    assert out["position"] == 1, "first in the X lane, not third overall"


def test_pending_cap_is_per_lane(queue):
    """A full Instagram queue must not block enqueueing on an idle lane."""
    from promptstudio.config import CREATOR_SCRAPE_MAX_PENDING

    for i in range(CREATOR_SCRAPE_MAX_PENDING):
        queue.enqueue(f"ig_{i}", source="instagram")

    with pytest.raises(ValueError, match="instagram queue is full"):
        queue.enqueue("one_too_many", source="instagram")

    out = queue.enqueue("kaya", source="x")
    assert out["status"] == "queued", "a full IG lane must not close the X lane"
    assert out["queue_depth"] == 1, "depth must count this lane, not the archive"
    assert out["position"] == 1


def test_cancel_all_pending_can_target_one_lane(queue):
    queue.enqueue("a", source="x")
    queue.enqueue("b", source="reddit")
    assert queue.cancel_all_pending("x") == 1
    assert queue.pending_count("x") == 0
    assert queue.pending_count("reddit") == 1
