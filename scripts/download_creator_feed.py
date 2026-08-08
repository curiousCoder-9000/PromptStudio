#!/usr/bin/env python3
"""Download recent posts from a single Instagram creator."""

import argparse
import os
import sys

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from promptstudio.scraping.downloader import InstagramDownloader


def main():
    parser = argparse.ArgumentParser(description="Download Instagram creator feed")
    parser.add_argument("username", help="Instagram handle (without @)")
    parser.add_argument(
        "--max-posts",
        type=int,
        default=50,
        help="Maximum posts to download (default: 50)",
    )
    parser.add_argument(
        "--include-reels",
        action="store_true",
        help="Also download reels / video posts (skipped by default)",
    )
    args = parser.parse_args()
    InstagramDownloader().sync_creator_feed(
        args.username,
        max_posts=args.max_posts,
        include_videos=args.include_reels,
    )


if __name__ == "__main__":
    main()
