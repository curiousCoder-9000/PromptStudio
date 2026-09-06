#!/usr/bin/env python3
"""
Find near-duplicate media in the archive by perceptual hash.

Catches what `deduplicate.py` cannot: the same picture re-encoded, resized,
lightly cropped, or reposted by a second creator — all byte-different.

    py scripts/find_duplicates.py                  # hash what is missing, then report
    py scripts/find_duplicates.py --distance 4     # stricter (near-identical only)
    py scripts/find_duplicates.py --rehash         # recompute every hash
    py scripts/find_duplicates.py --json dupes.json

Report-only by design. It never deletes: grouping is a heuristic, and deletions
belong in the UI where they land in the trash and can be undone.
"""

import argparse
import json
import os
import sys
import time

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from promptstudio.config import SAVED_DIR
from promptstudio.storage.db import ArchiveIndex
from promptstudio.storage.dedupe import (
    DEFAULT_MAX_DISTANCE,
    find_near_duplicate_groups,
    hash_indexed_photos,
    pick_keeper,
)


def _human(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f}{unit}" if unit == "B" else f"{n:.1f}{unit}"
        n /= 1024.0
    return str(n)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--distance",
        type=int,
        default=DEFAULT_MAX_DISTANCE,
        help=f"max differing bits, 0-64 (default {DEFAULT_MAX_DISTANCE}; 0-4 = re-encode only)",
    )
    ap.add_argument("--rehash", action="store_true", help="recompute hashes that exist")
    ap.add_argument("--creator", default="", help="limit hashing to one creator folder")
    ap.add_argument("--json", default="", help="write the groups to this file")
    ap.add_argument("--limit", type=int, default=0, help="hash at most N files")
    args = ap.parse_args()

    index = ArchiveIndex.get()
    index.ensure_ready()

    started = time.perf_counter()
    hashed, failed, remapped = hash_indexed_photos(
        index,
        base_dir=SAVED_DIR,
        limit=args.limit,
        rehash=args.rehash,
        creator=args.creator,
    )
    if remapped:
        print(f"Aligned {remapped} hash path(s) to the catalog.")
    if hashed or failed:
        print(
            f"Hashed {hashed} in {time.perf_counter() - started:.1f}s"
            + (f" ({failed} unreadable)" if failed else "")
        )

    hashes = index.all_phashes()
    if not hashes:
        print("No hashes — is the archive empty?")
        return

    groups = find_near_duplicate_groups(hashes, max_distance=args.distance)
    total_dupes = sum(len(g) - 1 for g in groups)
    reclaimable = 0

    print(
        f"\n{len(hashes)} hashed, {len(groups)} duplicate group(s), "
        f"{total_dupes} redundant file(s) at distance <= {args.distance}\n"
    )

    payload = []
    for group in groups:
        keeper = pick_keeper(group, SAVED_DIR)
        others = [r for r in group if r != keeper]
        for rel in others:
            try:
                reclaimable += os.path.getsize(os.path.join(SAVED_DIR, *rel.split("/")))
            except OSError:
                pass
        payload.append({"keep": keeper, "duplicates": others})
        print(f"  keep  {keeper}")
        for rel in others:
            print(f"  dup   {rel}")
        print()

    if reclaimable:
        print(f"Deleting the duplicates would free ~{_human(reclaimable)}.")
    print("Nothing was deleted. Review in the UI and delete there (goes to trash).")

    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump({"distance": args.distance, "groups": payload}, f, indent=2)
        print(f"Wrote {args.json}")


if __name__ == "__main__":
    main()
