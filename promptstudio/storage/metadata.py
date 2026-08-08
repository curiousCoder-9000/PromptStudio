"""Sidecar metadata for scraped Instagram images."""

import json
import os
from datetime import datetime, timezone
from typing import Any, Dict, Optional  # Dict used by group_by_post_id

from promptstudio.config import METADATA_SUFFIX, SAVED_DIR


def metadata_path_for_image(image_path: str) -> str:
    return image_path + METADATA_SUFFIX


def save_post_metadata(image_path: str, metadata: Dict[str, Any]) -> None:
    metadata.setdefault("updated_at", datetime.now(timezone.utc).isoformat())
    path = metadata_path_for_image(image_path)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(metadata, f, indent=2, ensure_ascii=False)


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


def delete_metadata_for_image(image_path: str) -> None:
    path = metadata_path_for_image(image_path)
    if os.path.isfile(path):
        try:
            os.remove(path)
        except OSError:
            pass


def rel_path_from_full(full_path: str) -> str:
    base = os.path.normpath(SAVED_DIR)
    full = os.path.normpath(full_path)
    if full.startswith(base):
        return os.path.relpath(full, base).replace("\\", "/")
    return full.replace("\\", "/")


def group_by_post_id(creator: str, base_dir: str = SAVED_DIR) -> Dict[str, list]:
    """Group image paths under a creator by Instagram post_id from sidecars."""
    from promptstudio.config import IMAGE_EXTENSIONS

    folder = os.path.join(os.path.expanduser(base_dir), creator)
    groups: Dict[str, list] = {}
    if not os.path.isdir(folder):
        return groups
    for name in os.listdir(folder):
        if not name.lower().endswith(IMAGE_EXTENSIONS):
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
