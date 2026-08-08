"""SQLite photo catalog for fast gallery list/filter/sort."""

from __future__ import annotations

import os
import re
import sqlite3
import threading
import urllib.parse
from typing import Any, Dict, List, Optional, Tuple

from promptstudio.config import (
    ARCHIVE_DB_FILE,
    EXCLUDED_FOLDERS,
    IMAGE_EXTENSIONS,
    VIDEO_EXTENSIONS,
    MEDIA_EXTENSIONS,
    PROMPT_PIPELINE_VERSION,
    REBUILD_INDEX,
    SAVED_DIR,
)
from promptstudio.storage.thumbs import thumb_url

_FILENAME_TS = re.compile(
    r"_(\d{4}-\d{2}-\d{2})_(\d{2}-\d{2}-\d{2})_UTC",
    re.IGNORECASE,
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS photos (
  rel_path TEXT PRIMARY KEY,
  creator TEXT NOT NULL,
  filename TEXT NOT NULL,
  taken_at TEXT,
  mtime REAL,
  favorite INTEGER NOT NULL DEFAULT 0,
  has_prompt INTEGER NOT NULL DEFAULT 0,
  prompt_stale INTEGER NOT NULL DEFAULT 0,
  prompt_search TEXT,
  post_id TEXT,
  shortcode TEXT,
  glam_score INTEGER NOT NULL DEFAULT -1
);
CREATE INDEX IF NOT EXISTS idx_photos_creator ON photos(creator);
CREATE INDEX IF NOT EXISTS idx_photos_taken ON photos(taken_at);
CREATE INDEX IF NOT EXISTS idx_photos_fav ON photos(favorite);
CREATE INDEX IF NOT EXISTS idx_photos_prompt ON photos(has_prompt);
CREATE TABLE IF NOT EXISTS meta (
  key TEXT PRIMARY KEY,
  value TEXT
);
CREATE TABLE IF NOT EXISTS deleted_posts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  creator TEXT NOT NULL,
  shortcode TEXT,
  post_id TEXT,
  rel_path TEXT,
  deleted_at TEXT NOT NULL,
  source TEXT DEFAULT 'ui'
);
CREATE INDEX IF NOT EXISTS idx_deleted_creator ON deleted_posts(creator);
"""

_IDENTITY_COLUMNS = (
    ("post_id", "TEXT"),
    ("shortcode", "TEXT"),
    # glam_score: -1 unscored, 0 modest/no-woman, 1 woman, 2 sexy, 3 sexy+figure
    ("glam_score", "INTEGER NOT NULL DEFAULT -1"),
)

_DELETED_POSTS_SCHEMA = """
CREATE TABLE IF NOT EXISTS deleted_posts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  creator TEXT NOT NULL,
  shortcode TEXT,
  post_id TEXT,
  rel_path TEXT,
  deleted_at TEXT NOT NULL,
  source TEXT DEFAULT 'ui'
);
CREATE INDEX IF NOT EXISTS idx_deleted_creator ON deleted_posts(creator);
"""

def is_media_file(name: str) -> bool:
    return name.lower().endswith(MEDIA_EXTENSIONS)


def normalize_rel_path(rel_path: str) -> str:
    return rel_path.replace("\\", "/").lstrip("/")


def taken_at_for_image(full_path: str, filename: str) -> str:
    """Resolve sortable timestamp: meta → filename UTC → mtime."""
    try:
        from promptstudio.storage.metadata import load_post_metadata

        meta = load_post_metadata(full_path) if full_path else None
        if meta and meta.get("taken_at"):
            return str(meta["taken_at"])
        if meta and meta.get("downloaded_at"):
            return str(meta["downloaded_at"])
    except Exception:
        pass
    m = _FILENAME_TS.search(filename)
    if m:
        return f"{m.group(1)}T{m.group(2).replace('-', ':')}"
    try:
        mtime = os.path.getmtime(full_path) if full_path and os.path.isfile(full_path) else 0
        return f"{mtime:020.3f}"
    except OSError:
        return ""


def prompt_search_blob(entry: Optional[Dict[str, Any]]) -> str:
    if not entry:
        return ""
    return " ".join(
        [
            str(entry.get("positive_prompt") or ""),
            str(entry.get("negative_prompt") or ""),
            str(entry.get("raw_vision_description") or ""),
            " ".join(entry.get("visual_tags") or []),
        ]
    ).lower()


def prompt_flags(
    entry: Optional[Dict[str, Any]], engine_id: str
) -> Tuple[int, int, str]:
    """Match PromptCache.annotate_photos semantics for has_prompt / prompt_stale."""
    if not entry:
        return 0, 0, ""
    params = entry.get("parameters") or {}
    engine_ok = params.get("vision_engine") == engine_id
    pipeline_ok = params.get("pipeline_version") == PROMPT_PIPELINE_VERSION
    has_prompt = 1 if engine_ok else 0
    prompt_stale = 0 if (engine_ok and pipeline_ok) else 1
    return has_prompt, prompt_stale, prompt_search_blob(entry)


class ArchiveIndex:
    """Thread-safe SQLite index over local archive photos."""

    _instance: Optional["ArchiveIndex"] = None
    _instance_lock = threading.Lock()

    def __init__(self, db_path: str = ARCHIVE_DB_FILE, base_dir: str = SAVED_DIR) -> None:
        self.db_path = os.path.expanduser(db_path)
        self.base_dir = os.path.expanduser(base_dir)
        self._lock = threading.RLock()
        os.makedirs(os.path.dirname(self.db_path) or self.base_dir, exist_ok=True)
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.executescript(_SCHEMA)
            self._migrate_identity_columns()
            self._migrate_deleted_posts()
            self._conn.commit()

    def _migrate_identity_columns(self) -> None:
        """Add post_id/shortcode/glam_score to existing DBs."""
        cols = {
            row[1]
            for row in self._conn.execute("PRAGMA table_info(photos)").fetchall()
        }
        for name, col_type in _IDENTITY_COLUMNS:
            if name not in cols:
                self._conn.execute(f"ALTER TABLE photos ADD COLUMN {name} {col_type}")
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_photos_post_id ON photos(post_id)"
        )
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_photos_shortcode ON photos(shortcode)"
        )
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_photos_glam ON photos(glam_score)"
        )

    def _migrate_deleted_posts(self) -> None:
        """Ensure deleted_posts tombstone table exists on older DBs."""
        self._conn.executescript(_DELETED_POSTS_SCHEMA)
        # Partial unique indexes (SQLite) — recreate if missing
        self._conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_deleted_shortcode "
            "ON deleted_posts(creator, shortcode) "
            "WHERE shortcode IS NOT NULL AND shortcode != ''"
        )
        self._conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_deleted_post_id "
            "ON deleted_posts(creator, post_id) "
            "WHERE post_id IS NOT NULL AND post_id != ''"
        )

    @classmethod
    def get(cls) -> "ArchiveIndex":
        with cls._instance_lock:
            if cls._instance is None:
                cls._instance = cls()
            return cls._instance

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    @staticmethod
    def _identity_from_file(full_path: str) -> Tuple[str, str]:
        """Return (post_id, shortcode) from sidecar metadata if present."""
        try:
            from promptstudio.storage.metadata import load_post_metadata

            meta = load_post_metadata(full_path) or {}
            return str(meta.get("post_id") or ""), str(meta.get("shortcode") or "")
        except Exception:
            return "", ""

    @staticmethod
    def _glam_from_file(full_path: str) -> int:
        """Return glam_score from sidecar (-1 if missing)."""
        try:
            from promptstudio.storage.metadata import load_post_metadata

            meta = load_post_metadata(full_path) or {}
            if "glam_score" in meta:
                return int(meta.get("glam_score"))
            glam = meta.get("glam")
            if isinstance(glam, dict) and "score" in glam:
                return int(glam.get("score"))
        except Exception:
            pass
        return -1

    def _file_exists_for_rel(self, rel_path: str) -> bool:
        rel = normalize_rel_path(rel_path)
        full = os.path.join(self.base_dir, *rel.split("/"))
        return os.path.isfile(full)

    def _meta_get(self, key: str) -> Optional[str]:
        row = self._conn.execute(
            "SELECT value FROM meta WHERE key = ?", (key,)
        ).fetchone()
        return row["value"] if row else None

    def _meta_set(self, key: str, value: str) -> None:
        self._conn.execute(
            "INSERT INTO meta(key, value) VALUES(?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )

    def count(self) -> int:
        with self._lock:
            row = self._conn.execute("SELECT COUNT(*) AS c FROM photos").fetchone()
            return int(row["c"])

    def ensure_ready(self, force: bool = False) -> None:
        """Rebuild if forced, empty, or env PROMPTSTUDIO_REBUILD_INDEX."""
        need = force or REBUILD_INDEX or self.count() == 0 or not os.path.isfile(self.db_path)
        if need:
            print("Building archive SQLite index...")
            self.rebuild()
            print(f"Archive index ready ({self.count()} photos)")

    def rebuild(self) -> int:
        """Full filesystem scan into photos table."""
        from promptstudio.prompts.cache import PromptCache
        from promptstudio.prompts.engine import ENGINE_ID
        from promptstudio.storage.favorites import FavoritesStore

        cache = PromptCache().load()
        favs = FavoritesStore().load()
        engine_id = ENGINE_ID

        rows: List[Tuple] = []
        if os.path.isdir(self.base_dir):
            for creator in sorted(os.listdir(self.base_dir)):
                folder = os.path.join(self.base_dir, creator)
                if not os.path.isdir(folder) or creator in EXCLUDED_FOLDERS:
                    continue
                try:
                    names = os.listdir(folder)
                except OSError:
                    continue
                for filename in names:
                    if not is_media_file(filename):
                        continue
                    full = os.path.join(folder, filename)
                    rel = normalize_rel_path(f"{creator}/{filename}")
                    try:
                        mtime = os.path.getmtime(full)
                    except OSError:
                        mtime = 0.0
                    taken = taken_at_for_image(full, filename)
                    entry = cache.get(rel) or cache.get(filename)
                    has_p, stale, blob = prompt_flags(entry, engine_id)
                    fav = 1 if rel in favs else 0
                    post_id, shortcode = self._identity_from_file(full)
                    glam = self._glam_from_file(full)
                    rows.append(
                        (
                            rel,
                            creator,
                            filename,
                            taken,
                            mtime,
                            fav,
                            has_p,
                            stale,
                            blob,
                            post_id or None,
                            shortcode or None,
                            glam,
                        )
                    )

        with self._lock:
            self._conn.execute("DELETE FROM photos")
            self._conn.executemany(
                "INSERT INTO photos("
                "rel_path, creator, filename, taken_at, mtime, "
                "favorite, has_prompt, prompt_stale, prompt_search, "
                "post_id, shortcode, glam_score"
                ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                rows,
            )
            self._meta_set(
                "rebuilt_at",
                str(os.path.getmtime(self.db_path) if os.path.isfile(self.db_path) else ""),
            )
            self._conn.commit()
        return len(rows)

    def upsert_photo(
        self,
        rel_path: str,
        *,
        favorite: Optional[int] = None,
        has_prompt: Optional[int] = None,
        prompt_stale: Optional[int] = None,
        prompt_search: Optional[str] = None,
        taken_at: Optional[str] = None,
        post_id: Optional[str] = None,
        shortcode: Optional[str] = None,
        glam_score: Optional[int] = None,
    ) -> None:
        rel = normalize_rel_path(rel_path)
        creator, _, filename = rel.partition("/")
        if not filename:
            return
        full = os.path.join(self.base_dir, creator, filename)
        if not os.path.isfile(full):
            return
        try:
            mtime = os.path.getmtime(full)
        except OSError:
            mtime = 0.0
        if taken_at is None:
            taken_at = taken_at_for_image(full, filename)
        if post_id is None or shortcode is None:
            meta_pid, meta_sc = self._identity_from_file(full)
            if post_id is None:
                post_id = meta_pid or None
            if shortcode is None:
                shortcode = meta_sc or None
        if glam_score is None:
            side = self._glam_from_file(full)
            if side >= 0:
                glam_score = side

        with self._lock:
            existing = self._conn.execute(
                "SELECT favorite, has_prompt, prompt_stale, prompt_search, "
                "post_id, shortcode, glam_score FROM photos WHERE rel_path = ?",
                (rel,),
            ).fetchone()
            fav = favorite if favorite is not None else (int(existing["favorite"]) if existing else 0)
            hp = has_prompt if has_prompt is not None else (int(existing["has_prompt"]) if existing else 0)
            st = prompt_stale if prompt_stale is not None else (int(existing["prompt_stale"]) if existing else 0)
            blob = prompt_search if prompt_search is not None else (existing["prompt_search"] if existing else "")
            if post_id is None and existing:
                post_id = existing["post_id"]
            if shortcode is None and existing:
                shortcode = existing["shortcode"]
            if glam_score is None:
                if existing and "glam_score" in existing.keys() and existing["glam_score"] is not None:
                    glam_score = int(existing["glam_score"])
                else:
                    glam_score = -1
            self._conn.execute(
                "INSERT INTO photos("
                "rel_path, creator, filename, taken_at, mtime, "
                "favorite, has_prompt, prompt_stale, prompt_search, "
                "post_id, shortcode, glam_score"
                ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(rel_path) DO UPDATE SET "
                "creator=excluded.creator, filename=excluded.filename, "
                "taken_at=excluded.taken_at, mtime=excluded.mtime, "
                "favorite=excluded.favorite, has_prompt=excluded.has_prompt, "
                "prompt_stale=excluded.prompt_stale, prompt_search=excluded.prompt_search, "
                "post_id=COALESCE(excluded.post_id, photos.post_id), "
                "shortcode=COALESCE(excluded.shortcode, photos.shortcode), "
                "glam_score=excluded.glam_score",
                (
                    rel,
                    creator,
                    filename,
                    taken_at,
                    mtime,
                    fav,
                    hp,
                    st,
                    blob or "",
                    post_id or None,
                    shortcode or None,
                    int(glam_score),
                ),
            )
            self._conn.commit()

    def get_glam_score(self, rel_path: str) -> int:
        """Return glam_score for rel_path, or -1 if missing/unscored."""
        rel = normalize_rel_path(rel_path)
        with self._lock:
            row = self._conn.execute(
                "SELECT glam_score FROM photos WHERE rel_path = ?", (rel,)
            ).fetchone()
        if not row:
            return -1
        try:
            return int(row["glam_score"]) if row["glam_score"] is not None else -1
        except (TypeError, ValueError):
            return -1

    def set_glam_score(
        self,
        rel_path: str,
        glam_score: int,
        *,
        has_woman: int = 0,
        sexy: int = 0,
    ) -> None:
        """Update glam_score for an existing row (upsert minimal if missing)."""
        rel = normalize_rel_path(rel_path)
        score = int(glam_score)
        with self._lock:
            cur = self._conn.execute(
                "UPDATE photos SET glam_score = ? WHERE rel_path = ?",
                (score, rel),
            )
            if cur.rowcount == 0:
                # Row not indexed yet — full upsert
                pass
            else:
                self._conn.commit()
                return
        self.upsert_photo(rel, glam_score=score)

    def delete_photo(self, rel_path: str) -> None:
        rel = normalize_rel_path(rel_path)
        with self._lock:
            self._conn.execute("DELETE FROM photos WHERE rel_path = ?", (rel,))
            self._conn.commit()

    def get_photo_identity(self, rel_path: str) -> Tuple[Optional[str], Optional[str], Optional[str]]:
        """Return (creator, post_id, shortcode) from index for a rel_path."""
        rel = normalize_rel_path(rel_path)
        with self._lock:
            row = self._conn.execute(
                "SELECT creator, post_id, shortcode FROM photos WHERE rel_path = ?",
                (rel,),
            ).fetchone()
        if not row:
            return None, None, None
        return (
            str(row["creator"] or "") or None,
            str(row["post_id"] or "") or None,
            str(row["shortcode"] or "") or None,
        )

    @staticmethod
    def _norm_creator(creator: str) -> str:
        return (creator or "").lstrip("@").strip().lower()

    def record_deleted_post(
        self,
        creator: str,
        *,
        shortcode: Optional[str] = None,
        post_id: Optional[str] = None,
        rel_path: Optional[str] = None,
        source: str = "ui",
    ) -> bool:
        """Tombstone an Instagram post so sync never re-downloads it.

        Returns True if a row was written/updated.
        """
        from datetime import datetime, timezone

        creator_key = self._norm_creator(creator)
        sc = (shortcode or "").strip() or None
        pid = str(post_id or "").strip() or None
        if not creator_key or (not sc and not pid):
            return False
        now = datetime.now(timezone.utc).isoformat()
        rel = normalize_rel_path(rel_path) if rel_path else None
        with self._lock:
            # Prefer update existing match by shortcode or post_id
            existing = None
            if sc:
                existing = self._conn.execute(
                    "SELECT id FROM deleted_posts WHERE creator = ? AND shortcode = ?",
                    (creator_key, sc),
                ).fetchone()
            if not existing and pid:
                existing = self._conn.execute(
                    "SELECT id FROM deleted_posts WHERE creator = ? AND post_id = ?",
                    (creator_key, pid),
                ).fetchone()
            if existing:
                self._conn.execute(
                    "UPDATE deleted_posts SET shortcode = COALESCE(?, shortcode), "
                    "post_id = COALESCE(?, post_id), rel_path = COALESCE(?, rel_path), "
                    "deleted_at = ?, source = ? WHERE id = ?",
                    (sc, pid, rel, now, source or "ui", existing["id"]),
                )
            else:
                self._conn.execute(
                    "INSERT INTO deleted_posts(creator, shortcode, post_id, rel_path, deleted_at, source) "
                    "VALUES(?, ?, ?, ?, ?, ?)",
                    (creator_key, sc, pid, rel, now, source or "ui"),
                )
            self._conn.commit()
        return True

    def is_deleted_post(
        self,
        creator: str,
        *,
        shortcode: Optional[str] = None,
        post_id: Optional[str] = None,
    ) -> bool:
        """True if this Instagram identity was intentionally deleted for creator."""
        creator_key = self._norm_creator(creator)
        sc = (shortcode or "").strip()
        pid = str(post_id or "").strip()
        if not creator_key or (not sc and not pid):
            return False
        clauses: List[str] = []
        params: List[Any] = [creator_key]
        if sc:
            clauses.append("shortcode = ?")
            params.append(sc)
        if pid:
            clauses.append("post_id = ?")
            params.append(pid)
        where_id = " OR ".join(clauses)
        with self._lock:
            row = self._conn.execute(
                f"SELECT 1 FROM deleted_posts WHERE creator = ? AND ({where_id}) LIMIT 1",
                params,
            ).fetchone()
        return row is not None

    def clear_deleted_post(
        self,
        creator: str,
        *,
        shortcode: Optional[str] = None,
        post_id: Optional[str] = None,
    ) -> int:
        """Remove tombstone(s). Returns number of rows deleted."""
        creator_key = self._norm_creator(creator)
        sc = (shortcode or "").strip()
        pid = str(post_id or "").strip()
        if not creator_key or (not sc and not pid):
            return 0
        clauses: List[str] = []
        params: List[Any] = [creator_key]
        if sc:
            clauses.append("shortcode = ?")
            params.append(sc)
        if pid:
            clauses.append("post_id = ?")
            params.append(pid)
        where_id = " OR ".join(clauses)
        with self._lock:
            cur = self._conn.execute(
                f"DELETE FROM deleted_posts WHERE creator = ? AND ({where_id})",
                params,
            )
            self._conn.commit()
            return int(cur.rowcount or 0)

    def carousel_paths(
        self,
        *,
        shortcode: Optional[str] = None,
        post_id: Optional[str] = None,
    ) -> List[str]:
        """Return on-disk rel_paths for an Instagram post identity."""
        clauses: List[str] = []
        params: List[Any] = []
        if shortcode:
            clauses.append("shortcode = ?")
            params.append(str(shortcode))
        if post_id:
            clauses.append("post_id = ?")
            params.append(str(post_id))
        if not clauses:
            return []
        where = " OR ".join(clauses)
        with self._lock:
            rows = self._conn.execute(
                f"SELECT rel_path FROM photos WHERE {where}", params
            ).fetchall()
        out: List[str] = []
        for row in rows:
            rel = row["rel_path"]
            if self._file_exists_for_rel(rel):
                out.append(rel)
            else:
                # Stale index row — drop so re-download is allowed
                self.delete_photo(rel)
        return out

    def has_instagram_post(
        self,
        *,
        shortcode: Optional[str] = None,
        post_id: Optional[str] = None,
    ) -> bool:
        """True if at least one image for this Instagram identity exists on disk."""
        return bool(self.carousel_paths(shortcode=shortcode, post_id=post_id))

    def backfill_creator_identity(self, creator: str) -> int:
        """Scan creator folder meta.json into post_id/shortcode columns. Returns rows updated."""
        creator = creator.lstrip("@").strip()
        folder = os.path.join(self.base_dir, creator)
        if not os.path.isdir(folder):
            return 0
        updated = 0
        try:
            names = os.listdir(folder)
        except OSError:
            return 0
        for filename in names:
            if not is_media_file(filename):
                continue
            full = os.path.join(folder, filename)
            post_id, shortcode = self._identity_from_file(full)
            if not post_id and not shortcode:
                continue
            rel = normalize_rel_path(f"{creator}/{filename}")
            with self._lock:
                row = self._conn.execute(
                    "SELECT post_id, shortcode FROM photos WHERE rel_path = ?",
                    (rel,),
                ).fetchone()
            if row and row["post_id"] and row["shortcode"]:
                continue
            self.upsert_photo(rel, post_id=post_id or None, shortcode=shortcode or None)
            updated += 1
        return updated

    def set_favorite(self, rel_path: str, favorite: bool) -> None:
        rel = normalize_rel_path(rel_path)
        with self._lock:
            cur = self._conn.execute(
                "UPDATE photos SET favorite = ? WHERE rel_path = ?",
                (1 if favorite else 0, rel),
            )
            self._conn.commit()
            updated = cur.rowcount > 0
        if not updated and favorite:
            self.upsert_photo(rel, favorite=1)

    def update_prompt_flags(
        self,
        rel_path: str,
        entry: Optional[Dict[str, Any]],
        engine_id: str,
    ) -> None:
        has_p, stale, blob = prompt_flags(entry, engine_id)
        self.upsert_photo(
            rel_path,
            has_prompt=has_p,
            prompt_stale=stale,
            prompt_search=blob,
        )

    def clear_prompt(self, rel_path: str) -> None:
        rel = normalize_rel_path(rel_path)
        with self._lock:
            self._conn.execute(
                "UPDATE photos SET has_prompt = 0, prompt_stale = 0, prompt_search = '' "
                "WHERE rel_path = ?",
                (rel,),
            )
            self._conn.commit()

    def _row_to_photo(self, row: sqlite3.Row) -> Dict[str, Any]:
        rel = row["rel_path"]
        creator = row["creator"]
        filename = row["filename"]
        keys = row.keys()
        glam = -1
        if "glam_score" in keys and row["glam_score"] is not None:
            try:
                glam = int(row["glam_score"])
            except (TypeError, ValueError):
                glam = -1
        return {
            "filename": filename,
            "creator": creator,
            "rel_path": rel,
            "url": f"/media/{creator}/{urllib.parse.quote(filename)}",
            "thumb_url": thumb_url(rel),
            "full_path": os.path.join(self.base_dir, creator, filename),
            "taken_at": row["taken_at"] or "",
            "favorite": bool(row["favorite"]),
            "has_prompt": bool(row["has_prompt"]),
            "prompt_stale": bool(row["prompt_stale"]),
            "post_id": row["post_id"] if "post_id" in keys else None,
            "shortcode": row["shortcode"] if "shortcode" in keys else None,
            "glam_score": glam,
        }

    def list_creators(self) -> List[Dict[str, Any]]:
        from promptstudio.scraping.checkpoints import SyncCheckpoints

        sync = SyncCheckpoints().load()
        with self._lock:
            rows = self._conn.execute(
                "SELECT creator, "
                "COUNT(*) AS photo_count, "
                "SUM(CASE WHEN IFNULL(glam_score, -1) >= 0 THEN 1 ELSE 0 END) AS scored_count, "
                "SUM(CASE WHEN IFNULL(glam_score, -1) < 0 THEN 1 ELSE 0 END) AS unscored_count, "
                "SUM(CASE WHEN glam_score BETWEEN 0 AND 1 THEN 1 ELSE 0 END) AS reject_count, "
                "MIN(filename) AS cover "
                "FROM photos GROUP BY creator ORDER BY photo_count DESC"
            ).fetchall()
        creators = []
        for row in rows:
            name = row["creator"]
            cover = row["cover"]
            entry = sync.get(name.lower()) or sync.get(name) or {}
            photo_count = int(row["photo_count"] or 0)
            scored = int(row["scored_count"] or 0)
            unscored = int(row["unscored_count"] or 0)
            rejects = int(row["reject_count"] or 0)
            creators.append(
                {
                    "name": name,
                    "photo_count": photo_count,
                    "scored_count": scored,
                    "unscored_count": unscored,
                    "reject_count": rejects,
                    "cover_url": f"/media/{name}/{urllib.parse.quote(cover)}",
                    "cover_thumb_url": thumb_url(f"{name}/{cover}"),
                    "last_synced_at": entry.get("updated_at") or None,
                    "synced_count": entry.get("downloaded_count"),
                }
            )
        return creators

    def stats(self) -> Dict[str, int]:
        with self._lock:
            photos = self._conn.execute("SELECT COUNT(*) AS c FROM photos").fetchone()
            creators = self._conn.execute(
                "SELECT COUNT(DISTINCT creator) AS c FROM photos"
            ).fetchone()
        return {
            "total_photos": int(photos["c"]),
            "total_creators": int(creators["c"]),
        }

    def query_photos(
        self,
        *,
        creator: Optional[str] = None,
        search: Optional[str] = None,
        unanalyzed: bool = False,
        favorite_only: bool = False,
        media_type: Optional[str] = None,
        glam_min: Optional[int] = None,
        glam_max: Optional[int] = None,
        unscored_only: bool = False,
        reject_only: bool = False,
        sort: str = "name",
        limit: Optional[int] = None,
        offset: int = 0,
    ) -> Tuple[List[Dict[str, Any]], int]:
        where: List[str] = []
        params: List[Any] = []

        if creator:
            where.append("creator = ?")
            params.append(creator)
        if favorite_only:
            where.append("favorite = 1")
        if unanalyzed:
            where.append("has_prompt = 0")
        # reject_only: scored non-keep ≈ glam 0–1 (excludes unscored -1)
        if reject_only:
            where.append("glam_score BETWEEN 0 AND 1")
        if unscored_only:
            where.append("IFNULL(glam_score, -1) = -1")
        if glam_min is not None:
            where.append("IFNULL(glam_score, -1) >= ?")
            params.append(int(glam_min))
        if glam_max is not None:
            where.append("IFNULL(glam_score, -1) <= ?")
            params.append(int(glam_max))
        if media_type == "video":
            video_likes = " OR ".join("LOWER(filename) LIKE ?" for _ in VIDEO_EXTENSIONS)
            where.append(f"({video_likes})")
            params.extend(f"%{ext}" for ext in VIDEO_EXTENSIONS)
        elif media_type == "photo":
            photo_likes = " OR ".join("LOWER(filename) LIKE ?" for _ in IMAGE_EXTENSIONS)
            where.append(f"({photo_likes})")
            params.extend(f"%{ext}" for ext in IMAGE_EXTENSIONS)
        if search:
            q = search.lower().strip()
            where.append(
                "(LOWER(creator) LIKE ? OR LOWER(filename) LIKE ? OR IFNULL(prompt_search, '') LIKE ?)"
            )
            like = f"%{q}%"
            params.extend([like, like, like])

        where_sql = (" WHERE " + " AND ".join(where)) if where else ""
        sort = (sort or "name").lower()
        if sort == "newest":
            order = "ORDER BY taken_at DESC, filename DESC"
        elif sort == "oldest":
            order = "ORDER BY taken_at ASC, filename ASC"
        elif sort == "glam":
            order = "ORDER BY IFNULL(glam_score, -1) DESC, taken_at DESC, filename DESC"
        else:
            order = "ORDER BY creator ASC, filename ASC"

        with self._lock:
            total = self._conn.execute(
                f"SELECT COUNT(*) AS c FROM photos{where_sql}", params
            ).fetchone()["c"]
            sql = f"SELECT * FROM photos{where_sql} {order}"
            page_params = list(params)
            if limit is not None:
                sql += " LIMIT ? OFFSET ?"
                page_params.extend([int(limit), max(0, int(offset))])
            rows = self._conn.execute(sql, page_params).fetchall()

        return [self._row_to_photo(r) for r in rows], int(total)
