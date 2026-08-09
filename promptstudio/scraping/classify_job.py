"""Background per-creator glam / outfit classification job."""

from __future__ import annotations

import os
import threading
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from promptstudio.config import (
    EXCLUDED_FOLDERS,
    IMAGE_EXTENSIONS,
    MEDIA_EXTENSIONS,
    MODEL_NAME,
    SAVED_DIR,
    VIDEO_EXTENSIONS,
)
from promptstudio.jobs import LEASES, OLLAMA
from promptstudio.logging_setup import get_logger
from promptstudio.scraping.outfit_classifier import (
    active_prompt_versions,
    classify_media,
    ollama_reachable,
    persist_glam_score,
)
from promptstudio.storage.db import ArchiveIndex, normalize_rel_path
from promptstudio.storage.journal import RunJournal

log = get_logger(__name__)

LEASE_OWNER = "classify"


class ClassifyJobManager:
    """Singleton background job: classify unscored media for one creator."""

    _instance: Optional["ClassifyJobManager"] = None
    _lock = threading.Lock()

    def __init__(self) -> None:
        self._job_lock = threading.Lock()
        self._cancel = threading.Event()
        self._status: Dict[str, Any] = self._idle_status()

    @staticmethod
    def _empty_hist() -> Dict[str, int]:
        """Glam buckets. "-1" is the error/unscored bucket, not a score."""
        return {"-1": 0, "0": 0, "1": 0, "2": 0, "3": 0}

    @staticmethod
    def _idle_status() -> Dict[str, Any]:
        return {
            "running": False,
            "creator": None,
            "total": 0,
            "completed": 0,
            "failed": 0,
            "kept": 0,
            "rejected": 0,
            "current": "",
            "started_at": None,
            "finished_at": None,
            "error": None,
            "cancelled": False,
            "include_videos": True,
            "force": False,
            "model": MODEL_NAME,
            # Distribution guard: a classifier that emits one value for most of
            # the archive carries almost no information, which is how the v2
            # prompt shipped 85% glam=3 unnoticed. Surfacing the histogram makes
            # that visible per run without needing a labeled eval set.
            "score_hist": ClassifyJobManager._empty_hist(),
            "top_score_share": 0.0,
            "unscored_rate": 0.0,
        }

    @classmethod
    def get(cls) -> "ClassifyJobManager":
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = ClassifyJobManager()
        return cls._instance

    def get_status(self) -> Dict[str, Any]:
        with self._job_lock:
            status = dict(self._status)
            # dict() is shallow — copy the histogram too, or callers observe it
            # mutating under them while the job runs.
            hist = status.get("score_hist")
            if isinstance(hist, dict):
                status["score_hist"] = dict(hist)
            return status

    def _record_score(self, score: int) -> None:
        """Bucket one result and refresh the derived shares. Call under lock."""
        hist = self._status.get("score_hist")
        if not isinstance(hist, dict):
            hist = self._empty_hist()
            self._status["score_hist"] = hist
        key = str(int(score)) if 0 <= int(score) <= 3 else "-1"
        hist[key] = int(hist.get(key, 0)) + 1

        scored = [int(hist.get(str(s), 0)) for s in range(4)]
        total_scored = sum(scored)
        errors = int(hist.get("-1", 0))
        total = total_scored + errors
        self._status["top_score_share"] = (
            round(max(scored) / total_scored, 4) if total_scored else 0.0
        )
        self._status["unscored_rate"] = round(errors / total, 4) if total else 0.0

    def is_running(self) -> bool:
        return bool(self.get_status().get("running"))

    def cancel(self) -> bool:
        """Request cooperative cancel; returns True if a job was running."""
        if not self.is_running():
            return False
        self._cancel.set()
        return True

    def list_pending(
        self,
        creator: str,
        *,
        force: bool = False,
        include_videos: bool = True,
        limit: Optional[int] = None,
        rescore_stale: bool = False,
    ) -> List[Dict[str, Any]]:
        """List media paths for creator that still need classification.

        `rescore_stale` also picks up files scored by a prompt version that is
        no longer current. Without it, improving a prompt never re-runs anything
        and the only way to adopt it is --force over the whole archive.
        """
        creator = (creator or "").strip().lstrip("@")
        if not creator:
            return []
        if creator in EXCLUDED_FOLDERS or creator.startswith((".", "_")):
            return []

        folder = os.path.join(os.path.expanduser(SAVED_DIR), creator)
        if not os.path.isdir(folder):
            return []

        exts = MEDIA_EXTENSIONS if include_videos else IMAGE_EXTENSIONS
        index = ArchiveIndex.get()
        stale: set[str] = set()
        if rescore_stale and not force:
            stale = set(
                index.list_stale_glam(active_prompt_versions(), creator=creator)
            )
        pending: List[Dict[str, Any]] = []

        try:
            names = sorted(os.listdir(folder))
        except OSError:
            return []

        for fname in names:
            if not fname.lower().endswith(exts):
                continue
            if fname.endswith(".meta.json"):
                continue
            rel = normalize_rel_path(f"{creator}/{fname}")
            full = os.path.join(folder, fname)
            if not os.path.isfile(full):
                continue
            if not force:
                score = index.get_glam_score(rel)
                if score is not None and int(score) >= 0 and rel not in stale:
                    continue
            pending.append(
                {
                    "filename": fname,
                    "creator": creator,
                    "rel_path": rel,
                    "full_path": full,
                    "is_video": fname.lower().endswith(VIDEO_EXTENSIONS),
                }
            )
            if limit is not None and len(pending) >= int(limit):
                break
        return pending

    def start(
        self,
        creator: str,
        *,
        force: bool = False,
        include_videos: bool = True,
        limit: Optional[int] = None,
        only_unscored: bool = True,
        rescore_stale: bool = False,
    ) -> Dict[str, Any]:
        """
        Start a classify job.

        Returns a result dict:
          status: started | nothing_to_do | busy | ollama_down | bad_creator
        """
        creator = (creator or "").strip().lstrip("@")
        if not creator:
            return {"status": "bad_creator", "message": "creator required"}

        if not ollama_reachable():
            return {
                "status": "ollama_down",
                "message": f"Ollama not reachable (need model {MODEL_NAME})",
            }

        # force overrides only_unscored
        use_force = bool(force) or not only_unscored
        pending = self.list_pending(
            creator,
            force=use_force,
            include_videos=include_videos,
            limit=limit,
            rescore_stale=bool(rescore_stale),
        )
        if not pending:
            return {"status": "nothing_to_do", "pending": 0, "creator": creator}

        # Taken only once there is work to do, and released in the runner's
        # finally. Atomic against a concurrent batch start — the old
        # is_running() poll left a window where both could pass.
        blocker = LEASES.acquire([OLLAMA], LEASE_OWNER)
        if blocker:
            return {
                "status": "busy",
                "message": (
                    f"{LEASES.holder(blocker) or 'another job'} is using the "
                    "vision model — wait or cancel it first"
                ),
            }

        with self._job_lock:
            if self._status.get("running"):
                LEASES.release(LEASE_OWNER)
                return {
                    "status": "busy",
                    "message": "Classify job already running",
                    "creator": self._status.get("creator"),
                }
            self._cancel.clear()
            self._status = {
                "running": True,
                "creator": creator,
                "total": len(pending),
                "completed": 0,
                "failed": 0,
                "kept": 0,
                "rejected": 0,
                "current": "",
                "started_at": datetime.now(timezone.utc).isoformat(),
                "finished_at": None,
                "error": None,
                "cancelled": False,
                "include_videos": bool(include_videos),
                "force": bool(use_force),
                "model": MODEL_NAME,
                "score_hist": self._empty_hist(),
                "top_score_share": 0.0,
                "unscored_rate": 0.0,
            }

        def runner() -> None:
            journal = RunJournal.for_kind("classify")
            try:
                with journal.run(
                    creator=creator,
                    total=len(pending),
                    force=bool(use_force),
                    include_videos=bool(include_videos),
                    model=MODEL_NAME,
                ) as run:
                    for item in pending:
                        if self._cancel.is_set():
                            with self._job_lock:
                                self._status["cancelled"] = True
                                self._status["error"] = "cancelled"
                            run.event("cancelled", completed=self._status["completed"])
                            break
                        rel = item["rel_path"]
                        full = item["full_path"]
                        with self._job_lock:
                            self._status["current"] = rel
                        if not os.path.isfile(full):
                            with self._job_lock:
                                self._status["failed"] += 1
                                self._status["completed"] += 1
                                self._record_score(-1)
                            run.item(path=rel, ok=False, reason="missing_file")
                            continue

                        started = time.monotonic()
                        try:
                            verdict = classify_media(full)
                        except Exception as exc:
                            log.warning("classify failed for %s: %s", rel, exc)
                            with self._job_lock:
                                self._status["failed"] += 1
                                self._status["completed"] += 1
                                self._record_score(-1)
                            run.item(path=rel, ok=False, reason=str(exc)[:200])
                            continue
                        elapsed_ms = int((time.monotonic() - started) * 1000)

                        if verdict.ok:
                            persist_glam_score(rel, verdict, full_path=full)
                            keep = bool(verdict.matches_keep())
                            score = int(verdict.glam_score)
                            if score < 0:
                                score = int(verdict.compute_glam_score())
                            with self._job_lock:
                                self._status["completed"] += 1
                                if keep:
                                    self._status["kept"] += 1
                                else:
                                    self._status["rejected"] += 1
                                self._record_score(score)
                            run.item(
                                path=rel,
                                ok=True,
                                glam=score,
                                tier=verdict.exposure_tier,
                                keep=keep,
                                source=verdict.source,
                                prompt_version=verdict.prompt_version,
                                calls=(verdict.evidence or {}).get("frames_sent_to_vision"),
                                ms=elapsed_ms,
                            )
                        else:
                            # glam_score stays -1, but record *why* so the row is
                            # findable via list_glam_errors() instead of looking
                            # identical to something never attempted.
                            persist_glam_score(rel, verdict, full_path=full)
                            with self._job_lock:
                                self._status["failed"] += 1
                                self._status["completed"] += 1
                                self._record_score(-1)
                            run.item(
                                path=rel,
                                ok=False,
                                reason=verdict.error[:200],
                                ms=elapsed_ms,
                            )

                    # Distribution is the signal that a prompt change collapsed
                    # the output space; keep it per run so drift is visible.
                    with self._job_lock:
                        run.summary(
                            score_hist=dict(self._status.get("score_hist") or {}),
                            top_score_share=self._status.get("top_score_share"),
                            unscored_rate=self._status.get("unscored_rate"),
                            kept=self._status.get("kept"),
                            rejected=self._status.get("rejected"),
                        )
            except Exception as exc:
                with self._job_lock:
                    self._status["error"] = str(exc)
                log.exception("classify job crashed")
            finally:
                LEASES.release(LEASE_OWNER)
                with self._job_lock:
                    self._status["running"] = False
                    self._status["finished_at"] = datetime.now(timezone.utc).isoformat()
                    self._status["current"] = ""

        threading.Thread(target=runner, daemon=True, name="classify-job").start()
        return {
            "status": "started",
            "pending": len(pending),
            "creator": creator,
            "force": bool(use_force),
            "include_videos": bool(include_videos),
        }
