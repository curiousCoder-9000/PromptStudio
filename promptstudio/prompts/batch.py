"""Background batch prompt generation."""

import os
import threading
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

from promptstudio.prompts.cache import PromptCache
from promptstudio.prompts.engine import ENGINE_ID, get_prompt_for_image
from promptstudio.storage.archive import ArchiveStore

LogFn = Optional[Callable[[str], None]]


class BatchPromptManager:
    _instance: Optional["BatchPromptManager"] = None
    _lock = threading.Lock()

    def __init__(self) -> None:
        self._job_lock = threading.Lock()
        self._cancel = threading.Event()
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
                entry = cache.get(photo["rel_path"], photo["filename"])
                if not entry or entry.get("parameters", {}).get("vision_engine") != ENGINE_ID:
                    pending.append(photo)
            return pending

        photos = store.iter_photos(creator=creator)
        if force:
            return photos
        pending = []
        for photo in photos:
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
        with self._job_lock:
            if self._status.get("running"):
                return False
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
            for photo in pending:
                if self._cancel.is_set():
                    with self._job_lock:
                        self._status["cancelled"] = True
                    break
                rel = photo["rel_path"]
                with self._job_lock:
                    self._status["current"] = rel
                try:
                    get_prompt_for_image(
                        photo["full_path"],
                        photo["creator"],
                        force_refresh=force,
                        rel_path=rel,
                    )
                    with self._job_lock:
                        self._status["completed"] += 1
                except Exception as exc:
                    print(f"Batch prompt error {rel}: {exc}")
                    with self._job_lock:
                        self._status["failed"] += 1
                with self._job_lock:
                    self._status["pending"] = max(
                        0,
                        self._status["total"]
                        - self._status["completed"]
                        - self._status["failed"],
                    )
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

        threading.Thread(target=runner, daemon=True).start()
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
