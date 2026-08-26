"""Instagram source adapter.

A thin `MediaSource` face over the existing `InstagramDownloader`, with an
optional gallery-dl backend behind the same source name. All Instagram
behaviour that is Instaloader-specific — ranking, catch-up streaks, anti-ban
pacing, abuse-signal aborts — stays in `downloader.py`. gallery-dl is a
switch (`IG_BACKEND=gallery-dl`), not a second registry source, so folders
and `photos.source` stay `instagram`.
"""

from __future__ import annotations

from typing import Any, Optional, Sequence

from promptstudio.config import (
    DEFAULT_MAX_POSTS_PER_CREATOR,
    SAVED_DIR,
    SESSION_USER,
    instagram_backend,
)
from promptstudio.scraping.results import SyncResult
from promptstudio.scraping.sources.base import (
    ScrapeOptions,
    SourceContext,
    SourceTarget,
    resolve_folder_name,
    sanitize_folder,
)


def uses_gallery_dl() -> bool:
    return instagram_backend() == "gallery-dl"


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
        if uses_gallery_dl():
            from promptstudio.scraping.sources.gallery_dl_source import (
                InstagramGalleryDlSource,
            )

            return InstagramGalleryDlSource().run(target, options, ctx)

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


def run_saved(ctx: SourceContext) -> SyncResult:
    """Saved-posts job. Instaloader or gallery-dl, same backend flag as feeds."""
    if uses_gallery_dl():
        from promptstudio.scraping.sources.gallery_dl_source import (
            InstagramGalleryDlSource,
        )

        src = InstagramGalleryDlSource()
        try:
            target = src.parse_saved_target(SESSION_USER)
        except ValueError as exc:
            result = SyncResult(job_type="saved", source="instagram")
            result.errors = 1
            result.stop_reason = "error"
            result.messages.append(str(exc))
            ctx.log(str(exc))
            return result
        return src.run(target, ScrapeOptions.normalize("full", deep=True), ctx)

    from promptstudio.scraping.downloader import InstagramDownloader

    downloader = InstagramDownloader(
        save_dir=ctx.save_dir or SAVED_DIR,
        log=ctx.log,
        on_rate_limit=ctx.on_rate_limit,
        should_cancel=ctx.should_cancel,
    )
    return downloader.sync_saved_posts()


def run_following(
    ctx: SourceContext,
    *,
    max_accounts: Optional[int] = None,
    max_posts_per_account: int = 20,
    keywords: Optional[Sequence[str]] = None,
    min_media_count: int = 5,
    include_videos: bool = True,
    public_only: bool = True,
) -> SyncResult:
    """Following bulk. gallery-dl still uses the Instaloader queue/pacing loop."""
    from promptstudio.scraping.downloader import InstagramDownloader

    downloader = InstagramDownloader(
        save_dir=ctx.save_dir or SAVED_DIR,
        log=ctx.log,
        on_rate_limit=ctx.on_rate_limit,
        should_cancel=ctx.should_cancel,
    )
    feed_fn = None
    if uses_gallery_dl():
        gdl = InstagramSource()

        def feed_fn(username, max_posts=DEFAULT_MAX_POSTS_PER_CREATOR, include_videos=True, **_kw):
            target = gdl.parse_target(username)
            opts = ScrapeOptions.normalize(
                "bounded",
                max_posts=max_posts,
                include_videos=include_videos,
            )
            return gdl.run(target, opts, ctx)

    return downloader.sync_following(
        max_accounts=max_accounts,
        max_posts_per_account=max_posts_per_account,
        keywords=keywords,
        min_media_count=min_media_count,
        include_videos=include_videos,
        public_only=public_only,
        feed_fn=feed_fn,
    )
