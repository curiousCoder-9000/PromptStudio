#!/usr/bin/env python3
"""Download recent posts from a single Instagram creator."""

import argparse
import os
import sys

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from promptstudio.config import INCLUDE_VIDEOS_DEFAULT
from promptstudio.scraping.downloader import InstagramDownloader


def main():
    parser = argparse.ArgumentParser(description="Download Instagram creator feed")
    parser.add_argument("username", help="Instagram handle (without @)")
    parser.add_argument(
        "--max-posts",
        type=int,
        default=50,
        help="Maximum posts to download (default: 50; full mode uses IG_FULL_SCRAPE_MAX_POSTS if 0)",
    )
    parser.add_argument(
        "--full",
        action="store_true",
        help="Stream full feed (no glam rank / top-N). Default is bounded.",
    )
    parser.add_argument(
        "--latest",
        action="store_true",
        help="Catch-up only: newest posts until already-local/deleted streak (existing folders).",
    )
    parser.add_argument(
        "--deep",
        action="store_true",
        default=None,
        help="With --full: disable catch-up (true archive). Default on for --full.",
    )
    parser.add_argument(
        "--no-deep",
        action="store_true",
        help="With --full: enable catch-up streak stop (resume walk).",
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
    if args.no_reels:
        include_videos = False
    elif args.include_reels:
        include_videos = True
    else:
        include_videos = INCLUDE_VIDEOS_DEFAULT

    if args.latest:
        mode = "latest"
    elif args.full:
        mode = "full"
    else:
        mode = "bounded"
    if args.no_deep:
        deep = False
    elif args.deep:
        deep = True
    else:
        deep = True  # default deep for full (ignored for latest)

    InstagramDownloader().sync_creator_feed(
        args.username,
        max_posts=args.max_posts,
        include_videos=include_videos,
        mode=mode,
        deep=deep,
    )


if __name__ == "__main__":
    main()
