"""Sidecar metadata for scraped Instagram images."""

import json
import os
from datetime import datetime, timezone
from typing import Any, Dict, Optional  # Dict used by group_by_post_id

from promptstudio.config import METADATA_SUFFIX, SAVED_DIR
from promptstudio.storage.atomic import atomic_write_json


def metadata_path_for_image(image_path: str) -> str:
    return image_path + METADATA_SUFFIX


def save_post_metadata(image_path: str, metadata: Dict[str, Any]) -> None:
    metadata.setdefault("updated_at", datetime.now(timezone.utc).isoformat())
    path = metadata_path_for_image(image_path)
    atomic_write_json(path, metadata)


def load_post_metadata(image_path: str) -> Optional[Dict[str, Any]]:
    path = metadata_path_for_image(image_path)
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def build_metadata_from_post(post, carousel_index: int = 0) -> Dict[str, Any]:
    """Build metadata dict from an instaloader Post object."""
    caption = post.caption or ""
    return {
        "post_id": str(post.mediaid),
        "shortcode": post.shortcode,
        "owner_username": post.owner_username,
        "taken_at": post.date_utc.isoformat(),
        "caption": caption,
        "post_url": f"https://www.instagram.com/p/{post.shortcode}/",
        "is_video": post.is_video,
        "carousel_index": carousel_index,
        "source": "instagram",
        "downloaded_at": datetime.now(timezone.utc).isoformat(),
    }


def build_metadata_from_normalized(post, carousel_index: int = 0) -> Dict[str, Any]:
    """Build the same sidecar shape from a `NormalizedPost` (any source).

    Deliberately identical in shape to `build_metadata_from_post` so the gallery,
    prompt engine read one format regardless of platform.
    `owner_username` stays the archive folder key; the real author (which differs
    for Reddit submissions and X retweets) goes in `author`.
    """
    taken_at = ""
    if post.taken_at is not None:
        try:
            taken_at = post.taken_at.isoformat()
        except Exception:
            taken_at = str(post.taken_at)
    meta: Dict[str, Any] = {
        "post_id": str(post.post_id or ""),
        "shortcode": str(post.shortcode or ""),
        "owner_username": post.creator,
        "taken_at": taken_at,
        "caption": post.caption or "",
        "post_url": post.post_url or "",
        "is_video": bool(post.is_video),
        "carousel_index": carousel_index,
        "source": post.source,
        "downloaded_at": datetime.now(timezone.utc).isoformat(),
    }
    if post.author and post.author != post.creator:
        meta["author"] = post.author
    if post.extra:
        meta["source_extra"] = post.extra
    return meta


def delete_metadata_for_image(image_path: str) -> None:
    path = metadata_path_for_image(image_path)
    if os.path.isfile(path):
        try:
            os.remove(path)
        except OSError:
            pass


def group_by_post_id(creator: str, base_dir: str = SAVED_DIR) -> Dict[str, list]:
    """Group media paths under a creator by Instagram post_id from sidecars."""
    from promptstudio.config import MEDIA_EXTENSIONS

    folder = os.path.join(os.path.expanduser(base_dir), creator)
    groups: Dict[str, list] = {}
    if not os.path.isdir(folder):
        return groups
    for name in os.listdir(folder):
        if not name.lower().endswith(MEDIA_EXTENSIONS):
            continue
        full = os.path.join(folder, name)
        meta = load_post_metadata(full) or {}
        post_id = str(meta.get("post_id") or f"unknown:{name}")
        groups.setdefault(post_id, []).append(
            {
                "filename": name,
                "full_path": full,
                "carousel_index": meta.get("carousel_index", 0),
                "shortcode": meta.get("shortcode"),
                "post_url": meta.get("post_url"),
            }
        )
    for items in groups.values():
        items.sort(key=lambda x: x.get("carousel_index") or 0)
    return groups
