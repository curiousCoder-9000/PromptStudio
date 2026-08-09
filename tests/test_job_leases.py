"""Exclusive leases on shared external resources.

The bug being fixed is a race, so most of these run threads: the old guards
polled `is_running()` and then started the job, with no lock held across both,
so two requests arriving together could each see "free".
"""

import threading

import pytest

from promptstudio.jobs import (
    COMFY,
    INSTAGRAM,
    OLLAMA,
    LeaseRegistry,
    ResourceBusy,
    all_resources,
    scrape_resource,
)


@pytest.fixture
def leases():
    return LeaseRegistry()


def test_uncontended_acquire_succeeds(leases):
    assert leases.acquire([OLLAMA], "classify") is None
    assert leases.holder(OLLAMA) == "classify"


def test_second_owner_is_told_which_resource_blocked(leases):
    leases.acquire([OLLAMA], "classify")
    assert leases.acquire([OLLAMA], "batch_prompt") == OLLAMA


def test_release_frees_the_resource(leases):
    leases.acquire([OLLAMA], "classify")
    assert leases.release("classify") == [OLLAMA]
    assert leases.acquire([OLLAMA], "batch_prompt") is None


def test_release_only_touches_your_own(leases):
    leases.acquire([OLLAMA], "classify")
    leases.acquire([INSTAGRAM], "sync")
    leases.release("classify")
    assert leases.holder(INSTAGRAM) == "sync"
    assert leases.holder(OLLAMA) is None


def test_unrelated_resources_do_not_block_each_other(leases):
    assert leases.acquire([OLLAMA], "classify") is None
    assert leases.acquire([INSTAGRAM], "sync") is None
    assert leases.acquire([COMFY], "comfy") is None


# ── per-source scrape lanes (docs/design_scrape_lanes.md §3) ────────────

def test_scrape_resource_is_namespaced_per_source():
    assert scrape_resource("x") == "scrape:x"
    assert scrape_resource("  Reddit ") == "scrape:reddit"


def test_instagram_alias_is_the_instagram_lane():
    """Existing callers meaning "the Instagram session" keep working."""
    assert INSTAGRAM == scrape_resource("instagram")


def test_scrape_resource_rejects_an_empty_source():
    with pytest.raises(ValueError):
        scrape_resource("")


def test_two_sources_scrape_concurrently(leases):
    """The whole point: Reddit must not wait on Instagram."""
    assert leases.acquire([scrape_resource("instagram")], "sync:instagram") is None
    assert leases.acquire([scrape_resource("x")], "sync:x") is None
    assert leases.acquire([scrape_resource("reddit")], "sync:reddit") is None


def test_same_source_still_blocks_with_an_attributable_holder(leases):
    """Instagram stays pinned to one job — that part must not regress."""
    leases.acquire([scrape_resource("instagram")], "sync:instagram")
    blocked = leases.acquire([scrape_resource("instagram")], "creator_queue")
    assert blocked == scrape_resource("instagram")
    assert leases.holder(blocked) == "sync:instagram"


def test_releasing_one_lane_leaves_the_others_held(leases):
    leases.acquire([scrape_resource("instagram")], "sync:instagram")
    leases.acquire([scrape_resource("x")], "sync:x")
    leases.release("sync:x")
    assert leases.holder(scrape_resource("instagram")) == "sync:instagram"
    assert leases.holder(scrape_resource("x")) is None


def test_all_resources_covers_every_registered_source():
    from promptstudio.scraping.sources import known_sources

    names = all_resources()
    assert OLLAMA in names and COMFY in names
    for source in known_sources():
        assert scrape_resource(source) in names, f"{source} lane missing"


def test_snapshot_surfaces_an_unregistered_lease(leases):
    """A lease under an unexpected name must not vanish from /api/health."""
    leases.acquire(["scrape:mystery"], "someone")
    assert leases.snapshot()["scrape:mystery"] == "someone"


def test_reacquiring_your_own_lease_succeeds(leases):
    leases.acquire([OLLAMA], "classify")
    assert leases.acquire([OLLAMA], "classify") is None


def test_multi_resource_acquire_is_all_or_nothing(leases):
    """A partial grab would strand a resource nobody releases."""
    leases.acquire([INSTAGRAM], "sync")
    assert leases.acquire([OLLAMA, INSTAGRAM], "greedy") == INSTAGRAM
    assert leases.holder(OLLAMA) is None, "must not keep the half it could take"


def test_snapshot_lists_every_resource(leases):
    leases.acquire([OLLAMA], "classify")
    snap = leases.snapshot()
    assert set(snap) == set(all_resources())
    assert snap[OLLAMA] == "classify"
    assert snap[INSTAGRAM] is None


def test_hold_releases_on_exit(leases):
    with leases.hold([OLLAMA], "classify"):
        assert leases.holder(OLLAMA) == "classify"
    assert leases.holder(OLLAMA) is None


def test_hold_releases_when_the_body_raises(leases):
    with pytest.raises(ValueError):
        with leases.hold([OLLAMA], "classify"):
            raise ValueError("boom")
    assert leases.holder(OLLAMA) is None


def test_hold_raises_when_taken(leases):
    leases.acquire([OLLAMA], "classify")
    with pytest.raises(ResourceBusy) as exc:
        with leases.hold([OLLAMA], "batch_prompt"):
            pass
    assert exc.value.resource == OLLAMA
    assert exc.value.holder == "classify"


# ── the race ─────────────────────────────────────────────────────────


def test_exactly_one_of_many_racing_owners_wins(leases):
    """The whole point: `is_running()` + start was two steps and could double-book."""
    winners = []
    barrier = threading.Barrier(16)

    def contend(i):
        barrier.wait()
        if leases.acquire([OLLAMA], f"owner{i}") is None:
            winners.append(i)

    threads = [threading.Thread(target=contend, args=(i,)) for i in range(16)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(winners) == 1


def test_repeated_acquire_release_cycles_leave_nothing_held(leases):
    barrier = threading.Barrier(8)

    def cycle(i):
        barrier.wait()
        for _ in range(50):
            if leases.acquire([OLLAMA, COMFY], f"owner{i}") is None:
                leases.release(f"owner{i}")

    threads = [threading.Thread(target=cycle, args=(i,)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert leases.snapshot() == {name: None for name in all_resources()}


# ── wiring into the real managers ────────────────────────────────────


def test_batch_takes_the_ollama_lease():
    from promptstudio.jobs import LEASES
    from promptstudio.prompts import batch as batch_mod

    LEASES.reset()
    try:
        assert LEASES.acquire([OLLAMA], batch_mod.LEASE_OWNER) is None
        # A second holder is refused and told who has it.
        assert LEASES.acquire([OLLAMA], "someone_else") == OLLAMA
    finally:
        LEASES.reset()



def test_batch_refuses_and_explains_when_ollama_is_held(make_photo):
    from promptstudio.jobs import LEASES
    from promptstudio.prompts.batch import BatchPromptManager

    make_photo(creator="nina", name="a.jpg")
    LEASES.reset()
    LEASES.acquire([OLLAMA], "some_other_job")
    try:
        mgr = BatchPromptManager()
        assert mgr.start_batch(creator="nina") is False
        assert "some_other_job" in mgr.last_refusal
    finally:
        LEASES.reset()


def test_sync_refuses_when_instagram_is_held():
    from promptstudio.jobs import LEASES
    from promptstudio.scraping.sync_manager import SyncManager

    LEASES.reset()
    LEASES.acquire([INSTAGRAM], "someone_else")
    try:
        mgr = SyncManager()
        assert mgr.start_job("saved", lambda log: None) is False
    finally:
        LEASES.reset()


def test_sync_releases_the_lease_when_the_job_finishes():
    import time

    from promptstudio.jobs import LEASES
    from promptstudio.scraping.sync_manager import SyncManager

    LEASES.reset()
    mgr = SyncManager()
    done = threading.Event()
    try:
        assert mgr.start_job("saved", lambda log: done.set() or {"ok": True}) is True
        assert done.wait(5)
        for _ in range(100):
            if LEASES.holder(INSTAGRAM) is None:
                break
            time.sleep(0.02)
        assert LEASES.holder(INSTAGRAM) is None
    finally:
        LEASES.reset()


def test_sync_releases_the_lease_when_the_job_raises():
    import time

    from promptstudio.jobs import LEASES
    from promptstudio.scraping.sync_manager import SyncManager

    LEASES.reset()
    mgr = SyncManager()

    def boom(log, *_a):
        raise RuntimeError("scrape exploded")

    try:
        assert mgr.start_job("saved", boom) is True
        for _ in range(150):
            if LEASES.holder(INSTAGRAM) is None:
                break
            time.sleep(0.02)
        assert LEASES.holder(INSTAGRAM) is None, "a crash must not strand the session"
    finally:
        LEASES.reset()


def test_comfy_takes_the_comfy_lease_while_generating(make_photo, monkeypatch):
    """COMFY was declared in jobs.py but never acquired — start() self-checked
    `_status["running"]` instead, which is the race the lease exists to close.
    """
    import time

    from promptstudio.comfy import client as comfy
    from promptstudio.comfy.client import ComfyJobManager
    from promptstudio.jobs import LEASES

    rel, _ = make_photo(creator="leasetest", name="a.jpg")
    held = threading.Event()
    release = threading.Event()

    def fake_queue(self, workflow, client_id):
        held.set()
        release.wait(5)
        return "prompt-1"

    monkeypatch.setattr(comfy, "upload_image_to_comfy", lambda *a, **k: "ref.jpg")
    monkeypatch.setattr(ComfyJobManager, "_queue_prompt", fake_queue)
    monkeypatch.setattr(
        ComfyJobManager,
        "_wait_for_images",
        lambda self, pid, timeout_sec=600: [
            {"filename": "o.png", "subfolder": "", "type": "output"}
        ],
    )
    monkeypatch.setattr(ComfyJobManager, "_download_image", lambda self, m: b"\x89PNG")

    LEASES.reset()
    mgr = ComfyJobManager()
    try:
        assert mgr.start(source_rel=rel, positive="a", negative="b") is True
        assert held.wait(5)
        assert LEASES.holder(COMFY) == "comfy"
    finally:
        release.set()
        for _ in range(150):
            if LEASES.holder(COMFY) is None:
                break
            time.sleep(0.02)
        assert LEASES.holder(COMFY) is None, "a finished job must release ComfyUI"
        LEASES.reset()


def test_comfy_refuses_and_explains_when_comfyui_is_held(make_photo):
    from promptstudio.comfy.client import ComfyJobManager
    from promptstudio.jobs import LEASES

    rel, _ = make_photo(creator="leasetest", name="b.jpg")
    LEASES.reset()
    LEASES.acquire([COMFY], "someone_else")
    try:
        mgr = ComfyJobManager()
        assert mgr.start(source_rel=rel, positive="a", negative="b") is False
        assert "someone_else" in mgr.last_refusal
    finally:
        LEASES.reset()


def test_sync_explains_which_holder_refused_it():
    """The API turns last_refusal into the 409 body, so "busy" is attributable."""
    from promptstudio.jobs import LEASES
    from promptstudio.scraping.sync_manager import SyncManager

    LEASES.reset()
    LEASES.acquire([INSTAGRAM], "creator_queue")
    try:
        mgr = SyncManager()
        assert mgr.start_job("saved", lambda log: None) is False
        assert "creator_queue" in mgr.last_refusal
    finally:
        LEASES.reset()
