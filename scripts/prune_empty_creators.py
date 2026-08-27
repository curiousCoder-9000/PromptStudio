#!/usr/bin/env python3
"""Remove creator folders that contain no photos or videos.

Folders left behind when a scrape created the directory then downloaded
nothing, or after the last photo was deleted. Leftover sidecars/json without
media count as empty. System folders (_thumbs, _trash, …) are never touched.

Usage:
    py scripts/prune_empty_creators.py
    py scripts/prune_empty_creators.py --dry-run
"""

import argparse
import os
import sys

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from promptstudio.scraping.organizer import prune_empty_creator_folders


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Remove creator folders that contain no photos or videos"
    )
    ap.add_argument(
        "--dry-run",
        action="store_true",
        help="list empty folders, do not delete",
    )
    args = ap.parse_args()
    pruned = prune_empty_creator_folders(dry_run=args.dry_run, log=print)
    if not pruned:
        print("No empty creator folders.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
