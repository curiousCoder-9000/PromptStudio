#!/usr/bin/env python3
"""Fetch REAL Instagram profile metadata using Instaloader for unmatched creator folders.

Queries live Instagram API for each handle via Instaloader, extracts actual profile
metadata (user_id, full_name, bio, media_count, followers_count, is_verified, is_private),
and updates following_list.json with real data.
"""

import json
import os
import sys
import time

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import instaloader
from promptstudio.config import EXCLUDED_FOLDERS, SAVED_DIR, SESSION_USER
from promptstudio.scraping.session import create_instaloader, load_session

FOLLOWING_PATH = os.path.join(_REPO_ROOT, "following_list.json")


def fetch_real_profile_metadata():
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

    missing_handles = [f for f in folders if f.lower() not in existing_usernames]

    print(f"Total creator folders: {len(folders)}")
    print(f"Already in following_list.json: {len(folders) - len(missing_handles)}")
    print(f"Missing handles to look up on Instagram: {len(missing_handles)}")
    print(f"Handles: {missing_handles}\n")

    if not missing_handles:
        print("All folders already matched in following_list.json.")
        return

    # Initialize Instaloader session
    L = create_instaloader()
    load_session(L, SESSION_USER)
    print(f"Instaloader session authenticated as @{SESSION_USER}\n")

    max_index = max([e.get("index", 0) for e in following] + [0])
    added_count = 0
    failed_handles = []

    for idx, handle in enumerate(missing_handles, 1):
        print(f"[{idx}/{len(missing_handles)}] Querying Instagram for @{handle}...")
        try:
            profile = instaloader.Profile.from_username(L.context, handle)
            max_index += 1

            real_entry = {
                "index": max_index,
                "username": profile.username,
                "full_name": profile.full_name or "",
                "biography": profile.biography or "",
                "is_verified": bool(profile.is_verified),
                "is_private": bool(profile.is_private),
                "profile_url": f"https://www.instagram.com/{profile.username}/",
                "media_count": int(profile.mediacount or 0),
                "followers_count": int(profile.followers or 0),
                "user_id": str(profile.userid),
            }

            following.append(real_entry)
            added_count += 1
            print(
                f"  SUCCESS: @{profile.username:20s} | {profile.full_name} | "
                f"Media: {profile.mediacount} | Followers: {profile.followers} | ID: {profile.userid}"
            )

        except instaloader.exceptions.ProfileNotExistsException:
            print(f"  NOT FOUND: Profile @{handle} does not exist on Instagram.")
            failed_handles.append((handle, "ProfileNotExists"))

        except instaloader.exceptions.ConnectionException as exc:
            print(f"  RATE LIMIT / CONNECTION ERROR for @{handle}: {exc}")
            failed_handles.append((handle, f"ConnectionError: {exc}"))

        except Exception as exc:
            print(f"  ERROR fetching @{handle}: {exc}")
            failed_handles.append((handle, str(exc)))

        # Delay to avoid rate limiting
        time.sleep(3.0)

    if added_count > 0:
        with open(FOLLOWING_PATH, "w", encoding="utf-8") as f:
            json.dump(following, f, indent=2, ensure_ascii=False)
        print(f"\nSaved {added_count} REAL Instagram profile entries to following_list.json.")

    print(f"\nSummary:")
    print(f"  Successfully fetched & added: {added_count}")
    print(f"  Failed / Not found on IG: {len(failed_handles)}")
    if failed_handles:
        for h, reason in failed_handles:
            print(f"    @{h}: {reason}")


if __name__ == "__main__":
    fetch_real_profile_metadata()
