#!/usr/bin/env python3
"""Bulk download from accounts in following_list.json (Phase A anti-ban pacing)."""

import argparse
import os
import sys

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from promptstudio.config import (
    DEFAULT_ACCOUNTS_PER_DAY,
    DEFAULT_BIO_KEYWORDS,
    DEFAULT_MIN_MEDIA_COUNT,
    INCLUDE_VIDEOS_DEFAULT,
)
from promptstudio.scraping.downloader import InstagramDownloader


def main():
    parser = argparse.ArgumentParser(
        description="Bulk sync from Instagram following list with daily caps and anti-ban pacing"
    )
    parser.add_argument(
        "--accounts-per-day",
        type=int,
        default=None,
        help=f"Max accounts to process this run / today (default: {DEFAULT_ACCOUNTS_PER_DAY})",
    )
    parser.add_argument(
        "--max-accounts",
        type=int,
        default=None,
        help="Alias for --accounts-per-day (compatibility)",
    )
    parser.add_argument(
        "--max-posts",
        type=int,
        default=20,
        help="Max posts per account (default: 20)",
    )
    parser.add_argument(
        "--include-private",
        action="store_true",
        help="Attempt private accounts (usually skipped)",
    )
    parser.add_argument(
        "--keywords",
        type=str,
        default=",".join(DEFAULT_BIO_KEYWORDS),
        help="Comma-separated bio/name keywords (empty string disables keyword filter)",
    )
    parser.add_argument(
        "--min-media",
        type=int,
        default=DEFAULT_MIN_MEDIA_COUNT,
        help=f"Minimum media_count (default: {DEFAULT_MIN_MEDIA_COUNT})",
    )
    reels = parser.add_mutually_exclusive_group()
    reels.add_argument(
        "--include-reels",
        action="store_true",
        default=None,
        help="Download reels / video posts (default: on via IG_INCLUDE_VIDEOS)",
    )
    reels.add_argument(
        "--no-reels",
        action="store_true",
        help="Skip reels / video posts",
    )
    args = parser.parse_args()

    if args.accounts_per_day is not None and args.max_accounts is not None:
        if args.accounts_per_day != args.max_accounts:
            parser.error("Pass only one of --accounts-per-day or --max-accounts")
    accounts_cap = (
        args.accounts_per_day
        if args.accounts_per_day is not None
        else args.max_accounts
        if args.max_accounts is not None
        else DEFAULT_ACCOUNTS_PER_DAY
    )

    if args.no_reels:
        include_videos = False
    elif args.include_reels:
        include_videos = True
    else:
        include_videos = INCLUDE_VIDEOS_DEFAULT

    keywords = [k.strip() for k in args.keywords.split(",") if k.strip()] if args.keywords else []
    result = InstagramDownloader().sync_following(
        max_accounts=accounts_cap,
        max_posts_per_account=args.max_posts,
        public_only=not args.include_private,
        keywords=keywords,
        min_media_count=args.min_media,
        include_videos=include_videos,
    )
    if result.queue_summary:
        print(f"Queue summary: {result.queue_summary}")
    if result.aborted:
        print(f"Aborted: {result.abort_reason}", file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
