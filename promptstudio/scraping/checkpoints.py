"""Per-creator resume checkpoints for Instagram feed sync."""

import json
import os
import threading
from datetime import datetime, timezone
from typing import Any, Dict

from promptstudio.config import SYNC_STATE_FILE
from promptstudio.logging_setup import get_logger
from promptstudio.storage.atomic import atomic_write_json

log = get_logger(__name__)

# `update()` is a read-modify-write of the WHOLE file, so two threads doing it
# concurrently lose one of the two updates entirely — not one field, the whole
# creator entry. That was unreachable while exactly one scrape thread existed;
# per-source lanes (docs/design_scrape_lanes.md) and gallery-dl sources writing
# checkpoints both make it reachable. Module-level, because the guarded resource
# is the file, and callers construct SyncCheckpoints() freely rather than
# sharing one instance.
_UPDATE_LOCK = threading.Lock()


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
            atomic_write_json(self.path, state)
        except OSError as e:
            log.error("saving sync checkpoints %s: %s", self.path, e)

    def get(self, username: str) -> Dict[str, Any]:
        return dict(self.load().get(username.lstrip("@").lower(), {}))

    def update(
        self,
        username: str,
        *,
        shortcode: str = "",
        post_id: str = "",
        downloaded_delta: int = 1,
    ) -> None:
        key = username.lstrip("@").lower()
        with _UPDATE_LOCK:
            state = self.load()
            prev = state.get(key, {})
            state[key] = {
                "last_shortcode": shortcode or prev.get("last_shortcode"),
                "last_post_id": str(post_id) if post_id else prev.get("last_post_id"),
                "downloaded_count": (
                    int(prev.get("downloaded_count") or 0) + downloaded_delta
                ),
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
            self.save(state)
