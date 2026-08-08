#!/usr/bin/env python3
"""Sync all photos & reels for creators who already have local folders.

Matches local archive folders to following_list.json entries and runs
sync_creator_feed() for each with anti-ban pacing.

Usage:
    py scripts/sync_all_local_creators.py --accounts-per-day 20 --max-posts 500 --include-reels
"""

import argparse
import json
import os
import random
import sys
import time
from datetime import datetime, timezone

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

# Fix Windows console encoding
if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from promptstudio.config import (
    ACCOUNT_PAUSE_MAX_SEC,
    ACCOUNT_PAUSE_MIN_SEC,
    BATCH_PAUSE_EVERY,
    BATCH_PAUSE_MAX_SEC,
    BATCH_PAUSE_MIN_SEC,
    DEFAULT_ACCOUNTS_PER_DAY,
    DEFAULT_MAX_POSTS_PER_CREATOR,
    EXCLUDED_FOLDERS,
    FOLLOWING_LIST_FILE,
    SAVED_DIR,
)
from promptstudio.scraping.downloader import InstagramDownloader

# Folders that are system/utility, not creator folders
SKIP_FOLDERS = EXCLUDED_FOLDERS


def load_following_list(path: str) -> list[dict]:
    """Load the following_list.json."""
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def get_local_creator_folders(base_dir: str) -> list[str]:
    """Return sorted list of creator folder names."""
    folders = []
    for name in os.listdir(base_dir):
        full = os.path.join(base_dir, name)
        if not os.path.isdir(full):
            continue
        if name.startswith((".", "_")):
            continue
        if name.lower() in {s.lower() for s in SKIP_FOLDERS}:
            continue
        folders.append(name)
    folders.sort()
    return folders


def match_folders_to_accounts(
    folders: list[str], following: list[dict]
) -> dict[str, dict]:
    """Match local folder names to following_list entries.

    Returns dict mapping folder_name -> following entry (with 'username' key).
    Uses direct match first, then substring/fuzzy matching.
    """
    # Build lookup by username
    by_username: dict[str, dict] = {}
    for entry in following:
        uname = entry.get("username", "")
        if uname:
            by_username[uname.lower()] = entry

    matched: dict[str, dict] = {}
    unmatched: list[str] = []

    for folder in folders:
        folder_lower = folder.lower()

        # 1. Direct match: folder name IS the username
        if folder_lower in by_username:
            matched[folder] = by_username[folder_lower]
            continue

        # 2. Folder is a prefix of a username (e.g., "jvivid" -> "jvivid_euna")
        prefix_matches = [
            entry for uname, entry in by_username.items()
            if uname.startswith(folder_lower) and uname != folder_lower
        ]
        if len(prefix_matches) == 1:
            matched[folder] = prefix_matches[0]
            continue

        # 3. Folder is a suffix/contains match
        contains_matches = [
            entry for uname, entry in by_username.items()
            if folder_lower in uname and uname != folder_lower
        ]
        if len(contains_matches) == 1:
            matched[folder] = contains_matches[0]
            continue

        # 4. Username contains folder name in full_name
        name_matches = [
            entry for entry in following
            if folder_lower in (entry.get("full_name", "") or "").lower()
            and entry.get("username", "")
        ]
        if len(name_matches) == 1:
            matched[folder] = name_matches[0]
            continue

        unmatched.append(folder)

    return matched, unmatched


def load_sync_progress(progress_file: str) -> dict:
    """Load progress tracking file."""
    if os.path.isfile(progress_file):
        with open(progress_file, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"synced": {}, "day_key": "", "accounts_today": 0}


def save_sync_progress(progress_file: str, progress: dict) -> None:
    """Save progress tracking file."""
    with open(progress_file, "w", encoding="utf-8") as f:
        json.dump(progress, f, indent=2, ensure_ascii=False)


def main():
    parser = argparse.ArgumentParser(
        description="Sync all photos & reels for local creators from following list"
    )
    parser.add_argument(
        "--accounts-per-day",
        type=int,
        default=DEFAULT_ACCOUNTS_PER_DAY,
        help=f"Max accounts to process this run (default: {DEFAULT_ACCOUNTS_PER_DAY})",
    )
    parser.add_argument(
        "--max-posts",
        type=int,
        default=DEFAULT_MAX_POSTS_PER_CREATOR,
        help=f"Max posts per account (default: {DEFAULT_MAX_POSTS_PER_CREATOR})",
    )
    parser.add_argument(
        "--include-reels",
        action="store_true",
        help="Also download reels / video posts",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show matched accounts without downloading",
    )
    parser.add_argument(
        "--reset",
        action="store_true",
        help="Reset progress tracker (re-sync all accounts)",
    )
    args = parser.parse_args()

    base_dir = os.path.expanduser(SAVED_DIR)
    progress_file = os.path.join(base_dir, "local_sync_progress.json")

    # Load following list and local folders
    if not os.path.isfile(FOLLOWING_LIST_FILE):
        print(f"Missing {FOLLOWING_LIST_FILE} — run export_following_list.py first")
        sys.exit(1)

    following = load_following_list(FOLLOWING_LIST_FILE)
    folders = get_local_creator_folders(base_dir)
    matched, unmatched = match_folders_to_accounts(folders, following)

    print(f"Local creator folders: {len(folders)}")
    print(f"Matched to following list: {len(matched)}")
    print(f"Unmatched (skipped): {len(unmatched)}")
    if unmatched:
        print(f"  Unmatched: {', '.join(unmatched[:20])}")
        if len(unmatched) > 20:
            print(f"  ... and {len(unmatched) - 20} more")
    print()

    # Load/reset progress
    if args.reset and os.path.isfile(progress_file):
        os.remove(progress_file)
        print("Progress reset.")

    progress = load_sync_progress(progress_file)

    # Day rollover
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if progress.get("day_key") != today:
        progress["day_key"] = today
        progress["accounts_today"] = 0

    # Filter out already-synced accounts
    pending = {
        folder: entry
        for folder, entry in matched.items()
        if entry["username"] not in progress.get("synced", {})
    }

    already_done = len(matched) - len(pending)
    budget = max(0, args.accounts_per_day - progress.get("accounts_today", 0))

    print(f"Already synced (previous runs): {already_done}")
    print(f"Pending this run: {len(pending)}")
    print(f"Daily budget remaining: {budget}/{args.accounts_per_day}")
    print(f"Max posts per account: {args.max_posts}")
    print(f"Include reels: {args.include_reels}")
    print()

    if budget <= 0:
        print("Daily budget exhausted. Run again tomorrow.")
        sys.exit(0)

    # Sort by media count (ascending) — do smaller accounts first
    to_process = sorted(
        pending.items(),
        key=lambda kv: kv[1].get("media_count", 0) or 0,
    )
    batch = to_process[:budget]

    if args.dry_run:
        print("=== DRY RUN — would process these accounts ===")
        for i, (folder, entry) in enumerate(batch, 1):
            username = entry["username"]
            media = entry.get("media_count", "?")
            name = entry.get("full_name", "")
            print(f"  {i:3d}. @{username} ({name}) — {media} posts [folder: {folder}]")
        print(f"\nTotal: {len(batch)} accounts")
        remaining = len(pending) - len(batch)
        if remaining > 0:
            days = (remaining + args.accounts_per_day - 1) // args.accounts_per_day
            print(f"Remaining after this run: {remaining} ({days} more days)")
        return

    # Download
    downloader = InstagramDownloader()
    processed = 0
    total_downloaded = 0
    total_errors = 0

    for folder, entry in batch:
        username = entry["username"]
        media_count = entry.get("media_count", "?")
        name = entry.get("full_name", "")

        processed += 1
        print(f"\n{'='*60}")
        print(
            f"[{processed}/{len(batch)}] @{username} ({name}) "
            f"— {media_count} posts [folder: {folder}]"
        )
        print(f"{'='*60}")

        result = downloader.sync_creator_feed(
            username,
            max_posts=args.max_posts,
            include_videos=args.include_reels,
        )

        total_downloaded += result.downloaded
        total_errors += result.errors

        # Record progress
        progress["synced"][username] = {
            "folder": folder,
            "downloaded": result.downloaded,
            "skipped": result.skipped,
            "errors": result.errors,
            "aborted": result.aborted,
            "synced_at": datetime.now(timezone.utc).isoformat(),
        }
        progress["accounts_today"] = progress.get("accounts_today", 0) + 1
        save_sync_progress(progress_file, progress)

        if result.aborted:
            print(f"\nABORTED on @{username}: {result.abort_reason}")
            print("Stopping — resume tomorrow.")
            break

        # Anti-ban pause between accounts
        if processed < len(batch):
            delay = random.uniform(ACCOUNT_PAUSE_MIN_SEC, ACCOUNT_PAUSE_MAX_SEC)
            print(f"Account pause: {delay:.0f}s")
            time.sleep(delay)

            # Batch pause every N accounts
            if BATCH_PAUSE_EVERY > 0 and processed % BATCH_PAUSE_EVERY == 0:
                batch_delay = random.uniform(BATCH_PAUSE_MIN_SEC, BATCH_PAUSE_MAX_SEC)
                print(
                    f"Batch pause after {processed} accounts "
                    f"— waiting {batch_delay / 60:.1f} min"
                )
                time.sleep(batch_delay)

    # Summary
    remaining = len(pending) - processed
    print(f"\n{'='*60}")
    print("SYNC COMPLETE")
    print(f"{'='*60}")
    print(f"Accounts processed: {processed}")
    print(f"Total downloaded: {total_downloaded}")
    print(f"Total errors: {total_errors}")
    print(f"Remaining: {remaining}")
    if remaining > 0:
        days = (remaining + args.accounts_per_day - 1) // args.accounts_per_day
        print(f"Estimated days remaining: {days}")
    save_sync_progress(progress_file, progress)


if __name__ == "__main__":
    main()
