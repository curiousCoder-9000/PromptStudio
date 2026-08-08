"""Per-creator style summaries learned from cached prompts."""

import json
import os
import re
from collections import Counter
from typing import Any, Dict, List, Optional

from promptstudio.config import CREATOR_STYLE_MIN_PROMPTS, CREATOR_STYLES_FILE
from promptstudio.logging_setup import get_logger
from promptstudio.prompts.cache import PromptCache
from promptstudio.storage.atomic import atomic_write_json

log = get_logger(__name__)


def _tokenize(text: str) -> List[str]:
    return re.findall(r"[a-zA-Z][a-zA-Z\-]{2,}", (text or "").lower())


class CreatorStyleStore:
    def __init__(self, path: str = CREATOR_STYLES_FILE) -> None:
        self.path = path
        self.cache = PromptCache()

    def load(self) -> Dict[str, Any]:
        if not os.path.isfile(self.path):
            return {}
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    def save(self, data: Dict[str, Any]) -> None:
        try:
            atomic_write_json(self.path, data)
        except OSError as e:
            log.error("saving creator styles %s: %s", self.path, e)

    def get_style_prefix(self, creator: str) -> str:
        entry = self.load().get(creator.lstrip("@"))
        if not entry:
            return ""
        return entry.get("style_prefix") or ""

    def rebuild_for_creator(self, creator: str) -> Optional[Dict[str, Any]]:
        """Aggregate common descriptors from cached prompts for one creator."""
        cache = self.cache.load()
        blobs: List[str] = []
        for key, entry in cache.items():
            if not isinstance(entry, dict):
                continue
            rel = key.replace("\\", "/")
            if "/" in rel and rel.split("/", 1)[0] != creator:
                continue
            if "/" not in rel:
                continue
            text = " ".join(
                [
                    entry.get("raw_vision_description") or "",
                    entry.get("positive_prompt") or "",
                    " ".join(entry.get("visual_tags") or []),
                ]
            )
            if text.strip():
                blobs.append(text)

        if len(blobs) < CREATOR_STYLE_MIN_PROMPTS:
            return None

        stop = {
            "with", "from", "that", "this", "her", "she", "the", "and", "for",
            "very", "into", "over", "under", "while", "looking", "standing",
            "masterpiece", "quality", "photorealistic", "detailed", "ultra",
            "sharp", "focus", "natural", "realistic", "anatomy", "prompt",
        }
        counts: Counter = Counter()
        for blob in blobs:
            for tok in _tokenize(blob):
                if tok in stop or len(tok) < 4:
                    continue
                counts[tok] += 1

        top = [w for w, _ in counts.most_common(12)]
        style_prefix = ", ".join(top[:8])
        entry = {
            "creator": creator,
            "sample_count": len(blobs),
            "top_terms": top,
            "style_prefix": f"{creator.replace('_', ' ')} look, {style_prefix}" if style_prefix else "",
        }
        data = self.load()
        data[creator] = entry
        self.save(data)
        return entry

    def maybe_update(self, creator: str) -> None:
        if not creator:
            return
        try:
            self.rebuild_for_creator(creator)
        except Exception as e:
            log.warning("creator style update failed for @%s: %s", creator, e)
