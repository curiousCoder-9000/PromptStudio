"""Persistent serial creator scrape job queue (ad-hoc handles, not following bulk)."""

from __future__ import annotations

import copy
import json
import os
import random
import string
import threading
from datetime import date, datetime, timezone
from typing import Any, Dict, List, Optional

from promptstudio.config import (
    CREATOR_SCRAPE_HISTORY_MAX,
    CREATOR_SCRAPE_MAX_PENDING,
    CREATOR_SCRAPE_QUEUE_FILE,
)
from promptstudio.storage.atomic import atomic_write_json
from promptstudio.storage.db import DEFAULT_SOURCE


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalize_username(username: str) -> str:
    return (username or "").lstrip("@").strip().lower()


def normalize_source(source: str) -> str:
    return (source or DEFAULT_SOURCE).strip().lower() or DEFAULT_SOURCE


def job_key(job: Dict[str, Any]) -> tuple:
    """Queue identity of a job.

    Keyed on (source, username) rather than username alone: the same handle can
    legitimately be queued for Instagram and X at once, and they are different
    accounts landing in different folders.
    """
    return (
        normalize_source(job.get("source") or DEFAULT_SOURCE),
        normalize_username(job.get("username") or ""),
    )


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
        # Jobs finished per lane *in this process*. Deliberately in memory and
        # not in the file: the pacing pauses it drives exist to space out
        # consecutive jobs, so the first job after a restart has nothing to be
        # spaced from. Per-lane because the cooldowns are per-platform anti-ban
        # pacing — a Reddit job finishing used to trigger Instagram's 5-15
        # minute batch pause off a single shared counter.
        self._finished_session: Dict[str, int] = {}

    @classmethod
    def get(cls) -> "CreatorScrapeQueue":
        if cls._instance is None:
            with cls._init_lock:
                if cls._instance is None:
                    cls._instance = CreatorScrapeQueue()
        return cls._instance

    @staticmethod
    def _default_lane() -> Dict[str, Any]:
        return {
            "paused": False,
            "pause_reason": "",
            "paused_at": None,
            "stats": {
                "completed_today": 0,
                "downloaded_today": 0,
                "errors_today": 0,
            },
        }

    def _default(self) -> Dict[str, Any]:
        return {
            "version": 2,
            "day_key": date.today().isoformat(),
            "lanes": {},
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
            if not isinstance(base["jobs"], list):
                base["jobs"] = []
            if not isinstance(base["history"], list):
                base["history"] = []
            # Queue files written before multi-source support have no `source`.
            # Every job in them is Instagram — make that explicit on load.
            for bucket in ("jobs", "history"):
                for job in base.get(bucket) or []:
                    if isinstance(job, dict) and not job.get("source"):
                        job["source"] = DEFAULT_SOURCE
            self._migrate_lanes(base, data)
            return base
        except Exception:
            return self._default()

    def _migrate_lanes(self, base: Dict[str, Any], raw: Dict[str, Any]) -> None:
        """Fold a v1 file's global pause/stats into the Instagram lane.

        v1 had one `paused` flag and one `stats` block for the whole queue,
        because there was one worker. Everything in such a file was Instagram
        (see the source backfill above), so that is the lane it belongs to.
        Same shape as that backfill: in place, on load, no separate step.
        """
        lanes = base.get("lanes")
        if not isinstance(lanes, dict):
            lanes = {}
        base["lanes"] = {
            name: {**self._default_lane(), **lane}
            for name, lane in lanes.items()
            if isinstance(lane, dict)
        }
        if lanes:
            base.pop("paused", None)
            base.pop("pause_reason", None)
            base.pop("paused_at", None)
            base.pop("stats", None)
            return

        legacy = self._default_lane()
        legacy["paused"] = bool(raw.get("paused"))
        legacy["pause_reason"] = str(raw.get("pause_reason") or "")
        legacy["paused_at"] = raw.get("paused_at")
        if isinstance(raw.get("stats"), dict):
            legacy["stats"] = {**legacy["stats"], **raw["stats"]}
        base["lanes"] = {DEFAULT_SOURCE: legacy}
        base["version"] = 2
        for key in ("paused", "pause_reason", "paused_at", "stats"):
            base.pop(key, None)

    def _lane(self, source: str) -> Dict[str, Any]:
        """The lane record for `source`, created on first use.

        Caller MUST hold self._lock. Lanes are created lazily rather than
        seeded from the registry so that a queue file never carries lanes for
        sources this build does not know about.
        """
        lanes = self._data.setdefault("lanes", {})
        key = normalize_source(source)
        lane = lanes.get(key)
        if not isinstance(lane, dict):
            lane = self._default_lane()
            lanes[key] = lane
        return lane

    def _lane_names(self) -> List[str]:
        """Every lane with state, plus every lane with queued work."""
        with self._lock:
            names = set(self._data.get("lanes") or {})
            names.update(self._queued_sources_unlocked())
        return sorted(names)

    def queued_sources(self) -> List[str]:
        """Sources named by pending or running jobs.

        Includes sources this build has no adapter for. The dispatcher needs
        them so such a job drains and fails with a clear error rather than
        sitting pending forever.
        """
        with self._lock:
            return sorted(self._queued_sources_unlocked())

    def _queued_sources_unlocked(self) -> set:
        return {
            normalize_source(job.get("source"))
            for job in (self._data.get("jobs") or [])
            if isinstance(job, dict) and job.get("status") in ("pending", "running")
        }

    def _save(self) -> None:
        """Caller MUST hold self._lock. Atomic replace."""
        atomic_write_json(self.path, self._data)

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
            if self._data.get("day_key") == today:
                return
            self._data["day_key"] = today
            for lane in (self._data.get("lanes") or {}).values():
                if isinstance(lane, dict):
                    lane["stats"] = {
                        "completed_today": 0,
                        "downloaded_today": 0,
                        "errors_today": 0,
                    }
            self._save()

    def is_paused(self, source: Optional[str] = None) -> bool:
        """Is this lane paused? With no source, is *every* lane paused?

        The all-lanes reading is what keeps legacy callers honest: they asked
        "is the queue stopped", and it is only stopped if nothing can run.
        """
        with self._lock:
            if source is not None:
                return bool(self._lane(source).get("paused"))
            lanes = self._data.get("lanes") or {}
            if not lanes:
                return False
            return all(bool(lane.get("paused")) for lane in lanes.values())

    def pause(
        self,
        reason: str = "",
        *,
        source: Optional[str] = None,
        persist: bool = True,
    ) -> None:
        """Pause one lane, or every known lane when `source` is None.

        Auto-pauses (an expired X cookie, an Instagram abuse signal) always
        name their lane: the failure is platform-specific, and stopping the
        other platforms because one set of credentials expired is the behaviour
        lanes exist to remove.
        """
        with self._lock:
            if source is not None:
                targets = [self._lane(source)]
            else:
                for name in set(self._lane_names()) | {DEFAULT_SOURCE}:
                    self._lane(name)
                targets = list((self._data.get("lanes") or {}).values())
            for lane in targets:
                lane["paused"] = True
                lane["pause_reason"] = reason or "Paused"
                lane["paused_at"] = _utc_now()
            if persist:
                self._save()

    def resume(self, source: Optional[str] = None) -> None:
        with self._lock:
            if source is not None:
                targets = [self._lane(source)]
            else:
                targets = list((self._data.get("lanes") or {}).values())
            for lane in targets:
                lane["paused"] = False
                lane["pause_reason"] = ""
                lane["paused_at"] = None
            self._save()

    def pending_count(self, source: Optional[str] = None) -> int:
        with self._lock:
            return self._pending_count_unlocked(source)

    def running_job(self, source: Optional[str] = None) -> Optional[Dict[str, Any]]:
        with self._lock:
            for j in self._running_unlocked(source):
                return copy.deepcopy(j)
            return None

    def running_jobs(self) -> List[Dict[str, Any]]:
        """Every running job — one per lane at most."""
        with self._lock:
            return [copy.deepcopy(j) for j in self._running_unlocked(None)]

    def _running_unlocked(self, source: Optional[str]) -> List[Dict[str, Any]]:
        wanted = normalize_source(source) if source is not None else None
        out = []
        for j in self._data.get("jobs") or []:
            if not isinstance(j, dict) or j.get("status") != "running":
                continue
            if wanted is not None and job_key(j)[0] != wanted:
                continue
            out.append(j)
        return out

    def find_active_by_username(
        self,
        username: str,
        source: str = DEFAULT_SOURCE,
    ) -> Optional[Dict[str, Any]]:
        key = (normalize_source(source), normalize_username(username))
        with self._lock:
            for j in self._data.get("jobs") or []:
                if not isinstance(j, dict):
                    continue
                if j.get("status") not in ("pending", "running"):
                    continue
                if job_key(j) == key:
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
        source: str = DEFAULT_SOURCE,
    ) -> Dict[str, Any]:
        """
        Enqueue a job. Returns:
          {status: started|queued|already_pending|already_running, job, position, queue_depth}
        Or raises ValueError for cap / invalid.

        Option semantics (including the latest → full+deep upgrade) live in
        `ScrapeOptions.normalize`; this stores the result so the drain does not
        have to re-derive it.
        """
        from promptstudio.scraping.sources.base import ScrapeOptions

        key = normalize_username(username)
        if not key:
            raise ValueError("username required")
        src = normalize_source(source)
        ident = (src, key)
        opts = ScrapeOptions.normalize(
            mode,
            deep=deep,
            max_posts=max_posts,
            include_videos=include_videos,
            catch_up_only=catch_up_only,
            strict=True,
        )

        with self._lock:
            existing = None
            for j in self._data.get("jobs") or []:
                if not isinstance(j, dict):
                    continue
                if j.get("status") not in ("pending", "running"):
                    continue
                if job_key(j) == ident:
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

            job = {
                "id": _new_job_id(),
                "username": key,
                "source": src,
                "mode": opts.mode,
                "deep": opts.deep,
                "max_posts": opts.max_posts,
                "include_videos": opts.include_videos,
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
                "requested_mode": opts.requested_mode,
                "upgraded_from_latest": opts.upgraded_from_latest,
                "catch_up_only": opts.catch_up_only,
            }
            jobs: List[Any] = self._data.setdefault("jobs", [])
            jobs.append(job)
            self._save()
            return {
                "status": "queued",
                "job": copy.deepcopy(job),
                "position": self._position_unlocked(job["id"]),
                "queue_depth": self._pending_count_unlocked(),
                "upgraded_from_latest": opts.upgraded_from_latest,
            }

    def _pending_count_unlocked(self, source: Optional[str] = None) -> int:
        return len(self._ordered_pending_unlocked(source))

    def _position_unlocked(self, job_id: Optional[str]) -> int:
        """1-based position of a job *within its own lane*.

        Lane-scoped because that is what the number now means to the user: with
        three lanes draining in parallel, "3rd in the global list" predicts
        nothing about when a job starts.
        """
        if not job_id:
            return 0
        for job in self._data.get("jobs") or []:
            if isinstance(job, dict) and job.get("id") == job_id:
                ordered = self._ordered_pending_unlocked(job_key(job)[0])
                for i, j in enumerate(ordered, start=1):
                    if j.get("id") == job_id:
                        return i
                return 0
        return 0

    def _ordered_pending_unlocked(
        self, source: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        wanted = normalize_source(source) if source is not None else None
        pending = [
            j
            for j in (self._data.get("jobs") or [])
            if isinstance(j, dict)
            and j.get("status") == "pending"
            and (wanted is None or job_key(j)[0] == wanted)
        ]
        pending.sort(
            key=lambda j: (
                -int(j.get("priority") or 0),
                str(j.get("created_at") or ""),
            )
        )
        return pending

    def peek_next(self, source: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """Head of one lane, or of the whole queue when `source` is None."""
        with self._lock:
            ordered = self._ordered_pending_unlocked(source)
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
            lane_name = job_key(target)[0]
            self._finished_session[lane_name] = (
                self._finished_session.get(lane_name, 0) + 1
            )
            self._save()

    def record_stats_from_result(
        self, result: Any, *, source: str = DEFAULT_SOURCE
    ) -> None:
        self._roll_day_if_needed()
        with self._lock:
            stats = self._lane(source).setdefault("stats", {})
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

    def cancel_all_pending(self, source: Optional[str] = None) -> int:
        with self._lock:
            ids = [
                j.get("id") for j in self._ordered_pending_unlocked(source)
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

    def lane_finished_count(self, source: str) -> int:
        with self._lock:
            return int(self._finished_session.get(normalize_source(source), 0))

    def should_account_pause_before(
        self, job_id: str, *, source: str = DEFAULT_SOURCE
    ) -> bool:
        """Pause between jobs when a previous job *in this lane* finished here.

        Per-lane, not global: the cooldown is anti-ban pacing for one platform,
        so a Reddit job completing is not a reason to slow Instagram down (nor
        the reverse).
        """
        return self.lane_finished_count(source) > 0

    def should_batch_pause(self, *, source: str = DEFAULT_SOURCE) -> bool:
        from promptstudio.config import batch_pause_every_for

        every = batch_pause_every_for(source)
        if every <= 0:
            return False
        finished = self.lane_finished_count(source)
        return finished > 0 and finished % every == 0

    def status_snapshot(self) -> Dict[str, Any]:
        """Queue state for `/api/scrape/status`.

        `lanes` is the real shape. The flat keys beside it are the union across
        lanes, kept so a client written against the single-worker API degrades
        sensibly instead of breaking: `paused` means nothing can run, and
        `running_job` is whichever lane started first.
        """
        self._roll_day_if_needed()
        with self._lock:
            pending = self._ordered_pending_unlocked()
            running_all = [copy.deepcopy(j) for j in self._running_unlocked(None)]
            lanes = self._lane_snapshots_unlocked()
            first_running = (
                min(running_all, key=lambda j: str(j.get("started_at") or ""))
                if running_all
                else None
            )
            all_paused = bool(lanes) and all(v["paused"] for v in lanes.values())
            reasons = [v["pause_reason"] for v in lanes.values() if v["pause_reason"]]
            return {
                "lanes": lanes,
                "paused": all_paused,
                "pause_reason": reasons[0] if reasons else "",
                "stats": self._merged_stats_unlocked(),
                "pending": copy.deepcopy(pending),
                "pending_count": len(pending),
                "running_job": first_running,
                "running_jobs": running_all,
                "history": copy.deepcopy((self._data.get("history") or [])[:20]),
                "day_key": self._data.get("day_key"),
            }

    def _lane_snapshots_unlocked(self) -> Dict[str, Dict[str, Any]]:
        out: Dict[str, Dict[str, Any]] = {}
        names = set(self._data.get("lanes") or {})
        for job in self._data.get("jobs") or []:
            if isinstance(job, dict) and job.get("status") in ("pending", "running"):
                names.add(job_key(job)[0])
        for name in sorted(names):
            lane = self._lane(name)
            pending = self._ordered_pending_unlocked(name)
            running = self._running_unlocked(name)
            out[name] = {
                "source": name,
                "paused": bool(lane.get("paused")),
                "pause_reason": lane.get("pause_reason") or "",
                "paused_at": lane.get("paused_at"),
                "stats": copy.deepcopy(lane.get("stats") or {}),
                "pending": copy.deepcopy(pending),
                "pending_count": len(pending),
                "running_job": copy.deepcopy(running[0]) if running else None,
                "depth": len(pending) + len(running),
                "finished_session": int(self._finished_session.get(name, 0)),
            }
        return out

    def _merged_stats_unlocked(self) -> Dict[str, int]:
        merged = {"completed_today": 0, "downloaded_today": 0, "errors_today": 0}
        for lane in (self._data.get("lanes") or {}).values():
            for key in merged:
                merged[key] += int((lane.get("stats") or {}).get(key) or 0)
        return merged

    def summary_for_sync_status(self) -> Dict[str, Any]:
        snap = self.status_snapshot()
        running = snap.get("running_job") or {}
        lanes = snap.get("lanes") or {}
        return {
            "depth": int(snap.get("pending_count") or 0)
            + len(snap.get("running_jobs") or []),
            "pending_count": int(snap.get("pending_count") or 0),
            "paused": bool(snap.get("paused")),
            "pause_reason": snap.get("pause_reason") or "",
            "current_username": running.get("username"),
            "current_source": running.get("source") or (DEFAULT_SOURCE if running else None),
            "current_job_id": running.get("id"),
            "lanes": {
                name: {
                    "depth": lane["depth"],
                    "pending_count": lane["pending_count"],
                    "paused": lane["paused"],
                    "pause_reason": lane["pause_reason"],
                    "current_username": (lane["running_job"] or {}).get("username"),
                    "current_job_id": (lane["running_job"] or {}).get("id"),
                }
                for name, lane in lanes.items()
            },
            "enabled": True,
        }
