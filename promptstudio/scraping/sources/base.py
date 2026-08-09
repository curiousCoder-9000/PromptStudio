"""Source-agnostic scrape contracts.

The seam that lets PromptStudio scrape more than Instagram. Three pieces:

* `NormalizedPost` — what every source produces per post, replacing the
  duck-typed `instaloader.Post` attribute access in `downloader.py`.
* `SourceTarget` — a parsed scrape target (handle / subreddit / URL) plus the
  archive folder it lands in.
* `MediaSource` — the protocol a source implements.

The protocol is deliberately *job*-level, not post-level. Instaloader iterates
posts in-process, but gallery-dl does discovery and download in one subprocess
invocation; forcing both into an `iter_posts()` / `fetch()` split would mean
running gallery-dl twice per target. `run()` returns the existing `SyncResult`
so `SyncManager` and `CreatorScrapeQueue` need no new result handling.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Dict, Optional, Protocol

from promptstudio.config import (
    DEFAULT_MAX_POSTS_PER_CREATOR,
    FOLDER_SUFFIX_NON_DEFAULT,
    FOLDER_SUFFIX_SEP,
    FULL_SCRAPE_MAX_POSTS,
    INCLUDE_VIDEOS_DEFAULT,
)
from promptstudio.storage.db import DEFAULT_SOURCE

# Mirrors ensure_creator_folder's sanitizer so a folder name we build here can
# never be silently rewritten into a different one downstream.
_FOLDER_SAFE = re.compile(r"[^a-zA-Z0-9_\.]")


def sanitize_folder(name: str) -> str:
    return _FOLDER_SAFE.sub("", (name or "").strip().lstrip("@"))


def resolve_folder_name(source: str, handle: str, *, kind: str = "") -> str:
    """Archive folder for a target.

    Instagram keeps the bare handle — the existing archive depends on it. Other
    sources get a `__<source>` suffix so that two different people who happen to
    share a handle on two platforms don't collide into one folder (which would
    also pollute per-creator glam stats and creator-style rebuilds).

    `kind` disambiguates namespaces within one source: Reddit's r/foo and u/foo
    are unrelated, so they become `r_foo__reddit` and `u_foo__reddit`.
    """
    src = (source or DEFAULT_SOURCE).strip().lower()
    base = sanitize_folder(handle)
    if not base:
        raise ValueError("empty handle")
    if kind:
        base = f"{sanitize_folder(kind)}_{base}"
    if src == DEFAULT_SOURCE or not FOLDER_SUFFIX_NON_DEFAULT:
        return base
    return f"{base}{FOLDER_SUFFIX_SEP}{sanitize_folder(src)}"


@dataclass
class NormalizedPost:
    """One post from any source, in the shape the storage layer wants."""

    source: str
    creator: str  # archive folder key (already resolved)
    post_id: str
    shortcode: str = ""
    taken_at: Optional[datetime] = None
    caption: str = ""
    is_video: bool = False
    media_count: int = 1
    post_url: str = ""
    author: str = ""  # true author when it differs from `creator`
    extra: Dict[str, Any] = field(default_factory=dict)

    def identity(self) -> str:
        return f"{self.source}:{self.post_id or self.shortcode}"


@dataclass
class SourceTarget:
    """A parsed scrape target."""

    source: str
    raw: str  # what the user typed
    url: str  # what the source should fetch
    folder: str  # archive folder name
    handle: str = ""  # bare handle/subreddit, no prefixes
    kind: str = ""  # source-specific namespace ("r", "u", "user", ...)
    label: str = ""  # human-readable, for logs and the UI

    def __post_init__(self) -> None:
        if not self.label:
            self.label = self.raw


VALID_MODES = ("full", "bounded", "latest")


@dataclass
class ScrapeOptions:
    """Per-job knobs, mapped onto whatever each source supports.

    Build these with `normalize()` rather than the constructor. The rules for
    turning a requested (mode, deep, max_posts) into a runnable one used to be
    re-derived in four places — the enqueue route, the one-shot sync route,
    `CreatorScrapeQueue.enqueue`, and `SyncManager.try_drain_creator_queue` —
    each with slightly different code. Three of the four then had their answer
    thrown away and recomputed by the next layer down, which made it impossible
    to tell by reading any single site what a given request would actually do.
    """

    mode: str = "full"  # full | bounded | latest
    deep: bool = True  # full+deep disables catch-up (true archive)
    max_posts: Optional[int] = None  # stored ceiling; None = "no explicit limit"
    include_videos: bool = INCLUDE_VIDEOS_DEFAULT
    catch_up_only: bool = False  # only meaningful when the request said "latest"
    requested_mode: str = ""  # what the caller asked for, before normalization
    upgraded_from_latest: bool = False  # latest → full+deep was applied

    @staticmethod
    def parse_mode(mode: Optional[str], *, strict: bool = False) -> str:
        """Clean a mode string. Coerces to "full" unless `strict`, which raises."""
        value = (mode or "full").strip().lower()
        if value not in VALID_MODES:
            if strict:
                raise ValueError("mode must be full, bounded, or latest")
            return "full"
        return value

    @classmethod
    def normalize(
        cls,
        mode: Optional[str] = "full",
        *,
        deep: bool = True,
        max_posts: Optional[int] = None,
        include_videos: bool = INCLUDE_VIDEOS_DEFAULT,
        catch_up_only: bool = False,
        strict: bool = False,
    ) -> "ScrapeOptions":
        """Resolve a scrape request into the options a source will actually run.

        `latest` without `catch_up_only` is upgraded to full+deep — walk the
        whole feed for every missing post. The old behaviour (stop after ~50
        newest) left partial archives behind, which is the Mikayla / roxeuoon
        ceiling bug. Pass `catch_up_only=True` for a true catch-up stream.

        **Idempotent**: normalizing an already-normalized job is a no-op, which
        is what lets the queue store the result and the drain re-derive from it
        without the two disagreeing.
        """
        requested = cls.parse_mode(mode, strict=strict)
        catch_up = bool(catch_up_only) and requested == "latest"
        upgraded = False

        if requested == "latest" and not catch_up:
            mode_out, deep_out, max_posts = "full", True, None
            upgraded = True
        elif requested == "latest":
            mode_out, deep_out = "latest", False
            if max_posts is None:
                max_posts = DEFAULT_MAX_POSTS_PER_CREATOR
        elif requested == "full":
            mode_out, deep_out = "full", bool(deep)
            # A deep full scrape has no low ceiling unless the caller set one;
            # <=0 means "unlimited", which is stored as None.
            if deep_out and max_posts is not None and int(max_posts) <= 0:
                max_posts = None
        else:
            mode_out, deep_out = "bounded", False

        return cls(
            mode=mode_out,
            deep=deep_out,
            max_posts=max_posts,
            include_videos=bool(include_videos),
            catch_up_only=catch_up,
            requested_mode=requested,
            upgraded_from_latest=upgraded,
        )

    def resolved_max_posts(self) -> int:
        """The download ceiling to actually run with.

        `max_posts` records intent ("no explicit limit"); this turns it into the
        number a source needs. Full scrapes fall back to the archive ceiling,
        everything else to the per-creator default.
        """
        if self.max_posts is not None and int(self.max_posts) > 0:
            return int(self.max_posts)
        if self.mode == "full":
            return int(FULL_SCRAPE_MAX_POSTS)
        return int(DEFAULT_MAX_POSTS_PER_CREATOR)


@dataclass
class SourceContext:
    """Callbacks and paths a source needs while running."""

    save_dir: str
    log: Callable[[str], None] = print
    should_cancel: Callable[[], bool] = lambda: False
    on_rate_limit: Optional[Callable[[int, int], None]] = None

    def cancelled(self) -> bool:
        try:
            return bool(self.should_cancel())
        except Exception:
            return False


class MediaSource(Protocol):
    """What a scrapeable platform must implement."""

    name: str
    label: str

    def parse_target(self, target: str, **kwargs: Any) -> SourceTarget:
        """Turn user input into a fetchable target. Raises ValueError if invalid."""
        ...

    def run(
        self,
        target: SourceTarget,
        options: ScrapeOptions,
        ctx: SourceContext,
    ) -> Any:
        """Scrape `target` into the archive. Returns a SyncResult."""
        ...
