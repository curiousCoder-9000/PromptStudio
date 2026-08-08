"""Background sync job tracking for the HTTP API."""

import json
import threading
import traceback
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Optional

from promptstudio.config import SYNC_STATUS_FILE


class SyncManager:
    """Runs Instagram sync jobs in background threads with persisted status."""

    _instance: Optional["SyncManager"] = None
    _lock = threading.Lock()

    def __init__(self) -> None:
        self._job_lock = threading.Lock()
        self._status: Dict[str, Any] = self._load_status()

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
                return json.load(f)
        except Exception:
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
        }

    def _save_status(self) -> None:
        try:
            import os

            os.makedirs(os.path.dirname(SYNC_STATUS_FILE), exist_ok=True)
            with open(SYNC_STATUS_FILE, "w", encoding="utf-8") as f:
                json.dump(self._status, f, indent=2)
        except OSError:
            pass

    def get_status(self) -> Dict[str, Any]:
        with self._job_lock:
            return dict(self._status)

    def is_running(self) -> bool:
        return self.get_status().get("running", False)

    def record_rate_limit(self, consecutive: int, backoff_sec: int) -> None:
        with self._job_lock:
            self._status["consecutive_rate_limits"] = consecutive
            self._status["last_backoff_sec"] = backoff_sec
            self._status["rate_limit_hits"] = int(self._status.get("rate_limit_hits") or 0) + 1
            self._status["progress"] = (
                f"Rate limited — waiting {backoff_sec}s (streak {consecutive})"
            )
            self._save_status()

    def start_job(self, job_type: str, fn: Callable[[], Any]) -> bool:
        """Start a background job. Returns False if one is already running."""
        with self._job_lock:
            if self._status.get("running"):
                return False
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
                # Jobs may accept (log) or (log, on_rate_limit)
                try:
                    result = fn(log, on_rate_limit)
                except TypeError:
                    result = fn(log)
                with self._job_lock:
                    self._status["running"] = False
                    self._status["finished_at"] = datetime.now(timezone.utc).isoformat()
                    self._status["progress"] = "Complete"
                    self._status["result"] = (
                        result.to_dict() if hasattr(result, "to_dict") else result
                    )
                    if hasattr(result, "rate_limit_hits"):
                        self._status["rate_limit_hits"] = result.rate_limit_hits
                    self._status["logs"] = logs[-50:]
                    self._save_status()
            except Exception as exc:
                with self._job_lock:
                    self._status["running"] = False
                    self._status["finished_at"] = datetime.now(timezone.utc).isoformat()
                    self._status["error"] = str(exc)
                    self._status["progress"] = "Failed"
                    self._status["traceback"] = traceback.format_exc()
                    self._save_status()

        thread = threading.Thread(target=runner, daemon=True)
        thread.start()
        return True
