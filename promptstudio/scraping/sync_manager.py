"""Background sync job tracking for the HTTP API."""

from __future__ import annotations

import json
import threading
import time
import traceback
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Optional

from promptstudio.config import (
    ACCOUNT_PAUSE_MAX_SEC,
    ACCOUNT_PAUSE_MIN_SEC,
    AUTO_DRAIN_ON_START,
    AUTO_DRAIN_ON_START_DELAY_SEC,
    BATCH_PAUSE_MAX_SEC,
    BATCH_PAUSE_MIN_SEC,
    CREATOR_SCRAPE_QUEUE_ENABLED,
    DEFAULT_MAX_POSTS_PER_CREATOR,
    FULL_SCRAPE_MAX_POSTS,
    INCLUDE_VIDEOS_DEFAULT,
    SYNC_STATUS_FILE,
)
from promptstudio.logging_setup import get_logger
from promptstudio.storage.atomic import atomic_write_json

log = get_logger(__name__)


class SyncManager:
    """Runs Instagram sync jobs in background threads with persisted status."""

    _instance: Optional["SyncManager"] = None
    _lock = threading.Lock()

    def __init__(self) -> None:
        self._job_lock = threading.Lock()
        self._cancel = threading.Event()
        self._status: Dict[str, Any] = self._load_status()
        self._recover_stuck_running()
        self._auto_drain_scheduled = False
        if CREATOR_SCRAPE_QUEUE_ENABLED and AUTO_DRAIN_ON_START:
            self._schedule_auto_drain()

    @classmethod
    def get(cls) -> "SyncManager":
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = SyncManager()
        return cls._instance

    def _load_status(self) -> Dict[str, Any]:
        try:
            with open(SYNC_STATUS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                return data
        except Exception:
            pass
        return self._default_status()

    def _default_status(self) -> Dict[str, Any]:
        return {
            "running": False,
            "job_type": None,
            "started_at": None,
            "finished_at": None,
            "progress": "",
            "result": None,
            "error": None,
            "rate_limit_hits": 0,
            "consecutive_rate_limits": 0,
            "last_backoff_sec": 0,
            "cancel_requested": False,
            "scrape_job_id": None,
            "scrape_username": None,
            "scrape_source": None,
        }

    def _recover_stuck_running(self) -> None:
        with self._job_lock:
            if not self._status.get("running"):
                return
            self._status["running"] = False
            self._status["finished_at"] = datetime.now(timezone.utc).isoformat()
            self._status["error"] = "Server restarted"
            self._status["progress"] = "Interrupted by server restart"
            self._status["cancel_requested"] = False
            self._status["scrape_job_id"] = None
            self._status["scrape_username"] = None
            self._status["scrape_source"] = None
            self._save_status()

    def _save_status(self) -> None:
        try:
            # fsync=False: status is rewritten every few seconds and is fully
            # reconstructible, so durability is not worth the barrier.
            atomic_write_json(SYNC_STATUS_FILE, self._status, fsync=False)
        except OSError as e:
            log.debug("sync status write failed: %s", e)

    def get_status(self) -> Dict[str, Any]:
        with self._job_lock:
            status = dict(self._status)
        # Attach creator_queue summary without holding job lock during queue I/O
        if CREATOR_SCRAPE_QUEUE_ENABLED:
            try:
                from promptstudio.scraping.creator_queue import CreatorScrapeQueue

                status["creator_queue"] = CreatorScrapeQueue.get().summary_for_sync_status()
            except Exception:
                status["creator_queue"] = {
                    "depth": 0,
                    "pending_count": 0,
                    "paused": False,
                    "pause_reason": "",
                    "current_username": None,
                    "enabled": True,
                }
        else:
            status["creator_queue"] = {"enabled": False, "depth": 0, "pending_count": 0}
        return status

    def is_running(self) -> bool:
        with self._job_lock:
            return bool(self._status.get("running"))

    def request_cancel(self) -> bool:
        """Request cooperative cancel of the running IG job."""
        if not self.is_running():
            return False
        self._cancel.set()
        with self._job_lock:
            self._status["cancel_requested"] = True
            self._status["progress"] = "Cancel requested..."
            self._save_status()
        return True

    def is_cancel_requested(self) -> bool:
        return self._cancel.is_set()

    def record_rate_limit(self, consecutive: int, backoff_sec: int) -> None:
        with self._job_lock:
            self._status["consecutive_rate_limits"] = consecutive
            self._status["last_backoff_sec"] = backoff_sec
            self._status["rate_limit_hits"] = int(self._status.get("rate_limit_hits") or 0) + 1
            self._status["progress"] = (
                f"Rate limited — waiting {backoff_sec}s (streak {consecutive})"
            )
            self._save_status()

    def _set_scrape_meta(
        self,
        job_id: Optional[str],
        username: Optional[str],
        source: Optional[str] = None,
    ) -> None:
        with self._job_lock:
            self._status["scrape_job_id"] = job_id
            self._status["scrape_username"] = username
            self._status["scrape_source"] = source
            self._save_status()

    def _clear_scrape_meta(self) -> None:
        with self._job_lock:
            self._status["scrape_job_id"] = None
            self._status["scrape_username"] = None
            self._status["scrape_source"] = None
            self._status["cancel_requested"] = False
            self._save_status()

    def start_job(self, job_type: str, fn: Callable[..., Any]) -> bool:
        """Start a background job. Returns False if one is already running."""
        with self._job_lock:
            if self._status.get("running"):
                return False
            self._cancel.clear()
            self._status = {
                "running": True,
                "job_type": job_type,
                "started_at": datetime.now(timezone.utc).isoformat(),
                "finished_at": None,
                "progress": "Starting...",
                "result": None,
                "error": None,
                "rate_limit_hits": 0,
                "consecutive_rate_limits": 0,
                "last_backoff_sec": 0,
                "cancel_requested": False,
                "scrape_job_id": None,
                "scrape_username": None,
                "scrape_source": None,
            }
            self._save_status()

        def runner() -> None:
            logs: list = []

            def log(msg: str) -> None:
                logs.append(msg)
                with self._job_lock:
                    self._status["progress"] = msg
                    self._save_status()

            def on_rate_limit(consecutive: int, backoff_sec: int) -> None:
                self.record_rate_limit(consecutive, backoff_sec)

            try:
                try:
                    result = fn(log, on_rate_limit)
                except TypeError:
                    result = fn(log)
                with self._job_lock:
                    self._status["running"] = False
                    self._status["finished_at"] = datetime.now(timezone.utc).isoformat()
                    aborted = bool(getattr(result, "aborted", False))
                    if isinstance(result, dict):
                        aborted = bool(result.get("aborted"))
                    stop_reason = getattr(result, "stop_reason", None)
                    if isinstance(result, dict):
                        stop_reason = result.get("stop_reason")
                    abort_reason = getattr(result, "abort_reason", "") or ""
                    if isinstance(result, dict):
                        abort_reason = result.get("abort_reason") or ""
                    if aborted:
                        self._status["progress"] = f"Aborted — {abort_reason}".strip()
                    elif stop_reason:
                        self._status["progress"] = f"Complete ({stop_reason})"
                    else:
                        self._status["progress"] = "Complete"
                    self._status["result"] = (
                        result.to_dict() if hasattr(result, "to_dict") else result
                    )
                    if hasattr(result, "rate_limit_hits"):
                        self._status["rate_limit_hits"] = result.rate_limit_hits
                    elif isinstance(result, dict) and "rate_limit_hits" in result:
                        self._status["rate_limit_hits"] = result["rate_limit_hits"]
                    self._status["logs"] = logs[-50:]
                    self._status["cancel_requested"] = False
                    self._status["scrape_job_id"] = None
                    self._status["scrape_username"] = None
                    self._status["scrape_source"] = None
                    self._save_status()
            except Exception as exc:
                with self._job_lock:
                    self._status["running"] = False
                    self._status["finished_at"] = datetime.now(timezone.utc).isoformat()
                    self._status["error"] = str(exc)
                    self._status["progress"] = "Failed"
                    self._status["traceback"] = traceback.format_exc()
                    self._status["cancel_requested"] = False
                    self._status["scrape_job_id"] = None
                    self._status["scrape_username"] = None
                    self._status["scrape_source"] = None
                    self._save_status()
            finally:
                self._cancel.clear()
                # Drain next creator-queue job if allowed
                try:
                    from promptstudio.scraping.creator_queue import CreatorScrapeQueue

                    if CREATOR_SCRAPE_QUEUE_ENABLED and not CreatorScrapeQueue.get().is_paused():
                        # Avoid re-entrancy deadlock: schedule drain on new thread
                        threading.Thread(
                            target=self.try_drain_creator_queue,
                            daemon=True,
                        ).start()
                except Exception:
                    pass

        thread = threading.Thread(target=runner, daemon=True)
        thread.start()
        return True

    def _interruptible_sleep(
        self,
        seconds: float,
        log: Optional[Callable[[str], None]] = None,
        *,
        label: str = "Cooldown",
    ) -> bool:
        """Sleep in 1s slices; return True if cancelled early."""
        end = time.monotonic() + max(0.0, float(seconds))
        while time.monotonic() < end:
            if self._cancel.is_set():
                if log:
                    log(f"{label} cancelled")
                return True
            remaining = end - time.monotonic()
            if log and remaining > 1:
                log(f"{label} — {int(remaining)}s remaining")
            time.sleep(min(1.0, max(0.0, remaining)))
        return False

    def try_drain_creator_queue(self) -> bool:
        """Start next pending creator scrape job if idle and not paused."""
        if not CREATOR_SCRAPE_QUEUE_ENABLED:
            return False
        from promptstudio.scraping.creator_queue import CreatorScrapeQueue
        from promptstudio.storage.archive import ensure_creator_folder

        q = CreatorScrapeQueue.get()
        if q.is_paused() or self.is_running():
            return False
        job = q.peek_next()
        if not job:
            return False

        job_id = job["id"]
        username = job.get("username") or ""
        source_name = (job.get("source") or "instagram").strip().lower()
        mode = (job.get("mode") or "full").strip().lower()
        if mode not in ("full", "bounded", "latest"):
            mode = "full"
        # latest always catch-up; full uses deep flag; bounded ignores deep
        if mode == "latest":
            deep = False
        elif mode == "full":
            deep = bool(job.get("deep", True))
        else:
            deep = False
        include_videos = job.get("include_videos")
        if include_videos is None:
            include_videos = INCLUDE_VIDEOS_DEFAULT
        max_posts = job.get("max_posts")
        if mode == "full":
            if max_posts is None or int(max_posts) <= 0:
                max_posts = FULL_SCRAPE_MAX_POSTS
            else:
                max_posts = int(max_posts)
        else:
            # bounded + latest: default 50 new downloads ceiling
            max_posts = int(max_posts or DEFAULT_MAX_POSTS_PER_CREATOR)

        def fn(log, on_rate_limit=None):
            q.mark_running(job_id)
            self._set_scrape_meta(job_id, username, source_name)
            self._cancel.clear()

            if q.should_account_pause_before(job_id):
                import random

                lo = max(0.0, float(ACCOUNT_PAUSE_MIN_SEC))
                hi = max(lo, float(ACCOUNT_PAUSE_MAX_SEC))
                delay = random.uniform(lo, hi)
                log(f"Cooldown between creators — {int(delay)}s")
                if self._interruptible_sleep(delay, log, label="Cooldown between creators"):
                    q.finalize_job(
                        job_id,
                        status="cancelled",
                        error="Cancelled by user",
                        result=None,
                        stop_reason="cancel",
                    )
                    return {
                        "aborted": True,
                        "abort_reason": "Cancelled by user",
                        "stop_reason": "cancel",
                        "downloaded": 0,
                        "errors": 0,
                    }

            if q.should_batch_pause():
                import random

                lo = max(0.0, float(BATCH_PAUSE_MIN_SEC))
                hi = max(lo, float(BATCH_PAUSE_MAX_SEC))
                delay = random.uniform(lo, hi)
                log(f"Batch pause between queue jobs — {delay / 60:.1f} min")
                if self._interruptible_sleep(delay, log, label="Batch pause"):
                    q.finalize_job(
                        job_id,
                        status="cancelled",
                        error="Cancelled by user",
                        result=None,
                        stop_reason="cancel",
                    )
                    return {
                        "aborted": True,
                        "abort_reason": "Cancelled by user",
                        "stop_reason": "cancel",
                        "downloaded": 0,
                        "errors": 0,
                    }

            from promptstudio.config import SAVED_DIR
            from promptstudio.scraping.sources import get_source
            from promptstudio.scraping.sources.base import ScrapeOptions, SourceContext

            try:
                source = get_source(source_name)
                target = source.parse_target(username)
            except ValueError as exc:
                q.finalize_job(
                    job_id,
                    status="error",
                    error=str(exc),
                    result=None,
                    stop_reason="error",
                )
                return {"errors": 1, "stop_reason": "error", "messages": [str(exc)]}

            try:
                # Non-Instagram sources land in a suffixed folder (nina__x), so
                # the folder must come from the target, not the raw handle.
                ensure_creator_folder(target.folder)
            except ValueError as exc:
                q.finalize_job(
                    job_id,
                    status="error",
                    error=str(exc),
                    result=None,
                    stop_reason="error",
                )
                return {"errors": 1, "stop_reason": "error", "messages": [str(exc)]}

            ctx = SourceContext(
                save_dir=SAVED_DIR,
                log=log,
                should_cancel=self.is_cancel_requested,
                on_rate_limit=on_rate_limit,
            )
            options = ScrapeOptions(
                mode=mode,
                deep=deep,
                max_posts=max_posts,
                include_videos=bool(include_videos),
            )
            try:
                result = source.run(target, options, ctx)
            except Exception as exc:
                from promptstudio.scraping.downloader import InstagramDownloader as IDL

                abuse = IDL._is_abuse_signal(exc)
                q.finalize_job(
                    job_id,
                    status="error",
                    error=str(exc),
                    result=None,
                    stop_reason="error",
                )
                if abuse:
                    q.pause(str(exc), persist=True)
                return {
                    "aborted": abuse,
                    "abort_reason": str(exc),
                    "errors": 1,
                    "stop_reason": "abort" if abuse else "error",
                }

            stop = getattr(result, "stop_reason", "") or ""
            aborted = bool(result.aborted)
            user_cancel = aborted and (
                stop == "cancel"
                or "cancelled by user" in (result.abort_reason or "").lower()
            )
            hard_ig = aborted and not user_cancel

            if user_cancel:
                q.finalize_job(
                    job_id,
                    status="cancelled",
                    error=result.abort_reason or "Cancelled by user",
                    result=result.to_dict(),
                    stop_reason="cancel",
                )
            elif hard_ig:
                q.finalize_job(
                    job_id,
                    status="error",
                    error=result.abort_reason or "IG abort",
                    result=result.to_dict(),
                    stop_reason="abort",
                )
                q.pause(result.abort_reason or "IG abort", persist=True)
            elif stop in ("not_found", "private", "error") or (
                result.errors
                and result.downloaded == 0
                and stop not in ("nothing_new", "end_of_feed", "catch_up", "ceiling", "")
            ):
                err_msg = result.messages[-1] if result.messages else (stop or "error")
                q.finalize_job(
                    job_id,
                    status="error",
                    error=err_msg,
                    result=result.to_dict(),
                    stop_reason=stop or "error",
                )
            else:
                # Profile not found may only set errors without stop_reason in edge cases
                if result.errors and result.downloaded == 0 and "not found" in " ".join(
                    result.messages
                ).lower():
                    q.finalize_job(
                        job_id,
                        status="error",
                        error=result.messages[-1] if result.messages else "not found",
                        result=result.to_dict(),
                        stop_reason="not_found",
                    )
                else:
                    q.finalize_job(
                        job_id,
                        status="done",
                        error="",
                        result=result.to_dict(),
                        stop_reason=stop or "end_of_feed",
                    )

            q.record_stats_from_result(result)
            return result

        return self.start_job("creator_queue", fn)

    def _schedule_auto_drain(self) -> None:
        if self._auto_drain_scheduled:
            return
        self._auto_drain_scheduled = True

        def _run() -> None:
            time.sleep(max(0.0, float(AUTO_DRAIN_ON_START_DELAY_SEC)))
            try:
                self.try_drain_creator_queue()
            except Exception:
                pass

        threading.Thread(target=_run, daemon=True).start()
