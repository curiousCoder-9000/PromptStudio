"""Append-only JSONL journal of background job runs.

Job status is a *live* dict that is overwritten on the next run, so the moment a
six-hour scrape finishes, the record of what happened during it is gone. The
questions that actually come up afterwards — why did the sync stop at account
12, is the classifier's score distribution drifting, how far apart were the rate
limits — all need history, not a snapshot.

One file per job kind under ``<archive>/_journal/``. Lines are independent JSON
objects, so a truncated tail costs one record rather than the file, and readers
skip malformed lines rather than failing. That is why this deliberately does
*not* use ``atomic_write_json``: whole-file replacement is the wrong shape for
an append-only log, and O_APPEND writes below the pipe-buffer size do not
interleave between threads.

    journal = RunJournal.for_kind("classify")
    with journal.run(creator="someone", total=42) as run:
        run.item(rel_path="a.mp4", ok=True, glam=3, ms=1180)
        run.event("rate_limit", consecutive=2, backoff_sec=60)
        run.summary(score_hist={"3": 40, "0": 2})
"""

from __future__ import annotations

import json
import os
import secrets
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Dict, Iterator, List

from promptstudio.config import (
    JOURNAL_BACKUPS,
    JOURNAL_DIR,
    JOURNAL_ENABLED,
    JOURNAL_MAX_BYTES,
)
from promptstudio.logging_setup import get_logger

log = get_logger(__name__)

_registry_lock = threading.Lock()
_registry: Dict[str, "RunJournal"] = {}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_run_id(kind: str) -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{kind}_{stamp}_{secrets.token_hex(2)}"


class RunHandle:
    """Emits records for one run. Created by ``RunJournal.run()``."""

    def __init__(self, journal: "RunJournal", run_id: str) -> None:
        self.journal = journal
        self.run_id = run_id
        self.items = 0
        self.failures = 0
        self._summary: Dict[str, Any] = {}

    def item(self, **fields: Any) -> None:
        """One unit of work. `ok=False` counts toward the run's failure total."""
        self.items += 1
        if fields.get("ok") is False:
            self.failures += 1
        self.journal._write(self.run_id, "item", fields)

    def event(self, name: str, **fields: Any) -> None:
        """Something noteworthy that is not an item — a backoff, an abort."""
        self.journal._write(self.run_id, "event", {"name": name, **fields})

    def summary(self, **fields: Any) -> None:
        """Merge fields into the run_end record. Safe to call repeatedly."""
        self._summary.update(fields)


class RunJournal:
    """One JSONL file per job kind. Thread-safe, size-rotated."""

    def __init__(self, kind: str, directory: str = "") -> None:
        self.kind = kind
        self.dir = directory or JOURNAL_DIR
        self.path = os.path.join(self.dir, f"{kind}.jsonl")
        self._lock = threading.Lock()

    @classmethod
    def for_kind(cls, kind: str) -> "RunJournal":
        with _registry_lock:
            journal = _registry.get(kind)
            if journal is None:
                journal = cls(kind)
                _registry[kind] = journal
            return journal

    # ── writing ──────────────────────────────────────────────────────

    def _rotate_if_needed(self) -> None:
        """Caller MUST hold self._lock."""
        try:
            if os.path.getsize(self.path) < int(JOURNAL_MAX_BYTES):
                return
        except OSError:
            return
        keep = max(0, int(JOURNAL_BACKUPS))
        if keep == 0:
            try:
                os.remove(self.path)
            except OSError:
                pass
            return
        for i in range(keep - 1, 0, -1):
            src, dst = f"{self.path}.{i}", f"{self.path}.{i + 1}"
            if os.path.exists(src):
                try:
                    os.replace(src, dst)
                except OSError:
                    pass
        try:
            os.replace(self.path, f"{self.path}.1")
        except OSError:
            pass

    def _write(self, run_id: str, event: str, fields: Dict[str, Any]) -> None:
        if not JOURNAL_ENABLED:
            return
        record = {"ts": _utc_now(), "run_id": run_id, "kind": self.kind, "event": event}
        record.update(fields)
        try:
            line = json.dumps(record, ensure_ascii=False, default=str)
        except (TypeError, ValueError) as e:
            log.debug("journal record not serializable (%s): %s", self.kind, e)
            return

        with self._lock:
            try:
                os.makedirs(self.dir, exist_ok=True)
                self._rotate_if_needed()
                with open(self.path, "a", encoding="utf-8") as f:
                    f.write(line + "\n")
            except OSError as e:
                # Journalling is diagnostics; it must never break the job.
                log.debug("journal write failed (%s): %s", self.kind, e)

    @contextmanager
    def run(self, **meta: Any) -> Iterator[RunHandle]:
        """Bracket a run with run_start / run_end records.

        Emits ``outcome`` of ok | error | cancelled. Exceptions are recorded and
        re-raised — the journal observes, it does not swallow.
        """
        run_id = _new_run_id(self.kind)
        handle = RunHandle(self, run_id)
        started = time.monotonic()
        self._write(run_id, "run_start", dict(meta))

        outcome = "ok"
        error = ""
        try:
            yield handle
        except BaseException as exc:
            outcome = "cancelled" if isinstance(exc, KeyboardInterrupt) else "error"
            error = f"{type(exc).__name__}: {exc}"
            raise
        finally:
            end: Dict[str, Any] = {
                "outcome": outcome,
                "duration_sec": round(time.monotonic() - started, 3),
                "items": handle.items,
                "failures": handle.failures,
            }
            if error:
                end["error"] = error[:500]
            end.update(handle._summary)
            self._write(run_id, "run_end", end)

    # ── reading ──────────────────────────────────────────────────────

    def read_records(self, limit: int = 500) -> List[Dict[str, Any]]:
        """Most recent records, oldest first. Malformed lines are skipped."""
        if not os.path.isfile(self.path):
            return []
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                lines = f.readlines()
        except OSError as e:
            log.debug("journal read failed (%s): %s", self.kind, e)
            return []

        out: List[Dict[str, Any]] = []
        for line in lines[-max(1, int(limit)) :]:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                # A crash can leave one partial line; that is the design cost.
                continue
            if isinstance(record, dict):
                out.append(record)
        return out

    def read_runs(self, limit: int = 20) -> List[Dict[str, Any]]:
        """Completed and in-flight runs, newest first.

        Item records are counted, not returned — a run over 4000 photos should
        not be 4000 objects in an API response.
        """
        runs: Dict[str, Dict[str, Any]] = {}
        order: List[str] = []
        for record in self.read_records(limit=20000):
            run_id = record.get("run_id")
            if not run_id:
                continue
            if run_id not in runs:
                runs[run_id] = {
                    "run_id": run_id,
                    "kind": record.get("kind", self.kind),
                    "started_at": None,
                    "finished_at": None,
                    "events": [],
                    "item_count": 0,
                }
                order.append(run_id)
            run = runs[run_id]
            event = record.get("event")
            if event == "run_start":
                run["started_at"] = record.get("ts")
                run.update(
                    {
                        k: v
                        for k, v in record.items()
                        if k not in ("ts", "run_id", "kind", "event")
                    }
                )
            elif event == "run_end":
                run["finished_at"] = record.get("ts")
                run.update(
                    {
                        k: v
                        for k, v in record.items()
                        if k not in ("ts", "run_id", "kind", "event")
                    }
                )
            elif event == "item":
                run["item_count"] += 1
            elif event == "event":
                run["events"].append(
                    {k: v for k, v in record.items() if k not in ("run_id", "kind", "event")}
                )
        return [runs[r] for r in reversed(order)][: max(1, int(limit))]


def list_kinds() -> List[str]:
    """Journal kinds present on disk."""
    try:
        names = os.listdir(JOURNAL_DIR)
    except OSError:
        return []
    return sorted(n[: -len(".jsonl")] for n in names if n.endswith(".jsonl"))


def read_runs(kind: str, limit: int = 20) -> List[Dict[str, Any]]:
    return RunJournal.for_kind(kind).read_runs(limit=limit)


def journal_for(kind: str) -> RunJournal:
    return RunJournal.for_kind(kind)


__all__ = [
    "RunJournal",
    "RunHandle",
    "journal_for",
    "list_kinds",
    "read_runs",
]
