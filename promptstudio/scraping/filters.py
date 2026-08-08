"""Filter Instagram following-list entries and rank posts for glam acquisition."""

from typing import Any, Iterable, List, Optional, Sequence

from promptstudio.config import (
    DEFAULT_BIO_KEYWORDS,
    DEFAULT_CAPTION_KEYWORDS,
    DEFAULT_MIN_MEDIA_COUNT,
)


def normalize_keywords(keywords: Optional[Sequence[str]]) -> List[str]:
    if keywords is None:
        return list(DEFAULT_BIO_KEYWORDS)
    return [k.strip().lower() for k in keywords if k and k.strip()]


def entry_matches_keywords(entry: dict, keywords: Sequence[str]) -> bool:
    """True if keywords empty (no filter) or any keyword appears in bio/name/username."""
    if not keywords:
        return True
    haystack = " ".join(
        [
            str(entry.get("biography") or ""),
            str(entry.get("full_name") or ""),
            str(entry.get("username") or ""),
        ]
    ).lower()
    return any(k in haystack for k in keywords)


def filter_following_entries(
    entries: Iterable[dict],
    *,
    keywords: Optional[Sequence[str]] = None,
    min_media_count: int = DEFAULT_MIN_MEDIA_COUNT,
    public_only: bool = True,
) -> List[dict]:
    """Return following entries that pass privacy, media count, and bio filters."""
    kw = normalize_keywords(keywords)
    selected: List[dict] = []
    for entry in entries:
        if public_only and entry.get("is_private"):
            continue
        media_count = entry.get("media_count")
        # None = unknown (edge-only export); do not reject
        if media_count is not None and int(media_count) < min_media_count:
            continue
        if not entry_matches_keywords(entry, kw):
            continue
        selected.append(entry)
    return selected


def score_instagram_post(
    post: Any,
    *,
    caption_keywords: Optional[Sequence[str]] = None,
    feed_index: int = 0,
) -> float:
    """Cheap glam preference score for an Instaloader Post (no network).

    Higher = prefer download first within a feed scan window.
    """
    kws = [
        k.strip().lower()
        for k in (caption_keywords if caption_keywords is not None else DEFAULT_CAPTION_KEYWORDS)
        if k and str(k).strip()
    ]
    score = 0.0
    caption = (getattr(post, "caption", None) or "").lower()
    hits = 0
    for kw in kws:
        if kw and kw in caption:
            hits += 1
    # Diminishing returns after first few hits
    if hits:
        score += 3.0 + min(hits - 1, 4) * 1.0
    if getattr(post, "is_video", False):
        score += 1.5
    try:
        slides = int(getattr(post, "mediacount", 0) or 0)
    except (TypeError, ValueError):
        slides = 0
    if slides > 1:
        score += 0.5 + min(slides - 2, 4) * 0.15
    # Mild recency bias: earlier in feed (newer) ranks slightly higher
    score -= min(feed_index, 200) * 0.01
    return score
