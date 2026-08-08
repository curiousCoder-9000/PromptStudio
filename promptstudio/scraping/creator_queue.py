"""Persistent serial creator scrape job queue (ad-hoc handles, not following bulk)."""

from __future__ import annotations

import copy
import json
import os
import random
import string
import tempfile
import threading
from datetime import date, datetime, timezone
from typing import Any, Dict, List, Optional

from promptstudio.config import (
    CREATOR_SCRAPE_HISTORY_MAX,
    CREATOR_SCRAPE_MAX_PENDING,
    CREATOR_SCRAPE_QUEUE_FILE,
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalize_username(username: str) -> str:
    return (username or "").lstrip("@").strip().lower()


def _new_job_id() -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    suffix = "".join(random.choices(string.ascii_lowercase + string.digits, k=4))
    return f"csq_{stamp}_{suffix}"


class CreatorScrapeQueue:
    """Singleton FIFO/priority queue of creator full/bounded scrape jobs."""

    _instance: Optional["CreatorScrapeQueue"] = None
    _init_lock = threading.Lock()

    def __init__(self, path: str = CREATOR_SCRAPE_QUEUE_FILE) -> None:
        self.path = path
        self._lock = threading.RLock()
        self._data: Dict[str, Any] = self._load()
        self._recover_interrupted_jobs()
        self._roll_day_if_needed()
        self._last_finished_at: Optional[str] = None
        self._jobs_finished_session = 0

    @classmethod
    def get(cls) -> "CreatorScrapeQueue":
        if cls._instance is None:
            with cls._init_lock:
                if cls._instance is None:
                    cls._instance = CreatorScrapeQueue()
        return cls._instance

    def _default(self) -> Dict[str, Any]:
        return {
            "version": 1,
            "paused": False,
            "pause_reason": "",
            "paused_at": None,
            "day_key": date.today().isoformat(),
            "stats": {
                "completed_today": 0,
                "downloaded_today": 0,
                "errors_today": 0,
            },
            "jobs": [],
            "history": [],
        }

    def _load(self) -> Dict[str, Any]:
        if not os.path.isfile(self.path):
            return self._default()
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, dict):
                return self._default()
            base = self._default()
            base.update(data)
            base.setdefault("jobs", [])
            base.setdefault("history", [])
            base.setdefault("stats", self._default()["stats"])
            if not isinstance(base["jobs"], list):
                base["jobs"] = []
            if not isinstance(base["history"], list):
                base["history"] = []
            if not isinstance(base["stats"], dict):
                base["stats"] = self._default()["stats"]
            return base
        except Exception:
            return self._default()

    def _save(self) -> None:
        """Caller MUST hold self._lock. Atomic replace."""
        dir_name = os.path.dirname(self.path) or "."
        os.makedirs(dir_name, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=".csq_", suffix=".tmp", dir=dir_name)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(self._data, f, indent=2, ensure_ascii=False)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, self.path)
        except Exception:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    def _recover_interrupted_jobs(self) -> None:
        with self._lock:
            changed = False
            for job in self._data.get("jobs") or []:
                if not isinstance(job, dict):
                    continue
                if job.get("status") == "running":
                    job["status"] = "pending"
                    job["started_at"] = None
                    note = "Interrupted by server restart"
                    prev = (job.get("error") or "").strip()
                    job["error"] = f"{prev}; {note}" if prev else note
                    job["cancel_requested"] = False
                    changed = True
            if changed:
                self._save()

    def _roll_day_if_needed(self) -> None:
        today = date.today().isoformat()
        with self._lock:
            if self._data.get("day_key") != today:
                self._data["day_key"] = today
                self._data["stats"] = {
                    "completed_today": 0,
                    "downloaded_today": 0,
                    "errors_today": 0,
                }
                self._save()

    def is_paused(self) -> bool:
        with self._lock:
            return bool(self._data.get("paused"))

    def pause(self, reason: str = "", *, persist: bool = True) -> None:
        with self._lock:
            self._data["paused"] = True
            self._data["pause_reason"] = reason or "Paused"
            self._data["paused_at"] = _utc_now()
            if persist:
                self._save()

    def resume(self) -> None:
        with self._lock:
            self._data["paused"] = False
            self._data["pause_reason"] = ""
            self._data["paused_at"] = None
            self._save()

    def pending_count(self) -> int:
        with self._lock:
            return sum(
                1
                for j in (self._data.get("jobs") or [])
                if isinstance(j, dict) and j.get("status") == "pending"
            )

    def running_job(self) -> Optional[Dict[str, Any]]:
        with self._lock:
            for j in self._data.get("jobs") or []:
                if isinstance(j, dict) and j.get("status") == "running":
                    return copy.deepcopy(j)
            return None

    def find_active_by_username(self, username: str) -> Optional[Dict[str, Any]]:
        key = normalize_username(username)
        with self._lock:
            for j in self._data.get("jobs") or []:
                if not isinstance(j, dict):
                    continue
                if j.get("status") not in ("pending", "running"):
                    continue
                if normalize_username(j.get("username") or "") == key:
                    return copy.deepcopy(j)
            return None

    def enqueue(
        self,
        username: str,
        *,
        mode: str = "full",
        deep: bool = True,
        max_posts: Optional[int] = None,
        include_videos: bool = True,
        priority: int = 0,
        folder_name: str = "",
        folder_created: bool = False,
        catch_up_only: bool = False,
    ) -> Dict[str, Any]:
        """
        Enqueue a job. Returns:
          {status: started|queued|already_pending|already_running, job, position, queue_depth}
        Or raises ValueError for cap / invalid.

        Safety: mode=latest without catch_up_only is upgraded to full+deep
        (walk entire feed for all missing posts). Old latest+max_posts=50 left
        partial glam archives incomplete (Mikayla / roxeuoon ceiling bug).
        Pass catch_up_only=True to keep true catch-up + optional max_posts ceiling.
        """
        key = normalize_username(username)
        if not key:
            raise ValueError("username required")
        mode = (mode or "full").strip().lower()
        if mode not in ("full", "bounded", "latest"):
            raise ValueError("mode must be full, bounded, or latest")

        requested_mode = mode
        upgraded_from_latest = False
        # Default product path: never stop after 50 newest missing only
        if mode == "latest" and not bool(catch_up_only):
            mode = "full"
            deep = True
            max_posts = None
            upgraded_from_latest = True

        with self._lock:
            existing = None
            for j in self._data.get("jobs") or []:
                if not isinstance(j, dict):
                    continue
                if j.get("status") not in ("pending", "running"):
                    continue
                if normalize_username(j.get("username") or "") == key:
                    existing = j
                    break
            if existing:
                st = existing.get("status")
                return {
                    "status": "already_running" if st == "running" else "already_pending",
                    "job": copy.deepcopy(existing),
                    "position": self._position_unlocked(existing.get("id")),
                    "queue_depth": self._pending_count_unlocked(),
                }

            if self._pending_count_unlocked() >= CREATOR_SCRAPE_MAX_PENDING:
                raise ValueError(
                    f"Queue full (max {CREATOR_SCRAPE_MAX_PENDING} pending)"
                )

            if mode == "latest":
                # Explicit catch_up_only path
                job_deep = False
                if max_posts is None:
                    from promptstudio.config import DEFAULT_MAX_POSTS_PER_CREATOR

                    max_posts = DEFAULT_MAX_POSTS_PER_CREATOR
            elif mode == "full":
                job_deep = bool(deep)
                # full+deep: no low ceiling unless caller set one
                if job_deep and (max_posts is not None and int(max_posts) <= 0):
                    max_posts = None
            else:
                job_deep = False

            job = {
                "id": _new_job_id(),
                "username": key,
                "mode": mode,
                "deep": job_deep,
                "max_posts": max_posts,
                "include_videos": bool(include_videos),
                "priority": int(priority or 0),
                "status": "pending",
                "created_at": _utc_now(),
                "started_at": None,
                "finished_at": None,
                "folder_created": bool(folder_created),
                "folder_name": folder_name or key,
                "error": "",
                "result": None,
                "stop_reason": None,
                "cancel_requested": False,
                "requested_mode": requested_mode,
                "upgraded_from_latest": upgraded_from_latest,
                "catch_up_only": bool(catch_up_only) and requested_mode == "latest",
            }
            jobs: List[Any] = self._data.setdefault("jobs", [])
            jobs.append(job)
            self._save()
            return {
                "status": "queued",
                "job": copy.deepcopy(job),
                "position": self._position_unlocked(job["id"]),
                "queue_depth": self._pending_count_unlocked(),
                "upgraded_from_latest": upgraded_from_latest,
            }

    def _pending_count_unlocked(self) -> int:
        return sum(
            1
            for j in (self._data.get("jobs") or [])
            if isinstance(j, dict) and j.get("status") == "pending"
        )

    def _position_unlocked(self, job_id: Optional[str]) -> int:
        if not job_id:
            return 0
        ordered = self._ordered_pending_unlocked()
        for i, j in enumerate(ordered, start=1):
            if j.get("id") == job_id:
                return i
        return 0

    def _ordered_pending_unlocked(self) -> List[Dict[str, Any]]:
        pending = [
            j
            for j in (self._data.get("jobs") or [])
            if isinstance(j, dict) and j.get("status") == "pending"
        ]
        pending.sort(
            key=lambda j: (
                -int(j.get("priority") or 0),
                str(j.get("created_at") or ""),
            )
        )
        return pending

    def peek_next(self) -> Optional[Dict[str, Any]]:
        with self._lock:
            ordered = self._ordered_pending_unlocked()
            return copy.deepcopy(ordered[0]) if ordered else None

    def mark_running(self, job_id: str) -> bool:
        with self._lock:
            for j in self._data.get("jobs") or []:
                if isinstance(j, dict) and j.get("id") == job_id:
                    j["status"] = "running"
                    j["started_at"] = _utc_now()
                    j["error"] = ""
                    self._save()
                    return True
            return False

    def finalize_job(
        self,
        job_id: str,
        *,
        status: str,
        error: str = "",
        result: Optional[dict] = None,
        stop_reason: str = "",
    ) -> None:
        if status not in ("done", "error", "cancelled"):
            raise ValueError(f"Invalid terminal status: {status}")
        with self._lock:
            jobs: List[Any] = self._data.setdefault("jobs", [])
            target = None
            for j in jobs:
                if isinstance(j, dict) and j.get("id") == job_id:
                    target = j
                    break
            if not target:
                return
            target["status"] = status
            target["finished_at"] = _utc_now()
            target["error"] = error or ""
            target["result"] = result
            target["stop_reason"] = stop_reason or None
            target["cancel_requested"] = False
            # Move to history, remove from active jobs
            hist = copy.deepcopy(target)
            history: List[Any] = self._data.setdefault("history", [])
            history.insert(0, hist)
            max_h = max(1, int(CREATOR_SCRAPE_HISTORY_MAX))
            self._data["history"] = history[:max_h]
            self._data["jobs"] = [
                j for j in jobs if not (isinstance(j, dict) and j.get("id") == job_id)
            ]
            self._last_finished_at = target["finished_at"]
            self._jobs_finished_session += 1
            self._save()

    def record_stats_from_result(self, result: Any) -> None:
        self._roll_day_if_needed()
        with self._lock:
            stats = self._data.setdefault("stats", {})
            stats["completed_today"] = int(stats.get("completed_today") or 0) + 1
            dl = int(getattr(result, "downloaded", 0) or 0)
            if isinstance(result, dict):
                dl = int(result.get("downloaded") or 0)
            stats["downloaded_today"] = int(stats.get("downloaded_today") or 0) + dl
            errs = int(getattr(result, "errors", 0) or 0)
            if isinstance(result, dict):
                errs = int(result.get("errors") or 0)
            if errs:
                stats["errors_today"] = int(stats.get("errors_today") or 0) + 1
            self._save()

    def cancel_pending(self, job_id: str) -> bool:
        with self._lock:
            for j in self._data.get("jobs") or []:
                if isinstance(j, dict) and j.get("id") == job_id and j.get("status") == "pending":
                    j["status"] = "cancelled"
                    j["finished_at"] = _utc_now()
                    j["stop_reason"] = "cancel"
                    j["error"] = "Cancelled while pending"
                    hist = copy.deepcopy(j)
                    history: List[Any] = self._data.setdefault("history", [])
                    history.insert(0, hist)
                    self._data["history"] = history[: max(1, int(CREATOR_SCRAPE_HISTORY_MAX))]
                    self._data["jobs"] = [
                        x
                        for x in self._data["jobs"]
                        if not (isinstance(x, dict) and x.get("id") == job_id)
                    ]
                    self._save()
                    return True
            return False

    def cancel_all_pending(self) -> int:
        with self._lock:
            ids = [
                j.get("id")
                for j in (self._data.get("jobs") or [])
                if isinstance(j, dict) and j.get("status") == "pending"
            ]
        n = 0
        for jid in ids:
            if jid and self.cancel_pending(str(jid)):
                n += 1
        return n

    def get_job(self, job_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            for j in self._data.get("jobs") or []:
                if isinstance(j, dict) and j.get("id") == job_id:
                    return copy.deepcopy(j)
            for j in self._data.get("history") or []:
                if isinstance(j, dict) and j.get("id") == job_id:
                    return copy.deepcopy(j)
            return None

    def should_account_pause_before(self, job_id: str) -> bool:
        """Pause between jobs when a previous job finished in this process."""
        return self._jobs_finished_session > 0

    def should_batch_pause(self) -> bool:
        from promptstudio.config import BATCH_PAUSE_EVERY

        if BATCH_PAUSE_EVERY <= 0:
            return False
        return (
            self._jobs_finished_session > 0
            and self._jobs_finished_session % BATCH_PAUSE_EVERY == 0
        )

    def status_snapshot(self) -> Dict[str, Any]:
        self._roll_day_if_needed()
        with self._lock:
            pending = self._ordered_pending_unlocked()
            running = None
            for j in self._data.get("jobs") or []:
                if isinstance(j, dict) and j.get("status") == "running":
                    running = copy.deepcopy(j)
                    break
            return {
                "paused": bool(self._data.get("paused")),
                "pause_reason": self._data.get("pause_reason") or "",
                "paused_at": self._data.get("paused_at"),
                "stats": copy.deepcopy(self._data.get("stats") or {}),
                "pending": copy.deepcopy(pending),
                "pending_count": len(pending),
                "running_job": running,
                "history": copy.deepcopy((self._data.get("history") or [])[:20]),
                "day_key": self._data.get("day_key"),
            }

    def summary_for_sync_status(self) -> Dict[str, Any]:
        snap = self.status_snapshot()
        running = snap.get("running_job") or {}
        return {
            "depth": int(snap.get("pending_count") or 0)
            + (1 if running else 0),
            "pending_count": int(snap.get("pending_count") or 0),
            "paused": bool(snap.get("paused")),
            "pause_reason": snap.get("pause_reason") or "",
            "current_username": running.get("username"),
            "current_job_id": running.get("id"),
            "enabled": True,
        }
