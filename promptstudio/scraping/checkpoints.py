"""Per-creator resume checkpoints for Instagram feed sync."""

import json
import os
from datetime import datetime, timezone
from typing import Any, Dict

from promptstudio.config import SYNC_STATE_FILE


class SyncCheckpoints:
    """Persist last-downloaded post per creator in sync_state.json."""

    def __init__(self, path: str = SYNC_STATE_FILE) -> None:
        self.path = path

    def load(self) -> Dict[str, Any]:
        if not os.path.isfile(self.path):
            return {}
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    def save(self, state: Dict[str, Any]) -> None:
        try:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump(state, f, indent=2, ensure_ascii=False)
        except OSError as e:
            print(f"Error saving sync checkpoints: {e}")

    def get(self, username: str) -> Dict[str, Any]:
        return dict(self.load().get(username.lstrip("@").lower(), {}))

    def should_skip_post(self, username: str, shortcode: str, post_id: str) -> bool:
        """Skip if this post was already recorded as last downloaded (resume)."""
        entry = self.get(username)
        if not entry:
            return False
        if shortcode and entry.get("last_shortcode") == shortcode:
            return True
        if post_id and str(entry.get("last_post_id")) == str(post_id):
            return True
        return False

    def update(
        self,
        username: str,
        *,
        shortcode: str = "",
        post_id: str = "",
        downloaded_delta: int = 1,
    ) -> None:
        key = username.lstrip("@").lower()
        state = self.load()
        prev = state.get(key, {})
        state[key] = {
            "last_shortcode": shortcode or prev.get("last_shortcode"),
            "last_post_id": str(post_id) if post_id else prev.get("last_post_id"),
            "downloaded_count": int(prev.get("downloaded_count") or 0) + downloaded_delta,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        self.save(state)
