"""Background sync job tracking for the HTTP API.

**Lanes.** Scrape capacity is per platform, not global: one `ScrapeLane` per
source, each running at most one job. Instagram is pinned to one forever (its
session cannot be shared, and `SyncCheckpoints` is a whole-file rewrite), but
Reddit has nothing in common with it and never needed to wait.

A lane owns the four things that were global while there was one worker, and
each of them was a distinct defect once two sources could run at once:

* its own **status** dict (one `sync_status.json` entry per lane)
* its own **cancel** Event — the important one. A single shared Event meant
  cancelling X also killed a running Reddit job, because every source got
  `should_cancel=self.is_cancel_requested`.
* its own **RunHandle** for the journal
* its own **pacing**, via `creator_queue`'s per-lane finished counters

See docs/design_scrape_lanes.md.
"""

from __future__ import annotations

import json
import random
import threading
import time
import traceback
from dataclasses import replace
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

from promptstudio.config import (
    AUTO_DRAIN_ON_START,
    AUTO_DRAIN_ON_START_DELAY_SEC,
    CREATOR_SCRAPE_QUEUE_ENABLED,
    INCLUDE_VIDEOS_DEFAULT,
    SYNC_STATUS_FILE,
    account_pause_range_for,
    batch_pause_range_for,
)
from promptstudio.jobs import LEASES, scrape_resource
from promptstudio.logging_setup import get_logger
from promptstudio.scraping.sources.base import ScrapeOptions
from promptstudio.storage.atomic import atomic_write_json
from promptstudio.storage.db import DEFAULT_SOURCE
from promptstudio.storage.journal import RunHandle, RunJournal

log = get_logger(__name__)

# Lease owner names are user-facing — they appear in "busy" messages, so they
# have to name the lane rather than just "sync".
LEASE_OWNER = "sync"


def lease_owner_for(source: str) -> str:
    return f"{LEASE_OWNER}:{normalize_source(source)}"


def normalize_source(source: Optional[str]) -> str:
    return (source or DEFAULT_SOURCE).strip().lower() or DEFAULT_SOURCE


def _default_status(source: str) -> Dict[str, Any]:
    return {
        "source": source,
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


class ScrapeLane:
    """One source's slot: its status, its cancel signal, its journal handle."""

    def __init__(self, source: str) -> None:
        self.source = source
        self.cancel = threading.Event()
        self.run: Optional[RunHandle] = None
        self.status: Dict[str, Any] = _default_status(source)

    def is_cancel_requested(self) -> bool:
        """Bound per lane and handed to SourceContext.

        The whole point of lanes: this used to be `SyncManager`'s single Event,
        so cancelling any source cancelled all of them.
        """
        return self.cancel.is_set()


class SyncManager:
    """Runs scrape jobs in background threads, one per source lane."""

    _instance: Optional["SyncManager"] = None
    _lock = threading.Lock()

    def __init__(self) -> None:
        # One lock over every lane's status dict and the status file. Lane
        # contention is a handful of dict writes per second, so splitting it
        # per lane would buy nothing and make the file write racy.
        self._job_lock = threading.RLock()
        # Why the last start_job() returned False, for the API's 409 message.
        self.last_refusal = ""
        self._lanes: Dict[str, ScrapeLane] = {}
        self._load_status()
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

    # ------------------------------------------------------------- lanes

    def _lane(self, source: Optional[str]) -> ScrapeLane:
        """Lane for `source`, created on first use. Caller holds `_job_lock`."""
        key = normalize_source(source)
        lane = self._lanes.get(key)
        if lane is None:
            lane = ScrapeLane(key)
            self._lanes[key] = lane
        return lane

    def lane_names(self) -> List[str]:
        """Every lane a sweep must consider.

        Registered sources, lanes that already have state, and — importantly —
        any source named by queued work, even one this build does not know.
        A hand-edited or downgrade-era job naming `tiktok` still needs a lane
        to drain into, or it sits pending forever instead of failing cleanly
        with "Unknown source".
        """
        names = set(self._lanes)
        try:
            from promptstudio.scraping.sources import known_sources

            names.update(known_sources())
        except Exception:
            names.add(DEFAULT_SOURCE)
        if CREATOR_SCRAPE_QUEUE_ENABLED:
            try:
                from promptstudio.scraping.creator_queue import CreatorScrapeQueue

                names.update(CreatorScrapeQueue.get().queued_sources())
            except Exception:
                log.warning("could not read queued sources", exc_info=True)
        return sorted(names)

    # ------------------------------------------------------------ status

    def _load_status(self) -> None:
        """Read `sync_status.json`, migrating the pre-lane flat shape.

        A v1 file described the single global worker. Everything it could have
        been running was Instagram in practice, so that is the lane it restores
        into — same rule the queue file's migration uses.
        """
        data: Any = None
        try:
            with open(SYNC_STATUS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError):
            data = None
        if not isinstance(data, dict):
            return

        lanes = data.get("lanes")
        if isinstance(lanes, dict) and lanes:
            for name, status in lanes.items():
                if not isinstance(status, dict):
                    continue
                lane = self._lane(name)
                lane.status = {**_default_status(lane.source), **status}
                lane.status["source"] = lane.source
            return

        legacy = self._lane(DEFAULT_SOURCE)
        merged = {**_default_status(DEFAULT_SOURCE), **data}
        merged.pop("creator_queue", None)
        merged["source"] = DEFAULT_SOURCE
        legacy.status = merged

    def _recover_stuck_running(self) -> None:
        with self._job_lock:
            changed = False
            for lane in self._lanes.values():
                if not lane.status.get("running"):
                    continue
                lane.status.update(
                    running=False,
                    finished_at=datetime.now(timezone.utc).isoformat(),
                    error="Server restarted",
                    progress="Interrupted by server restart",
                    cancel_requested=False,
                    scrape_job_id=None,
                    scrape_username=None,
                    scrape_source=None,
                )
                changed = True
            if changed:
                self._save_status()

    def _save_status(self) -> None:
        """Caller MUST hold `_job_lock`."""
        payload = {
            "lanes": {name: dict(lane.status) for name, lane in self._lanes.items()}
        }
        try:
            # fsync=False: status is rewritten every few seconds and is fully
            # reconstructible, so durability is not worth the barrier.
            atomic_write_json(SYNC_STATUS_FILE, payload, fsync=False)
        except OSError as e:
            log.debug("sync status write failed: %s", e)

    def get_status(self) -> Dict[str, Any]:
        """Status for `/api/sync/status`.

        `lanes` is the real shape. The flat keys beside it are the Instagram
        lane, kept because that is what every pre-lane client was reading and a
        one-source archive is still the common case.
        """
        with self._job_lock:
            for name in self.lane_names():
                self._lane(name)
            lanes = {name: dict(lane.status) for name, lane in self._lanes.items()}
            primary = dict(self._lane(DEFAULT_SOURCE).status)

        running_lanes = [name for name, s in lanes.items() if s.get("running")]
        status = {
            **primary,
            "lanes": lanes,
            "running_lanes": running_lanes,
            "any_running": bool(running_lanes),
        }
        status["creator_queue"] = self._creator_queue_summary()
        return status

    @staticmethod
    def _creator_queue_summary() -> Dict[str, Any]:
        if not CREATOR_SCRAPE_QUEUE_ENABLED:
            return {"enabled": False, "depth": 0, "pending_count": 0, "lanes": {}}
        try:
            from promptstudio.scraping.creator_queue import CreatorScrapeQueue

            return CreatorScrapeQueue.get().summary_for_sync_status()
        except Exception:
            log.warning("creator queue summary failed", exc_info=True)
            return {
                "depth": 0,
                "pending_count": 0,
                "paused": False,
                "pause_reason": "",
                "current_username": None,
                "enabled": True,
                "lanes": {},
            }

    def is_running(self, source: Optional[str] = None) -> bool:
        """Is this lane busy? With no source, is *any* lane busy?"""
        with self._job_lock:
            if source is not None:
                return bool(self._lane(source).status.get("running"))
            return any(l.status.get("running") for l in self._lanes.values())

    # ------------------------------------------------------------ cancel

    def request_cancel(self, source: Optional[str] = None) -> bool:
        """Cancel one lane, or every running lane when `source` is None.

        `/api/sync/cancel` passes None and means it — a bare "stop" from the
        user should stop everything, not whichever lane happens to be first.
        """
        with self._job_lock:
            if source is not None:
                lanes = [self._lane(source)]
            else:
                lanes = list(self._lanes.values())
            hit = False
            for lane in lanes:
                if not lane.status.get("running"):
                    continue
                lane.cancel.set()
                lane.status["cancel_requested"] = True
                lane.status["progress"] = "Cancel requested..."
                hit = True
            if hit:
                self._save_status()
            return hit

    def is_cancel_requested(self, source: Optional[str] = None) -> bool:
        """With no source, true if *any* lane is cancelling.

        Only meaningful for legacy callers; a running job must use its own
        lane's `is_cancel_requested`, or one source's cancel stops another's.
        """
        with self._job_lock:
            if source is not None:
                return self._lane(source).cancel.is_set()
            return any(l.cancel.is_set() for l in self._lanes.values())

    # ------------------------------------------------------- rate limits

    def record_rate_limit(
        self,
        consecutive: int,
        backoff_sec: int,
        *,
        source: str = DEFAULT_SOURCE,
    ) -> None:
        with self._job_lock:
            lane = self._lane(source)
            lane.status["consecutive_rate_limits"] = consecutive
            lane.status["last_backoff_sec"] = backoff_sec
            lane.status["rate_limit_hits"] = (
                int(lane.status.get("rate_limit_hits") or 0) + 1
            )
            lane.status["progress"] = (
                f"Rate limited — waiting {backoff_sec}s (streak {consecutive})"
            )
            self._save_status()
            run = lane.run
            username = lane.status.get("scrape_username")
        # Timestamped in the journal so backoff interarrival can be measured
        # instead of guessed when tuning the pacing constants.
        if run is not None:
            run.event(
                "rate_limit",
                consecutive=consecutive,
                backoff_sec=backoff_sec,
                username=username,
                source=source,
            )

    def _set_scrape_meta(
        self,
        source: str,
        job_id: Optional[str],
        username: Optional[str],
        job_source: Optional[str] = None,
    ) -> None:
        with self._job_lock:
            lane = self._lane(source)
            lane.status["scrape_job_id"] = job_id
            lane.status["scrape_username"] = username
            lane.status["scrape_source"] = job_source
            self._save_status()

    # --------------------------------------------------------- start job

    def start_job(
        self,
        job_type: str,
        fn: Callable[..., Any],
        *,
        source: str = DEFAULT_SOURCE,
    ) -> bool:
        """Start a background job on `source`'s lane. False if it is busy.

        On False, `last_refusal` says why — the API turns that into the 409
        message so "busy" is attributable to a named holder.
        """
        lane_name = normalize_source(source)
        owner = lease_owner_for(lane_name)
        # One session per platform, one job per lane. Taken before the status
        # flip so two simultaneous requests cannot both observe running=False.
        blocker = LEASES.acquire([scrape_resource(lane_name)], owner)
        if blocker:
            holder = LEASES.holder(blocker) or "another job"
            self.last_refusal = f"{holder} is using the {lane_name} session"
            return False

        with self._job_lock:
            lane = self._lane(lane_name)
            if lane.status.get("running"):
                LEASES.release(owner)
                self.last_refusal = f"A {lane_name} sync is already running"
                return False
            self.last_refusal = ""
            lane.cancel.clear()
            lane.status = {
                **_default_status(lane_name),
                "running": True,
                "job_type": job_type,
                "started_at": datetime.now(timezone.utc).isoformat(),
                "progress": "Starting...",
            }
            self._save_status()

        thread = threading.Thread(
            target=self._runner,
            args=(lane_name, owner, job_type, fn),
            daemon=True,
        )
        thread.start()
        return True

    def _runner(
        self,
        lane_name: str,
        owner: str,
        job_type: str,
        fn: Callable[..., Any],
    ) -> None:
        logs: List[str] = []
        journal = RunJournal.for_kind("sync")
        run_cm = journal.run(job_type=job_type, source=lane_name)
        run = run_cm.__enter__()
        with self._job_lock:
            self._lane(lane_name).run = run

        def job_log(msg: str) -> None:
            logs.append(msg)
            with self._job_lock:
                self._lane(lane_name).status["progress"] = msg
                self._save_status()
            # The per-step trail is the whole point: "stopped at account 12"
            # is only answerable if each step left a timestamped record.
            run.event("progress", msg=str(msg)[:300])

        def on_rate_limit(consecutive: int, backoff_sec: int) -> None:
            self.record_rate_limit(consecutive, backoff_sec, source=lane_name)

        try:
            try:
                result = fn(job_log, on_rate_limit)
            except TypeError:
                result = fn(job_log)
            self._record_success(lane_name, result, logs)
        except Exception as exc:
            self._record_failure(lane_name, exc)
        finally:
            with self._job_lock:
                lane = self._lane(lane_name)
                status = dict(lane.status)
                lane.run = None
            run.summary(
                outcome_progress=status.get("progress"),
                error=status.get("error"),
                rate_limit_hits=status.get("rate_limit_hits"),
                result=status.get("result"),
            )
            # __exit__(None, ...) — the handler above already recorded the
            # failure into status, so nothing is propagating here.
            run_cm.__exit__(None, None, None)
            # Released before the drain is scheduled, or the next queued job
            # on this lane would find the session still leased by this one.
            LEASES.release(owner)
            with self._job_lock:
                self._lane(lane_name).cancel.clear()
            self._schedule_post_job_drain(lane_name)

    def _record_success(self, lane_name: str, result: Any, logs: List[str]) -> None:
        def field(name: str, default: Any = None) -> Any:
            if isinstance(result, dict):
                return result.get(name, default)
            return getattr(result, name, default)

        aborted = bool(field("aborted", False))
        stop_reason = field("stop_reason")
        abort_reason = field("abort_reason", "") or ""

        with self._job_lock:
            status = self._lane(lane_name).status
            status["running"] = False
            status["finished_at"] = datetime.now(timezone.utc).isoformat()
            if aborted:
                status["progress"] = f"Aborted — {abort_reason}".strip()
            elif stop_reason:
                status["progress"] = f"Complete ({stop_reason})"
            else:
                status["progress"] = "Complete"
            status["result"] = (
                result.to_dict() if hasattr(result, "to_dict") else result
            )
            hits = field("rate_limit_hits")
            if hits is not None:
                status["rate_limit_hits"] = hits
            status["logs"] = logs[-50:]
            status["cancel_requested"] = False
            status["scrape_job_id"] = None
            status["scrape_username"] = None
            status["scrape_source"] = None
            self._save_status()

    def _record_failure(self, lane_name: str, exc: Exception) -> None:
        with self._job_lock:
            status = self._lane(lane_name).status
            status["running"] = False
            status["finished_at"] = datetime.now(timezone.utc).isoformat()
            status["error"] = str(exc)
            status["progress"] = "Failed"
            status["traceback"] = traceback.format_exc()
            status["cancel_requested"] = False
            status["scrape_job_id"] = None
            status["scrape_username"] = None
            status["scrape_source"] = None
            self._save_status()

    def _schedule_post_job_drain(self, lane_name: str) -> None:
        """Re-drain after a job finishes, own lane first.

        On a new thread to avoid re-entrancy: the drain calls back into
        `start_job`, and this is still inside the finishing job's runner.
        """
        if not CREATOR_SCRAPE_QUEUE_ENABLED:
            return
        try:
            from promptstudio.scraping.creator_queue import CreatorScrapeQueue

            queue = CreatorScrapeQueue.get()
        except Exception:
            log.warning("post-job drain skipped: queue unavailable", exc_info=True)
            return

        def _drain() -> None:
            try:
                if not queue.is_paused(lane_name):
                    self.try_drain_creator_queue(lane_name)
                # Then sweep: a lane can be idle because it was paused when its
                # predecessor finished, and nothing else would wake it.
                self.try_drain_creator_queue()
            except Exception:
                log.warning("creator queue drain failed", exc_info=True)

        threading.Thread(target=_drain, daemon=True).start()

    # ------------------------------------------------------------- sleep

    def _interruptible_sleep(
        self,
        lane_name: str,
        seconds: float,
        job_log: Optional[Callable[[str], None]] = None,
        *,
        label: str = "Cooldown",
    ) -> bool:
        """Sleep in 1s slices; return True if this lane was cancelled early."""
        with self._job_lock:
            cancel = self._lane(lane_name).cancel
        end = time.monotonic() + max(0.0, float(seconds))
        while time.monotonic() < end:
            if cancel.is_set():
                if job_log:
                    job_log(f"{label} cancelled")
                return True
            remaining = end - time.monotonic()
            if job_log and remaining > 1:
                job_log(f"{label} — {int(remaining)}s remaining")
            time.sleep(min(1.0, max(0.0, remaining)))
        return False

    # ------------------------------------------------------------- drain

    def try_drain_creator_queue(self, source: Optional[str] = None) -> bool:
        """Start the next queued job on `source`'s lane.

        With no source, sweep every lane. Returns True if anything started —
        with a sweep, True means at least one lane started.
        """
        if not CREATOR_SCRAPE_QUEUE_ENABLED:
            return False
        if source is None:
            started = False
            for name in self.lane_names():
                started = self._drain_lane(name) or started
            return started
        return self._drain_lane(normalize_source(source))

    def _drain_lane(self, lane_name: str) -> bool:
        from promptstudio.scraping.creator_queue import CreatorScrapeQueue

        queue = CreatorScrapeQueue.get()
        if queue.is_paused(lane_name) or self.is_running(lane_name):
            return False
        job = queue.peek_next(lane_name)
        if not job:
            return False
        return self.start_job(
            "creator_queue",
            self._build_queue_job(queue, job, lane_name),
            source=lane_name,
        )

    def _build_queue_job(
        self,
        queue: Any,
        job: Dict[str, Any],
        lane_name: str,
    ) -> Callable[..., Any]:
        """Close over one queued job and return the callable `start_job` runs."""
        job_id = job["id"]
        username = job.get("username") or ""
        include_videos = job.get("include_videos")
        if include_videos is None:
            include_videos = INCLUDE_VIDEOS_DEFAULT
        # The queue stored an already-normalized job; normalize() is idempotent,
        # so this re-derives nothing — it just re-applies the same rules to a
        # file that may have been written by an older build or hand-edited.
        opts = ScrapeOptions.normalize(
            job.get("mode"),
            deep=bool(job.get("deep", True)),
            max_posts=job.get("max_posts"),
            include_videos=bool(include_videos),
            catch_up_only=bool(job.get("catch_up_only", False)),
        )
        max_posts = opts.resolved_max_posts()

        def run_job(job_log, on_rate_limit=None):
            queue.mark_running(job_id)
            self._set_scrape_meta(lane_name, job_id, username, lane_name)
            with self._job_lock:
                self._lane(lane_name).cancel.clear()

            cancelled = self._apply_lane_pacing(queue, job_id, lane_name, job_log)
            if cancelled:
                return cancelled

            return self._run_source_job(
                queue, job_id, username, lane_name, opts, max_posts,
                job_log, on_rate_limit,
            )

        return run_job

    def _apply_lane_pacing(
        self,
        queue: Any,
        job_id: str,
        lane_name: str,
        job_log: Callable[[str], None],
    ) -> Optional[Dict[str, Any]]:
        """Anti-ban waits between jobs *on this lane*. None unless cancelled.

        Both the interval and whether there is one at all come from the lane's
        own config: Instagram keeps its 30-120s cooldown and 5-15 min batch
        pause, gallery-dl lanes get near-zero because they already self-pace.
        """
        def cancelled() -> Dict[str, Any]:
            queue.finalize_job(
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

        if queue.should_account_pause_before(job_id, source=lane_name):
            lo, hi = account_pause_range_for(lane_name)
            delay = random.uniform(lo, hi)
            if delay > 0:
                job_log(f"Cooldown between {lane_name} creators — {int(delay)}s")
                if self._interruptible_sleep(
                    lane_name, delay, job_log, label="Cooldown between creators"
                ):
                    return cancelled()

        if queue.should_batch_pause(source=lane_name):
            lo, hi = batch_pause_range_for(lane_name)
            delay = random.uniform(lo, hi)
            if delay > 0:
                job_log(f"Batch pause on {lane_name} — {delay / 60:.1f} min")
                if self._interruptible_sleep(
                    lane_name, delay, job_log, label="Batch pause"
                ):
                    return cancelled()
        return None

    def _run_source_job(
        self,
        queue: Any,
        job_id: str,
        username: str,
        lane_name: str,
        opts: ScrapeOptions,
        max_posts: int,
        job_log: Callable[[str], None],
        on_rate_limit: Optional[Callable[[int, int], None]],
    ) -> Any:
        from promptstudio.config import SAVED_DIR
        from promptstudio.scraping.sources import get_source
        from promptstudio.scraping.sources.base import SourceContext
        from promptstudio.storage.archive import ensure_creator_folder

        def fail(message: str, stop_reason: str = "error") -> Dict[str, Any]:
            queue.finalize_job(
                job_id,
                status="error",
                error=message,
                result=None,
                stop_reason=stop_reason,
            )
            return {"errors": 1, "stop_reason": stop_reason, "messages": [message]}

        try:
            source = get_source(lane_name)
            target = source.parse_target(username)
        except ValueError as exc:
            return fail(str(exc))

        try:
            # Non-Instagram sources land in a suffixed folder (nina__x), so
            # the folder must come from the target, not the raw handle.
            ensure_creator_folder(target.folder)
        except ValueError as exc:
            return fail(str(exc))

        with self._job_lock:
            lane = self._lane(lane_name)
        ctx = SourceContext(
            save_dir=SAVED_DIR,
            log=job_log,
            # This lane's Event, never the manager's. A shared one is what made
            # cancelling X kill a running Reddit job.
            should_cancel=lane.is_cancel_requested,
            on_rate_limit=on_rate_limit,
        )
        # resolved_max_posts() turns the stored "no explicit limit" into the
        # number the source runs with; everything else is already normalized.
        options = replace(opts, max_posts=max_posts)
        try:
            result = source.run(target, options, ctx)
        except Exception as exc:
            from promptstudio.scraping.downloader import InstagramDownloader as IDL

            abuse = IDL._is_abuse_signal(exc)
            queue.finalize_job(
                job_id,
                status="error",
                error=str(exc),
                result=None,
                stop_reason="error",
            )
            if abuse:
                # This lane only. An expired credential on one platform is not
                # a reason to stop the others.
                queue.pause(str(exc), source=lane_name, persist=True)
            return {
                "aborted": abuse,
                "abort_reason": str(exc),
                "errors": 1,
                "stop_reason": "abort" if abuse else "error",
            }

        self._finalize_source_result(queue, job_id, lane_name, result)
        queue.record_stats_from_result(result, source=lane_name)
        return result

    @staticmethod
    def _finalize_source_result(
        queue: Any, job_id: str, lane_name: str, result: Any
    ) -> None:
        stop = getattr(result, "stop_reason", "") or ""
        aborted = bool(result.aborted)
        user_cancel = aborted and (
            stop == "cancel"
            or "cancelled by user" in (result.abort_reason or "").lower()
        )
        hard_abort = aborted and not user_cancel

        if user_cancel:
            queue.finalize_job(
                job_id,
                status="cancelled",
                error=result.abort_reason or "Cancelled by user",
                result=result.to_dict(),
                stop_reason="cancel",
            )
            return

        if hard_abort:
            queue.finalize_job(
                job_id,
                status="error",
                error=result.abort_reason or "abort",
                result=result.to_dict(),
                stop_reason="abort",
            )
            queue.pause(
                result.abort_reason or "abort", source=lane_name, persist=True
            )
            return

        benign = ("nothing_new", "end_of_feed", "catch_up", "ceiling", "")
        if stop in ("not_found", "private", "error") or (
            result.errors and result.downloaded == 0 and stop not in benign
        ):
            queue.finalize_job(
                job_id,
                status="error",
                error=result.messages[-1] if result.messages else (stop or "error"),
                result=result.to_dict(),
                stop_reason=stop or "error",
            )
            return

        # Profile not found may only set errors without stop_reason in edge cases
        if (
            result.errors
            and result.downloaded == 0
            and "not found" in " ".join(result.messages).lower()
        ):
            queue.finalize_job(
                job_id,
                status="error",
                error=result.messages[-1] if result.messages else "not found",
                result=result.to_dict(),
                stop_reason="not_found",
            )
            return

        queue.finalize_job(
            job_id,
            status="done",
            error="",
            result=result.to_dict(),
            stop_reason=stop or "end_of_feed",
        )

    # -------------------------------------------------------- auto drain

    def _schedule_auto_drain(self) -> None:
        if self._auto_drain_scheduled:
            return
        self._auto_drain_scheduled = True

        def _run() -> None:
            time.sleep(max(0.0, float(AUTO_DRAIN_ON_START_DELAY_SEC)))
            try:
                self.try_drain_creator_queue()
            except Exception:
                log.warning("auto-drain on start failed", exc_info=True)

        threading.Thread(target=_run, daemon=True).start()
