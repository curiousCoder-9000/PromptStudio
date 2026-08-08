#!/usr/bin/env python3
"""
Classify local archive media with the outfit / glam vision filter.

Scores images and videos (frame samples) for woman + sexy outfit + breasts.
Writes a resumable JSON report and persists glam_score to archive.db + sidecars.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from typing import Any, Dict, List, Tuple

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from promptstudio.config import (
    EXCLUDED_FOLDERS,
    IMAGE_EXTENSIONS,
    MEDIA_EXTENSIONS,
    MODEL_NAME,
    SAVED_DIR,
    VIDEO_EXTENSIONS,
)
from promptstudio.scraping.outfit_classifier import (
    classify_media,
    ollama_reachable,
    persist_glam_score,
)

REPORT_JSON = os.path.join(_REPO_ROOT, "local_photo_classify_report.json")


def _log(msg: str) -> None:
    print(msg, flush=True)


def list_all_archive_media(
    *,
    include_videos: bool = True,
) -> List[Tuple[str, str]]:
    """Return (creator, abs_path) for every media file in SAVED_DIR creator folders."""
    out: List[Tuple[str, str]] = []
    if not os.path.isdir(SAVED_DIR):
        return out
    exts = MEDIA_EXTENSIONS if include_videos else IMAGE_EXTENSIONS
    for name in sorted(os.listdir(SAVED_DIR)):
        folder = os.path.join(SAVED_DIR, name)
        if not os.path.isdir(folder):
            continue
        if name in EXCLUDED_FOLDERS or name.startswith((".", "_")):
            continue
        for fname in sorted(os.listdir(folder)):
            if fname.lower().endswith(exts):
                out.append((name, os.path.join(folder, fname)))
    return out


def _load_report(path: str) -> Dict[str, Any]:
    if not os.path.isfile(path):
        return {
            "version": 1,
            "model": MODEL_NAME,
            "started_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "photos": {},
        }
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        data = {"photos": {}}
    data.setdefault("photos", {})
    data["model"] = MODEL_NAME
    return data


def _save_report(path: str, report: Dict[str, Any]) -> None:
    report["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    photos = report.get("photos") or {}
    flagged = sum(1 for p in photos.values() if p.get("matches_keep"))
    failed = sum(1 for p in photos.values() if p.get("ok") and not p.get("matches_keep"))
    errors = sum(1 for p in photos.values() if not p.get("ok"))
    report["summary"] = {
        "classified": len(photos),
        "flagged_keep": flagged,
        "not_match": failed,
        "errors": errors,
    }
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)
    os.replace(tmp, path)

    # Also update local_photo_classify_false.json automatically
    false_path = path.replace("_report.json", "_false.json")
    if false_path != path:
        false_photos = {k: v for k, v in photos.items() if not v.get("matches_keep")}
        false_doc = {
            "source": os.path.basename(path),
            "filter": "matches_keep == false",
            "updated_at": report["updated_at"],
            "model": report.get("model", MODEL_NAME),
            "count": len(false_photos),
            "photos": false_photos,
        }
        tmp_false = false_path + ".tmp"
        with open(tmp_false, "w", encoding="utf-8") as f:
            json.dump(false_doc, f, indent=2, ensure_ascii=False)
        os.replace(tmp_false, false_path)



def _rel_key(abs_path: str) -> str:
    try:
        return os.path.relpath(abs_path, SAVED_DIR).replace("\\", "/")
    except ValueError:
        return abs_path.replace("\\", "/")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Classify local InstagramSaved media with glam / outfit vision filter"
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Max new items to classify this run (0 = all remaining)",
    )
    parser.add_argument(
        "--creator",
        default="",
        help="Only classify this creator folder (optional)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-classify items already in the report",
    )
    parser.add_argument(
        "--reclassify-false",
        action="store_true",
        help="Only re-classify items currently marked matches_keep=false",
    )
    parser.add_argument(
        "--no-videos",
        action="store_true",
        help="Skip .mp4/.webm (images only)",
    )
    parser.add_argument(
        "--report",
        default=REPORT_JSON,
        help="Path to JSON report (resume checkpoint)",
    )
    parser.add_argument(
        "--save-every",
        type=int,
        default=5,
        help="Flush report to disk every N items (default: 5)",
    )
    parser.add_argument(
        "--no-persist",
        action="store_true",
        help="Do not write glam_score to archive.db / sidecars",
    )
    args = parser.parse_args()

    if not ollama_reachable():
        raise SystemExit(
            f"Ollama not reachable. Start Ollama and ensure model {MODEL_NAME} is available."
        )

    all_media = list_all_archive_media(include_videos=not args.no_videos)
    if args.creator:
        want = args.creator.strip().lower()
        all_media = [(c, p) for c, p in all_media if c.lower() == want]

    report = _load_report(args.report)
    done = set(report.get("photos") or {})
    report["prompt_version"] = "v2-skin-exposure"

    todo = []
    for creator, path in all_media:
        key = _rel_key(path)
        if args.reclassify_false:
            prev = (report.get("photos") or {}).get(key)
            if not prev or prev.get("matches_keep"):
                continue
            todo.append((creator, path, key))
            continue
        if not args.force and key in done:
            continue
        todo.append((creator, path, key))
    if args.limit and args.limit > 0:
        todo = todo[: args.limit]

    n_vid = sum(1 for _c, p, _k in todo if p.lower().endswith(VIDEO_EXTENSIONS))
    _log("=== Local glam / outfit classifier ===")
    _log(f"Model: {MODEL_NAME}")
    _log(f"Archive: {SAVED_DIR}")
    _log(f"Total media in scope: {len(all_media)}")
    _log(f"Already classified: {len(done)} · to process now: {len(todo)} (videos in batch: {n_vid})")
    _log(f"Report: {args.report}\n")

    processed = 0
    flagged_this_run = 0
    for i, (creator, path, key) in enumerate(todo, 1):
        t0 = time.time()
        try:
            verdict = classify_media(path)
        except KeyboardInterrupt:
            _save_report(args.report, report)
            _log("Interrupted — report saved.")
            break
        except Exception as e:
            from promptstudio.scraping.outfit_classifier import PostVerdict

            verdict = PostVerdict(path=path, error=str(e))

        row = verdict.to_dict()
        row["creator"] = creator
        row["rel_path"] = key
        row["matches_keep"] = bool(verdict.ok and verdict.matches_keep())
        if verdict.ok and int(row.get("glam_score", -1)) < 0:
            row["glam_score"] = verdict.compute_glam_score()
        report["photos"][key] = row

        if not args.no_persist and verdict.ok:
            persist_glam_score(key, verdict, full_path=path)

        elapsed = time.time() - t0
        flag = "FLAGGED" if row["matches_keep"] else ("ERROR" if not verdict.ok else "no-match")
        if row["matches_keep"]:
            flagged_this_run += 1
        _log(
            f"[{i}/{len(todo)}] {flag} glam={row.get('glam_score', -1)} {key} "
            f"w={verdict.has_woman} sexy={verdict.sexy_revealing_outfit} "
            f"breasts={verdict.good_breasts} conf={verdict.confidence:.2f} "
            f"({elapsed:.1f}s) {verdict.brief_reason or verdict.error}"
        )

        processed += 1
        if processed % max(1, args.save_every) == 0:
            _save_report(args.report, report)

    _save_report(args.report, report)
    summary = report.get("summary") or {}
    _log(
        f"\nDone this run: {processed} (flagged {flagged_this_run}). "
        f"Totals — classified={summary.get('classified', 0)} "
        f"flagged_keep={summary.get('flagged_keep', 0)} "
        f"not_match={summary.get('not_match', 0)} "
        f"errors={summary.get('errors', 0)}"
    )
    _log(f"JSON: {args.report}")
    if not args.no_persist:
        _log("Glam scores written to archive.db + *.meta.json (use gallery Sexy filter)")


if __name__ == "__main__":
    main()
