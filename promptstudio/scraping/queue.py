"""Persistent following-sync account queue with daily budget tracking."""

from __future__ import annotations

import json
import os
from datetime import date, datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple

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

    def ensure_accounts(
        self,
        usernames: Iterable[str],
        *,
        priority: Optional[int] = None,
        reason: str = "",
    ) -> int:
        """Merge usernames as pending. Never overwrite done/skipped/error status.

        When an account already exists as pending, optionally raise priority if
        the new value is higher. When missing, create with optional priority/reason.
        """
        added = 0
        updated = 0
        accounts: Dict[str, Any] = self._data.setdefault("accounts", {})
        for raw in usernames:
            key = _normalize_username(raw)
            if not key:
                continue
            if key not in accounts:
                entry: Dict[str, Any] = {
                    "status": "pending",
                    "downloaded": 0,
                    "last_error": "",
                    "updated_at": _utc_now(),
                }
                if priority is not None:
                    entry["priority"] = int(priority)
                if reason:
                    entry["reason"] = reason
                accounts[key] = entry
                added += 1
                continue
            # Existing entry: bump priority on pending only
            if priority is None:
                continue
            entry = accounts[key]
            if not isinstance(entry, dict):
                continue
            if (entry.get("status") or "pending") != "pending":
                continue
            prev_p = int(entry.get("priority") or 0)
            if int(priority) > prev_p:
                entry["priority"] = int(priority)
                if reason:
                    entry["reason"] = reason
                entry["updated_at"] = _utc_now()
                updated += 1
        if added or updated:
            self.save()
        return added

    def set_priority(
        self,
        username: str,
        priority: int,
        *,
        reason: str = "",
        requeue: bool = False,
    ) -> bool:
        """Set priority on an account. Optionally reset done/error → pending."""
        key = _normalize_username(username)
        if not key:
            return False
        accounts: Dict[str, Any] = self._data.setdefault("accounts", {})
        entry = dict(accounts.get(key) or {})
        if not entry:
            entry = {
                "status": "pending",
                "downloaded": 0,
                "last_error": "",
            }
        if requeue and entry.get("status") in ("done", "error", "skipped"):
            entry["status"] = "pending"
            entry["last_error"] = ""
        entry["priority"] = int(priority)
        if reason:
            entry["reason"] = reason
        entry["updated_at"] = _utc_now()
        accounts[key] = entry
        self.save()
        return True

    def next_pending(
        self,
        limit: int,
        *,
        daily_cap: int = DEFAULT_ACCOUNTS_PER_DAY,
        only: Optional[Iterable[str]] = None,
    ) -> List[str]:
        """Return up to `limit` pending usernames, capped by remaining daily budget.

        Ordered by priority desc, then username. If `only` is set, restrict to that set.
        """
        self._roll_day_if_needed()
        budget = min(int(limit), self.remaining_today(daily_cap))
        if budget <= 0:
            return []
        allow = None
        if only is not None:
            allow = {_normalize_username(u) for u in only if u}
        accounts: Dict[str, Any] = self._data.get("accounts") or {}
        candidates: List[Tuple[int, str]] = []
        for username, entry in accounts.items():
            if allow is not None and username not in allow:
                continue
            status = (entry or {}).get("status", "pending")
            if status != "pending":
                continue
            prio = int((entry or {}).get("priority") or 0)
            candidates.append((prio, username))
        # Higher priority first; stable by username
        candidates.sort(key=lambda t: (-t[0], t[1]))
        return [u for _p, u in candidates[:budget]]

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
        # Preserve priority / reason across status transitions
        if "priority" in prev:
            entry["priority"] = prev["priority"]
        if prev.get("reason"):
            entry["reason"] = prev["reason"]
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
        high_priority_pending = 0
        for entry in accounts.values():
            status = (entry or {}).get("status", "pending")
            if status in counts:
                counts[status] += 1
            else:
                counts["pending"] += 1
            if status == "pending" and int((entry or {}).get("priority") or 0) >= 50:
                high_priority_pending += 1
        return {
            "day_key": self._data.get("day_key"),
            "accounts_today": int(self._data.get("accounts_today") or 0),
            "remaining_today": self.remaining_today(daily_cap),
            "daily_cap": int(daily_cap),
            "total": len(accounts),
            "high_priority_pending": high_priority_pending,
            **counts,
        }
