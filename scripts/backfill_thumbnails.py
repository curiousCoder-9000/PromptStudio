#!/usr/bin/env python3
"""
Fill in `_thumbs/` for media that predates thumbnail-at-ingest.

Thumbnails used to be created only inside `GET /media/thumb/`, so a file was
thumbed if and only if someone had already looked at its tile. On the live
archive that was 12,148 thumbs against 61,344 rows — and the newest 500 files,
the view you open right after a scrape, were 91% unthumbed
(docs/review_gallery_performance.md §4). Ingest keeps it warm from now on; this
is the one-time pass over everything that arrived before it did.

    py scripts/backfill_thumbnails.py                 # newest first, all of it
    py scripts/backfill_thumbnails.py --limit 500      # just the recent tail
    py scripts/backfill_thumbnails.py --dry-run        # census only
    py scripts/backfill_thumbnails.py --stills-only    # skip reels
    py scripts/backfill_thumbnails.py --sleep 0.05     # be gentler

Newest first on purpose: that is the page you will open, so coverage should
arrive in the order it is looked at rather than alphabetically by creator.

Safe to interrupt and re-run — existing thumbs are skipped, so a second pass
resumes rather than redoing. It reads the catalog and writes only under
`_thumbs/`; no media is touched.
"""

import argparse
import os
import sys
import time

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from promptstudio.config import SAVED_DIR, THUMB_DIR
from promptstudio.logging_setup import get_logger
from promptstudio.storage.db import ArchiveIndex
from promptstudio.storage.thumbs import ensure_thumbnail, resolve_thumb_file

log = get_logger("scripts.backfill_thumbnails")


def _parse_args(argv=None):
    ap = argparse.ArgumentParser(
        description="Generate missing gallery thumbnails.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument(
        "--limit",
        type=int,
        default=0,
        help="stop after this many missing thumbs (0 = no limit)",
    )
    ap.add_argument(
        "--sleep",
        type=float,
        default=0.0,
        help="seconds to pause between files, to stay out of the way",
    )
    ap.add_argument(
        "--stills-only",
        action="store_true",
        help="skip videos — a reel with no cover still decodes its timeline",
    )
    ap.add_argument(
        "--oldest-first",
        action="store_true",
        help="walk in ingest order instead of newest-first",
    )
    ap.add_argument(
        "--dry-run",
        action="store_true",
        help="report coverage and what would be generated, write nothing",
    )
    return ap.parse_args(argv)


def main(argv=None) -> int:
    args = _parse_args(argv)
    if not os.path.isdir(SAVED_DIR):
        log.error("archive not found: %s", SAVED_DIR)
        return 1

    index = ArchiveIndex.get()
    order = "ASC" if args.oldest_first else "DESC"
    # Straight off the catalog rather than a directory walk: the rows are the
    # thing the gallery pages over, and `added_at` is what "newest" means.
    with index._lock:
        rows = index._conn.execute(
            "SELECT rel_path, creator, filename, media_kind FROM photos "
            f"ORDER BY added_at {order}, filename ASC"
        ).fetchall()

    total = len(rows)
    log.info("catalog: %d rows · thumbs under %s", total, THUMB_DIR)

    pending = []
    for row in rows:
        if args.stills_only and row["media_kind"] == "video":
            continue
        if resolve_thumb_file(row["rel_path"]):
            continue
        pending.append(row)

    have = total - len(pending)
    pct = (have / total * 100.0) if total else 100.0
    log.info("covered: %d / %d (%.1f%%) · missing: %d", have, total, pct, len(pending))

    if args.limit > 0:
        pending = pending[: args.limit]
        log.info("limited to %d this pass", len(pending))

    if args.dry_run:
        for row in pending[:20]:
            log.info("  would generate %s", row["rel_path"])
        if len(pending) > 20:
            log.info("  ... and %d more", len(pending) - 20)
        return 0

    made = failed = gone = 0
    started = time.time()
    for n, row in enumerate(pending, 1):
        rel = row["rel_path"]
        full = os.path.join(SAVED_DIR, row["creator"], row["filename"])
        if not os.path.isfile(full):
            # The catalog can outlive the file (external delete). Not an error
            # worth failing the pass over.
            gone += 1
            continue
        try:
            if ensure_thumbnail(full, rel):
                made += 1
            else:
                failed += 1
                log.warning("no thumbnail produced for %s", rel)
        except KeyboardInterrupt:
            log.info("interrupted — %d generated, safe to re-run", made)
            return 130
        except Exception as exc:
            failed += 1
            log.warning("thumbnail failed for %s: %s", rel, exc)
        if n % 200 == 0:
            rate = n / max(time.time() - started, 0.001)
            log.info(
                "%d / %d · %.1f files/s · %d failed · %d missing on disk",
                n,
                len(pending),
                rate,
                failed,
                gone,
            )
        if args.sleep > 0:
            time.sleep(args.sleep)

    elapsed = time.time() - started
    log.info(
        "done: %d generated, %d failed, %d absent, in %.1fs",
        made,
        failed,
        gone,
        elapsed,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
