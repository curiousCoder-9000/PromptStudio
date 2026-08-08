#!/usr/bin/env python3
"""
Seed / re-order following_queue.json from classify report + bio keywords.

Priority (high → low):
  keep (classify) → unsure (classify) → keyword-matched following → rest

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
    QUEUE_PRIORITY_KEEP,
    QUEUE_PRIORITY_UNSURE,
)
from promptstudio.scraping.filters import filter_following_entries, normalize_keywords
from promptstudio.scraping.queue import FollowingQueue

DEFAULT_REPORT = os.path.join(_REPO_ROOT, "following_classify_report.json")


def _load_json(path: str):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Prioritize following sync queue from classify report"
    )
    parser.add_argument(
        "--report",
        default=DEFAULT_REPORT,
        help=f"Classify report JSON (default: {DEFAULT_REPORT})",
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
        "--requeue-keep",
        action="store_true",
        help="Reset done/error keep accounts back to pending so they re-sync",
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

    keep: list[str] = []
    unsure: list[str] = []
    unfollow: set[str] = set()
    if os.path.isfile(args.report):
        report = _load_json(args.report)
        accounts = report.get("accounts") or {}
        if isinstance(accounts, dict):
            for key, row in accounts.items():
                if not isinstance(row, dict):
                    continue
                user = (row.get("username") or key or "").lstrip("@").strip().lower()
                if not user:
                    continue
                decision = (row.get("decision") or "").lower()
                if decision == "keep":
                    keep.append(user)
                elif decision == "unsure":
                    unsure.append(user)
                elif decision == "unfollow":
                    unfollow.add(user)
    else:
        print(f"No classify report at {args.report} — keyword-only prioritization")

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
            keyword_users = [u for u in keyword_users if u and u not in unfollow]
    else:
        print(f"Missing following list: {args.following}")

    # Unique in priority order
    seen: set[str] = set()
    ordered: list[tuple[str, int, str]] = []

    def add_many(users: list[str], prio: int, reason: str) -> None:
        for u in users:
            if u in seen or u in unfollow:
                continue
            seen.add(u)
            ordered.append((u, prio, reason))

    add_many(keep, QUEUE_PRIORITY_KEEP, "classify:keep")
    add_many(unsure, QUEUE_PRIORITY_UNSURE, "classify:unsure")
    add_many(keyword_users, QUEUE_PRIORITY_DEFAULT, "bio:keyword")

    print(
        f"Plan: keep={len(keep)} unsure={len(unsure)} "
        f"keyword_extra≈{max(0, len(ordered) - len(keep) - len(unsure))} "
        f"unfollow_skip={len(unfollow)} total_queue_writes={len(ordered)}"
    )
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
        if args.requeue_keep and reason == "classify:keep":
            q.set_priority(user, prio, reason=reason, requeue=True)
            bumped += 1
            continue
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
