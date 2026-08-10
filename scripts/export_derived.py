#!/usr/bin/env python3
"""
Export or restore the archive's derived state as one portable file.

Derived state is everything the archive cannot re-download: prompts and
keep/reject verdicts that cost GPU hours, favourites and generation ratings
that are your own judgement, creator styles, perceptual hashes, and the
generation index with its seeds.

    py scripts/export_derived.py                        # -> derived_state.json.gz
    py scripts/export_derived.py backup.json            # uncompressed
    py scripts/export_derived.py --kinds prompts,verdicts

    py scripts/export_derived.py --import backup.json.gz
    py scripts/export_derived.py --import backup.json.gz --dry-run
    py scripts/export_derived.py --import backup.json.gz --kinds prompts

Media is **not** included — it is the one thing that can be fetched again, and
bundling it would just be a second copy of the archive. The bundle is keyed by
archive-relative path, so it restores onto any machine with the same layout.

Import is idempotent and merges rather than replacing: re-running a half-finished
restore overwrites row-for-row, and importing a backup onto a live archive will
not un-favourite anything starred since the export.
"""

import argparse
import os
import sys

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from promptstudio.logging_setup import get_logger
from promptstudio.storage.export_bundle import (
    DERIVED_KINDS,
    export_derived,
    import_derived,
)

log = get_logger("scripts.export_derived")

DEFAULT_OUT = "derived_state.json.gz"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Export or restore derived archive state.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "path",
        nargs="?",
        default=None,
        help=f"bundle path (default {DEFAULT_OUT}; .gz compresses)",
    )
    parser.add_argument(
        "--import",
        dest="do_import",
        metavar="BUNDLE",
        default=None,
        help="restore from BUNDLE instead of exporting",
    )
    parser.add_argument(
        "--kinds",
        default=None,
        help=f"comma-separated subset of: {', '.join(DERIVED_KINDS)}",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="with --import, report what would be applied and write nothing",
    )
    args = parser.parse_args()

    kinds = None
    if args.kinds:
        kinds = [k.strip() for k in args.kinds.split(",") if k.strip()]

    try:
        if args.do_import:
            summary = import_derived(
                args.do_import, kinds=kinds, dry_run=args.dry_run
            )
            verb = "Would restore" if args.dry_run else "Restored"
            log.info("%s from %s", verb, args.do_import)
        else:
            out = args.path or DEFAULT_OUT
            summary = export_derived(out, kinds=kinds)
            log.info("Wrote %s (%s)", out, _size(out))
    except (ValueError, FileNotFoundError, OSError) as e:
        log.error("%s", e)
        return 1

    if not summary:
        log.info("nothing to do — the bundle had none of the requested kinds")
        return 0
    width = max(len(k) for k in summary)
    for kind, count in summary.items():
        log.info("  %-*s %d", width, kind, count)
    return 0


def _size(path: str) -> str:
    try:
        n = os.path.getsize(path)
    except OSError:
        return "unknown size"
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024.0
    return f"{n:.1f} GB"


if __name__ == "__main__":
    raise SystemExit(main())
