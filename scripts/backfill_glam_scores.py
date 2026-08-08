#!/usr/bin/env python3
"""
Apply glam scores to archive.db + sidecars from an existing classify report
(no Ollama re-run).

Reads local_photo_classify_report.json (or --report) and writes glam_score.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from promptstudio.config import SAVED_DIR
from promptstudio.scraping.outfit_classifier import PostVerdict, persist_glam_score

DEFAULT_REPORT = os.path.join(_REPO_ROOT, "local_photo_classify_report.json")


def verdict_from_row(rel_path: str, row: dict) -> PostVerdict:
    v = PostVerdict(path=rel_path)
    v.has_woman = bool(row.get("has_woman"))
    v.sexy_revealing_outfit = bool(row.get("sexy_revealing_outfit"))
    v.good_breasts = bool(row.get("good_breasts"))
    try:
        v.confidence = float(row.get("confidence") or 0.0)
    except (TypeError, ValueError):
        v.confidence = 0.0
    v.brief_reason = str(row.get("brief_reason") or "")[:160]
    v.ok = bool(row.get("ok", True)) and not row.get("error")
    if "glam_score" in row and row["glam_score"] is not None:
        try:
            v.glam_score = int(row["glam_score"])
        except (TypeError, ValueError):
            v.glam_score = v.compute_glam_score() if v.ok else -1
    else:
        v.glam_score = v.compute_glam_score() if v.ok else -1
    return v


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill glam_score from classify report")
    parser.add_argument("--report", default=DEFAULT_REPORT)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--min-score", type=int, default=-1, help="Only apply if score >= N")
    args = parser.parse_args()

    if not os.path.isfile(args.report):
        raise SystemExit(f"Missing report: {args.report}")

    with open(args.report, "r", encoding="utf-8") as f:
        data = json.load(f)
    photos = data.get("photos") or {}
    if not isinstance(photos, dict):
        raise SystemExit("report.photos must be an object")

    applied = 0
    skipped = 0
    for rel, row in photos.items():
        if not isinstance(row, dict):
            skipped += 1
            continue
        v = verdict_from_row(rel, row)
        if not v.ok:
            skipped += 1
            continue
        if v.glam_score < args.min_score:
            skipped += 1
            continue
        full = os.path.join(SAVED_DIR, *rel.replace("\\", "/").split("/"))
        if args.dry_run:
            applied += 1
            continue
        persist_glam_score(rel, v, full_path=full if os.path.isfile(full) else "")
        applied += 1

    mode = "would apply" if args.dry_run else "applied"
    print(f"{mode} {applied} glam scores (skipped {skipped}) from {args.report}")
    if not args.dry_run:
        print("Gallery Sexy filter: GET /api/photos?sexy=1  (glam_score >= 2)")


if __name__ == "__main__":
    main()
