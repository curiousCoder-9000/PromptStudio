#!/usr/bin/env python3
"""
Seed / re-order following_queue.json from bio keywords.

Priority (high → low):
  keyword-matched following → rest

This used to rank `classify:keep` / `classify:unsure` accounts first, from
`following_classify_report.json`. The glam classifier that wrote that report was
removed, so the branch went with it rather than being left to rank off a stale
file that can never be refreshed.

Does not start downloads — run download_following.py or UI Sync Afterward.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from promptstudio.config import (
    DEFAULT_BIO_KEYWORDS,
    FOLLOWING_LIST_FILE,
    QUEUE_PRIORITY_DEFAULT,
)
from promptstudio.scraping.filters import filter_following_entries, normalize_keywords
from promptstudio.scraping.queue import FollowingQueue

def _load_json(path: str):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Prioritize following sync queue from bio keywords"
    )
    parser.add_argument(
        "--following",
        default=FOLLOWING_LIST_FILE,
        help="following_list.json path",
    )
    parser.add_argument(
        "--keywords",
        type=str,
        default=",".join(DEFAULT_BIO_KEYWORDS),
        help="Bio keywords for baseline priority (empty = all public in list)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print plan only; do not write queue",
    )
    args = parser.parse_args()

    keywords = normalize_keywords(
        [k.strip() for k in args.keywords.split(",") if k.strip()] if args.keywords else []
    )

    keyword_users: list[str] = []
    if os.path.isfile(args.following):
        following = _load_json(args.following)
        if isinstance(following, list):
            filtered = filter_following_entries(following, keywords=keywords)
            keyword_users = [
                str(e.get("username") or "").lstrip("@").strip().lower()
                for e in filtered
                if e.get("username")
            ]
            keyword_users = [u for u in keyword_users if u]
    else:
        print(f"Missing following list: {args.following}")

    # Unique in priority order
    seen: set[str] = set()
    ordered: list[tuple[str, int, str]] = []

    def add_many(users: list[str], prio: int, reason: str) -> None:
        for u in users:
            if u in seen:
                continue
            seen.add(u)
            ordered.append((u, prio, reason))

    add_many(keyword_users, QUEUE_PRIORITY_DEFAULT, "bio:keyword")

    print(f"Plan: keyword_matched={len(ordered)} total_queue_writes={len(ordered)}")
    for u, p, r in ordered[:15]:
        print(f"  [{p:3d}] @{u}  ({r})")
    if len(ordered) > 15:
        print(f"  … +{len(ordered) - 15} more")

    if args.dry_run:
        print("Dry-run — queue not modified")
        return

    q = FollowingQueue()
    added = 0
    bumped = 0
    for user, prio, reason in ordered:
        before = (q._data.get("accounts") or {}).get(user)
        n = q.ensure_accounts([user], priority=prio, reason=reason)
        if n:
            added += n
        else:
            # ensure may bump priority without counting as added
            after = (q._data.get("accounts") or {}).get(user) or {}
            if before and int(after.get("priority") or 0) > int(
                (before or {}).get("priority") or 0
            ):
                bumped += 1
            elif not before:
                q.set_priority(user, prio, reason=reason)
                added += 1

    summary = q.summary()
    print(
        f"Queue updated: ensure_added≈{added}, priority_updates≈{bumped}, "
        f"pending={summary.get('pending')}, high_priority_pending={summary.get('high_priority_pending')}"
    )
    print("Next: py scripts/download_following.py --accounts-per-day 15 --max-posts 30")


if __name__ == "__main__":
    main()
