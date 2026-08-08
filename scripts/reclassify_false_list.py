#!/usr/bin/env python3
"""
Re-classify the target list of photos in local_photo_classify_false.json
with the updated Ollama Vision prompt (CLASSIFY_PROMPT).
"""

import json
import os
import sys
import time

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from promptstudio.config import SAVED_DIR
from promptstudio.scraping.outfit_classifier import classify_image, ollama_reachable

FALSE_JSON = os.path.join(_REPO_ROOT, "local_photo_classify_false.json")
REPORT_JSON = os.path.join(_REPO_ROOT, "local_photo_classify_report.json")


def main() -> None:
    if not ollama_reachable():
        print("Error: Ollama is not reachable.")
        sys.exit(1)

    if not os.path.isfile(FALSE_JSON):
        print(f"Error: {FALSE_JSON} not found.")
        sys.exit(1)

    with open(FALSE_JSON, "r", encoding="utf-8") as f:
        false_data = json.load(f)

    photos_to_reclassify = false_data.get("photos", {})
    print(f"Found {len(photos_to_reclassify)} photos in {os.path.basename(FALSE_JSON)} to re-classify with Ollama Vision...\n")

    with open(REPORT_JSON, "r", encoding="utf-8") as f:
        report = json.load(f)

    photos_report = report.setdefault("photos", {})

    reclassified_count = 0
    flagged_now = 0

    for i, (rel_path, item) in enumerate(photos_to_reclassify.items(), 1):
        path = item.get("path")
        if not path or not os.path.isfile(path):
            path = os.path.join(SAVED_DIR, rel_path.replace("/", os.sep))
        if not os.path.isfile(path):
            print(f"[{i}/{len(photos_to_reclassify)}] SKIP: File not found: {path}")
            continue

        print(f"[{i}/{len(photos_to_reclassify)}] Classifying via Ollama Vision: {rel_path}...")
        t0 = time.time()
        verdict = classify_image(path)
        elapsed = time.time() - t0

        row = verdict.to_dict()
        row["creator"] = item.get("creator") or rel_path.split("/")[0]
        row["rel_path"] = rel_path
        row["matches_keep"] = bool(verdict.ok and verdict.matches_keep())

        photos_report[rel_path] = row
        reclassified_count += 1
        if row["matches_keep"]:
            flagged_now += 1

        flag_str = "FLAGGED (KEEP)" if row["matches_keep"] else "REJECT (FALSE)"
        print(
            f"   -> Result: {flag_str} | woman={verdict.has_woman} "
            f"sexy={verdict.sexy_revealing_outfit} breasts={verdict.good_breasts} "
            f"conf={verdict.confidence:.2f} ({elapsed:.1f}s)"
        )
        print(f"      Reason: {verdict.brief_reason or verdict.error}\n")

    # Update report summary
    report["prompt_version"] = "v3-revealing-back-ass-figure"
    report["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")

    photos_all = report.get("photos") or {}
    flagged_total = sum(1 for p in photos_all.values() if p.get("matches_keep"))
    not_match_total = sum(1 for p in photos_all.values() if p.get("ok") and not p.get("matches_keep"))
    errors_total = sum(1 for p in photos_all.values() if not p.get("ok"))

    report["summary"] = {
        "classified": len(photos_all),
        "flagged_keep": flagged_total,
        "not_match": not_match_total,
        "errors": errors_total,
    }

    with open(REPORT_JSON, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)

    # Refresh false report
    false_photos_new = {k: v for k, v in photos_all.items() if not v.get("matches_keep")}
    false_doc = {
        "source": "local_photo_classify_report.json",
        "filter": "matches_keep == false",
        "updated_at": report["updated_at"],
        "model": report.get("model", "qwen2.5vl:7b"),
        "count": len(false_photos_new),
        "photos": false_photos_new,
    }
    with open(FALSE_JSON, "w", encoding="utf-8") as f:
        json.dump(false_doc, f, indent=2, ensure_ascii=False)

    print("==========================================")
    print("Re-classification Complete!")
    print(f"Re-processed: {reclassified_count} photos")
    print(f"Now Flagged to KEEP: {flagged_now} / {reclassified_count}")
    print(f"Remaining Non-Matches in {os.path.basename(FALSE_JSON)}: {len(false_photos_new)}")
    print("==========================================")


if __name__ == "__main__":
    main()
