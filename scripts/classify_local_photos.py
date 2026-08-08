#!/usr/bin/env python3
"""
Classify ALL local archive photos with the outfit vision filter.

Scores each image for woman + sexy/revealing outfit + good breasts.
Writes a photo-level JSON report (resumable).
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
    MODEL_NAME,
    SAVED_DIR,
)
from promptstudio.scraping.outfit_classifier import (
    classify_image,
    ollama_reachable,
)

REPORT_JSON = os.path.join(_REPO_ROOT, "local_photo_classify_report.json")


def _log(msg: str) -> None:
    print(msg, flush=True)


def list_all_archive_images() -> List[Tuple[str, str]]:
    """Return (creator, abs_path) for every image in SAVED_DIR creator folders."""
    out: List[Tuple[str, str]] = []
    if not os.path.isdir(SAVED_DIR):
        return out
    for name in sorted(os.listdir(SAVED_DIR)):
        folder = os.path.join(SAVED_DIR, name)
        if not os.path.isdir(folder):
            continue
        if name in EXCLUDED_FOLDERS or name.startswith(".") or name.startswith("_"):
            continue
        for fname in sorted(os.listdir(folder)):
            if fname.lower().endswith(IMAGE_EXTENSIONS):
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
        description="Classify all local InstagramSaved photos with outfit vision filter"
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Max new photos to classify this run (0 = all remaining)",
    )
    parser.add_argument(
        "--creator",
        default="",
        help="Only classify this creator folder (optional)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-classify photos already in the report",
    )
    parser.add_argument(
        "--reclassify-false",
        action="store_true",
        help="Only re-classify photos currently marked matches_keep=false",
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
        help="Flush report to disk every N photos (default: 5)",
    )
    args = parser.parse_args()

    if not ollama_reachable():
        raise SystemExit(
            f"Ollama not reachable. Start Ollama and ensure model {MODEL_NAME} is available."
        )

    all_imgs = list_all_archive_images()
    if args.creator:
        want = args.creator.strip().lower()
        all_imgs = [(c, p) for c, p in all_imgs if c.lower() == want]

    report = _load_report(args.report)
    done = set(report.get("photos") or {})
    report["prompt_version"] = "v2-skin-exposure"

    todo = []
    for creator, path in all_imgs:
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

    _log("=== Local photo outfit classifier ===")
    _log(f"Model: {MODEL_NAME}")
    _log(f"Archive: {SAVED_DIR}")
    _log(f"Total images in scope: {len(all_imgs)}")
    _log(f"Already classified: {len(done)} · to process now: {len(todo)}")
    _log(f"Report: {args.report}\n")

    processed = 0
    flagged_this_run = 0
    for i, (creator, path, key) in enumerate(todo, 1):
        t0 = time.time()
        try:
            verdict = classify_image(path)
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
        report["photos"][key] = row

        elapsed = time.time() - t0
        flag = "FLAGGED" if row["matches_keep"] else ("ERROR" if not verdict.ok else "no-match")
        if row["matches_keep"]:
            flagged_this_run += 1
        _log(
            f"[{i}/{len(todo)}] {flag} {key} "
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


if __name__ == "__main__":
    main()
