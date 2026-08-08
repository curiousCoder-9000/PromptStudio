"""Queue → SyncManager dispatch across sources.

Covers the wiring that decides *which* source runs a job, without touching the
network: the real `MediaSource.run` is swapped for a recorder.
"""

import os

import pytest

from promptstudio.scraping.creator_queue import CreatorScrapeQueue, job_key
from promptstudio.scraping.results import SyncResult
from promptstudio.scraping.sync_manager import SyncManager


@pytest.fixture
def queue(tmp_path):
    """A fresh queue on its own file (the real one is a singleton)."""
    return CreatorScrapeQueue(path=str(tmp_path / "queue.json"))


# ── queue identity ──────────────────────────────────────────────────────

def test_enqueue_defaults_to_instagram(queue):
    out = queue.enqueue("roxeuoon")
    assert out["job"]["source"] == "instagram"


def test_enqueue_records_source(queue):
    assert queue.enqueue("nina", source="x")["job"]["source"] == "x"
    assert queue.enqueue("r_fashion", source="reddit")["job"]["source"] == "reddit"


def test_same_handle_allowed_once_per_source(queue):
    """Queue identity is (source, username), not username alone."""
    first = queue.enqueue("nina", source="instagram")
    second = queue.enqueue("nina", source="x")

    assert first["status"] == "queued"
    assert second["status"] == "queued", (
        "the same handle on a different platform must not be treated as a duplicate"
    )
    assert queue.pending_count() == 2


def test_duplicate_within_one_source_is_still_rejected(queue):
    queue.enqueue("nina", source="x")
    assert queue.enqueue("nina", source="x")["status"] == "already_pending"
    assert queue.pending_count() == 1


def test_find_active_is_source_scoped(queue):
    queue.enqueue("nina", source="x")
    assert queue.find_active_by_username("nina", source="x") is not None
    assert queue.find_active_by_username("nina", source="instagram") is None


def test_source_is_normalized_on_enqueue(queue):
    assert queue.enqueue("nina", source="  X  ")["job"]["source"] == "x"


def test_legacy_queue_file_without_source_loads_as_instagram(tmp_path):
    """Queue files written before multi-source support must keep working."""
    import json

    path = tmp_path / "queue.json"
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "jobs": [
                    {
                        "id": "csq_old",
                        "username": "roxeuoon",
                        "mode": "full",
                        "status": "pending",
                        "created_at": "2026-01-01T00:00:00+00:00",
                        "priority": 0,
                    }
                ],
                "history": [{"id": "csq_older", "username": "someone"}],
            }
        ),
        encoding="utf-8",
    )
    loaded = CreatorScrapeQueue(path=str(path))
    assert loaded.peek_next()["source"] == "instagram"
    assert job_key(loaded.peek_next()) == ("instagram", "roxeuoon")


def test_status_snapshot_exposes_running_source(queue):
    job = queue.enqueue("nina", source="x")["job"]
    queue.mark_running(job["id"])
    assert queue.summary_for_sync_status()["current_source"] == "x"


# ── dispatch ────────────────────────────────────────────────────────────

@pytest.fixture
def dispatch(monkeypatch, tmp_path):
    """Drain the queue with every source's run() replaced by a recorder."""
    calls = []

    def make_stub(name):
        class Stub:
            def __init__(self):
                self.name = name
                self.label = name

            def parse_target(self, target, **kw):
                real = _real_get_source(name)
                return real.parse_target(target, **kw)

            def run(self, target, options, ctx):
                calls.append(
                    {
                        "source": name,
                        "folder": target.folder,
                        "url": target.url,
                        "mode": options.mode,
                        "include_videos": options.include_videos,
                    }
                )
                ctx.log(f"stub {name} ran")
                return SyncResult(
                    job_type="creator",
                    source=name,
                    downloaded=1,
                    stop_reason="end_of_feed",
                )

        return Stub()

    import promptstudio.scraping.sources as sources_pkg

    _real_get_source = sources_pkg.get_source
    stubs = {n: make_stub(n) for n in ("instagram", "x", "reddit")}

    def fake_get_source(name):
        key = sources_pkg.normalize_source(name)
        if key not in stubs:
            raise ValueError(f"Unknown source '{name}'")
        return stubs[key]

    monkeypatch.setattr(sources_pkg, "get_source", fake_get_source)

    queue = CreatorScrapeQueue(path=str(tmp_path / "queue.json"))
    monkeypatch.setattr(CreatorScrapeQueue, "get", classmethod(lambda cls: queue))

    manager = SyncManager()
    monkeypatch.setattr(SyncManager, "get", classmethod(lambda cls: manager))

    def drain():
        """Run one job synchronously (start_job spawns a thread)."""
        import threading

        done = threading.Event()
        original = manager.start_job

        def sync_start(job_type, fn):
            try:
                fn(lambda _m: None, None)
            finally:
                done.set()
            return True

        monkeypatch.setattr(manager, "start_job", sync_start)
        started = manager.try_drain_creator_queue()
        monkeypatch.setattr(manager, "start_job", original)
        return started

    return calls, queue, drain


def test_instagram_job_dispatches_to_instagram_source(dispatch):
    calls, queue, drain = dispatch
    queue.enqueue("roxeuoon", source="instagram")
    assert drain() is True
    assert len(calls) == 1
    assert calls[0]["source"] == "instagram"
    assert calls[0]["folder"] == "roxeuoon"  # bare handle preserved


def test_x_job_dispatches_to_x_source_with_suffixed_folder(dispatch):
    calls, queue, drain = dispatch
    queue.enqueue("nina", source="x")
    assert drain() is True
    assert calls[0]["source"] == "x"
    assert calls[0]["folder"] == "nina__x"
    assert calls[0]["url"] == "https://x.com/nina/media"


def test_reddit_job_dispatches_with_subreddit_url(dispatch):
    """The 'r/' in the stored username must survive the queue round-trip."""
    calls, queue, drain = dispatch
    queue.enqueue("r/fashion", source="reddit")
    assert drain() is True
    assert calls[0]["source"] == "reddit"
    assert calls[0]["url"] == "https://www.reddit.com/r/fashion/"
    assert calls[0]["folder"] == "r_fashion__reddit"


def test_reddit_bare_name_is_treated_as_a_subreddit(dispatch):
    calls, queue, drain = dispatch
    queue.enqueue("streetwear", source="reddit")
    drain()
    assert calls[0]["url"] == "https://www.reddit.com/r/streetwear/"


def test_reddit_user_target_round_trips(dispatch):
    calls, queue, drain = dispatch
    queue.enqueue("u/bob", source="reddit")
    drain()
    assert calls[0]["url"] == "https://www.reddit.com/user/bob/submitted/"
    assert calls[0]["folder"] == "u_bob__reddit"


def test_dispatch_creates_the_suffixed_folder(dispatch):
    from promptstudio.config import SAVED_DIR

    _calls, queue, drain = dispatch
    queue.enqueue("nina", source="x")
    drain()
    assert os.path.isdir(os.path.join(SAVED_DIR, "nina__x"))


def test_unknown_source_fails_the_job_without_crashing_the_queue(dispatch):
    calls, queue, drain = dispatch
    # Bypass enqueue validation to simulate a hand-edited queue file.
    job = queue.enqueue("nina", source="x")["job"]
    with queue._lock:
        for entry in queue._data["jobs"]:
            if entry["id"] == job["id"]:
                entry["source"] = "tiktok"
        queue._save()

    assert drain() is True
    assert calls == []                      # nothing ran
    assert queue.pending_count() == 0       # job was finalized, not left stuck
    assert queue.get_job(job["id"])["status"] == "error"


def test_job_options_reach_the_source(dispatch):
    calls, queue, drain = dispatch
    queue.enqueue("nina", source="x", mode="bounded", max_posts=10, include_videos=False)
    drain()
    assert calls[0]["mode"] == "bounded"
    assert calls[0]["include_videos"] is False


def test_successful_run_is_finalized_as_done(dispatch):
    _calls, queue, drain = dispatch
    job = queue.enqueue("nina", source="x")["job"]
    drain()
    assert queue.get_job(job["id"])["status"] == "done"
