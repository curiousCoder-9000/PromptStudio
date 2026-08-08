#!/usr/bin/env python3
"""Remove duplicate images by filename and MD5 hash."""

from promptstudio.scraping.organizer import deduplicate_archive

if __name__ == "__main__":
    deduplicate_archive()
