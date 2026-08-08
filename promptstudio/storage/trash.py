"""Recycle bin for archive media — soft delete with full restore.

Deleting from the UI moves media into `_trash/<entry_id>/` instead of unlinking.
Each entry carries an `entry.json` manifest with everything needed to put the
photo back exactly as it was:

    _trash/20260808T191204Z-a1b2c3/
        entry.json          # manifest (below)
        IMG_123.jpg         # the media file
        IMG_123.jpg.meta.json   # sidecar, when one existed

Manifest fields: `rel_path`, `creator`, `filename`, `deleted_at`, `file_size`,
`favorite`, `prompt_bundle`, `post_id`, `shortcode`, `tombstoned`, `taken_at`.

`_trash` is in `EXCLUDED_FOLDERS`, so trashed media never appears in the
gallery, index rebuilds, or classify jobs. Thumbnails are *not* preserved —
they are derived data and regenerate on demand after a restore.
"""

import json
import os
import secrets
import shutil
import threading
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from promptstudio.config import (
    METADATA_SUFFIX,
    SAVED_DIR,
    TRASH_DIR,
    TRASH_RETENTION_DAYS,
)
from promptstudio.logging_setup import get_logger
from promptstudio.storage.atomic import atomic_write_json
from promptstudio.storage.db import DEFAULT_SOURCE

log = get_logger(__name__)

ENTRY_MANIFEST = "entry.json"

# Serializes entry creation so two deletes never claim the same directory.
_WRITE_LOCK = threading.Lock()


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_entry_id() -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{stamp}-{secrets.token_hex(3)}"


class TrashStore:
    """Move-to-trash and restore for media under the archive root."""

    def __init__(self, trash_dir: str = TRASH_DIR, base_dir: str = SAVED_DIR) -> None:
        self.trash_dir = os.path.expanduser(trash_dir)
        self.base_dir = os.path.expanduser(base_dir)

    # ---------------------------------------------------------------- helpers

    def _entry_dir(self, entry_id: str) -> Optional[str]:
        """Resolve an entry directory, refusing anything outside the trash root."""
        clean = os.path.basename((entry_id or "").strip())
        if not clean or clean in (".", ".."):
            return None
        path = os.path.normpath(os.path.join(self.trash_dir, clean))
        if os.path.dirname(path) != os.path.normpath(self.trash_dir):
            return None
        return path

    def _read_manifest(self, entry_dir: str) -> Optional[Dict[str, Any]]:
        manifest_path = os.path.join(entry_dir, ENTRY_MANIFEST)
        if not os.path.isfile(manifest_path):
            return None
        try:
            with open(manifest_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, dict) else None
        except (OSError, ValueError):
            return None

    def _write_manifest(self, entry_dir: str, entry: Dict[str, Any]) -> None:
        # The manifest is the only record of how to restore this entry; a
        # truncated one strands the media in _trash with no way back.
        atomic_write_json(os.path.join(entry_dir, ENTRY_MANIFEST), entry)

    def _media_path(self, entry_dir: str, entry: Dict[str, Any]) -> Optional[str]:
        filename = entry.get("filename") or ""
        if not filename:
            return None
        candidate = os.path.join(entry_dir, os.path.basename(filename))
        return candidate if os.path.isfile(candidate) else None

    # ----------------------------------------------------------------- delete

    def move_to_trash(
        self,
        *,
        full_path: str,
        rel_path: str,
        creator: str,
        favorite: bool = False,
        prompt_bundle: Optional[Dict[str, Any]] = None,
        post_id: Optional[str] = None,
        shortcode: Optional[str] = None,
        tombstoned: bool = False,
        taken_at: Optional[str] = None,
        platform: str = DEFAULT_SOURCE,
    ) -> Dict[str, Any]:
        """Move a media file (and its sidecar) into the trash.

        Returns the manifest. Raises OSError if the move fails, in which case
        nothing has been removed from the archive.
        """
        filename = os.path.basename(full_path)
        try:
            file_size = os.path.getsize(full_path)
        except OSError:
            file_size = 0

        with _WRITE_LOCK:
            os.makedirs(self.trash_dir, exist_ok=True)
            entry_id = _new_entry_id()
            entry_dir = os.path.join(self.trash_dir, entry_id)
            while os.path.exists(entry_dir):
                entry_id = _new_entry_id()
                entry_dir = os.path.join(self.trash_dir, entry_id)
            os.makedirs(entry_dir)

        entry: Dict[str, Any] = {
            "id": entry_id,
            "rel_path": rel_path.replace("\\", "/").lstrip("/"),
            "creator": creator,
            "filename": filename,
            "deleted_at": _utc_now_iso(),
            "file_size": file_size,
            "favorite": bool(favorite),
            "prompt_bundle": prompt_bundle or None,
            "post_id": post_id or None,
            "shortcode": shortcode or None,
            "tombstoned": bool(tombstoned),
            "taken_at": taken_at or None,
            # Needed at restore time: clear_deleted_post is platform-scoped, so
            # restoring without it would leave a non-IG tombstone in place.
            "platform": (platform or DEFAULT_SOURCE).strip().lower() or DEFAULT_SOURCE,
        }

        # Manifest first: a crash mid-move leaves a recoverable entry on disk.
        self._write_manifest(entry_dir, entry)
        try:
            shutil.move(full_path, os.path.join(entry_dir, filename))
        except OSError:
            shutil.rmtree(entry_dir, ignore_errors=True)
            raise

        sidecar = full_path + METADATA_SUFFIX
        if os.path.isfile(sidecar):
            try:
                shutil.move(sidecar, os.path.join(entry_dir, filename + METADATA_SUFFIX))
            except OSError:
                pass  # media is safe; sidecar can be rebuilt from the index

        return entry

    # ------------------------------------------------------------------- read

    def list_entries(
        self, *, limit: Optional[int] = None, offset: int = 0
    ) -> Tuple[List[Dict[str, Any]], int]:
        """Return (entries, total) newest-deleted first."""
        if not os.path.isdir(self.trash_dir):
            return [], 0
        entries: List[Dict[str, Any]] = []
        for name in os.listdir(self.trash_dir):
            entry_dir = os.path.join(self.trash_dir, name)
            if not os.path.isdir(entry_dir):
                continue
            entry = self._read_manifest(entry_dir)
            if not entry:
                continue
            entry["id"] = entry.get("id") or name
            entry["media_present"] = self._media_path(entry_dir, entry) is not None
            entries.append(entry)
        entries.sort(key=lambda e: str(e.get("deleted_at") or ""), reverse=True)
        total = len(entries)
        start = max(0, offset)
        window = entries[start : start + limit] if limit else entries[start:]
        return window, total

    def count_entries(self) -> int:
        """Cheap entry count — directory scan only, no manifest reads.

        Used by hot endpoints like /api/stats; prefer this over list_entries().
        """
        if not os.path.isdir(self.trash_dir):
            return 0
        try:
            with os.scandir(self.trash_dir) as it:
                return sum(1 for e in it if e.is_dir())
        except OSError:
            return 0

    def stats(self) -> Dict[str, Any]:
        entries, total = self.list_entries()
        return {
            "count": total,
            "bytes": sum(int(e.get("file_size") or 0) for e in entries),
            "oldest_deleted_at": entries[-1].get("deleted_at") if entries else None,
            "retention_days": TRASH_RETENTION_DAYS,
        }

    # ---------------------------------------------------------------- restore

    def restore(self, entry_id: str) -> Dict[str, Any]:
        """Put a trashed photo back, including prompt bundle and favorite flag.

        Returns {"status": "restored" | "not_found" | "conflict" | "error", ...}.
        Never overwrites an existing file at the target path.
        """
        entry_dir = self._entry_dir(entry_id)
        if not entry_dir or not os.path.isdir(entry_dir):
            return {"status": "not_found", "id": entry_id}
        entry = self._read_manifest(entry_dir)
        if not entry:
            return {"status": "not_found", "id": entry_id}

        rel_path = str(entry.get("rel_path") or "").replace("\\", "/").lstrip("/")
        if not rel_path:
            return {"status": "error", "id": entry_id, "message": "manifest missing rel_path"}

        media = self._media_path(entry_dir, entry)
        if not media:
            return {"status": "error", "id": entry_id, "message": "media file missing from trash"}

        target = os.path.normpath(os.path.join(self.base_dir, rel_path))
        if not target.startswith(os.path.normpath(self.base_dir)):
            return {"status": "error", "id": entry_id, "message": "unsafe target path"}
        if os.path.exists(target):
            return {"status": "conflict", "id": entry_id, "rel_path": rel_path}

        os.makedirs(os.path.dirname(target), exist_ok=True)
        try:
            shutil.move(media, target)
        except OSError as exc:
            return {"status": "error", "id": entry_id, "message": str(exc)}

        # Sidecar metadata
        trashed_sidecar = os.path.join(entry_dir, entry["filename"] + METADATA_SUFFIX)
        if os.path.isfile(trashed_sidecar):
            try:
                shutil.move(trashed_sidecar, target + METADATA_SUFFIX)
            except OSError:
                pass

        # Un-tombstone so a future sync is allowed to see this post again
        if entry.get("tombstoned") and (entry.get("post_id") or entry.get("shortcode")):
            try:
                from promptstudio.storage.db import ArchiveIndex

                ArchiveIndex.get().clear_deleted_post(
                    str(entry.get("creator") or ""),
                    shortcode=entry.get("shortcode"),
                    post_id=entry.get("post_id"),
                    # Older manifests predate this field — they are all Instagram.
                    platform=str(entry.get("platform") or DEFAULT_SOURCE),
                )
            except Exception as exc:
                log.warning("could not clear tombstone for %s: %s", rel_path, exc)

        # Re-index before restoring prompt/favorite state so their index sync lands
        try:
            from promptstudio.storage.db import ArchiveIndex

            ArchiveIndex.get().upsert_photo(rel_path, taken_at=entry.get("taken_at"))
        except Exception as exc:
            log.warning("could not reindex restored %s: %s", rel_path, exc)

        bundle = entry.get("prompt_bundle")
        if isinstance(bundle, dict) and bundle:
            try:
                from promptstudio.prompts.cache import PromptCache

                PromptCache().set(rel_path, bundle, push_history=False)
            except Exception as exc:
                log.warning("could not restore prompt bundle for %s: %s", rel_path, exc)

        if entry.get("favorite"):
            try:
                from promptstudio.storage.favorites import FavoritesStore

                FavoritesStore().set_favorite(rel_path, True)
            except Exception as exc:
                log.warning("could not restore favorite for %s: %s", rel_path, exc)

        shutil.rmtree(entry_dir, ignore_errors=True)
        return {
            "status": "restored",
            "id": entry_id,
            "rel_path": rel_path,
            "creator": entry.get("creator") or "",
            "filename": entry.get("filename") or "",
        }

    # ------------------------------------------------------------------ purge

    def purge(self, entry_id: str) -> bool:
        """Permanently remove one trash entry."""
        entry_dir = self._entry_dir(entry_id)
        if not entry_dir or not os.path.isdir(entry_dir):
            return False
        shutil.rmtree(entry_dir, ignore_errors=True)
        return not os.path.isdir(entry_dir)

    def purge_expired(self, days: Optional[int] = None) -> int:
        """Permanently remove entries older than the retention window."""
        window = TRASH_RETENTION_DAYS if days is None else days
        if window <= 0:
            return 0
        cutoff = datetime.now(timezone.utc) - timedelta(days=window)
        entries, _ = self.list_entries()
        removed = 0
        for entry in entries:
            raw = str(entry.get("deleted_at") or "")
            try:
                when = datetime.fromisoformat(raw)
            except ValueError:
                continue
            if when.tzinfo is None:
                when = when.replace(tzinfo=timezone.utc)
            if when < cutoff and self.purge(str(entry.get("id") or "")):
                removed += 1
        return removed

    def empty(self) -> int:
        """Permanently remove every trash entry. Returns the count removed."""
        entries, _ = self.list_entries()
        removed = 0
        for entry in entries:
            if self.purge(str(entry.get("id") or "")):
                removed += 1
        return removed
