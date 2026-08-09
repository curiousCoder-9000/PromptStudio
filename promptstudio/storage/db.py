"""SQLite photo catalog for fast gallery list/filter/sort."""

from __future__ import annotations

import json
import os
import re
import sqlite3
import threading
import urllib.parse
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple

from promptstudio.config import (
    ARCHIVE_DB_FILE,
    EXCLUDED_FOLDERS,
    FTS_SEARCH,
    IMAGE_EXTENSIONS,
    MEDIA_EXTENSIONS,
    PROMPT_PIPELINE_VERSION,
    REBUILD_INDEX,
    SAVED_DIR,
    VIDEO_EXTENSIONS,
)
from promptstudio.logging_setup import get_logger
from promptstudio.storage.thumbs import thumb_url

log = get_logger(__name__)

_FILENAME_TS = re.compile(
    r"_(\d{4}-\d{2}-\d{2})_(\d{2}-\d{2}-\d{2})_UTC",
    re.IGNORECASE,
)

# NOTE on the two differently-named platform columns:
#   photos.source        — which platform the media came from ("instagram", "x",
#                          "reddit"). Matches the sidecar's "source" key.
#   deleted_posts.platform — same meaning, but this table already has a `source`
#                          column meaning *who performed the delete* ("ui"),
#                          so the platform discriminator needs its own name.
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
  source TEXT NOT NULL DEFAULT 'instagram'
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
  source TEXT DEFAULT 'ui',
  platform TEXT NOT NULL DEFAULT 'instagram'
);
CREATE INDEX IF NOT EXISTS idx_deleted_creator ON deleted_posts(creator);
CREATE TABLE IF NOT EXISTS prompts (
  rel_path TEXT PRIMARY KEY,
  filename TEXT,
  payload TEXT NOT NULL,
  vision_engine TEXT,
  pipeline_version TEXT,
  updated_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_prompts_filename ON prompts(filename);
CREATE INDEX IF NOT EXISTS idx_prompts_engine ON prompts(vision_engine);
"""

# Perceptual hashes live in their own table rather than a column on `photos`:
# they are computed by a separate offline pass, are absent for most rows most of
# the time, and would otherwise widen the row that every gallery query reads.
# TEXT because a 64-bit hash does not fit SQLite's signed INTEGER.
_PHASH_SCHEMA = """
CREATE TABLE IF NOT EXISTS phashes (
  rel_path TEXT PRIMARY KEY,
  phash TEXT NOT NULL,
  computed_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_phashes_hash ON phashes(phash);
"""

# Standalone rather than an external-content FTS table: the content table is
# tiny, and standalone avoids rowid-sync triggers that silently rot if a write
# path forgets them.
_FTS_SCHEMA = """
CREATE VIRTUAL TABLE IF NOT EXISTS prompts_fts USING fts5(
  rel_path UNINDEXED,
  blob
);
"""

_IDENTITY_COLUMNS = (
    ("post_id", "TEXT"),
    ("shortcode", "TEXT"),
    # Back-fills every pre-existing row as Instagram, which is what they are.
    ("source", "TEXT NOT NULL DEFAULT 'instagram'"),
)

DEFAULT_SOURCE = "instagram"

_PROMPTS_IMPORTED_KEY = "prompts_imported_from_json"
_FTS_TOKEN = re.compile(r"[A-Za-z0-9_]+")


def _fts_query(raw: str) -> str:
    """Build an FTS5 MATCH expression from free text.

    Every token becomes a quoted prefix term, so "red bik" finds "red bikini".
    Quoting is what makes this injection-proof: FTS5 operators (NEAR, OR, ^, -)
    inside a quoted string are literal, and non-alphanumerics never survive
    tokenization. Returns "" when there is nothing searchable.
    """
    tokens = _FTS_TOKEN.findall(raw.lower())
    if not tokens:
        return ""
    return " AND ".join(f'"{t}"*' for t in tokens)


def is_media_file(name: str) -> bool:
    return name.lower().endswith(MEDIA_EXTENSIONS)


def normalize_rel_path(rel_path: str) -> str:
    return rel_path.replace("\\", "/").lstrip("/")


def read_sidecar(full_path: str, meta: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Sidecar metadata for a media file, or {}.

    Pass an already-loaded `meta` to reuse it. Indexing one file needs four
    different fields out of the sidecar, and each used to load and parse it
    independently — 4x the file opens for one photo.
    """
    if meta is not None:
        return meta
    if not full_path:
        return {}
    try:
        from promptstudio.storage.metadata import load_post_metadata

        return load_post_metadata(full_path) or {}
    except Exception:
        return {}


def taken_at_for_image(
    full_path: str,
    filename: str,
    meta: Optional[Dict[str, Any]] = None,
) -> str:
    """Resolve sortable timestamp: meta → filename UTC → mtime."""
    try:
        side = read_sidecar(full_path, meta)
        if side.get("taken_at"):
            return str(side["taken_at"])
        if side.get("downloaded_at"):
            return str(side["downloaded_at"])
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
        self._apply_pragmas()
        self.fts_enabled = False
        with self._lock:
            self._conn.executescript(_SCHEMA)
            self._conn.executescript(_PHASH_SCHEMA)
            self._init_fts()
            self._migrate_identity_columns()
            self._migrate_deleted_posts()
            self._conn.commit()

    def _apply_pragmas(self) -> None:
        """Connection tuning. Best-effort — an old SQLite must not stop startup.

        WAL matters most: under the default rollback journal a long write (a
        rebuild, a large batch update) blocks readers, which here means the whole
        gallery API stalls behind it.
        """
        for pragma in (
            "PRAGMA journal_mode=WAL",
            "PRAGMA busy_timeout=5000",
            # WAL already survives process crash; FULL only buys power-loss
            # durability for an index that can be rebuilt from disk.
            "PRAGMA synchronous=NORMAL",
        ):
            try:
                self._conn.execute(pragma)
            except sqlite3.DatabaseError as e:
                log.debug("pragma failed (%s): %s", pragma, e)

    def _init_fts(self) -> None:
        """Create the FTS5 index if this SQLite build has FTS5.

        Caller MUST hold self._lock. Absence is not fatal — search falls back to
        the old LIKE scan, just slower.
        """
        try:
            self._conn.executescript(_FTS_SCHEMA)
            self.fts_enabled = True
        except sqlite3.DatabaseError as e:
            self.fts_enabled = False
            log.warning("FTS5 unavailable, prompt search falls back to LIKE: %s", e)

    # ── prompt storage ───────────────────────────────────────────────

    @staticmethod
    def _prompt_row(rel_path: str, entry: Dict[str, Any]) -> Tuple:
        params = entry.get("parameters") or {}
        return (
            rel_path,
            os.path.basename(rel_path),
            json.dumps(entry, ensure_ascii=False),
            params.get("vision_engine"),
            params.get("pipeline_version"),
            datetime.now(timezone.utc).isoformat(),
        )

    def _reindex_prompt(self, rel_path: str, entry: Optional[Dict[str, Any]]) -> None:
        """Caller MUST hold self._lock."""
        if not self.fts_enabled:
            return
        self._conn.execute("DELETE FROM prompts_fts WHERE rel_path = ?", (rel_path,))
        if entry is None:
            return
        blob = prompt_search_blob(entry)
        if blob:
            self._conn.execute(
                "INSERT INTO prompts_fts(rel_path, blob) VALUES (?, ?)", (rel_path, blob)
            )

    def prompt_set(self, rel_path: str, entry: Dict[str, Any]) -> None:
        """Upsert one prompt. O(1) — the JSON file rewrote all ~4400 per save."""
        rel_path = normalize_rel_path(rel_path)
        with self._lock:
            self._conn.execute(
                "INSERT INTO prompts(rel_path, filename, payload, vision_engine, "
                "pipeline_version, updated_at) VALUES (?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(rel_path) DO UPDATE SET "
                "filename=excluded.filename, payload=excluded.payload, "
                "vision_engine=excluded.vision_engine, "
                "pipeline_version=excluded.pipeline_version, "
                "updated_at=excluded.updated_at",
                self._prompt_row(rel_path, entry),
            )
            self._reindex_prompt(rel_path, entry)
            self._conn.commit()

    def prompt_get(self, rel_path: str, filename: str = "") -> Optional[Dict[str, Any]]:
        rel_path = normalize_rel_path(rel_path)
        with self._lock:
            row = self._conn.execute(
                "SELECT payload FROM prompts WHERE rel_path = ?", (rel_path,)
            ).fetchone()
            if row is None and filename:
                # Legacy JSON keyed some entries by bare filename. Only trust it
                # when exactly one creator owns that name — otherwise two
                # creators with photo_1.jpg would read each other's prompt.
                rows = self._conn.execute(
                    "SELECT payload FROM prompts WHERE filename = ? LIMIT 2", (filename,)
                ).fetchall()
                row = rows[0] if len(rows) == 1 else None
        if row is None:
            return None
        try:
            data = json.loads(row["payload"])
        except (json.JSONDecodeError, TypeError):
            return None
        return data if isinstance(data, dict) else None

    def prompt_delete(self, rel_path: str, filename: str = "") -> None:
        rel_path = normalize_rel_path(rel_path)
        with self._lock:
            self._conn.execute("DELETE FROM prompts WHERE rel_path = ?", (rel_path,))
            if filename:
                self._conn.execute("DELETE FROM prompts WHERE filename = ?", (filename,))
            self._reindex_prompt(rel_path, None)
            self._conn.commit()

    def prompt_count(self) -> int:
        with self._lock:
            return int(
                self._conn.execute("SELECT COUNT(*) AS c FROM prompts").fetchone()["c"]
            )

    def prompt_all(self) -> Dict[str, Any]:
        """Whole cache as a dict, for callers that still think in whole-cache terms."""
        out: Dict[str, Any] = {}
        with self._lock:
            rows = self._conn.execute("SELECT rel_path, payload FROM prompts").fetchall()
        for row in rows:
            try:
                data = json.loads(row["payload"])
            except (json.JSONDecodeError, TypeError):
                continue
            if isinstance(data, dict):
                out[row["rel_path"]] = data
        return out

    def prompt_replace_all(self, cache: Dict[str, Any]) -> int:
        """Bulk replace. Used by the JSON import and the legacy save() path."""
        rows = [
            self._prompt_row(normalize_rel_path(k), v)
            for k, v in (cache or {}).items()
            if isinstance(v, dict)
        ]
        with self._lock:
            self._conn.execute("DELETE FROM prompts")
            if self.fts_enabled:
                self._conn.execute("DELETE FROM prompts_fts")
            if rows:
                self._conn.executemany(
                    "INSERT OR REPLACE INTO prompts(rel_path, filename, payload, "
                    "vision_engine, pipeline_version, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    rows,
                )
                if self.fts_enabled:
                    fts_rows = [
                        (r[0], prompt_search_blob(json.loads(r[2])))
                        for r in rows
                    ]
                    self._conn.executemany(
                        "INSERT INTO prompts_fts(rel_path, blob) VALUES (?, ?)",
                        [(p, b) for p, b in fts_rows if b],
                    )
            self._conn.commit()
        return len(rows)

    # ── perceptual hashes ────────────────────────────────────────────

    def set_phash(self, rel_path: str, value: int) -> None:
        from promptstudio.storage.dedupe import phash_hex

        rel = normalize_rel_path(rel_path)
        with self._lock:
            self._conn.execute(
                "INSERT INTO phashes(rel_path, phash, computed_at) VALUES (?, ?, ?) "
                "ON CONFLICT(rel_path) DO UPDATE SET "
                "phash=excluded.phash, computed_at=excluded.computed_at",
                (rel, phash_hex(value), datetime.now(timezone.utc).isoformat()),
            )
            self._conn.commit()

    def set_phashes(self, items: Sequence[Tuple[str, int]]) -> int:
        """Bulk upsert — one transaction for a whole hashing pass."""
        from promptstudio.storage.dedupe import phash_hex

        now = datetime.now(timezone.utc).isoformat()
        rows = [(normalize_rel_path(rel), phash_hex(value), now) for rel, value in items]
        if not rows:
            return 0
        with self._lock:
            self._conn.executemany(
                "INSERT INTO phashes(rel_path, phash, computed_at) VALUES (?, ?, ?) "
                "ON CONFLICT(rel_path) DO UPDATE SET "
                "phash=excluded.phash, computed_at=excluded.computed_at",
                rows,
            )
            self._conn.commit()
        return len(rows)

    def get_phash(self, rel_path: str) -> Optional[int]:
        from promptstudio.storage.dedupe import phash_from_hex

        with self._lock:
            row = self._conn.execute(
                "SELECT phash FROM phashes WHERE rel_path = ?",
                (normalize_rel_path(rel_path),),
            ).fetchone()
        return phash_from_hex(row["phash"]) if row else None

    def all_phashes(self) -> Dict[str, int]:
        from promptstudio.storage.dedupe import phash_from_hex

        with self._lock:
            rows = self._conn.execute("SELECT rel_path, phash FROM phashes").fetchall()
        out: Dict[str, int] = {}
        for row in rows:
            value = phash_from_hex(row["phash"])
            if value is not None:
                out[row["rel_path"]] = value
        return out

    def paths_missing_phash(self) -> List[str]:
        """Indexed media with no hash yet, so a pass can resume."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT p.rel_path FROM photos p "
                "LEFT JOIN phashes h ON h.rel_path = p.rel_path "
                "WHERE h.rel_path IS NULL ORDER BY p.rel_path"
            ).fetchall()
        return [row["rel_path"] for row in rows]

    def delete_phash(self, rel_path: str) -> None:
        with self._lock:
            self._conn.execute(
                "DELETE FROM phashes WHERE rel_path = ?",
                (normalize_rel_path(rel_path),),
            )
            self._conn.commit()

    def prompt_import_done(self) -> bool:
        return self._meta_get(_PROMPTS_IMPORTED_KEY) == "1"

    def mark_prompt_import_done(self) -> None:
        self._meta_set(_PROMPTS_IMPORTED_KEY, "1")

    def _migrate_identity_columns(self) -> None:
        """Add post_id/shortcode/source to existing DBs."""
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
            "CREATE INDEX IF NOT EXISTS idx_photos_source ON photos(source)"
        )
        # Drives "re-score everything the old prompt judged" without a rescan.

    def _migrate_deleted_posts(self) -> None:
        """Add deleted_posts.platform and scope the unique indexes by it.

        Without `platform` in the key, a Reddit submission id or X tweet id can
        collide with an Instagram mediaid/shortcode and a post gets silently
        skipped as "deleted" (or a deleted post comes back). The table is
        created by _SCHEMA; this only handles pre-existing DBs.
        """
        cols = {
            row[1]
            for row in self._conn.execute("PRAGMA table_info(deleted_posts)").fetchall()
        }
        if "platform" not in cols:
            self._conn.execute(
                "ALTER TABLE deleted_posts "
                f"ADD COLUMN platform TEXT NOT NULL DEFAULT '{DEFAULT_SOURCE}'"
            )
        # The old indexes are (creator, shortcode) / (creator, post_id). Adding
        # platform makes them strictly less restrictive, so any data that
        # satisfied the old constraint satisfies the new one — safe to recreate.
        self._conn.execute("DROP INDEX IF EXISTS idx_deleted_shortcode")
        self._conn.execute("DROP INDEX IF EXISTS idx_deleted_post_id")
        self._conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_deleted_shortcode "
            "ON deleted_posts(platform, creator, shortcode) "
            "WHERE shortcode IS NOT NULL AND shortcode != ''"
        )
        self._conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_deleted_post_id "
            "ON deleted_posts(platform, creator, post_id) "
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
    def _identity_from_file(
        full_path: str,
        meta: Optional[Dict[str, Any]] = None,
    ) -> Tuple[str, str]:
        """Return (post_id, shortcode) from sidecar metadata if present."""
        try:
            side = read_sidecar(full_path, meta)
            return str(side.get("post_id") or ""), str(side.get("shortcode") or "")
        except Exception:
            return "", ""

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
            log.info("building archive SQLite index...")
            self.rebuild()
            log.info("archive index ready (%d photos)", self.count())

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
                    # One read, four consumers. Each of these used to load and
                    # parse the sidecar itself: 4 opens per photo, 18k opens
                    # across a 4.5k-file archive, every time the index is built.
                    side = read_sidecar(full)
                    taken = taken_at_for_image(full, filename, side)
                    entry = cache.get(rel) or cache.get(filename)
                    has_p, stale, blob = prompt_flags(entry, engine_id)
                    fav = 1 if rel in favs else 0
                    post_id, shortcode = self._identity_from_file(full, side)
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
                            self._source_from_file(full, side),
                        )
                    )

        with self._lock:
            self._conn.execute("DELETE FROM photos")
            self._conn.executemany(
                "INSERT INTO photos("
                "rel_path, creator, filename, taken_at, mtime, "
                "favorite, has_prompt, prompt_stale, prompt_search, "
                "post_id, shortcode, source"
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
        source: Optional[str] = None,
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
        # Load the sidecar at most once for the up-to-four fields that come out
        # of it. Left as None when every one of them was supplied, so callers
        # that pass everything still touch no extra files.
        side_meta: Optional[Dict[str, Any]] = None
        if taken_at is None or post_id is None or shortcode is None:
            side_meta = read_sidecar(full)

        if taken_at is None:
            taken_at = taken_at_for_image(full, filename, side_meta)
        if post_id is None or shortcode is None:
            meta_pid, meta_sc = self._identity_from_file(full, side_meta)
            if post_id is None:
                post_id = meta_pid or None
            if shortcode is None:
                shortcode = meta_sc or None
        with self._lock:
            existing = self._conn.execute(
                "SELECT favorite, has_prompt, prompt_stale, prompt_search, "
                "post_id, shortcode, source FROM photos WHERE rel_path = ?",
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
            # Keep the row's existing platform unless told otherwise; only fall
            # back to the sidecar (then instagram) for rows we've never seen.
            if source is None and existing and "source" in existing.keys():  # noqa: SIM118
                source = existing["source"]
            if not source:
                # side_meta may be None here; read_sidecar loads on demand.
                source = self._source_from_file(full, side_meta)
            self._conn.execute(
                "INSERT INTO photos("
                "rel_path, creator, filename, taken_at, mtime, "
                "favorite, has_prompt, prompt_stale, prompt_search, "
                "post_id, shortcode, source"
                ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(rel_path) DO UPDATE SET "
                "creator=excluded.creator, filename=excluded.filename, "
                "taken_at=excluded.taken_at, mtime=excluded.mtime, "
                "favorite=excluded.favorite, has_prompt=excluded.has_prompt, "
                "prompt_stale=excluded.prompt_stale, prompt_search=excluded.prompt_search, "
                "post_id=COALESCE(excluded.post_id, photos.post_id), "
                "shortcode=COALESCE(excluded.shortcode, photos.shortcode), "
                "source=excluded.source",
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
                    self._norm_platform(source),
                ),
            )
            self._conn.commit()

    def delete_photo(self, rel_path: str) -> None:
        rel = normalize_rel_path(rel_path)
        with self._lock:
            self._conn.execute("DELETE FROM photos WHERE rel_path = ?", (rel,))
            self._conn.commit()

    def get_photo_source(self, rel_path: str) -> str:
        """Return the indexed platform for a rel_path (instagram if unknown)."""
        rel = normalize_rel_path(rel_path)
        with self._lock:
            row = self._conn.execute(
                "SELECT source FROM photos WHERE rel_path = ?", (rel,)
            ).fetchone()
        if not row:
            return DEFAULT_SOURCE
        return str(row["source"] or "").strip().lower() or DEFAULT_SOURCE

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

    @staticmethod
    def _norm_platform(platform: Optional[str]) -> str:
        return (platform or DEFAULT_SOURCE).strip().lower() or DEFAULT_SOURCE

    @staticmethod
    def _source_from_file(
        full_path: str,
        meta: Optional[Dict[str, Any]] = None,
    ) -> str:
        """Return the sidecar's `source`, defaulting to instagram for legacy files."""
        try:
            side = read_sidecar(full_path, meta)
            return str(side.get("source") or "").strip().lower() or DEFAULT_SOURCE
        except Exception:
            return DEFAULT_SOURCE

    def record_deleted_post(
        self,
        creator: str,
        *,
        shortcode: Optional[str] = None,
        post_id: Optional[str] = None,
        rel_path: Optional[str] = None,
        source: str = "ui",
        platform: str = DEFAULT_SOURCE,
    ) -> bool:
        """Tombstone a post so sync never re-downloads it.

        `source` is who deleted it ("ui"); `platform` is which site it came from.
        Returns True if a row was written/updated.
        """
        from datetime import datetime, timezone

        creator_key = self._norm_creator(creator)
        plat = self._norm_platform(platform)
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
                    "SELECT id FROM deleted_posts "
                    "WHERE platform = ? AND creator = ? AND shortcode = ?",
                    (plat, creator_key, sc),
                ).fetchone()
            if not existing and pid:
                existing = self._conn.execute(
                    "SELECT id FROM deleted_posts "
                    "WHERE platform = ? AND creator = ? AND post_id = ?",
                    (plat, creator_key, pid),
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
                    "INSERT INTO deleted_posts("
                    "creator, shortcode, post_id, rel_path, deleted_at, source, platform"
                    ") VALUES(?, ?, ?, ?, ?, ?, ?)",
                    (creator_key, sc, pid, rel, now, source or "ui", plat),
                )
            self._conn.commit()
        return True

    def is_deleted_post(
        self,
        creator: str,
        *,
        shortcode: Optional[str] = None,
        post_id: Optional[str] = None,
        platform: str = DEFAULT_SOURCE,
    ) -> bool:
        """True if this identity was intentionally deleted for creator on platform."""
        creator_key = self._norm_creator(creator)
        plat = self._norm_platform(platform)
        sc = (shortcode or "").strip()
        pid = str(post_id or "").strip()
        if not creator_key or (not sc and not pid):
            return False
        clauses: List[str] = []
        params: List[Any] = [plat, creator_key]
        if sc:
            clauses.append("shortcode = ?")
            params.append(sc)
        if pid:
            clauses.append("post_id = ?")
            params.append(pid)
        where_id = " OR ".join(clauses)
        with self._lock:
            row = self._conn.execute(
                "SELECT 1 FROM deleted_posts "
                f"WHERE platform = ? AND creator = ? AND ({where_id}) LIMIT 1",
                params,
            ).fetchone()
        return row is not None

    def clear_deleted_post(
        self,
        creator: str,
        *,
        shortcode: Optional[str] = None,
        post_id: Optional[str] = None,
        platform: str = DEFAULT_SOURCE,
    ) -> int:
        """Remove tombstone(s). Returns number of rows deleted."""
        creator_key = self._norm_creator(creator)
        plat = self._norm_platform(platform)
        sc = (shortcode or "").strip()
        pid = str(post_id or "").strip()
        if not creator_key or (not sc and not pid):
            return 0
        clauses: List[str] = []
        params: List[Any] = [plat, creator_key]
        if sc:
            clauses.append("shortcode = ?")
            params.append(sc)
        if pid:
            clauses.append("post_id = ?")
            params.append(pid)
        where_id = " OR ".join(clauses)
        with self._lock:
            cur = self._conn.execute(
                "DELETE FROM deleted_posts "
                f"WHERE platform = ? AND creator = ? AND ({where_id})",
                params,
            )
            self._conn.commit()
            return int(cur.rowcount or 0)

    def carousel_paths(
        self,
        *,
        shortcode: Optional[str] = None,
        post_id: Optional[str] = None,
        source: Optional[str] = DEFAULT_SOURCE,
    ) -> List[str]:
        """Return on-disk rel_paths for a post identity.

        Scoped by `source` because ids are only unique *within* a platform — an
        unscoped lookup lets an X tweet id match an Instagram mediaid and
        miscount a carousel as already complete. Pass source=None to search all.
        """
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
        if source is not None:
            where = f"({where}) AND source = ?"
            params.append(self._norm_platform(source))
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
        }

    def list_creators(self) -> List[Dict[str, Any]]:
        from promptstudio.scraping.checkpoints import SyncCheckpoints

        sync = SyncCheckpoints().load()
        with self._lock:
            rows = self._conn.execute(
                "SELECT creator, "
                "COUNT(*) AS photo_count, "
                "MIN(filename) AS cover "
                "FROM photos GROUP BY creator ORDER BY photo_count DESC"
            ).fetchall()
        creators = []
        for row in rows:
            name = row["creator"]
            cover = row["cover"]
            entry = sync.get(name.lower()) or sync.get(name) or {}
            photo_count = int(row["photo_count"] or 0)
            creators.append(
                {
                    "name": name,
                    "photo_count": photo_count,
                    "cover_url": f"/media/{name}/{urllib.parse.quote(cover)}",
                    "cover_thumb_url": thumb_url(f"{name}/{cover}"),
                    "last_synced_at": entry.get("updated_at") or None,
                    "synced_count": entry.get("downloaded_count"),
                }
            )
        return creators

    def stats(self) -> Dict[str, int]:
        """Gallery counters for /api/stats — all indexed, no filesystem walk.

        `prompts_ready` reads the `has_prompt` column (idx_photos_prompt), which
        PromptCache maintains write-through via update_prompt_flags. The old
        count_prompts_ready() iterated every photo in the archive and loaded the
        whole prompt cache on each call, and /api/stats is on the init path.
        """
        video_case = " OR ".join(
            "LOWER(filename) LIKE ?" for _ in VIDEO_EXTENSIONS
        )
        with self._lock:
            row = self._conn.execute(
                "SELECT COUNT(*) AS total, "
                "COUNT(DISTINCT creator) AS creators, "
                "SUM(CASE WHEN has_prompt = 1 THEN 1 ELSE 0 END) AS prompts_ready, "
                f"SUM(CASE WHEN ({video_case}) THEN 1 ELSE 0 END) AS videos "
                "FROM photos",
                [f"%{ext}" for ext in VIDEO_EXTENSIONS],
            ).fetchone()
        total = int(row["total"] or 0)
        videos = int(row["videos"] or 0)
        return {
            "total_photos": total - videos,
            "total_videos": videos,
            "total_creators": int(row["creators"] or 0),
            "prompts_ready": int(row["prompts_ready"] or 0),
        }

    def query_photos(
        self,
        *,
        creator: Optional[str] = None,
        search: Optional[str] = None,
        unanalyzed: bool = False,
        favorite_only: bool = False,
        media_type: Optional[str] = None,
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
            like = f"%{q}%"
            fts_query = _fts_query(q) if (self.fts_enabled and FTS_SEARCH) else ""
            if fts_query:
                # Prompt text goes through FTS5 instead of a leading-wildcard
                # LIKE. Creator and filename stay on LIKE: they are short, and
                # people type fragments of a handle rather than whole tokens.
                #
                # OFF BY DEFAULT — measured, not assumed. See docs/review_backend
                # _architecture.md S5: FTS wins ~1.4x on selective terms but
                # loses ~3x on common ones, because `IN (subquery)` materialises
                # every match while LIKE short-circuits per row. At archive
                # sizes where LIKE costs single-digit ms, that trade is a loss.
                # The index is still maintained so this can flip when the
                # archive is large enough to change the answer.
                where.append(
                    "(LOWER(creator) LIKE ? OR LOWER(filename) LIKE ? OR rel_path IN "
                    "(SELECT rel_path FROM prompts_fts WHERE prompts_fts MATCH ?))"
                )
                params.extend([like, like, fts_query])
            else:
                where.append(
                    "(LOWER(creator) LIKE ? OR LOWER(filename) LIKE ? "
                    "OR IFNULL(prompt_search, '') LIKE ?)"
                )
                params.extend([like, like, like])

        where_sql = (" WHERE " + " AND ".join(where)) if where else ""
        sort = (sort or "name").lower()
        if sort == "newest":
            order = "ORDER BY taken_at DESC, filename DESC"
        elif sort == "oldest":
            order = "ORDER BY taken_at ASC, filename ASC"
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
