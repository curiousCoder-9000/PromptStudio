#!/usr/bin/env python3
"""
Export Instagram following list — 429 avoidance is mandatory.

Strategy (Instaloader docs + field experience):
- Reuse saved session only (never login loops)
- NEVER call Profile.from_username / web_profile_info (main 429 source)
- Build own Profile from sessionid userid + edge GraphQL only
- Resume pagination via NodeIterator freeze/thaw (do not re-walk from page 1)
- Read only edge-node fields: username, full_name, is_private, is_verified, id
- Very strict RateController + long jitter between new rows
- max_connection_attempts=1 so we own cooldown (no rapid retries)
- Single-writer lock file so two exporters cannot clobber the JSON
- On any 429/connection fault: save + freeze, sleep 45–75 min, resume
- Outer loop until collected >= reported followee count
"""

from __future__ import annotations

import atexit
import json
import os
import random
import sys
import time
import urllib.parse
from typing import Any, Dict, List, Optional, Set, Tuple

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

import instaloader

from promptstudio.config import FOLLOWING_LIST_FILE, SESSION_USER
from promptstudio.scraping.session import load_session

MD_OUTPUT = "docs/following_list.md"

# Ultra-conservative pacing — prefer slow completion over any 429
JITTER_MIN_SEC = 12.0
JITTER_MAX_SEC = 22.0
PAUSE_EVERY_N = 8
PAUSE_EVERY_SEC = (90.0, 180.0)
COOLDOWN_ON_429_SEC = (45 * 60, 75 * 60)
PRESTART_COOLDOWN_SEC = (3 * 60, 5 * 60)  # short cool-off; freeze resume is cheap
SAVE_EVERY_N = 5
SLIDING_WINDOW_QUERIES = 6
MAX_OUTER_ROUNDS = 40
HEARTBEAT_SEC = 60.0


class UltraSafeRateController(instaloader.RateController):
    def count_per_sliding_window(self, query_type: str) -> int:
        return SLIDING_WINDOW_QUERIES

    def wait_before_query(self, query_type: str) -> None:
        time.sleep(random.uniform(2.0, 5.0))
        super().wait_before_query(query_type)


def _log(msg: str) -> None:
    print(msg, flush=True)


def _paths(out_path: str) -> Tuple[str, str, str]:
    freeze_path = out_path + ".freeze"
    lock_path = out_path + ".lock"
    target_path = out_path + ".target"
    return freeze_path, lock_path, target_path


def _load_partial(path: str) -> List[Dict[str, Any]]:
    if not os.path.isfile(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _save_json(path: str, rows: List[Dict[str, Any]]) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    for i, row in enumerate(rows, start=1):
        row["index"] = i
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(rows, f, indent=2, ensure_ascii=False)
    os.replace(tmp, path)


def _write_markdown(rows: List[Dict[str, Any]]) -> None:
    os.makedirs(os.path.dirname(MD_OUTPUT), exist_ok=True)
    with open(MD_OUTPUT, "w", encoding="utf-8") as f:
        f.write(f"# Instagram Following List (@{SESSION_USER})\n\n")
        f.write(f"**Total Accounts Followed:** `{len(rows)}`  \n")
        f.write(f"**Last Updated:** `{time.strftime('%Y-%m-%d %H:%M:%S')}`\n\n")
        f.write(
            "| # | Username | Full Name | Verified | Private | Media | Bio | Link |\n"
        )
        f.write("|---|---|---|---|---|---|---|---|\n")
        for item in rows:
            ver = "yes" if item.get("is_verified") else "no"
            priv = "private" if item.get("is_private") else "public"
            media = item.get("media_count")
            media_s = "—" if media is None else str(media)
            bio_snip = (
                (item.get("biography") or "").replace("|", "/").replace("\n", " ")[:40]
            )
            f.write(
                f"| {item['index']} | `@{item['username']}` | {item.get('full_name') or ''} | "
                f"{ver} | {priv} | {media_s} | {bio_snip} | "
                f"[Profile]({item.get('profile_url')}) |\n"
            )


def _edge_fields(followee) -> Dict[str, Any]:
    """Only use GraphQL edge node fields — never trigger profile metadata fetch."""
    node = getattr(followee, "_node", None) or {}
    username = node.get("username")
    if not username:
        username = followee.__dict__.get("username") or ""
    return {
        "username": username,
        "full_name": node.get("full_name") or "",
        "is_private": bool(node.get("is_private", False)),
        "is_verified": bool(node.get("is_verified", False)),
        "user_id": str(node.get("id") or node.get("pk") or ""),
        "biography": "",
        "media_count": None,
        "followers_count": None,
    }


def _cooldown(seconds_range: tuple, reason: str) -> None:
    wait = random.uniform(*seconds_range)
    deadline = time.time() + wait
    _log(f">>> {reason} — sleeping {wait / 60:.1f} min (no requests)…")
    while True:
        remaining = deadline - time.time()
        if remaining <= 0:
            break
        time.sleep(min(HEARTBEAT_SEC, remaining))
        left = deadline - time.time()
        if left > 0:
            _log(f"  …cooldown heartbeat, {left / 60:.1f} min left")


def _make_loader() -> instaloader.Instaloader:
    return instaloader.Instaloader(
        max_connection_attempts=1,
        request_timeout=90,
        rate_controller=lambda ctx: UltraSafeRateController(ctx),
    )


def _userid_from_session(L: instaloader.Instaloader) -> int:
    """Resolve own userid without web_profile_info."""
    raw = L.context._session.cookies.get("ds_user_id") or ""
    if str(raw).isdigit():
        return int(raw)
    sid = urllib.parse.unquote(L.context._session.cookies.get("sessionid") or "")
    head = sid.split(":", 1)[0].strip()
    if head.isdigit():
        return int(head)
    raise RuntimeError(
        "Could not resolve userid from session cookies "
        "(ds_user_id/sessionid). Re-login Instaloader session."
    )


def _own_profile(L: instaloader.Instaloader) -> instaloader.Profile:
    """
    Build own Profile without api/v1/users/web_profile_info/.

    get_followees() only needs userid + username when _has_full_metadata is True.
    """
    userid = _userid_from_session(L)
    profile = instaloader.Profile(
        L.context,
        {"id": str(userid), "username": SESSION_USER, "pk": str(userid)},
    )
    profile._has_full_metadata = True
    _log(f"Own profile built from session (id={userid}, @{SESSION_USER}) — no web_profile_info")
    return profile


def _load_target(path: str) -> Optional[int]:
    try:
        with open(path, "r", encoding="utf-8") as f:
            n = int(f.read().strip())
        return n if n > 0 else None
    except Exception:
        return None


def _save_target(path: str, total: int) -> None:
    with open(path, "w", encoding="utf-8") as f:
        f.write(str(total))


def _acquire_lock(lock_path: str) -> None:
    if os.path.isfile(lock_path):
        try:
            with open(lock_path, "r", encoding="utf-8") as f:
                old_pid = int((f.read() or "0").strip() or "0")
        except Exception:
            old_pid = 0
        if old_pid:
            try:
                os.kill(old_pid, 0)
            except OSError:
                _log(f"Removing stale lock (pid {old_pid} dead)")
            except Exception:
                pass
            else:
                raise SystemExit(
                    f"Another export is already running (pid {old_pid}, lock {lock_path}). "
                    "Stop it before starting a second one."
                )
    with open(lock_path, "w", encoding="utf-8") as f:
        f.write(str(os.getpid()))

    def _release() -> None:
        try:
            if os.path.isfile(lock_path):
                with open(lock_path, "r", encoding="utf-8") as f:
                    if f.read().strip() == str(os.getpid()):
                        os.remove(lock_path)
        except Exception:
            pass

    atexit.register(_release)


def _save_freeze(iterator, freeze_path: str) -> None:
    try:
        instaloader.save_structure_to_file(iterator.freeze(), freeze_path)
        _log(f"Freeze saved -> {freeze_path}")
    except Exception as e:
        _log(f"Freeze save failed (non-fatal): {e}")


def _thaw_if_present(iterator, freeze_path: str) -> bool:
    if not os.path.isfile(freeze_path):
        return False
    try:
        frozen = instaloader.load_structure_from_file(iterator._context, freeze_path)
        iterator.thaw(frozen)
        _log(f"Resumed GraphQL cursor from freeze (total_index={iterator.total_index})")
        return True
    except Exception as e:
        _log(f"Freeze thaw failed — starting from page 1 ({e})")
        try:
            os.remove(freeze_path)
        except OSError:
            pass
        return False


def _one_pass(
    following_data: List[Dict[str, Any]],
    seen: Set[str],
    out_path: str,
    freeze_path: str,
    target_path: str,
    cached_total: Optional[int],
) -> tuple[int, Optional[int], bool]:
    """
    Walk followees once. Returns (new_added, reported_total, hit_rate_limit).
    """
    L = _make_loader()
    load_session(L, SESSION_USER)
    _log("Session loaded.")

    try:
        profile = _own_profile(L)
    except Exception as e:
        _log(f"Blocked before profile build: {e}")
        return 0, cached_total, True

    try:
        iterator = profile.get_followees()
    except (
        instaloader.exceptions.TooManyRequestsException,
        instaloader.exceptions.ConnectionException,
        instaloader.exceptions.QueryReturnedBadRequestException,
        instaloader.exceptions.LoginRequiredException,
    ) as e:
        _log(f"Blocked creating followees iterator: {e}")
        return 0, cached_total, True

    _thaw_if_present(iterator, freeze_path)

    total = cached_total
    added = 0
    scanned = 0

    try:
        for followee in iterator:
            scanned += 1
            if total is None and iterator.count is not None:
                total = int(iterator.count)
                _save_target(target_path, total)
                _log(f"Instagram reports following count: {total}")
                _log(
                    f"Have {len(following_data)} so far — "
                    f"need {max(0, total - len(following_data))} more"
                )

            try:
                fields = _edge_fields(followee)
            except (
                instaloader.exceptions.TooManyRequestsException,
                instaloader.exceptions.ConnectionException,
            ) as e:
                _log(f"Rate/connection while reading edge node: {e}")
                _save_json(out_path, following_data)
                _write_markdown(following_data)
                _save_freeze(iterator, freeze_path)
                return added, total, True

            username = (fields.get("username") or "").lower()
            if not username:
                continue
            if username in seen:
                if scanned % 25 == 0:
                    _log(f"  …skipping already-saved (@{username}, scanned={scanned})")
                    time.sleep(random.uniform(2.0, 4.0))
                continue

            added += 1
            item = {
                "index": len(following_data) + 1,
                "username": fields["username"],
                "full_name": fields.get("full_name") or "",
                "biography": "",
                "is_verified": fields.get("is_verified", False),
                "is_private": fields.get("is_private", False),
                "profile_url": f"https://www.instagram.com/{fields['username']}/",
                "media_count": None,
                "followers_count": None,
                "user_id": fields.get("user_id") or "",
            }
            following_data.append(item)
            seen.add(username)

            label = f"{len(following_data)}" + (f"/{total}" if total else "")
            if len(following_data) % SAVE_EVERY_N == 0:
                _save_json(out_path, following_data)
                _save_freeze(iterator, freeze_path)
                _log(f"[{label}] checkpoint (@{item['username']}, +{added} this pass)")
            else:
                _log(f"[{label}] @{item['username']}")

            time.sleep(random.uniform(JITTER_MIN_SEC, JITTER_MAX_SEC))
            if added % PAUSE_EVERY_N == 0:
                pause = random.uniform(*PAUSE_EVERY_SEC)
                _log(f"  …safety pause {pause:.0f}s after {added} new this pass")
                time.sleep(pause)

            if total is not None and len(following_data) >= total:
                break

    except (
        instaloader.exceptions.TooManyRequestsException,
        instaloader.exceptions.ConnectionException,
        instaloader.exceptions.QueryReturnedBadRequestException,
    ) as e:
        _log(f"Pass interrupted by rate/connection: {e}")
        _save_json(out_path, following_data)
        _write_markdown(following_data)
        _save_freeze(iterator, freeze_path)
        return added, total, True
    except Exception as e:
        _log(f"Pass error (saved partial): {e}")
        _save_json(out_path, following_data)
        _write_markdown(following_data)
        _save_freeze(iterator, freeze_path)
        return added, total, True

    _save_json(out_path, following_data)
    _write_markdown(following_data)
    if total is not None and len(following_data) >= total:
        try:
            os.remove(freeze_path)
        except OSError:
            pass
    else:
        _save_freeze(iterator, freeze_path)
    return added, total, False


def fetch_following_list() -> None:
    out_path = FOLLOWING_LIST_FILE
    freeze_path, lock_path, target_path = _paths(out_path)

    _log(f"=== Ultra-safe following export for @{SESSION_USER} ===")
    _log("Rules: no web_profile_info, freeze/thaw resume, edge-fields only, 45–75m on 429")
    _log("Keep Instagram app/web CLOSED. One Instaloader instance only.\n")

    _acquire_lock(lock_path)

    # Pre-start cool-off after recent activity
    _cooldown(PRESTART_COOLDOWN_SEC, "Pre-start cooldown to avoid immediate 429")

    following_data = _load_partial(out_path)
    seen: Set[str] = {
        (row.get("username") or "").lower()
        for row in following_data
        if row.get("username")
    }
    if following_data:
        _log(f"Resuming with {len(following_data)} accounts already saved")

    reported_total: Optional[int] = _load_target(target_path)
    if reported_total:
        _log(f"Cached target following count: {reported_total}")

    for round_i in range(1, MAX_OUTER_ROUNDS + 1):
        _log(f"\n===== PASS {round_i}/{MAX_OUTER_ROUNDS} =====")
        # Re-load from disk each pass in case of external merge
        following_data = _load_partial(out_path)
        seen = {
            (row.get("username") or "").lower()
            for row in following_data
            if row.get("username")
        }

        added, total, hit_limit = _one_pass(
            following_data, seen, out_path, freeze_path, target_path, reported_total
        )
        if total is not None:
            reported_total = total
            _save_target(target_path, total)

        _log(
            f"Pass {round_i} done: +{added} new, total saved={len(following_data)}"
            + (f"/{reported_total}" if reported_total else "")
        )

        if reported_total is not None and len(following_data) >= reported_total:
            _save_json(out_path, following_data)
            _write_markdown(following_data)
            try:
                os.remove(freeze_path)
            except OSError:
                pass
            _log(f"\n=== COMPLETE: {len(following_data)}/{reported_total} following ===")
            return

        if hit_limit:
            _cooldown(COOLDOWN_ON_429_SEC, "429/connection cool-down before next pass")
            continue

        if added == 0:
            if reported_total and len(following_data) >= reported_total:
                _log("Complete.")
                return
            _log(
                "No new accounts this pass but count < reported total. "
                "Long cool-down then retry…"
            )
            _cooldown(COOLDOWN_ON_429_SEC, "Empty-pass cool-down")
            continue

        _cooldown((60.0, 120.0), "Between-pass pause")

    _save_json(out_path, following_data)
    _write_markdown(following_data)
    _log(
        f"Stopped after {MAX_OUTER_ROUNDS} passes with {len(following_data)} accounts. "
        "Re-run script to continue."
    )


if __name__ == "__main__":
    fetch_following_list()
