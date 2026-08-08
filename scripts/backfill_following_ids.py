#!/usr/bin/env python3
"""Backfill user_id on following_list.json via own followees edge (no web_profile_info)."""

from __future__ import annotations

import json
import os
import random
import sys
import time
import urllib.parse

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

import instaloader

from promptstudio.config import FOLLOWING_LIST_FILE, SESSION_USER
from promptstudio.scraping.session import load_session


def _log(msg: str) -> None:
    print(msg, flush=True)


def _userid_from_session(L: instaloader.Instaloader) -> int:
    raw = L.context._session.cookies.get("ds_user_id") or ""
    if str(raw).isdigit():
        return int(raw)
    sid = urllib.parse.unquote(L.context._session.cookies.get("sessionid") or "")
    head = sid.split(":", 1)[0].strip()
    if head.isdigit():
        return int(head)
    raise RuntimeError("Could not resolve own userid from session")


def main() -> None:
    path = FOLLOWING_LIST_FILE
    with open(path, "r", encoding="utf-8") as f:
        rows = json.load(f)
    by_user = {
        (r.get("username") or "").lower(): r for r in rows if r.get("username")
    }
    missing = sum(1 for r in rows if r.get("username") and not r.get("user_id"))
    _log(f"Following entries: {len(rows)} · missing user_id: {missing}")

    L = instaloader.Instaloader(max_connection_attempts=1, request_timeout=90)
    load_session(L, SESSION_USER)
    own_id = _userid_from_session(L)
    profile = instaloader.Profile(
        L.context,
        {"id": str(own_id), "pk": str(own_id), "username": SESSION_USER},
    )
    profile._has_full_metadata = True
    _log(f"Walking followees as id={own_id} @{SESSION_USER}")

    updated = 0
    scanned = 0
    try:
        for followee in profile.get_followees():
            scanned += 1
            node = getattr(followee, "_node", None) or {}
            username = (node.get("username") or "").lower()
            uid = str(node.get("id") or node.get("pk") or "")
            if not username or not uid:
                continue
            row = by_user.get(username)
            if row is None:
                continue
            if row.get("user_id") == uid:
                continue
            row["user_id"] = uid
            updated += 1
            if updated % 25 == 0:
                _log(f"  updated {updated} ids (scanned {scanned})")
                with open(path + ".tmp", "w", encoding="utf-8") as f:
                    json.dump(rows, f, indent=2, ensure_ascii=False)
                os.replace(path + ".tmp", path)
            if scanned % 40 == 0:
                time.sleep(random.uniform(2.0, 5.0))
    except Exception as e:
        _log(f"Walk interrupted (saving): {e}")

    for i, row in enumerate(rows, start=1):
        row["index"] = i
    with open(path + ".tmp", "w", encoding="utf-8") as f:
        json.dump(rows, f, indent=2, ensure_ascii=False)
    os.replace(path + ".tmp", path)
    still = sum(1 for r in rows if r.get("username") and not r.get("user_id"))
    _log(f"Done. updated={updated} scanned={scanned} still_missing_user_id={still}")


if __name__ == "__main__":
    main()
