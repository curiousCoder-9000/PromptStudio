"""Favorite photo flags with in-memory write-through to favorites.json."""

import json
import os
import threading
from typing import Any, Dict, List, Optional, Set

from promptstudio.config import FAVORITES_FILE
from promptstudio.logging_setup import get_logger
from promptstudio.storage.atomic import atomic_write_json

log = get_logger(__name__)

_MEM: Dict[str, Optional[Set[str]]] = {}
_MEM_LOCKS: Dict[str, threading.RLock] = {}
_REG_LOCK = threading.Lock()


def _lock_for(path: str) -> threading.RLock:
    with _REG_LOCK:
        if path not in _MEM_LOCKS:
            _MEM_LOCKS[path] = threading.RLock()
            _MEM.setdefault(path, None)
        return _MEM_LOCKS[path]


class FavoritesStore:
    def __init__(self, path: str = FAVORITES_FILE) -> None:
        self.path = path

    def _ensure_loaded(self) -> Set[str]:
        lock = _lock_for(self.path)
        with lock:
            data = _MEM.get(self.path)
            if data is None:
                data = self._read_file()
                _MEM[self.path] = data
            return data

    def _read_file(self) -> Set[str]:
        if not os.path.isfile(self.path):
            return set()
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list):
                return {str(p).replace("\\", "/") for p in data}
            if isinstance(data, dict) and isinstance(data.get("favorites"), list):
                return {str(p).replace("\\", "/") for p in data["favorites"]}
        except Exception:
            pass
        return set()

    def load(self) -> Set[str]:
        return set(self._ensure_loaded())

    def save(self, paths: Set[str]) -> None:
        lock = _lock_for(self.path)
        with lock:
            _MEM[self.path] = set(paths)
            self._write_file(_MEM[self.path])

    def _write_file(self, paths: Set[str]) -> None:
        try:
            atomic_write_json(self.path, sorted(paths))
        except OSError as e:
            log.error("saving favorites %s: %s", self.path, e)

    def invalidate_memory(self) -> None:
        lock = _lock_for(self.path)
        with lock:
            _MEM[self.path] = None

    def is_favorite(self, rel_path: str) -> bool:
        lock = _lock_for(self.path)
        with lock:
            return self.cache_key(rel_path) in self._ensure_loaded()

    def cache_key(self, rel_path: str) -> str:
        return rel_path.replace("\\", "/").lstrip("/")

    def _sync_index(self, rel_path: str, favorite: bool) -> None:
        try:
            from promptstudio.storage.db import ArchiveIndex

            ArchiveIndex.get().set_favorite(rel_path, favorite)
        except Exception as e:
            log.warning("favorite index sync failed for %s: %s", rel_path, e)

    def set_favorite(self, rel_path: str, favorite: bool = True) -> bool:
        key = self.cache_key(rel_path)
        lock = _lock_for(self.path)
        with lock:
            paths = self._ensure_loaded()
            if favorite:
                paths.add(key)
            else:
                paths.discard(key)
            self._write_file(paths)
        self._sync_index(key, favorite)
        return favorite

    def toggle(self, rel_path: str) -> bool:
        key = self.cache_key(rel_path)
        lock = _lock_for(self.path)
        with lock:
            paths = self._ensure_loaded()
            if key in paths:
                paths.discard(key)
                self._write_file(paths)
                favorite = False
            else:
                paths.add(key)
                self._write_file(paths)
                favorite = True
        self._sync_index(key, favorite)
        return favorite

    def annotate_photos(self, photos: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        favs = self.load()
        for photo in photos:
            if "favorite" in photo and photo.get("favorite") is not None:
                photo["favorite"] = bool(photo["favorite"])
                continue
            rel = self.cache_key(photo.get("rel_path", ""))
            photo["favorite"] = rel in favs
        return photos

    def filter_favorites(self, photos: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        favs = self.load()
        return [p for p in photos if self.cache_key(p.get("rel_path", "")) in favs]
