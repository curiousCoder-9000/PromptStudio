#!/usr/bin/env python3
"""
Dry-run classifier for following_list.json.

Scores each account (default: index >= 71) for woman + sexy/revealing outfit +
good/ample breasts using Ollama vision on a few recent photos. Never unfollows —
writes a report only.

Prefer local archive images; optionally download a few posts into
~/Pictures/InstagramSaved/_classify/<username>/ when --fetch is set.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import sys
import time
from typing import Any, Dict, List, Optional

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

import instaloader

from promptstudio.config import (
    ACCOUNT_PAUSE_MAX_SEC,
    ACCOUNT_PAUSE_MIN_SEC,
    FOLLOWING_LIST_FILE,
    MODEL_NAME,
    SAVED_DIR,
    SESSION_USER,
)
from promptstudio.scraping.outfit_classifier import (
    AccountVerdict,
    classify_paths,
    decide_account,
    list_local_images,
    list_staging_images,
    ollama_reachable,
)
from promptstudio.scraping.session import load_session

REPORT_JSON = os.path.join(_REPO_ROOT, "following_classify_report.json")
REPORT_MD = os.path.join(_REPO_ROOT, "docs", "following_classify_report.md")
STAGING_DIR = os.path.join(SAVED_DIR, "_classify")


def _log(msg: str) -> None:
    print(msg, flush=True)


def _load_following(path: str) -> List[Dict[str, Any]]:
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, list):
        raise SystemExit("following_list.json must be a JSON array")
    return data


def _load_report(path: str) -> Dict[str, Any]:
    if not os.path.isfile(path):
        return {
            "version": 1,
            "model": MODEL_NAME,
            "started_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "accounts": {},
        }
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        data = {"accounts": {}}
    data.setdefault("accounts", {})
    data["model"] = MODEL_NAME
    return data


def _save_report(path: str, report: Dict[str, Any]) -> None:
    report["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)
    os.replace(tmp, path)


def _write_markdown(path: str, report: Dict[str, Any]) -> None:
    accounts = report.get("accounts") or {}
    rows = sorted(
        accounts.values(),
        key=lambda r: int(r.get("index") or 0),
    )
    counts: Dict[str, int] = {}
    for r in rows:
        d = r.get("decision") or "unsure"
        counts[d] = counts.get(d, 0) + 1

    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write("# Following classify report (dry-run)\n\n")
        f.write(f"**Model:** `{report.get('model')}`  \n")
        f.write(f"**Updated:** `{report.get('updated_at', '')}`  \n")
        f.write(
            f"**Totals:** keep={counts.get('keep', 0)} · "
            f"unfollow={counts.get('unfollow', 0)} · "
            f"unsure={counts.get('unsure', 0)} · "
            f"skipped={counts.get('skipped', 0)} · "
            f"classified={len(rows)}\n\n"
        )
        f.write("| # | Username | Decision | Source | Reason |\n")
        f.write("|---|---|---|---|---|\n")
        for r in rows:
            f.write(
                f"| {r.get('index')} | `@{r.get('username')}` | "
                f"**{r.get('decision')}** | {r.get('source') or ''} | "
                f"{(r.get('reason') or '').replace('|', '/')} |\n"
            )


def _download_sample(
    username: str,
    *,
    max_posts: int,
    userid: str = "",
) -> tuple[List[str], Optional[str]]:
    """Download up to max_posts image posts into staging. Returns (paths, error)."""
    os.makedirs(os.path.join(STAGING_DIR, username), exist_ok=True)
    existing = list_staging_images(STAGING_DIR, username, limit=max_posts)
    if len(existing) >= max_posts:
        return existing[:max_posts], None

    L = instaloader.Instaloader(
        dirname_pattern=os.path.join(STAGING_DIR, "{target}"),
        filename_pattern="{date_utc:%Y-%m-%d_%H-%M-%S}_UTC",
        download_videos=False,
        download_video_thumbnails=False,
        download_geotags=False,
        download_comments=False,
        save_metadata=False,
        compress_json=False,
        max_connection_attempts=1,
        request_timeout=90,
    )
    load_session(L, SESSION_USER)

    profile: Optional[instaloader.Profile] = None
    last_err: Optional[str] = None

    # Prefer userid node — avoids web_profile_info 429s
    if userid and str(userid).isdigit():
        try:
            profile = instaloader.Profile(
                L.context,
                {"id": str(userid), "pk": str(userid), "username": username},
            )
            profile._has_full_metadata = True  # noqa: SLF001
            _log(f"  profile from user_id={userid} (no web_profile_info)")
        except Exception as e:
            last_err = str(e)
            _log(f"  userid profile build failed @{username}: {e}")
            profile = None

    if profile is None:
        try:
            profile = instaloader.Profile.from_username(L.context, username)
        except Exception as e:
            last_err = str(e)
            _log(f"  profile load failed @{username}: {e}")
            return list_staging_images(STAGING_DIR, username, limit=max_posts), last_err

    got = 0
    try:
        for post in profile.get_posts():
            if got >= max_posts:
                break
            if post.is_video:
                continue
            ok = False
            for attempt in range(3):
                try:
                    L.download_post(post, target=username)
                    ok = True
                    break
                except Exception as e:
                    last_err = str(e)
                    _log(f"  download attempt {attempt + 1}/3 @{username}: {e}")
                    time.sleep(random.uniform(3.0, 8.0))
            if ok:
                got += 1
                time.sleep(random.uniform(4.0, 9.0))
            # continue to next post on failure (transient CDN/DNS)
    except Exception as e:
        last_err = str(e)
        _log(f"  feed error @{username}: {e}")

    paths = list_staging_images(STAGING_DIR, username, limit=max_posts)
    if paths:
        return paths, None
    return [], last_err


def classify_one(
    entry: Dict[str, Any],
    *,
    max_posts: int,
    fetch: bool,
) -> AccountVerdict:
    username = (entry.get("username") or "").strip()
    index = int(entry.get("index") or 0)
    verdict = AccountVerdict(username=username, index=index, decision="unsure")

    if entry.get("is_private"):
        verdict.decision = "skipped"
        verdict.reason = "private account"
        verdict.source = "none"
        return verdict

    paths = list_local_images(username, limit=max_posts)
    source = "local" if paths else "none"
    fetch_err: Optional[str] = None
    if len(paths) < 1:
        staged = list_staging_images(STAGING_DIR, username, limit=max_posts)
        if staged:
            paths = staged
            source = "downloaded"
    if len(paths) < 1 and fetch:
        _log(f"  fetching up to {max_posts} posts for @{username}...")
        fetched, fetch_err = _download_sample(
            username,
            max_posts=max_posts,
            userid=str(entry.get("user_id") or ""),
        )
        if fetched:
            paths = fetched
            source = "downloaded"
        else:
            source = "none"

    if not paths:
        verdict.decision = "unsure"
        if fetch and fetch_err:
            verdict.reason = f"fetch failed: {fetch_err[:180]}"
        elif fetch:
            verdict.reason = "fetch returned no images"
        else:
            verdict.reason = "no local images (use --fetch)"
        verdict.source = source
        err_l = (fetch_err or "").lower()
        if fetch_err and (
            "429" in fetch_err
            or "too many" in err_l
            or "please wait" in err_l
            or "rate" in err_l
        ):
            verdict.reason = "RATE_LIMITED: " + verdict.reason
        return verdict

    posts = classify_paths(paths)
    verdict.posts = posts
    verdict.source = source
    decision, reason = decide_account(posts)
    verdict.decision = decision
    verdict.reason = reason
    return verdict


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Dry-run: classify following with glam/fashion keep filter (no unfollow)"
    )
    parser.add_argument(
        "--start-index",
        type=int,
        default=71,
        help="Only classify entries with index >= this (default: 71)",
    )
    parser.add_argument(
        "--max-posts",
        type=int,
        default=3,
        help="Images per account to score (default: 3)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Max new accounts to classify this run (0 = all remaining)",
    )
    parser.add_argument(
        "--fetch",
        action="store_true",
        help="Download sample posts when local archive has none (hits Instagram)",
    )
    parser.add_argument(
        "--skip-local",
        action="store_true",
        help="Skip accounts that already have local archive images (treat as already good)",
    )
    parser.add_argument(
        "--mark-local-keep",
        action="store_true",
        help="Write decision=keep for accounts with local images (no vision), then continue",
    )
    parser.add_argument(
        "--skip-missing",
        action="store_true",
        default=True,
        help="Without --fetch, skip accounts with no local images (default)",
    )
    parser.add_argument(
        "--include-missing",
        action="store_true",
        help="Record accounts with no local images as skipped in the report",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-classify accounts already present in the report",
    )
    parser.add_argument(
        "--following",
        default=FOLLOWING_LIST_FILE,
        help="Path to following_list.json",
    )
    parser.add_argument(
        "--report",
        default=REPORT_JSON,
        help="Path to JSON report (resume checkpoint)",
    )
    args = parser.parse_args()

    if not ollama_reachable():
        raise SystemExit(
            f"Ollama not reachable at {MODEL_NAME} / tags endpoint. "
            "Start Ollama first (ollama serve)."
        )

    following = _load_following(args.following)
    candidates = [
        e
        for e in following
        if int(e.get("index") or 0) >= args.start_index and e.get("username")
    ]
    candidates.sort(key=lambda e: int(e.get("index") or 0))

    report = _load_report(args.report)
    done = set(report.get("accounts") or {})

    if args.mark_local_keep:
        marked = 0
        for e in candidates:
            key = (e.get("username") or "").lower()
            if not key:
                continue
            if not list_local_images(e["username"], limit=1):
                continue
            if not args.force and key in done:
                continue
            report["accounts"][key] = AccountVerdict(
                username=e["username"],
                index=int(e.get("index") or 0),
                decision="keep",
                reason="local archive present — assumed good model",
                source="local",
            ).to_dict()
            marked += 1
        if marked:
            _save_report(args.report, report)
            _write_markdown(REPORT_MD, report)
            _log(f"Marked {marked} local-archive accounts as keep")
            done = set(report.get("accounts") or {})

    todo = []
    for e in candidates:
        key = (e.get("username") or "").lower()
        if not args.force and key in done:
            continue
        has_local = bool(list_local_images(e["username"], limit=1))
        if args.skip_local and has_local:
            continue
        if not args.fetch and not args.include_missing:
            if not has_local:
                continue
        todo.append(e)
    if args.limit and args.limit > 0:
        todo = todo[: args.limit]

    _log("=== Following outfit classifier (DRY-RUN, no unfollow) ===")
    _log(f"Model: {MODEL_NAME}")
    _log(f"Candidates index>={args.start_index}: {len(candidates)}")
    _log(f"Already in report: {len(done)} · to process now: {len(todo)}")
    _log(f"Fetch from Instagram: {'yes' if args.fetch else 'no (local archive only)'}")
    if args.skip_local:
        _log("Skipping accounts with local archive images")
    _log(f"Report: {args.report}\n")

    if args.fetch:
        cool = random.uniform(90.0, 180.0)
        _log(f"Pre-fetch cooldown {cool / 60:.1f} min (avoid immediate 429)...")
        time.sleep(cool)

    processed = 0
    i = 0
    while i < len(todo):
        entry = todo[i]
        username = entry["username"]
        idx = entry.get("index")
        _log(f"[{idx}] @{username}")
        try:
            verdict = classify_one(
                entry, max_posts=args.max_posts, fetch=args.fetch
            )
        except KeyboardInterrupt:
            _log("Interrupted — report saved.")
            break
        except Exception as e:
            verdict = AccountVerdict(
                username=username,
                index=int(idx or 0),
                decision="unsure",
                reason=f"error: {e}",
                source="none",
            )

        if (
            not args.fetch
            and not args.include_missing
            and verdict.decision == "skipped"
            and "no local images" in (verdict.reason or "")
        ):
            _log("  -> skip (no local images)")
            i += 1
            continue

        if (verdict.reason or "").startswith("RATE_LIMITED"):
            _log(f"  -> rate limited: {verdict.reason}")
            cool = random.uniform(45 * 60, 75 * 60)
            _log(f"  ...cooldown {cool / 60:.1f} min then retry @{username}")
            time.sleep(cool)
            continue  # retry same account

        report["accounts"][username.lower()] = verdict.to_dict()
        _save_report(args.report, report)
        _write_markdown(REPORT_MD, report)
        _log(f"  -> {verdict.decision}: {verdict.reason} ({verdict.source})")
        processed += 1
        i += 1

        if args.fetch:
            pause = random.uniform(ACCOUNT_PAUSE_MIN_SEC, ACCOUNT_PAUSE_MAX_SEC)
            _log(f"  ...pause {pause:.0f}s")
            time.sleep(pause)
        else:
            time.sleep(random.uniform(0.4, 1.2))

    _save_report(args.report, report)
    _write_markdown(REPORT_MD, report)

    accounts = report.get("accounts") or {}
    counts: Dict[str, int] = {}
    for r in accounts.values():
        d = r.get("decision") or "unsure"
        counts[d] = counts.get(d, 0) + 1

    _log(
        f"\nDone this run: {processed}. "
        f"Report totals — keep={counts.get('keep', 0)} "
        f"unfollow={counts.get('unfollow', 0)} "
        f"unsure={counts.get('unsure', 0)} "
        f"skipped={counts.get('skipped', 0)}"
    )
    _log(f"JSON: {args.report}")
    _log(f"Markdown: {REPORT_MD}")


if __name__ == "__main__":
    main()
