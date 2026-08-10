"""A2 — batch generate.

Two things separate this from batch analyze, and both are asserted here rather
than left to a comment:

* **Skips are counted, not fixed.** A photo with no prompt is reported as
  `skipped_no_prompt`, never auto-analyzed. Chaining the two jobs is a
  job-composition question and deliberately out of scope (design §9).
* **Cancel reaches into the in-flight item.** Batch analyze finishes the
  current photo because a half-written prompt poisons the cache; here nothing
  is persisted until the image is downloaded, so interrupting costs nothing.
"""

import os
import threading
import time

import pytest

from promptstudio.comfy.batch import ComfyBatchManager
from promptstudio.config import SAVED_DIR
from promptstudio.jobs import COMFY, LEASES
from promptstudio.prompts.cache import PromptCache
from promptstudio.storage.db import ArchiveIndex


@pytest.fixture(autouse=True)
def _clean_leases():
    LEASES.reset()
    yield
    LEASES.reset()


def _with_prompt(make_photo, creator="nina", name="a.jpg", prompt="a woman"):
    rel, full = make_photo(creator=creator, name=name)
    PromptCache().set(rel, {"positive_prompt": prompt}, push_history=False)
    return rel, full


def _make_video(creator="nina", name="clip.mp4"):
    folder = os.path.join(SAVED_DIR, creator)
    os.makedirs(folder, exist_ok=True)
    full = os.path.join(folder, name)
    with open(full, "wb") as f:
        f.write(b"\x00" * 64)
    rel = f"{creator}/{name}"
    ArchiveIndex.get().upsert_photo(rel)
    return rel, full


def _run(mgr, **kwargs):
    """Start a batch and block until it is done. Returns the final status."""
    result = mgr.start(**kwargs)
    assert result["status"] == "started", result
    for _ in range(600):
        if not mgr.is_running():
            break
        time.sleep(0.01)
    assert not mgr.is_running(), "batch did not finish"
    return mgr.get_status()


# ── planning: what gets picked, what gets skipped ────────────────────


def test_plan_selects_the_given_paths(make_photo):
    a, _ = _with_prompt(make_photo, name="a.jpg")
    b, _ = _with_prompt(make_photo, name="b.jpg")
    plan = ComfyBatchManager().plan(paths=[a, b])
    assert [item.rel_path for item in plan.items] == [a, b]


def test_plan_skips_and_counts_a_photo_with_no_prompt(make_photo):
    good, _ = _with_prompt(make_photo, name="a.jpg")
    make_photo(creator="nina", name="unanalyzed.jpg")
    plan = ComfyBatchManager().plan(paths=[good, "nina/unanalyzed.jpg"])
    assert [item.rel_path for item in plan.items] == [good]
    assert plan.skipped_no_prompt == 1


def test_plan_skips_and_counts_videos(make_photo):
    """img2img has no meaningful reference frame for a reel (design §3.5)."""
    good, _ = _with_prompt(make_photo, name="a.jpg")
    clip, _ = _make_video()
    plan = ComfyBatchManager().plan(paths=[good, clip])
    assert [item.rel_path for item in plan.items] == [good]
    assert plan.skipped_video == 1


def test_plan_skips_a_path_outside_the_archive(make_photo):
    good, _ = _with_prompt(make_photo, name="a.jpg")
    plan = ComfyBatchManager().plan(paths=[good, "../../etc/passwd"])
    assert [item.rel_path for item in plan.items] == [good]


def test_plan_selects_by_creator(make_photo):
    a, _ = _with_prompt(make_photo, creator="nina", name="a.jpg")
    _with_prompt(make_photo, creator="mia", name="b.jpg")
    plan = ComfyBatchManager().plan(creator="nina")
    assert [item.rel_path for item in plan.items] == [a]


def test_plan_reuses_the_photos_filter_vocabulary(make_photo):
    """`favorite` means what it means everywhere else — no second language."""
    a, _ = _with_prompt(make_photo, name="a.jpg")
    _with_prompt(make_photo, name="b.jpg")
    ArchiveIndex.get().set_favorite(a, True)
    plan = ComfyBatchManager().plan(favorite=True)
    assert [item.rel_path for item in plan.items] == [a]


def test_plan_honours_limit(make_photo):
    for i in range(5):
        _with_prompt(make_photo, name=f"p{i}.jpg")
    assert len(ComfyBatchManager().plan(creator="nina", limit=2).items) == 2


def test_plan_is_capped_by_comfy_batch_max(make_photo, monkeypatch):
    from promptstudio.comfy import batch as batch_mod

    monkeypatch.setattr(batch_mod, "COMFY_BATCH_MAX", 2)
    for i in range(5):
        _with_prompt(make_photo, name=f"p{i}.jpg")
    plan = ComfyBatchManager().plan(creator="nina")
    assert len(plan.items) == 2
    assert plan.capped is True


def test_plan_resolves_prompts_up_front(make_photo):
    """So the response can report skips honestly instead of discovering them
    an hour into the run."""
    rel, _ = _with_prompt(make_photo, name="a.jpg", prompt="a woman on a beach")
    plan = ComfyBatchManager().plan(paths=[rel], variant="txt2img")
    assert plan.items[0].positive == "a woman on a beach"


# ── starting ─────────────────────────────────────────────────────────


def test_start_with_nothing_to_do_reports_it(make_photo):
    make_photo(creator="nina", name="unanalyzed.jpg")
    result = ComfyBatchManager().start(creator="nina")
    assert result["status"] == "nothing_to_do"
    assert result["skipped_no_prompt"] == 1


def test_start_reports_the_batch_id_and_counts(make_photo, fake_comfy):
    a, _ = _with_prompt(make_photo, name="a.jpg")
    make_photo(creator="nina", name="unanalyzed.jpg")
    mgr = ComfyBatchManager()
    result = mgr.start(creator="nina")
    assert result["status"] == "started"
    assert result["pending"] == 1
    assert result["skipped_no_prompt"] == 1
    assert result["batch_id"]
    while mgr.is_running():
        time.sleep(0.01)


def test_start_is_refused_when_comfy_is_held(make_photo):
    _with_prompt(make_photo, name="a.jpg")
    LEASES.acquire([COMFY], "someone_else")
    result = ComfyBatchManager().start(creator="nina")
    assert result["status"] == "busy"
    assert "someone_else" in result["message"]


def test_the_comfy_lease_is_released_when_the_batch_ends(make_photo, fake_comfy):
    _with_prompt(make_photo, name="a.jpg")
    mgr = ComfyBatchManager()
    _run(mgr, creator="nina")
    assert LEASES.holder(COMFY) is None


# ── running ──────────────────────────────────────────────────────────


def test_every_selected_photo_produces_a_generation(make_photo, fake_comfy):
    for i in range(3):
        _with_prompt(make_photo, name=f"p{i}.jpg")
    mgr = ComfyBatchManager()
    status = _run(mgr, creator="nina")
    assert status["completed"] == 3
    assert status["failed"] == 0
    rows, total = ArchiveIndex.get().list_generations()
    assert total == 3


def test_every_row_carries_the_same_batch_id(make_photo, fake_comfy):
    """A run is a unit — A1 shows it as a contact sheet by filtering on this."""
    for i in range(3):
        _with_prompt(make_photo, name=f"p{i}.jpg")
    mgr = ComfyBatchManager()
    status = _run(mgr, creator="nina")
    rows, _ = ArchiveIndex.get().list_generations(batch_id=status["batch_id"])
    assert len(rows) == 3
    assert {r["batch_id"] for r in rows} == {status["batch_id"]}


def test_each_item_rolls_its_own_seed(make_photo, fake_comfy):
    """Otherwise a batch is one image rendered N times."""
    for i in range(4):
        _with_prompt(make_photo, name=f"p{i}.jpg")
    mgr = ComfyBatchManager()
    _run(mgr, creator="nina")
    rows, _ = ArchiveIndex.get().list_generations()
    seeds = {row["seed"] for row in rows}
    assert len(seeds) == 4, seeds


def test_a_pinned_seed_is_used_for_every_item(make_photo, fake_comfy):
    for i in range(3):
        _with_prompt(make_photo, name=f"p{i}.jpg")
    mgr = ComfyBatchManager()
    _run(mgr, creator="nina", seed=4242)
    rows, _ = ArchiveIndex.get().list_generations()
    assert {row["seed"] for row in rows} == {4242}


def test_one_failing_item_does_not_stop_the_run(make_photo, fake_comfy, monkeypatch):
    """A ComfyUI restart mid-batch must cost one item, not the other 49."""
    from promptstudio.comfy.runner import ComfyRunner

    for i in range(3):
        _with_prompt(make_photo, name=f"p{i}.jpg")

    calls = {"n": 0}
    real = ComfyRunner._queue_prompt

    def flaky(self, workflow, client_id):
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("ComfyUI went away")
        return real(self, workflow, client_id)

    monkeypatch.setattr(ComfyRunner, "_queue_prompt", flaky)
    mgr = ComfyBatchManager()
    status = _run(mgr, creator="nina")
    assert status["completed"] == 2
    assert status["failed"] == 1


def test_status_tracks_the_current_item(make_photo, fake_comfy):
    rel, _ = _with_prompt(make_photo, name="a.jpg")
    from promptstudio.comfy.runner import ComfyRunner

    mgr = ComfyBatchManager()
    seen = []
    entered, release = threading.Event(), threading.Event()
    real = ComfyRunner._wait_for_images

    def slow(self, prompt_id, timeout_sec=600):
        seen.append(mgr.get_status()["current"])
        entered.set()
        release.wait(5)
        return real(self, prompt_id, timeout_sec)

    ComfyRunner._wait_for_images, saved = slow, ComfyRunner._wait_for_images
    try:
        assert mgr.start(paths=[rel])["status"] == "started"
        assert entered.wait(5)
        release.set()
        while mgr.is_running():
            time.sleep(0.01)
    finally:
        ComfyRunner._wait_for_images = saved
    assert seen == [rel]


def test_status_is_idle_again_after_the_run(make_photo, fake_comfy):
    _with_prompt(make_photo, name="a.jpg")
    mgr = ComfyBatchManager()
    status = _run(mgr, creator="nina")
    assert status["running"] is False
    assert status["current"] == ""
    assert status["finished_at"]


# ── cancel ───────────────────────────────────────────────────────────


def test_cancel_between_items_drains_the_queue(make_photo, fake_comfy):
    from promptstudio.comfy.runner import ComfyRunner

    for i in range(6):
        _with_prompt(make_photo, name=f"p{i}.jpg")

    mgr = ComfyBatchManager()
    real = ComfyRunner._wait_for_images

    def cancel_after_first(self, prompt_id, timeout_sec=600):
        mgr.cancel()
        return real(self, prompt_id, timeout_sec)

    ComfyRunner._wait_for_images, saved = cancel_after_first, ComfyRunner._wait_for_images
    try:
        status = _run(mgr, creator="nina")
    finally:
        ComfyRunner._wait_for_images = saved

    assert status["cancelled"] is True
    assert status["completed"] == 1
    assert status["pending"] == 5, "the drained remainder must be reported"


def test_cancel_interrupts_the_in_flight_item(make_photo, fake_comfy, monkeypatch):
    """The opposite of batch analyze, and safe for the opposite reason: nothing
    is written until the image comes back, so a killed item loses nothing."""
    from promptstudio.comfy.runner import ComfyRunner

    _with_prompt(make_photo, name="a.jpg")
    interrupted = []
    monkeypatch.setattr(
        ComfyRunner, "interrupt", lambda self, pid: interrupted.append(pid) or True
    )

    mgr = ComfyBatchManager()
    entered, release = threading.Event(), threading.Event()

    def slow(self, prompt_id, timeout_sec=600):
        entered.set()
        release.wait(5)
        raise RuntimeError("interrupted")

    monkeypatch.setattr(ComfyRunner, "_wait_for_images", slow)
    assert mgr.start(creator="nina")["status"] == "started"
    assert entered.wait(5)
    mgr.cancel()
    release.set()
    for _ in range(500):
        if not mgr.is_running():
            break
        time.sleep(0.01)
    assert interrupted, "cancel must reach the running prompt, not just the queue"


def test_cancelling_an_idle_batch_is_a_no_op():
    assert ComfyBatchManager().cancel() is False
