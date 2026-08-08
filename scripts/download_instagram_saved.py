#!/usr/bin/env python3
"""Sync Instagram :saved posts into the local archive."""

from promptstudio.scraping.downloader import InstagramDownloader

if __name__ == "__main__":
    InstagramDownloader().sync_saved_posts()
