#!/usr/bin/env python3
"""Sync Instagram :saved posts into the local archive."""

from promptstudio.config import SAVED_DIR
from promptstudio.scraping.sources.base import SourceContext
from promptstudio.scraping.sources.instagram_source import run_saved

if __name__ == "__main__":
    run_saved(SourceContext(save_dir=SAVED_DIR, log=print))
