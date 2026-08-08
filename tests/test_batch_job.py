"""Batch prompt job control.

Batch analyze is the longest-running job in the app — thousands of photos at
~10s each. Before this it had no cancel at all, so the only way out of a batch
started on the wrong folder was killing the server. It also recomputed the
pending list on every status poll, which is a full archive scan.

The vision call itself is mocked: these tests are about job control, not Ollama.
"""

import threading
import time
from unittest.mock import patch

import pytest

from promptstudio.prompts.batch import BatchPromptManager


@pytest.fixture
def manager():
    """A fresh manager instance (bypassing the singleton) per test."""
    return BatchPromptManager()


def wait_until(predicate, timeout=5.0, interval=0.02):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return False


# ── idle status shape ────────────────────────────────────────────────

def test_idle_status_has_the_full_shape(manager):
    status = manager.get_status()
    for key in (
        "running", "total", "completed", "failed", "current",
        "started_at", "finished_at", "error", "cancelled",
        "cancel_requested", "pending",
    ):
        assert key in status, f"missing {key}"
    assert status["running"] is False
    assert status["pending"] == 0


def test_cancel_on_idle_returns_false(manager):
    assert manager.cancel() is False


# ── happy path ───────────────────────────────────────────────────────

def test_batch_processes_every_photo(manager, make_photo):
    for n in range(3):
        make_photo(name=f"p{n}.jpg")

    with patch("promptstudio.prompts.batch.get_prompt_for_image") as vision:
        assert manager.start_batch() is True
        assert wait_until(lambda: not manager.get_status()["running"])

    status = manager.get_status()
    assert status["total"] == 3
    assert status["completed"] == 3
    assert status["failed"] == 0
    assert status["cancelled"] is False
    assert status["pending"] == 0
    assert status["finished_at"]
    assert vision.call_count == 3


def test_start_with_nothing_pending_returns_false(manager):
    assert manager.start_batch() is False


def test_second_start_while_running_is_refused(manager, make_photo):
    for n in range(4):
        make_photo(name=f"p{n}.jpg")

    release = threading.Event()

    def slow(*a, **kw):
        release.wait(timeout=5)

    with patch("promptstudio.prompts.batch.get_prompt_for_image", side_effect=slow):
        assert manager.start_batch() is True
        assert wait_until(lambda: manager.get_status()["running"])
        assert manager.start_batch() is False, "a second batch must not start"
        release.set()
        assert wait_until(lambda: not manager.get_status()["running"])


def test_failures_are_counted_and_do_not_stop_the_run(manager, make_photo):
    for n in range(3):
        make_photo(name=f"p{n}.jpg")

    calls = {"n": 0}

    def sometimes_fails(*a, **kw):
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("ollama exploded")

    with patch("promptstudio.prompts.batch.get_prompt_for_image", side_effect=sometimes_fails):
        manager.start_batch()
        assert wait_until(lambda: not manager.get_status()["running"])

    status = manager.get_status()
    assert status["completed"] == 2
    assert status["failed"] == 1
    assert calls["n"] == 3, "the run should continue past a failure"


# ── cancel ───────────────────────────────────────────────────────────

def test_cancel_stops_the_run_early(manager, make_photo):
    for n in range(12):
        make_photo(name=f"p{n:02d}.jpg")

    started = threading.Event()
    gate = threading.Event()
    seen = []

    def blocking(full_path, creator, **kw):
        seen.append(full_path)
        started.set()
        gate.wait(timeout=5)

    with patch("promptstudio.prompts.batch.get_prompt_for_image", side_effect=blocking):
        manager.start_batch()
        assert started.wait(timeout=5), "job never began"

        assert manager.cancel() is True
        assert manager.get_status()["cancel_requested"] is True
        gate.set()  # let the in-flight item finish
        assert wait_until(lambda: not manager.get_status()["running"])

    status = manager.get_status()
    assert status["cancelled"] is True
    assert status["running"] is False
    assert len(seen) < 12, f"should have stopped early, processed {len(seen)}/12"
    assert status["pending"] > 0, "unprocessed work should still be reported as pending"


def test_cancel_lets_the_current_item_finish(manager, make_photo):
    """The Ollama call isn't interruptible; a partial write would poison the cache."""
    for n in range(4):
        make_photo(name=f"p{n}.jpg")

    finished = []
    started = threading.Event()
    gate = threading.Event()

    def slow(full_path, creator, **kw):
        started.set()
        gate.wait(timeout=5)
        finished.append(full_path)

    with patch("promptstudio.prompts.batch.get_prompt_for_image", side_effect=slow):
        manager.start_batch()
        assert started.wait(timeout=5)
        manager.cancel()
        gate.set()
        assert wait_until(lambda: not manager.get_status()["running"])

    assert len(finished) == 1, "the in-flight item must complete, not be abandoned"
    assert manager.get_status()["completed"] == 1


def test_cancel_flag_is_cleared_so_the_next_batch_runs_fully(manager, make_photo):
    for n in range(6):
        make_photo(name=f"p{n}.jpg")

    started = threading.Event()
    gate = threading.Event()

    def slow(*a, **kw):
        started.set()
        gate.wait(timeout=5)

    with patch("promptstudio.prompts.batch.get_prompt_for_image", side_effect=slow):
        manager.start_batch()
        assert started.wait(timeout=5)
        manager.cancel()
        gate.set()
        assert wait_until(lambda: not manager.get_status()["running"])
    assert manager.get_status()["cancelled"] is True

    # A fresh batch must not inherit the cancel
    with patch("promptstudio.prompts.batch.get_prompt_for_image") as vision:
        assert manager.start_batch() is True
        assert wait_until(lambda: not manager.get_status()["running"])
    status = manager.get_status()
    assert status["cancelled"] is False
    assert status["cancel_requested"] is False
    assert vision.call_count == status["completed"] > 0


# ── pending snapshot (no per-poll archive scan) ───────────────────────

def test_status_does_not_recompute_pending(manager, make_photo):
    """get_status() must be cheap — it used to trigger a full archive query.

    Asserted by proving list_uncached is not called while polling status.
    """
    for n in range(2):
        make_photo(name=f"p{n}.jpg")

    with patch("promptstudio.prompts.batch.get_prompt_for_image"):
        manager.start_batch()
        assert wait_until(lambda: not manager.get_status()["running"])

    with patch.object(
        manager, "list_uncached", side_effect=AssertionError("list_uncached called")
    ):
        for _ in range(5):
            manager.get_status()


def test_pending_counts_down_during_the_run(manager, make_photo):
    for n in range(5):
        make_photo(name=f"p{n}.jpg")

    seen_pending = []
    gate = threading.Event()

    def observe(*a, **kw):
        seen_pending.append(manager.get_status()["pending"])
        if len(seen_pending) == 1:
            gate.set()

    with patch("promptstudio.prompts.batch.get_prompt_for_image", side_effect=observe):
        manager.start_batch()
        assert gate.wait(timeout=5)
        assert wait_until(lambda: not manager.get_status()["running"])

    assert seen_pending[0] == 5, "pending starts at the full total"
    assert manager.get_status()["pending"] == 0
