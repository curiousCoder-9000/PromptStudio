"""Prompt cache with in-memory write-through to prompts_cache.json."""

import json
import os
import threading
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from promptstudio.config import PROMPT_CACHE_FILE, PROMPT_HISTORY_MAX, PROMPT_PIPELINE_VERSION

# Shared across PromptCache instances for the same file path
_MEM: Dict[str, Optional[dict]] = {}
_MEM_LOCKS: Dict[str, threading.RLock] = {}
_REG_LOCK = threading.Lock()


def _lock_for(path: str) -> threading.RLock:
    with _REG_LOCK:
        if path not in _MEM_LOCKS:
            _MEM_LOCKS[path] = threading.RLock()
            _MEM.setdefault(path, None)
        return _MEM_LOCKS[path]


class PromptCache:
    def __init__(self, cache_file: str = PROMPT_CACHE_FILE) -> None:
        self.cache_file = cache_file

    def _ensure_loaded(self) -> dict:
        lock = _lock_for(self.cache_file)
        with lock:
            data = _MEM.get(self.cache_file)
            if data is None:
                data = self._read_file()
                _MEM[self.cache_file] = data
            return data

    def _read_file(self) -> dict:
        if os.path.exists(self.cache_file):
            try:
                with open(self.cache_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                return data if isinstance(data, dict) else {}
            except Exception:
                pass
        return {}

    def load(self) -> dict:
        return self._ensure_loaded()

    def save(self, cache: dict) -> None:
        lock = _lock_for(self.cache_file)
        with lock:
            _MEM[self.cache_file] = cache
            self._write_file(cache)

    def _write_file(self, cache: dict) -> None:
        try:
            os.makedirs(os.path.dirname(self.cache_file), exist_ok=True)
            with open(self.cache_file, "w", encoding="utf-8") as f:
                json.dump(cache, f, indent=2, ensure_ascii=False)
        except OSError as e:
            print(f"Error saving prompt cache: {e}")

    def invalidate_memory(self) -> None:
        lock = _lock_for(self.cache_file)
        with lock:
            _MEM[self.cache_file] = None

    def cache_key(self, rel_path: str) -> str:
        return rel_path.replace("\\", "/")

    def get(self, rel_path: str, filename: str) -> Optional[Dict[str, Any]]:
        lock = _lock_for(self.cache_file)
        with lock:
            cache = self._ensure_loaded()
            return self._lookup(cache, rel_path, filename)

    def _lookup(self, cache: dict, rel_path: str, filename: str) -> Optional[Dict[str, Any]]:
        key = self.cache_key(rel_path)
        if key in cache:
            return cache[key]
        return cache.get(filename)

    def _snapshot(self, entry: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "positive_prompt": entry.get("positive_prompt", ""),
            "negative_prompt": entry.get("negative_prompt", ""),
            "visual_tags": list(entry.get("visual_tags") or []),
            "parameters": dict(entry.get("parameters") or {}),
            "exports": dict(entry.get("exports") or {}),
            "saved_at": datetime.now(timezone.utc).isoformat(),
        }

    def _sync_index(self, rel_path: str, entry: Optional[Dict[str, Any]]) -> None:
        try:
            from promptstudio.prompts.engine import ENGINE_ID
            from promptstudio.storage.db import ArchiveIndex

            ArchiveIndex.get().update_prompt_flags(rel_path, entry, ENGINE_ID)
        except Exception as e:
            print(f"Warning: prompt index sync failed for {rel_path}: {e}")

    def set(self, rel_path: str, data: Dict[str, Any], *, push_history: bool = True) -> None:
        lock = _lock_for(self.cache_file)
        with lock:
            cache = self._ensure_loaded()
            key = self.cache_key(rel_path)
            old = cache.get(key)
            payload = dict(data)
            if push_history and isinstance(old, dict) and old.get("positive_prompt"):
                if old.get("positive_prompt") != payload.get("positive_prompt") or old.get(
                    "negative_prompt"
                ) != payload.get("negative_prompt"):
                    hist = list(old.get("history") or [])
                    hist.insert(0, self._snapshot(old))
                    payload["history"] = hist[:PROMPT_HISTORY_MAX]
                elif "history" not in payload and old.get("history"):
                    payload["history"] = old["history"]
            elif isinstance(old, dict) and old.get("history") and "history" not in payload:
                payload["history"] = old["history"]

            cache[key] = payload
            filename = os.path.basename(rel_path)
            if filename in cache and filename != key:
                del cache[filename]
            self._write_file(cache)
        self._sync_index(rel_path, payload)

    def restore_history(self, rel_path: str, index: int = 0) -> Optional[Dict[str, Any]]:
        filename = os.path.basename(rel_path)
        entry = self.get(rel_path, filename)
        if not entry:
            return None
        hist = entry.get("history") or []
        if index < 0 or index >= len(hist):
            return None
        snap = hist[index]
        restored = dict(entry)
        restored["positive_prompt"] = snap.get("positive_prompt", "")
        restored["negative_prompt"] = snap.get("negative_prompt", "")
        restored["visual_tags"] = list(snap.get("visual_tags") or [])
        if snap.get("parameters"):
            restored["parameters"] = dict(snap["parameters"])
        if snap.get("exports"):
            restored["exports"] = dict(snap["exports"])
        else:
            from promptstudio.prompts.engine import build_export_variants

            restored["exports"] = build_export_variants(
                restored["positive_prompt"], restored["negative_prompt"]
            )
        new_hist = [self._snapshot(entry)] + [
            h for i, h in enumerate(hist) if i != index
        ]
        restored["history"] = new_hist[:PROMPT_HISTORY_MAX]
        self.set(rel_path, restored, push_history=False)
        return restored

    def delete(self, rel_path: str, filename: str) -> None:
        lock = _lock_for(self.cache_file)
        with lock:
            cache = self._ensure_loaded()
            key = self.cache_key(rel_path)
            cache.pop(key, None)
            cache.pop(filename, None)
            self._write_file(cache)
        try:
            from promptstudio.storage.db import ArchiveIndex

            ArchiveIndex.get().clear_prompt(rel_path)
        except Exception as e:
            print(f"Warning: prompt index clear failed for {rel_path}: {e}")

    def count_ready(self) -> int:
        return len(self.load())

    def _engine_id(self) -> str:
        from promptstudio.prompts.engine import ENGINE_ID

        return ENGINE_ID

    def has_prompt(self, rel_path: str, filename: str, cache: Optional[dict] = None) -> bool:
        data = cache if cache is not None else self.load()
        entry = self._lookup(data, rel_path, filename)
        if not entry:
            return False
        return entry.get("parameters", {}).get("vision_engine") == self._engine_id()

    def is_stale(self, rel_path: str, filename: str, cache: Optional[dict] = None) -> bool:
        data = cache if cache is not None else self.load()
        entry = self._lookup(data, rel_path, filename)
        if not entry:
            return False
        params = entry.get("parameters") or {}
        engine_ok = params.get("vision_engine") == self._engine_id()
        pipeline_ok = params.get("pipeline_version") == PROMPT_PIPELINE_VERSION
        return not engine_ok or not pipeline_ok

    def annotate_photos(self, photos: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        cache = self.load()
        engine_id = self._engine_id()
        for photo in photos:
            if "has_prompt" in photo and "prompt_stale" in photo:
                continue
            rel = photo.get("rel_path", "")
            filename = photo.get("filename", "")
            entry = self._lookup(cache, rel, filename)
            if not entry:
                photo["has_prompt"] = False
                photo["prompt_stale"] = False
                continue
            params = entry.get("parameters") or {}
            engine_ok = params.get("vision_engine") == engine_id
            pipeline_ok = params.get("pipeline_version") == PROMPT_PIPELINE_VERSION
            photo["has_prompt"] = bool(engine_ok)
            photo["prompt_stale"] = not engine_ok or not pipeline_ok
        return photos

    def filter_unanalyzed(self, photos: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        cache = self.load()
        engine_id = self._engine_id()
        pending = []
        for photo in photos:
            if "has_prompt" in photo:
                if not photo["has_prompt"]:
                    pending.append(photo)
                continue
            rel = photo.get("rel_path", "")
            filename = photo.get("filename", "")
            entry = self._lookup(cache, rel, filename)
            if not entry or entry.get("parameters", {}).get("vision_engine") != engine_id:
                pending.append(photo)
        return pending

    def search_photos(self, photos: list, query: str) -> list:
        if not query:
            return photos
        cache = self.load()
        q = query.lower()
        matched = []
        for photo in photos:
            rel = photo.get("rel_path", "")
            filename = photo.get("filename", "")
            entry = cache.get(rel) or cache.get(filename)
            if q in photo.get("creator", "").lower():
                matched.append(photo)
                continue
            if q in filename.lower():
                matched.append(photo)
                continue
            if entry:
                blob = " ".join(
                    [
                        entry.get("positive_prompt", ""),
                        entry.get("negative_prompt", ""),
                        entry.get("raw_vision_description", "") or "",
                        " ".join(entry.get("visual_tags", [])),
                    ]
                ).lower()
                if q in blob:
                    matched.append(photo)
        return matched


_default = PromptCache()


def get_or_create_prompt_cache() -> dict:
    return _default.load()


def save_prompt_cache(cache: dict) -> None:
    _default.save(cache)
