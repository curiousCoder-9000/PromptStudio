"""Persistent following-sync account queue with daily budget tracking."""

from __future__ import annotations

import json
import os
from datetime import date, datetime, timezone
from typing import Any, Dict, Iterable, List, Optional

from promptstudio.config import DEFAULT_ACCOUNTS_PER_DAY, FOLLOWING_QUEUE_FILE

VALID_STATUSES = frozenset({"pending", "done", "skipped", "error"})


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalize_username(username: str) -> str:
    return username.lstrip("@").strip().lower()


class FollowingQueue:
    """Track which following accounts are pending/done for multi-day bulk sync."""

    def __init__(self, path: str = FOLLOWING_QUEUE_FILE) -> None:
        self.path = path
        self._data: Dict[str, Any] = self._load()
        self._roll_day_if_needed()

    def _default(self) -> Dict[str, Any]:
        return {
            "day_key": date.today().isoformat(),
            "accounts_today": 0,
            "accounts": {},
        }

    def _load(self) -> Dict[str, Any]:
        if not os.path.isfile(self.path):
            return self._default()
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, dict):
                return self._default()
            data.setdefault("day_key", date.today().isoformat())
            data.setdefault("accounts_today", 0)
            data.setdefault("accounts", {})
            if not isinstance(data["accounts"], dict):
                data["accounts"] = {}
            return data
        except Exception:
            return self._default()

    def save(self) -> None:
        try:
            os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump(self._data, f, indent=2, ensure_ascii=False)
        except OSError as e:
            print(f"Error saving following queue: {e}")

    def _roll_day_if_needed(self) -> None:
        today = date.today().isoformat()
        if self._data.get("day_key") != today:
            self._data["day_key"] = today
            self._data["accounts_today"] = 0
            self.save()

    def remaining_today(self, daily_cap: int = DEFAULT_ACCOUNTS_PER_DAY) -> int:
        self._roll_day_if_needed()
        used = int(self._data.get("accounts_today") or 0)
        return max(0, int(daily_cap) - used)

    def ensure_accounts(self, usernames: Iterable[str]) -> int:
        """Merge usernames as pending. Never overwrite done/skipped/error."""
        added = 0
        accounts: Dict[str, Any] = self._data.setdefault("accounts", {})
        for raw in usernames:
            key = _normalize_username(raw)
            if not key:
                continue
            if key in accounts:
                continue
            accounts[key] = {
                "status": "pending",
                "downloaded": 0,
                "last_error": "",
                "updated_at": _utc_now(),
            }
            added += 1
        if added:
            self.save()
        return added

    def next_pending(
        self,
        limit: int,
        *,
        daily_cap: int = DEFAULT_ACCOUNTS_PER_DAY,
        only: Optional[Iterable[str]] = None,
    ) -> List[str]:
        """Return up to `limit` pending usernames, capped by remaining daily budget.

        If `only` is provided, restrict to that username set (current filter pass).
        """
        self._roll_day_if_needed()
        budget = min(int(limit), self.remaining_today(daily_cap))
        if budget <= 0:
            return []
        allow = None
        if only is not None:
            allow = {_normalize_username(u) for u in only if u}
        selected: List[str] = []
        accounts: Dict[str, Any] = self._data.get("accounts") or {}
        for username, entry in accounts.items():
            if len(selected) >= budget:
                break
            if allow is not None and username not in allow:
                continue
            status = (entry or {}).get("status", "pending")
            if status == "pending":
                selected.append(username)
        return selected

    def mark(
        self,
        username: str,
        status: str,
        *,
        downloaded: Optional[int] = None,
        last_error: str = "",
    ) -> None:
        key = _normalize_username(username)
        if status not in VALID_STATUSES:
            raise ValueError(f"Invalid queue status: {status}")
        accounts: Dict[str, Any] = self._data.setdefault("accounts", {})
        prev = dict(accounts.get(key) or {})
        entry = {
            "status": status,
            "downloaded": int(
                downloaded if downloaded is not None else prev.get("downloaded") or 0
            ),
            "last_error": last_error or "",
            "updated_at": _utc_now(),
        }
        accounts[key] = entry
        self.save()

    def record_processed_today(self, n: int = 1) -> None:
        self._roll_day_if_needed()
        self._data["accounts_today"] = int(self._data.get("accounts_today") or 0) + int(n)
        self.save()

    def summary(self, daily_cap: int = DEFAULT_ACCOUNTS_PER_DAY) -> Dict[str, Any]:
        self._roll_day_if_needed()
        counts = {s: 0 for s in VALID_STATUSES}
        accounts: Dict[str, Any] = self._data.get("accounts") or {}
        for entry in accounts.values():
            status = (entry or {}).get("status", "pending")
            if status in counts:
                counts[status] += 1
            else:
                counts["pending"] += 1
        return {
            "day_key": self._data.get("day_key"),
            "accounts_today": int(self._data.get("accounts_today") or 0),
            "remaining_today": self.remaining_today(daily_cap),
            "daily_cap": int(daily_cap),
            "total": len(accounts),
            **counts,
        }
