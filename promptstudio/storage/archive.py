"""Local image archive operations backed by SQLite catalog."""

import os
import re
from typing import Any, Dict, List, Optional, Tuple

from promptstudio.config import EXCLUDED_FOLDERS, SAVED_DIR
from promptstudio.storage.db import (
    ArchiveIndex,
    normalize_rel_path,
    taken_at_for_image,
)


def photo_sort_key(photo: Dict[str, Any]) -> Tuple[str, str]:
    """Return (iso_timestamp, filename) for date sorting."""
    filename = photo.get("filename") or ""
    if photo.get("taken_at"):
        return (str(photo["taken_at"]), filename)
    full = photo.get("full_path") or ""
    return (taken_at_for_image(full, filename), filename)


def sort_photos(photos: List[Dict[str, Any]], sort: str = "name") -> List[Dict[str, Any]]:
    sort = (sort or "name").lower()
    if sort == "newest":
        return sorted(photos, key=photo_sort_key, reverse=True)
    if sort == "oldest":
        return sorted(photos, key=photo_sort_key, reverse=False)
    return sorted(
        photos,
        key=lambda p: (p.get("creator") or "", p.get("filename") or ""),
    )


class ArchiveStore:
    """Read/write helpers for ~/Pictures/InstagramSaved."""

    def __init__(self, base_dir: str = SAVED_DIR) -> None:
        self.base_dir = os.path.expanduser(base_dir)
        os.makedirs(self.base_dir, exist_ok=True)
        self._index = ArchiveIndex.get()

    def ensure_ready(self, force: bool = False) -> None:
        self._index.ensure_ready(force=force)

    def rebuild_index(self) -> int:
        return self._index.rebuild()

    def _creator_dirs(self) -> List[str]:
        if not os.path.isdir(self.base_dir):
            return []
        dirs = []
        for item in os.listdir(self.base_dir):
            path = os.path.join(self.base_dir, item)
            if os.path.isdir(path) and item not in EXCLUDED_FOLDERS:
                dirs.append(item)
        return dirs

    def list_creators(self) -> List[Dict[str, Any]]:
        return self._index.list_creators()

    def iter_photos(
        self,
        creator: Optional[str] = None,
        limit: Optional[int] = None,
        offset: int = 0,
        *,
        search: Optional[str] = None,
        unanalyzed: bool = False,
        favorite_only: bool = False,
        media_type: Optional[str] = None,
        sort: str = "name",
    ) -> List[Dict[str, Any]]:
        photos, _ = self._index.query_photos(
            creator=creator,
            search=search,
            unanalyzed=unanalyzed,
            favorite_only=favorite_only,
            media_type=media_type,
            sort=sort,
            limit=limit,
            offset=offset,
        )
        return photos

    def query_photos(
        self,
        creator: Optional[str] = None,
        limit: Optional[int] = None,
        offset: int = 0,
        *,
        search: Optional[str] = None,
        unanalyzed: bool = False,
        favorite_only: bool = False,
        media_type: Optional[str] = None,
        sort: str = "name",
    ) -> Tuple[List[Dict[str, Any]], int]:
        return self._index.query_photos(
            creator=creator,
            search=search,
            unanalyzed=unanalyzed,
            favorite_only=favorite_only,
            media_type=media_type,
            sort=sort,
            limit=limit,
            offset=offset,
        )

    def count_photos(self, creator: Optional[str] = None) -> int:
        with self._index._lock:
            if creator:
                row = self._index._conn.execute(
                    "SELECT COUNT(*) AS c FROM photos WHERE creator = ?", (creator,)
                ).fetchone()
            else:
                row = self._index._conn.execute(
                    "SELECT COUNT(*) AS c FROM photos"
                ).fetchone()
        return int(row["c"])

    def resolve_path(self, rel_path: str) -> Optional[str]:
        full = os.path.normpath(os.path.join(self.base_dir, rel_path))
        base = os.path.normpath(self.base_dir)
        if full.startswith(base) and os.path.isfile(full):
            return full
        return None

    def stats(self) -> Dict[str, int]:
        return self._index.stats()

    def create_creator(self, name: str) -> str:
        clean = re.sub(r"[^a-zA-Z0-9_\.]", "", name.strip())
        if not clean:
            raise ValueError("Invalid creator handle name")
        path = os.path.join(self.base_dir, clean)
        os.makedirs(path, exist_ok=True)
        return clean

    def delete_photo(self, rel_path: str) -> Optional[str]:
        full = self.resolve_path(rel_path)
        if not full:
            return None
        filename = os.path.basename(full)
        os.remove(full)
        try:
            from promptstudio.storage.metadata import delete_metadata_for_image

            delete_metadata_for_image(full)
        except OSError:
            pass
        try:
            from promptstudio.storage.thumbs import resolve_thumb_file

            thumb = resolve_thumb_file(rel_path)
            if thumb and os.path.isfile(thumb):
                os.remove(thumb)
        except OSError:
            pass
        self._index.delete_photo(rel_path)
        return filename

    def save_upload(self, creator: str, filename: str, content: bytes) -> str:
        target_dir = os.path.join(self.base_dir, creator)
        os.makedirs(target_dir, exist_ok=True)
        safe_name = os.path.basename(filename)
        target_path = os.path.join(target_dir, safe_name)
        with open(target_path, "wb") as f:
            f.write(content)
        rel = normalize_rel_path(f"{creator}/{safe_name}")
        self._index.upsert_photo(rel)
        return safe_name

    def index_photo(self, rel_path: str, taken_at: Optional[str] = None) -> None:
        """Upsert a newly downloaded/saved image into the catalog."""
        self._index.upsert_photo(rel_path, taken_at=taken_at)
