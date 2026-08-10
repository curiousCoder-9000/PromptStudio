"""Background batch prompt generation."""

import os
import time
from typing import Any, Callable, Dict, List, Optional

from promptstudio.jobs import OLLAMA, BackgroundJob
from promptstudio.logging_setup import get_logger
from promptstudio.prompts.cache import PromptCache
from promptstudio.prompts.engine import ENGINE_ID, get_prompt_for_image
from promptstudio.storage.archive import ArchiveStore
from promptstudio.storage.journal import RunJournal

log = get_logger(__name__)

LEASE_OWNER = "batch_prompt"

LogFn = Optional[Callable[[str], None]]


class BatchPromptManager(BackgroundJob):
    """Run the vision prompt over every uncached photo.

    Cancel is cooperative and checked *between* items, so the in-flight image
    finishes first: the Ollama call is not interruptible and a partial write
    would poison the prompt cache. (`ComfyBatchManager` interrupts mid-item for
    the opposite reason — nothing there is persisted until the image is
    downloaded.)
    """

    resources = (OLLAMA,)
    owner = LEASE_OWNER
    busy_noun = "the vision model"
    busy_message = "Batch already running"

    def _idle_status(self) -> Dict[str, Any]:
        return {
            **super()._idle_status(),
            # Snapshot taken once at job start — listing pending work is a full
            # archive scan, far too expensive to redo on every status poll.
            "pending": 0,
        }

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
                        self._status["pending"] = self._remaining()
                with self._job_lock:
                    run.summary(
                        completed=self._status["completed"],
                        failed=self._status["failed"],
                        engine=ENGINE_ID,
                    )

        return self._start(runner, total=len(pending), pending=len(pending))

    def _remaining(self) -> int:
        return max(
            0,
            self._status["total"]
            - self._status["completed"]
            - self._status["failed"],
        )

    def _finalise(self) -> None:
        # A cancelled run leaves real work undone and must say so; a completed
        # one has nothing left regardless of what the last loop iteration wrote.
        self._status["pending"] = self._remaining() if self._status.get("cancelled") else 0
        super()._finalise()


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
