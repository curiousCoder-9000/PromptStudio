"""Instagram scrape cooldown after an automation / bot warning.

Instagram's "we detected an automated process" flag is account-level. Switching
tools (gallery-dl → Instaloader) does not clear it; more scraping while it is
hot makes a lock more likely. This module is the gate: every Instagram start
path reads `ig_cooldown.json` and refuses until `until`.
"""

from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

from promptstudio.config import IG_COOLDOWN_FILE, ig_cooldown_hours
from promptstudio.logging_setup import get_logger
from promptstudio.scraping.results import SyncResult
from promptstudio.storage.atomic import atomic_write_json

log = get_logger(__name__)

_LOCK = threading.Lock()


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_until(raw: Any) -> Optional[datetime]:
    text = str(raw or "").strip()
    if not text:
        return None
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _human(seconds: int) -> str:
    seconds = max(0, int(seconds))
    days, rem = divmod(seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes = rem // 60
    parts: list[str] = []
    if days:
        parts.append(f"{days}d")
    if hours:
        parts.append(f"{hours}h")
    if minutes and not days:
        parts.append(f"{minutes}m")
    return " ".join(parts) or "<1m"


def _load() -> Dict[str, Any]:
    if not os.path.isfile(IG_COOLDOWN_FILE):
        return {}
    try:
        with open(IG_COOLDOWN_FILE, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def status() -> Dict[str, Any]:
    """Live snapshot. `active` is the only field callers need to branch on."""
    data = _load()
    until = _parse_until(data.get("until"))
    now = _utc_now()
    if until is None or until <= now:
        return {
            "active": False,
            "until": None,
            "reason": "",
            "remaining_sec": 0,
            "remaining_human": "",
        }
    remaining = int((until - now).total_seconds())
    return {
        "active": True,
        "until": until.astimezone(timezone.utc).isoformat(),
        "reason": str(data.get("reason") or ""),
        "remaining_sec": remaining,
        "remaining_human": _human(remaining),
    }


def block_message() -> Optional[str]:
    snap = status()
    if not snap["active"]:
        return None
    until = snap["until"] or ""
    human = snap["remaining_human"]
    reason = snap["reason"] or "Instagram flagged an automated process"
    return (
        f"Instagram scraping is cooling down until {until} ({human} left). "
        f"{reason}. Do not scrape until then — switching tools will not help."
    )


def engage(
    *,
    hours: Optional[float] = None,
    reason: str = "Instagram flagged an automated process",
) -> Dict[str, Any]:
    """Start (or refresh) the sit-out. Writes `ig_cooldown.json` atomically."""
    wait = float(ig_cooldown_hours() if hours is None else hours)
    if wait <= 0:
        return clear()
    until = _utc_now() + timedelta(hours=wait)
    payload = {
        "until": until.isoformat(),
        "set_at": _utc_now().isoformat(),
        "reason": reason,
        "hours": wait,
    }
    with _LOCK:
        atomic_write_json(IG_COOLDOWN_FILE, payload)
    log.warning("instagram cooldown until %s (%s)", payload["until"], reason)
    return status()


def clear() -> Dict[str, Any]:
    """Drop the cooldown file so the next scrape is allowed."""
    with _LOCK:
        try:
            if os.path.isfile(IG_COOLDOWN_FILE):
                os.remove(IG_COOLDOWN_FILE)
        except OSError as exc:
            log.warning("could not clear instagram cooldown: %s", exc)
    return status()


def refuse_instagram_scrape(
    ctx: Any = None, *, job_type: str = "creator"
) -> Optional[SyncResult]:
    """SyncResult if a scrape must not start, else None."""
    msg = block_message()
    if not msg:
        return None
    result = SyncResult(job_type=job_type, source="instagram")
    result.aborted = True
    result.stop_reason = "cooldown"
    result.messages.append(msg)
    if ctx is not None:
        try:
            ctx.log(msg)
        except Exception:
            pass
    return result
