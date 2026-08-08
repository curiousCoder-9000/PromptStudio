"""Filter Instagram following-list entries for bulk sync."""

from typing import Iterable, List, Optional, Sequence

from promptstudio.config import DEFAULT_BIO_KEYWORDS, DEFAULT_MIN_MEDIA_COUNT


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
