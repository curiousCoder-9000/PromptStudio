"""Background batch prompt generation."""

import os
import threading
import time
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

from promptstudio.jobs import LEASES, OLLAMA
from promptstudio.logging_setup import get_logger
from promptstudio.prompts.cache import PromptCache
from promptstudio.prompts.engine import ENGINE_ID, get_prompt_for_image
from promptstudio.storage.archive import ArchiveStore
from promptstudio.storage.journal import RunJournal

log = get_logger(__name__)

LEASE_OWNER = "batch_prompt"

LogFn = Optional[Callable[[str], None]]


class BatchPromptManager:
    _instance: Optional["BatchPromptManager"] = None
    _lock = threading.Lock()

    def __init__(self) -> None:
        self._job_lock = threading.Lock()
        self._cancel = threading.Event()
        # Why the last start_batch() returned False, for the API's 409 message.
        self.last_refusal = ""
        self._status: Dict[str, Any] = self._idle_status()

    @staticmethod
    def _idle_status() -> Dict[str, Any]:
        return {
            "running": False,
            "total": 0,
            "completed": 0,
            "failed": 0,
            "current": "",
            "started_at": None,
            "finished_at": None,
            "error": None,
            "cancelled": False,
            "cancel_requested": False,
            # Snapshot taken once at job start — listing pending work is a full
            # archive scan, far too expensive to redo on every status poll.
            "pending": 0,
        }

    @classmethod
    def get(cls) -> "BatchPromptManager":
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = BatchPromptManager()
        return cls._instance

    def get_status(self) -> Dict[str, Any]:
        with self._job_lock:
            status = dict(self._status)
        status["cancel_requested"] = self._cancel.is_set()
        return status

    def is_running(self) -> bool:
        return self.get_status().get("running", False)

    def cancel(self) -> bool:
        """Request cooperative cancel; True if a job was running.

        Checked between items, so the in-flight image finishes first — the
        Ollama call is not interruptible and a partial write would poison the
        prompt cache.
        """
        with self._job_lock:
            if not self._status.get("running"):
                return False
        self._cancel.set()
        return True

    def list_uncached(
        self,
        creator: Optional[str] = None,
        force: bool = False,
        paths: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        store = ArchiveStore()
        cache = PromptCache()

        if paths:
            photos: List[Dict[str, Any]] = []
            for raw in paths:
                rel = (raw or "").replace("\\", "/").strip().lstrip("/")
                if not rel:
                    continue
                full = store.resolve_path(rel)
                if not full:
                    continue
                filename = os.path.basename(rel)
                creator_name = os.path.basename(os.path.dirname(full))
                photos.append(
                    {
                        "filename": filename,
                        "creator": creator_name,
                        "rel_path": rel,
                        "full_path": full,
                    }
                )
            if force:
                return photos
            pending = []
            for photo in photos:
                if not photo["filename"].lower().endswith(('.jpg', '.jpeg', '.png', '.webp')):
                    continue
                entry = cache.get(photo["rel_path"], photo["filename"])
                if not entry or entry.get("parameters", {}).get("vision_engine") != ENGINE_ID:
                    pending.append(photo)
            return pending

        photos = store.iter_photos(creator=creator)
        if force:
            return photos
        pending = []
        for photo in photos:
            if not photo["filename"].lower().endswith(('.jpg', '.jpeg', '.png', '.webp')):
                continue
            rel = photo["rel_path"]
            entry = cache.get(rel, photo["filename"])
            if not entry or entry.get("parameters", {}).get("vision_engine") != ENGINE_ID:
                pending.append(photo)
        return pending

    def start_batch(
        self,
        creator: Optional[str] = None,
        force: bool = False,
        limit: Optional[int] = None,
        paths: Optional[List[str]] = None,
    ) -> bool:
        pending = self.list_uncached(creator=creator, force=force, paths=paths)
        if limit:
            pending = pending[:limit]
        if not pending:
            return False

        # Same lease the classify job takes: whichever asks first gets Ollama,
        # decided under one lock rather than by two independent is_running()
        # polls that could both see "free".
        blocker = LEASES.acquire([OLLAMA], LEASE_OWNER)
        if blocker:
            self.last_refusal = (
                f"{LEASES.holder(blocker) or 'another job'} is using the vision model"
            )
            return False

        with self._job_lock:
            if self._status.get("running"):
                LEASES.release(LEASE_OWNER)
                self.last_refusal = "Batch already running"
                return False
            self.last_refusal = ""
            self._cancel.clear()
            self._status = self._idle_status()
            self._status.update(
                {
                    "running": True,
                    "total": len(pending),
                    "pending": len(pending),
                    "started_at": datetime.now(timezone.utc).isoformat(),
                }
            )

        def runner() -> None:
            journal = RunJournal.for_kind("batch_prompt")
            with journal.run(
                creator=creator, total=len(pending), force=bool(force)
            ) as run:
                for photo in pending:
                    if self._cancel.is_set():
                        with self._job_lock:
                            self._status["cancelled"] = True
                            done = self._status["completed"]
                        run.event("cancelled", completed=done)
                        break
                    rel = photo["rel_path"]
                    with self._job_lock:
                        self._status["current"] = rel
                    started = time.monotonic()
                    try:
                        get_prompt_for_image(
                            photo["full_path"],
                            photo["creator"],
                            force_refresh=force,
                            rel_path=rel,
                        )
                        with self._job_lock:
                            self._status["completed"] += 1
                        run.item(
                            path=rel,
                            ok=True,
                            ms=int((time.monotonic() - started) * 1000),
                        )
                    except Exception as exc:
                        log.warning("batch prompt failed for %s: %s", rel, exc)
                        with self._job_lock:
                            self._status["failed"] += 1
                        run.item(
                            path=rel,
                            ok=False,
                            reason=str(exc)[:200],
                            ms=int((time.monotonic() - started) * 1000),
                        )
                    with self._job_lock:
                        self._status["pending"] = max(
                            0,
                            self._status["total"]
                            - self._status["completed"]
                            - self._status["failed"],
                        )
                with self._job_lock:
                    run.summary(
                        completed=self._status["completed"],
                        failed=self._status["failed"],
                        engine=ENGINE_ID,
                    )
        def finish() -> None:
            LEASES.release(LEASE_OWNER)
            with self._job_lock:
                self._status["running"] = False
                self._status["finished_at"] = datetime.now(timezone.utc).isoformat()
                self._status["current"] = ""
                if self._status.get("cancelled"):
                    self._status["pending"] = max(
                        0,
                        self._status["total"]
                        - self._status["completed"]
                        - self._status["failed"],
                    )
                else:
                    self._status["pending"] = 0
            self._cancel.clear()

        def guarded_runner() -> None:
            # Without this, an exception escaping the loop stranded the lease
            # and left running=True forever — the job could never be restarted.
            try:
                runner()
            except Exception as exc:
                log.exception("batch prompt job crashed")
                with self._job_lock:
                    self._status["error"] = str(exc)
            finally:
                finish()

        threading.Thread(target=guarded_runner, daemon=True).start()
        return True


def count_prompts_ready() -> int:
    store = ArchiveStore()
    cache = PromptCache()
    cache_data = cache.load()
    count = 0
    for photo in store.iter_photos():
        rel = photo["rel_path"]
        entry = cache_data.get(rel) or cache_data.get(photo["filename"])
        if entry and entry.get("parameters", {}).get("vision_engine") == ENGINE_ID:
            count += 1
    return count
