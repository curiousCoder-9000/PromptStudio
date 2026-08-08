"""Source registry.

Sources are resolved lazily by name so that importing the registry does not drag
in `instaloader` (Instagram) or probe for the `gallery-dl` binary (X / Reddit).
"""

from __future__ import annotations

from typing import Any, Dict, List

from promptstudio.scraping.sources.base import (
    MediaSource,
    NormalizedPost,
    ScrapeOptions,
    SourceContext,
    SourceTarget,
    resolve_folder_name,
    sanitize_folder,
)
from promptstudio.storage.db import DEFAULT_SOURCE

__all__ = [
    "DEFAULT_SOURCE",
    "MediaSource",
    "NormalizedPost",
    "ScrapeOptions",
    "SourceContext",
    "SourceTarget",
    "get_source",
    "known_sources",
    "resolve_folder_name",
    "sanitize_folder",
    "source_info",
]

# name -> (module path, class name, human label)
_REGISTRY: Dict[str, tuple] = {
    "instagram": (
        "promptstudio.scraping.sources.instagram_source",
        "InstagramSource",
        "Instagram",
    ),
    "x": (
        "promptstudio.scraping.sources.gallery_dl_source",
        "XSource",
        "X / Twitter",
    ),
    "reddit": (
        "promptstudio.scraping.sources.gallery_dl_source",
        "RedditSource",
        "Reddit",
    ),
}

# Accepted spellings for each canonical source name.
_ALIASES: Dict[str, str] = {
    "ig": "instagram",
    "insta": "instagram",
    "instagram": "instagram",
    "x": "x",
    "twitter": "x",
    "tweet": "x",
    "reddit": "reddit",
    "r": "reddit",
}

_CACHE: Dict[str, Any] = {}


def normalize_source(name: str) -> str:
    key = (name or DEFAULT_SOURCE).strip().lower().lstrip("@")
    return _ALIASES.get(key, key)


def known_sources() -> List[str]:
    return list(_REGISTRY)


def source_info() -> List[Dict[str, str]]:
    """Registry summary for the UI / API."""
    return [
        {"name": name, "label": label}
        for name, (_mod, _cls, label) in _REGISTRY.items()
    ]


def get_source(name: str) -> MediaSource:
    """Resolve a source by name or alias. Raises ValueError if unknown."""
    key = normalize_source(name)
    if key in _CACHE:
        return _CACHE[key]
    entry = _REGISTRY.get(key)
    if not entry:
        raise ValueError(
            f"Unknown source '{name}'. Known: {', '.join(sorted(_REGISTRY))}"
        )
    module_path, class_name, _label = entry
    import importlib

    module = importlib.import_module(module_path)
    instance = getattr(module, class_name)()
    _CACHE[key] = instance
    return instance
