"""Shared scrape job result shape.

Lives outside `downloader.py` so non-Instagram sources can produce a `SyncResult`
without importing `instaloader`. `SyncManager` and `CreatorScrapeQueue` already
speak this shape (`to_dict()`, `aborted`, `stop_reason`, `downloaded`, ...), so
every source returning one needs no special handling downstream.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import List, Optional

# Terminal stop_reason values understood by CreatorScrapeQueue.finalize_job.
STOP_REASONS = (
    "end_of_feed",
    "catch_up",
    "ceiling",
    "nothing_new",
    "not_found",
    "private",
    "error",
    "abort",
    "cancel",
)


@dataclass
class SyncResult:
    job_type: str
    downloaded: int = 0
    skipped: int = 0
    skipped_deleted: int = 0
    errors: int = 0
    rate_limit_hits: int = 0
    aborted: bool = False
    abort_reason: str = ""
    accounts_processed: int = 0
    queue_summary: Optional[dict] = None
    messages: List[str] = field(default_factory=list)
    stop_reason: str = ""
    source: str = "instagram"

    def to_dict(self) -> dict:
        return asdict(self)
