"""Background per-creator glam / outfit classification job."""

from __future__ import annotations

import os
import threading
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
from promptstudio.scraping.outfit_classifier import (
    classify_media,
    ollama_reachable,
    persist_glam_score,
)
from promptstudio.storage.db import ArchiveIndex, normalize_rel_path


def _vision_busy_elsewhere() -> bool:
    """True if batch prompt analysis is currently using Ollama."""
    try:
        from promptstudio.prompts.batch import BatchPromptManager

        return BatchPromptManager.get().is_running()
    except Exception:
        return False


class ClassifyJobManager:
    """Singleton background job: classify unscored media for one creator."""

    _instance: Optional["ClassifyJobManager"] = None
    _lock = threading.Lock()

    def __init__(self) -> None:
        self._job_lock = threading.Lock()
        self._cancel = threading.Event()
        self._status: Dict[str, Any] = self._idle_status()

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
            return dict(self._status)

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
    ) -> List[Dict[str, Any]]:
        """List media paths for creator that still need classification."""
        creator = (creator or "").strip().lstrip("@")
        if not creator:
            return []
        if creator in EXCLUDED_FOLDERS or creator.startswith(".") or creator.startswith("_"):
            return []

        folder = os.path.join(os.path.expanduser(SAVED_DIR), creator)
        if not os.path.isdir(folder):
            return []

        exts = MEDIA_EXTENSIONS if include_videos else IMAGE_EXTENSIONS
        index = ArchiveIndex.get()
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
                if score is not None and int(score) >= 0:
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

        if _vision_busy_elsewhere():
            return {
                "status": "busy",
                "message": "Prompt batch is using the vision model — wait or cancel it first",
            }

        # force overrides only_unscored
        use_force = bool(force) or not only_unscored
        pending = self.list_pending(
            creator,
            force=use_force,
            include_videos=include_videos,
            limit=limit,
        )
        if not pending:
            return {"status": "nothing_to_do", "pending": 0, "creator": creator}

        with self._job_lock:
            if self._status.get("running"):
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
            }

        def runner() -> None:
            try:
                for item in pending:
                    if self._cancel.is_set():
                        with self._job_lock:
                            self._status["cancelled"] = True
                            self._status["error"] = "cancelled"
                        break
                    rel = item["rel_path"]
                    full = item["full_path"]
                    with self._job_lock:
                        self._status["current"] = rel
                    if not os.path.isfile(full):
                        with self._job_lock:
                            self._status["failed"] += 1
                            self._status["completed"] += 1
                        continue
                    try:
                        verdict = classify_media(full)
                    except Exception as exc:
                        print(f"Classify job error {rel}: {exc}")
                        with self._job_lock:
                            self._status["failed"] += 1
                            self._status["completed"] += 1
                        continue

                    if verdict.ok:
                        persist_glam_score(rel, verdict, full_path=full)
                        keep = bool(verdict.matches_keep())
                        with self._job_lock:
                            self._status["completed"] += 1
                            if keep:
                                self._status["kept"] += 1
                            else:
                                self._status["rejected"] += 1
                    else:
                        # Leave glam_score as -1 (unscored / retry later)
                        with self._job_lock:
                            self._status["failed"] += 1
                            self._status["completed"] += 1
            except Exception as exc:
                with self._job_lock:
                    self._status["error"] = str(exc)
                print(f"Classify job crashed: {exc}")
            finally:
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
