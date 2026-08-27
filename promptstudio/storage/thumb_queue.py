"""Thumbnail generation moved off the HTTP request thread.

`docs/review_gallery_performance.md` §4 found `ensure_thumbnail` had exactly one
caller — `GET /media/thumb/` — so a thumbnail existed only if somebody had
already looked at that tile. On the live archive that was 12,148 thumbs against
61,344 catalog rows, and the newest 500 files (the view you open right after a
scrape) were 91% unthumbed. Opening that page asked the server for 60 JPEG
encodes at once, across the six connections a browser will open, on the same
threads that serve the API. For a reel it was worse: a miss ran
`write_best_video_frame_jpeg`, which decodes the whole timeline to rank frames.

This module is the other end of that. Ingest submits; a small fixed pool
encodes; the request thread waits briefly and otherwise sends a placeholder.
Three properties are load-bearing:

* **Bounded concurrency.** N workers, not "however many tiles are on screen".
  The encode cost is the same, but it stops competing with the API for threads.
* **Deduplicated.** Sixty tiles, a re-render and a backfill pass can all name
  the same path; it is encoded once and everyone waiting is woken.
* **Never blocking the submitter.** A full queue drops the request. The tile
  falls back to the placeholder and the next backfill picks it up — an ingest
  that stalls because the thumbnailer is behind would be a worse bug than a
  grey tile.

`THUMB_WORKERS=0` keeps the old inline behaviour, for a environment where a
background thread is unwelcome. It is an escape hatch, not a mode to prefer.
"""

from __future__ import annotations

import os
import queue
import threading
from typing import Dict, Optional

from promptstudio.config import THUMB_WORKERS
from promptstudio.logging_setup import get_logger
from promptstudio.storage.thumbs import ensure_thumbnail, resolve_thumb_file

log = get_logger(__name__)

# Deep enough to hold a full scrape batch plus a page of tiles, shallow enough
# that a wedged worker cannot grow into the archive's size in RAM.
_MAX_QUEUED = 8192


class ThumbQueue:
    """Fixed worker pool that writes `_thumbs/` entries.

    One instance per process in practice (`get()`), but constructible for tests
    so a suite can drive it synchronously instead of racing threads.
    """

    def __init__(self, workers: int = 1, *, maxsize: int = _MAX_QUEUED) -> None:
        self._q: queue.Queue = queue.Queue(maxsize=maxsize)
        self._lock = threading.Lock()
        # rel_path -> the Event that fires when this path has been attempted.
        # Membership is also the dedupe set: present means queued or running.
        self._waiting: Dict[str, threading.Event] = {}
        self._workers = max(0, int(workers))
        self._threads: list[threading.Thread] = []
        self._started = False

    # ── submission ───────────────────────────────────────────────────

    def submit(self, rel_path: str, full_path: str) -> Optional[threading.Event]:
        """Queue `rel_path` for encoding. Returns the Event to wait on.

        None means "not queued": no workers configured, or the queue is full.
        Callers treat that as "no thumb right now", never as an error — the
        backfill CLI is the safety net for both.
        """
        if not rel_path or not full_path or not self._workers:
            return None
        with self._lock:
            existing = self._waiting.get(rel_path)
            if existing is not None:
                # Already queued or in flight. Wait on the same attempt rather
                # than encoding the same file twice.
                return existing
            event = threading.Event()
            self._waiting[rel_path] = event
            self._ensure_started()
        try:
            self._q.put_nowait((rel_path, full_path))
        except queue.Full:
            # Retract the reservation, or this path is permanently "in flight"
            # and no later submit for it will ever be queued.
            with self._lock:
                self._waiting.pop(rel_path, None)
            event.set()
            log.debug("thumb queue full, dropped %s", rel_path)
            return None
        return event

    def _ensure_started(self) -> None:
        """Spawn workers on first use. Caller holds `_lock`."""
        if self._started:
            return
        self._started = True
        for n in range(self._workers):
            thread = threading.Thread(
                target=self._run, name=f"thumbs-{n}", daemon=True
            )
            thread.start()
            self._threads.append(thread)
        log.debug("thumb queue started with %d worker(s)", self._workers)

    # ── the worker ───────────────────────────────────────────────────

    def _run(self) -> None:
        while True:
            rel_path, full_path = self._q.get()
            try:
                if not resolve_thumb_file(rel_path) and os.path.isfile(full_path):
                    ensure_thumbnail(full_path, rel_path)
            except Exception:
                # A single unreadable file must not take the worker down and
                # leave every later tile waiting on a thread that no longer
                # exists. ensure_thumbnail already logs the interesting cases.
                log.exception("thumbnail worker failed for %s", rel_path)
            finally:
                with self._lock:
                    event = self._waiting.pop(rel_path, None)
                if event is not None:
                    event.set()
                self._q.task_done()

    # ── introspection, for the health route and tests ────────────────

    @property
    def workers(self) -> int:
        return self._workers

    def pending(self) -> int:
        return self._q.qsize()

    def drain(self, timeout: Optional[float] = None) -> bool:
        """Block until the queue empties. Tests and the backfill CLI only."""
        if not self._workers:
            return True
        if timeout is None:
            self._q.join()
            return True
        deadline = threading.Event()
        waiter = threading.Thread(
            target=lambda: (self._q.join(), deadline.set()), daemon=True
        )
        waiter.start()
        return deadline.wait(timeout)


_instance: Optional[ThumbQueue] = None
_instance_lock = threading.Lock()


def get() -> ThumbQueue:
    global _instance
    with _instance_lock:
        if _instance is None:
            _instance = ThumbQueue(workers=THUMB_WORKERS)
        return _instance


def enqueue(rel_path: str, full_path: str) -> None:
    """Fire-and-forget ingest hook. Never raises, never blocks.

    Called from `ArchiveIndex.upsert_photo`, which is the one place every
    arrival funnels through — downloader, gallery-dl, manual upload and trash
    restore. `rebuild()` deliberately does not call it: a reindex of 61k rows
    is the backfill CLI's job, not 61k queue entries behind a page load.

    `upsert_photo` also runs for a favourite toggle and every prompt save, so
    the already-thumbed case is checked here rather than paid for as queue
    churn — one `isfile` against a round trip through the pool.
    """
    try:
        if resolve_thumb_file(rel_path):
            return
        get().submit(rel_path, full_path)
    except Exception:
        log.debug("thumb enqueue failed for %s", rel_path, exc_info=True)
