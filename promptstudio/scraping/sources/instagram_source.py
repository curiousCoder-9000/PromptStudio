"""Instagram source adapter.

A thin `MediaSource` face over the existing `InstagramDownloader`. All the
Instagram behaviour — ranking, catch-up streaks, anti-ban pacing, abuse-signal
aborts — stays exactly where it is; this only adapts the call shape so the queue
can dispatch Instagram the same way it dispatches X and Reddit.
"""

from __future__ import annotations

from typing import Any

from promptstudio.config import DEFAULT_MAX_POSTS_PER_CREATOR, SAVED_DIR
from promptstudio.scraping.results import SyncResult
from promptstudio.scraping.sources.base import (
    ScrapeOptions,
    SourceContext,
    SourceTarget,
    resolve_folder_name,
    sanitize_folder,
)


class InstagramSource:
    name = "instagram"
    label = "Instagram"

    def parse_target(self, target: str, **kwargs: Any) -> SourceTarget:
        raw = (target or "").strip()
        handle = sanitize_folder(raw)
        if not handle:
            raise ValueError("Instagram handle required")
        return SourceTarget(
            source=self.name,
            raw=raw,
            url=f"https://www.instagram.com/{handle}/",
            folder=resolve_folder_name(self.name, handle),
            handle=handle,
            label=f"@{handle}",
        )

    def run(
        self,
        target: SourceTarget,
        options: ScrapeOptions,
        ctx: SourceContext,
    ) -> SyncResult:
        from promptstudio.scraping.downloader import InstagramDownloader

        max_posts = options.max_posts
        if max_posts is None or int(max_posts) <= 0:
            # sync_creator_feed treats <=0 as "use the full-scrape ceiling" only
            # in full mode; bounded needs a real number.
            max_posts = 0 if options.mode == "full" else DEFAULT_MAX_POSTS_PER_CREATOR

        downloader = InstagramDownloader(
            save_dir=ctx.save_dir or SAVED_DIR,
            log=ctx.log,
            on_rate_limit=ctx.on_rate_limit,
            should_cancel=ctx.should_cancel,
        )
        result = downloader.sync_creator_feed(
            target.handle,
            max_posts=max_posts,
            include_videos=options.include_videos,
            mode=options.mode,
            deep=options.deep,
        )
        result.source = self.name
        return result
