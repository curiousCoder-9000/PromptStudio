#!/usr/bin/env python3
"""Ensure 100% of local creator folders in InstagramSaved have an entry in following_list.json.

Scans PROMPTSTUDIO_ARCHIVE, finds any creator folders not currently in
following_list.json, and appends them with valid profile metadata so every local folder matches 1:1.
"""

import json
import os
import sys

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from promptstudio.config import EXCLUDED_FOLDERS, SAVED_DIR

FOLLOWING_PATH = os.path.join(_REPO_ROOT, "following_list.json")


def backfill_missing_creators():
    base_dir = os.path.expanduser(SAVED_DIR)

    with open(FOLLOWING_PATH, "r", encoding="utf-8") as f:
        following = json.load(f)

    existing_usernames = {e["username"].lower(): e for e in following if "username" in e}

    folders = sorted([
        d for d in os.listdir(base_dir)
        if os.path.isdir(os.path.join(base_dir, d))
        and not d.startswith(".")
        and d not in EXCLUDED_FOLDERS
    ])

    missing = [f for f in folders if f.lower() not in existing_usernames]

    print(f"Total local creator folders: {len(folders)}")
    print(f"Already in following_list.json: {len(folders) - len(missing)}")
    print(f"Missing from following_list.json: {len(missing)}")

    if not missing:
        print("All local creator folders are already in following_list.json!")
        return

    max_index = max([e.get("index", 0) for e in following] + [0])

    added_count = 0
    for folder in missing:
        max_index += 1

        # Check sidecar metadata for real name or post info if available
        folder_path = os.path.join(base_dir, folder)
        full_name = folder.replace("_", " ").title()

        for fn in os.listdir(folder_path):
            if fn.endswith(".meta.json"):
                try:
                    with open(os.path.join(folder_path, fn), "r", encoding="utf-8") as mf:
                        mdata = json.load(mf)
                        if mdata.get("owner_username"):
                            full_name = mdata.get("owner_username").replace("_", " ").title()
                            break
                except Exception:
                    pass

        new_entry = {
            "index": max_index,
            "username": folder,
            "full_name": full_name,
            "biography": "Archived creator",
            "is_verified": False,
            "is_private": False,
            "profile_url": f"https://www.instagram.com/{folder}/",
            "media_count": len([x for x in os.listdir(folder_path) if not x.endswith(".json")]),
            "followers_count": 0,
            "user_id": f"local_{folder}",
        }

        following.append(new_entry)
        added_count += 1
        print(f"  + Backfilled: @{folder:25s} (Index: {max_index})")

    with open(FOLLOWING_PATH, "w", encoding="utf-8") as f:
        json.dump(following, f, indent=2, ensure_ascii=False)

    print(f"\nBackfilled {added_count} missing creators into following_list.json.")
    print(f"following_list.json now contains {len(following)} total entries.")


if __name__ == "__main__":
    backfill_missing_creators()
