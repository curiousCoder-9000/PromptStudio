"""Local image archive operations backed by SQLite catalog."""

import os
import re
from typing import Any, Dict, List, Optional, Tuple

from promptstudio.config import EXCLUDED_FOLDERS, SAVED_DIR, TRASH_ENABLED
from promptstudio.logging_setup import get_logger
from promptstudio.storage.db import (
    DEFAULT_SOURCE,
    ArchiveIndex,
    normalize_rel_path,
)
from promptstudio.storage.paths import safe_join

log = get_logger(__name__)


def ensure_creator_folder(name: str, base_dir: str = SAVED_DIR) -> Dict[str, Any]:
    """
    Create creator folder if missing.

    Returns {"name": str, "created": bool, "path": str}.
    Raises ValueError for empty/invalid/excluded names.
    """
    raw = (name or "").strip().lstrip("@")
    clean = re.sub(r"[^a-zA-Z0-9_\.]", "", raw)
    if not clean:
        raise ValueError("Invalid creator handle name")
    if clean in EXCLUDED_FOLDERS or clean.startswith(("_", ".")):
        raise ValueError(f"Reserved or excluded creator name: {clean}")
    path = os.path.join(os.path.expanduser(base_dir), clean)
    existed = os.path.isdir(path)
    os.makedirs(path, exist_ok=True)
    return {"name": clean, "created": not existed, "path": path}


class ArchiveStore:
    """Read/write helpers for ~/Pictures/InstagramSaved."""

    def __init__(self, base_dir: str = SAVED_DIR) -> None:
        self.base_dir = os.path.expanduser(base_dir)
        os.makedirs(self.base_dir, exist_ok=True)
        self._index = ArchiveIndex.get()

    def ensure_ready(self, force: bool = False) -> None:
        self._index.ensure_ready(force=force)

    def list_creators(self, *, source: Optional[str] = None) -> List[Dict[str, Any]]:
        return self._index.list_creators(source=source)

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
        verdict: Optional[str] = None,
        source: Optional[str] = None,
        sort: str = "name",
    ) -> Tuple[List[Dict[str, Any]], int]:
        return self._index.query_photos(
            creator=creator,
            search=search,
            unanalyzed=unanalyzed,
            favorite_only=favorite_only,
            media_type=media_type,
            verdict=verdict,
            source=source,
            sort=sort,
            limit=limit,
            offset=offset,
        )

    def resolve_path(self, rel_path: str) -> Optional[str]:
        """Resolve an archive-relative path, or None if it escapes the archive.

        Every media route (`/media/…`, `/api/media/detail`, `DELETE /api/photo`)
        goes through here, and CORS is `*`. Containment itself lives in
        `storage.paths.safe_join` — see that module for why a `startswith`
        prefix test is not enough.
        """
        full = safe_join(self.base_dir, rel_path)
        if full is None or not os.path.isfile(full):
            return None
        return full

    def stats(self) -> Dict[str, int]:
        return self._index.stats()

    def create_creator(self, name: str) -> str:
        return ensure_creator_folder(name, base_dir=self.base_dir)["name"]

    def delete_photo(
        self, rel_path: str, *, permanent: Optional[bool] = None
    ) -> Optional[Dict[str, Any]]:
        """Delete a photo, moving it to `_trash/` unless permanent is requested.

        Returns {"filename", "rel_path", "trash_id", "permanent"} or None when
        the path does not resolve. Soft deletes capture the prompt bundle and
        favorite flag into the trash manifest *before* clearing them, so a
        restore brings back the full state.
        """
        full = self.resolve_path(rel_path)
        if not full:
            return None
        soft = TRASH_ENABLED if permanent is None else not permanent
        filename = os.path.basename(full)
        rel = rel_path.replace("\\", "/").lstrip("/")
        creator = os.path.basename(os.path.dirname(full))

        # Tombstone Instagram identity so future sync never re-downloads this post
        post_id: Optional[str] = None
        shortcode: Optional[str] = None
        taken_at: Optional[str] = None
        try:
            c, pid, sc = self._index.get_photo_identity(rel)
            if c:
                creator = c
            post_id = pid
            shortcode = sc
        except Exception:
            pass
        meta: Dict[str, Any] = {}
        try:
            from promptstudio.storage.metadata import load_post_metadata

            meta = load_post_metadata(full) or {}
        except Exception:
            meta = {}
        if not post_id and not shortcode:
            post_id = str(meta.get("post_id") or "") or None
            shortcode = str(meta.get("shortcode") or "") or None
        taken_at = str(meta.get("taken_at") or "") or None
        # Which platform this media came from — the tombstone must be scoped to
        # it, or an X/Reddit id can shadow an Instagram one (and vice versa).
        platform = str(meta.get("source") or "").strip().lower()
        if not platform:
            try:
                platform = self._index.get_photo_source(rel)
            except Exception:
                platform = ""

        tombstoned = False
        if post_id or shortcode:
            try:
                tombstoned = bool(
                    self._index.record_deleted_post(
                        creator,
                        shortcode=shortcode,
                        post_id=post_id,
                        rel_path=rel,
                        source="ui",
                        platform=platform or DEFAULT_SOURCE,
                    )
                )
            except Exception:
                tombstoned = False

        # Snapshot restorable state before anything is cleared
        favorite = False
        prompt_bundle: Optional[Dict[str, Any]] = None
        if soft:
            try:
                from promptstudio.storage.favorites import FavoritesStore

                favorite = bool(FavoritesStore().is_favorite(rel))
            except Exception:
                favorite = False
            try:
                from promptstudio.prompts.cache import PromptCache

                prompt_bundle = PromptCache().get(rel, filename)
            except Exception:
                prompt_bundle = None

        trash_id: Optional[str] = None
        if soft:
            from promptstudio.storage.trash import TrashStore

            entry = TrashStore().move_to_trash(
                full_path=full,
                rel_path=rel,
                creator=creator,
                favorite=favorite,
                prompt_bundle=prompt_bundle,
                post_id=post_id,
                shortcode=shortcode,
                tombstoned=tombstoned,
                taken_at=taken_at,
                platform=platform or DEFAULT_SOURCE,
            )
            trash_id = str(entry.get("id") or "") or None
        else:
            os.remove(full)
            try:
                from promptstudio.storage.metadata import delete_metadata_for_image

                delete_metadata_for_image(full)
            except OSError:
                pass

        # Thumbs are derived data — drop them either way; they regenerate on demand
        try:
            from promptstudio.storage.thumbs import resolve_thumb_file

            thumb = resolve_thumb_file(rel_path)
            if thumb and os.path.isfile(thumb):
                os.remove(thumb)
        except OSError:
            pass

        try:
            from promptstudio.prompts.cache import PromptCache

            PromptCache().delete(rel, filename)
        except Exception as exc:
            log.warning("prompt cache delete failed for %s: %s", rel, exc)
        try:
            from promptstudio.storage.favorites import FavoritesStore

            FavoritesStore().set_favorite(rel, False)
        except Exception as exc:
            log.warning("favorite clear failed for %s: %s", rel, exc)

        # Soft delete keeps the classify verdict so Undo restores the review
        # pile intact; TrashStore.purge drops it when the file really goes.
        self._index.delete_photo(rel_path, drop_verdict=not soft)
        return {
            "filename": filename,
            "rel_path": rel,
            "trash_id": trash_id,
            "permanent": not soft,
        }

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
