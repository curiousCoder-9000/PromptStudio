"""SQLite photo catalog for fast gallery list/filter/sort."""

from __future__ import annotations

import json
import os
import re
import sqlite3
import threading
import time
import urllib.parse
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple

from promptstudio.config import (
    ARCHIVE_DB_FILE,
    CLASSIFY_REJECT_MAX_TIER,
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
  added_at REAL,
  favorite INTEGER NOT NULL DEFAULT 0,
  has_prompt INTEGER NOT NULL DEFAULT 0,
  prompt_stale INTEGER NOT NULL DEFAULT 0,
  prompt_search TEXT,
  -- The creator's own words, kept apart from prompt_search on purpose: the
  -- caption is fixed for the life of the file while the prompt blob is
  -- rewritten on every regenerate, so merging them would mean re-deriving
  -- the caption on every prompt save for no gain.
  caption_search TEXT,
  post_id TEXT,
  shortcode TEXT,
  source TEXT NOT NULL DEFAULT 'instagram'
);
CREATE INDEX IF NOT EXISTS idx_photos_creator ON photos(creator);
CREATE INDEX IF NOT EXISTS idx_photos_taken ON photos(taken_at);
CREATE INDEX IF NOT EXISTS idx_photos_fav ON photos(favorite);
CREATE INDEX IF NOT EXISTS idx_photos_prompt ON photos(has_prompt);
-- idx_photos_added is created in _migrate_identity_columns after the
-- added_at column is ensured (CREATE TABLE IF NOT EXISTS is a no-op on
-- older DBs that predate the column).
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

# Keep/reject verdicts from the media classifier. Its own table rather than
# columns on `photos`, for the same reason as phashes: written by a separate
# background pass, absent until that pass runs, and it would otherwise widen the
# row every gallery query reads. It also keeps a clean distance from the
# orphaned glam_* columns that 1cc0f44 left behind on pre-existing DBs.
#
# `creator` is denormalised so the sidebar counters are a single GROUP BY rather
# than a join back to photos on every sidebar render.
#
# What is NOT here: the keep/reject string. Only `tier` is stored, and the
# verdict is derived at query time against CLASSIFY_REJECT_MAX_TIER — so moving
# the threshold re-thresholds the whole archive without re-running the model.
_VERDICT_SCHEMA = """
CREATE TABLE IF NOT EXISTS media_verdicts (
  rel_path       TEXT PRIMARY KEY,
  creator        TEXT NOT NULL,
  tier           INTEGER NOT NULL DEFAULT -1,
  manual         TEXT,
  reason         TEXT,
  media_kind     TEXT,
  verdict_source TEXT,
  confidence     REAL,
  prompt_version TEXT,
  sheet_path     TEXT,
  error          TEXT,
  classified_at  TEXT,
  duration_ms    INTEGER
);
CREATE INDEX IF NOT EXISTS idx_verdicts_creator ON media_verdicts(creator);
CREATE INDEX IF NOT EXISTS idx_verdicts_tier ON media_verdicts(tier);
CREATE INDEX IF NOT EXISTS idx_verdicts_version ON media_verdicts(prompt_version);
"""

# Effective verdict: a manual override always wins, a failed attempt is its own
# state (so it is retryable and not silently counted as a keep), and everything
# else falls out of the tier. Written once here and formatted into every query
# that needs it — two copies of this CASE would drift.
_VERDICT_CASE = (
    "CASE WHEN v.manual IS NOT NULL THEN v.manual "
    "WHEN v.rel_path IS NULL THEN 'unclassified' "
    "WHEN v.tier < 0 THEN 'error' "
    "WHEN v.tier <= {cut} THEN 'reject' "
    "ELSE 'keep' END"
)

VERDICT_FILTERS = ("keep", "reject", "unclassified", "error", "unusable", "modest")

# Creators with no verdict row at all still need every key present, or the
# sidebar has to guard each counter individually.
_EMPTY_VERDICT_COUNTS: Dict[str, int] = {
    "keep_count": 0,
    "reject_count": 0,
    "unclassified_count": 0,
    "error_count": 0,
    "unusable_count": 0,
    "modest_count": 0,
    "stale_count": 0,
}

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
    # Wall-clock (or filesystem birth) when the file entered this archive.
    # Downloaders often set mtime to the Instagram post date, so mtime cannot
    # drive "newest downloaded first".
    ("added_at", "REAL"),
    # Creator-written text (caption, author) — see caption_search_blob.
    ("caption_search", "TEXT"),
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


def file_added_at(full_path: str) -> float:
    """When the file appeared on this machine (download/create), not post date.

    Instaloader/gallery-dl commonly stamp ``mtime`` to the remote post time.
    On Windows ``st_ctime`` is creation time; on some platforms ``st_birthtime``
    exists. Prefer those over mtime so "newest" means newly archived.
    """
    if not full_path:
        return 0.0
    try:
        st = os.stat(full_path)
    except OSError:
        return 0.0
    birth = getattr(st, "st_birthtime", None)
    if birth:
        return float(birth)
    if os.name == "nt":
        return float(st.st_ctime)
    return float(st.st_mtime)


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


def caption_search_blob(meta: Optional[Dict[str, Any]]) -> str:
    """Searchable text the *creator* wrote, from the sidecar.

    Everything in `prompt_search` is model-generated, so until this existed the
    only human-written text in the archive — hashtags, location, brand names —
    could not be found. `#` is kept: searching "#ootd" should work, and a bare
    "ootd" still matches on substring.
    """
    if not meta:
        return ""
    parts = [
        str(meta.get("caption") or ""),
        # Present on gallery-dl sources; the real author of a repost/retweet,
        # which is not the folder name and is otherwise unsearchable.
        str(meta.get("author") or ""),
    ]
    return " ".join(p for p in parts if p).strip().lower()


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
            self._conn.executescript(_VERDICT_SCHEMA)
            self._init_fts()
            self._migrate_identity_columns()
            self._migrate_deleted_posts()
            self._migrate_added_at()
            self._migrate_caption_search()
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

    # ── keep/reject verdicts ─────────────────────────────────────────

    @staticmethod
    def _reject_cut(cut: Optional[int] = None) -> int:
        """Highest tier still counted as a reject.

        Read through a helper rather than closing over the module constant, so a
        test (or a future per-creator override) can pass a different cut without
        reimporting config.
        """
        return int(CLASSIFY_REJECT_MAX_TIER if cut is None else cut)

    @staticmethod
    def _verdict_row_to_dict(row: Optional[sqlite3.Row], cut: int) -> Dict[str, Any]:
        if row is None:
            return {}
        tier = int(row["tier"] if row["tier"] is not None else -1)
        manual = row["manual"]
        if manual:
            verdict = str(manual)
        elif tier < 0:
            verdict = "error"
        else:
            verdict = "reject" if tier <= cut else "keep"
        return {
            "rel_path": row["rel_path"],
            "tier": tier,
            "verdict": verdict,
            "manual": manual or None,
            "reason": row["reason"] or "",
            "media_kind": row["media_kind"] or "",
            "verdict_source": row["verdict_source"] or "",
            "confidence": row["confidence"],
            "prompt_version": row["prompt_version"] or "",
            "sheet_path": row["sheet_path"] or None,
            "error": row["error"] or None,
            "classified_at": row["classified_at"] or "",
            "duration_ms": row["duration_ms"],
        }

    def set_verdict(
        self,
        rel_path: str,
        *,
        creator: str = "",
        tier: int = -1,
        reason: str = "",
        media_kind: str = "",
        verdict_source: str = "",
        confidence: Optional[float] = None,
        prompt_version: Optional[str] = None,
        sheet_path: Optional[str] = None,
        error: Optional[str] = None,
        duration_ms: Optional[int] = None,
    ) -> None:
        """Record one classify attempt. Upsert; the manual override survives.

        A failed attempt is written too, with tier -1 and the reason in `error`.
        Without that a timeout is indistinguishable from "never attempted", which
        is what made the old error rows unfindable.
        """
        rel = normalize_rel_path(rel_path)
        creator = (creator or rel.split("/", 1)[0]).strip().lstrip("@")
        with self._lock:
            self._conn.execute(
                "INSERT INTO media_verdicts ("
                "rel_path, creator, tier, reason, media_kind, verdict_source, "
                "confidence, prompt_version, sheet_path, error, classified_at, "
                "duration_ms) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(rel_path) DO UPDATE SET "
                "creator=excluded.creator, tier=excluded.tier, "
                "reason=excluded.reason, media_kind=excluded.media_kind, "
                "verdict_source=excluded.verdict_source, "
                "confidence=excluded.confidence, "
                "prompt_version=excluded.prompt_version, "
                "sheet_path=excluded.sheet_path, error=excluded.error, "
                "classified_at=excluded.classified_at, "
                "duration_ms=excluded.duration_ms",
                (
                    rel,
                    creator,
                    int(tier),
                    reason or None,
                    media_kind or None,
                    verdict_source or None,
                    float(confidence) if confidence is not None else None,
                    prompt_version or None,
                    sheet_path or None,
                    error or None,
                    datetime.now(timezone.utc).isoformat(),
                    int(duration_ms) if duration_ms is not None else None,
                ),
            )
            self._conn.commit()

    def get_verdict(self, rel_path: str, *, cut: Optional[int] = None) -> Dict[str, Any]:
        rel = normalize_rel_path(rel_path)
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM media_verdicts WHERE rel_path = ?", (rel,)
            ).fetchone()
        return self._verdict_row_to_dict(row, self._reject_cut(cut))

    def set_manual_verdict(self, rel_path: str, value: Optional[str]) -> bool:
        """Pin a file to keep/reject by hand, or clear back to the model's call.

        Returns False when there is no verdict row yet — a manual call on an
        unclassified file is a UI bug, not something to invent a tier for.
        """
        if value not in (None, "keep", "reject"):
            raise ValueError(f"bad manual verdict: {value!r}")
        rel = normalize_rel_path(rel_path)
        with self._lock:
            cur = self._conn.execute(
                "UPDATE media_verdicts SET manual = ? WHERE rel_path = ?",
                (value, rel),
            )
            self._conn.commit()
            return bool(cur.rowcount)

    def verdicts_for(
        self, rel_paths: Sequence[str], *, cut: Optional[int] = None
    ) -> Dict[str, Dict[str, Any]]:
        """Bulk fetch for annotating a gallery page. One query, not one per row."""
        rels = [normalize_rel_path(p) for p in rel_paths if p]
        if not rels:
            return {}
        cut_v = self._reject_cut(cut)
        out: Dict[str, Dict[str, Any]] = {}
        # SQLite caps host parameters (999 on older builds); a gallery page is
        # well under that, but chunk anyway so a caller passing the whole
        # archive degrades instead of raising.
        with self._lock:
            for start in range(0, len(rels), 400):
                chunk = rels[start : start + 400]
                placeholders = ",".join("?" for _ in chunk)
                rows = self._conn.execute(
                    f"SELECT * FROM media_verdicts WHERE rel_path IN ({placeholders})",
                    chunk,
                ).fetchall()
                for row in rows:
                    out[row["rel_path"]] = self._verdict_row_to_dict(row, cut_v)
        return out

    def delete_verdict(self, rel_path: str) -> None:
        with self._lock:
            self._conn.execute(
                "DELETE FROM media_verdicts WHERE rel_path = ?",
                (normalize_rel_path(rel_path),),
            )
            self._conn.commit()

    def creator_verdict_counts(
        self,
        *,
        cut: Optional[int] = None,
        stale_versions: Sequence[str] = (),
        source: Optional[str] = None,
    ) -> Dict[str, Dict[str, int]]:
        """Per-creator keep/reject/unclassified counters for the sidebar.

        `unusable` (tier 0) and `modest` (tier 1) are broken out of `reject` on
        purpose: the 0 boundary is a quality gate anyone would accept, while the
        1 boundary is a taste call that has never been measured. The review UI
        lets you act on them separately, so the counts have to arrive separately.

        `source` scopes the counters to one platform. Without it a merged folder
        would show its Instagram rejects while the user is filtered to X — a
        number that is confidently wrong, which is worse than a missing one.
        """
        sql, params = self._verdict_counts_sql(
            cut=cut, stale_versions=stale_versions, source=source
        )
        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()
        return {
            row["creator"]: {key: int(row[key] or 0) for key in _EMPTY_VERDICT_COUNTS}
            for row in rows
        }

    def _verdict_counts_sql(
        self,
        *,
        cut: Optional[int] = None,
        stale_versions: Sequence[str] = (),
        source: Optional[str] = None,
    ) -> Tuple[str, List[Any]]:
        """Build the per-creator verdict rollup. Returns (sql, params)."""
        verdict = _VERDICT_CASE.format(cut=self._reject_cut(cut))
        params: List[Any] = []

        stale = [v for v in stale_versions if v]
        stale_expr = "0"
        if stale:
            placeholders = ",".join("?" for _ in stale)
            stale_expr = (
                "CASE WHEN v.rel_path IS NOT NULL AND v.tier >= 0 AND "
                f"IFNULL(v.prompt_version, '') NOT IN ({placeholders}) "
                "THEN 1 ELSE 0 END"
            )
            params.extend(stale)

        where_sql = ""
        if source:
            where_sql = " WHERE p.source = ?"
            params.append(self._norm_platform(source))

        # Order matches _EMPTY_VERDICT_COUNTS so the row->dict comprehension in
        # the caller stays a plain key lookup rather than a hand-kept mapping.
        aggregates = (
            f"SUM(CASE WHEN {verdict} = 'keep' THEN 1 ELSE 0 END) AS keep_count",
            f"SUM(CASE WHEN {verdict} = 'reject' THEN 1 ELSE 0 END) AS reject_count",
            "SUM(CASE WHEN v.rel_path IS NULL THEN 1 ELSE 0 END) AS unclassified_count",
            "SUM(CASE WHEN v.rel_path IS NOT NULL AND v.tier < 0 "
            "AND v.manual IS NULL THEN 1 ELSE 0 END) AS error_count",
            "SUM(CASE WHEN v.tier = 0 AND v.manual IS NULL THEN 1 ELSE 0 END) "
            "AS unusable_count",
            "SUM(CASE WHEN v.tier = 1 AND v.manual IS NULL THEN 1 ELSE 0 END) "
            "AS modest_count",
            f"SUM({stale_expr}) AS stale_count",
        )
        sql = (
            "SELECT p.creator AS creator, " + ", ".join(aggregates) + " FROM photos p "
            "LEFT JOIN media_verdicts v ON v.rel_path = p.rel_path"
            f"{where_sql} GROUP BY p.creator"
        )
        return sql, params

    def list_unclassified(
        self,
        creator: str,
        *,
        include_videos: bool = True,
        force: bool = False,
        stale_versions: Sequence[str] = (),
        retry_errors: bool = True,
        limit: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """Media that a classify run should visit.

        An empty `creator` means **the whole archive** — excluded folders are
        filtered out by the `photos` table itself, which never indexes them.

        Indexed LEFT JOIN, not a directory walk: the old implementation listed
        the folder and issued one `get_glam_score` per filename, which is O(n)
        queries to answer a question SQLite can answer in one.

        `force` takes everything. Otherwise: never-classified, plus failed
        attempts (`retry_errors`), plus anything judged by a prompt version no
        longer in `stale_versions` — which is what makes "Re-score outdated"
        cheap instead of a full-archive rescore.
        """
        creator = (creator or "").strip().lstrip("@")
        if creator and (creator in EXCLUDED_FOLDERS or creator.startswith((".", "_"))):
            return []

        exts = MEDIA_EXTENSIONS if include_videos else IMAGE_EXTENSIONS
        ext_clause = " OR ".join("LOWER(p.filename) LIKE ?" for _ in exts)
        params: List[Any] = []
        where = [f"({ext_clause})"]
        if creator:
            where.insert(0, "p.creator = ?")
            params.append(creator)
        params.extend(f"%{ext}" for ext in exts)

        if not force:
            conds = ["v.rel_path IS NULL"]
            if retry_errors:
                conds.append("v.tier < 0")
            stale = [v for v in stale_versions if v]
            if stale:
                placeholders = ",".join("?" for _ in stale)
                conds.append(
                    "(v.tier >= 0 AND IFNULL(v.prompt_version, '') NOT IN "
                    f"({placeholders}))"
                )
            where.append("(" + " OR ".join(conds) + ")")
            if stale:
                params.extend(stale)

        sql = (
            "SELECT p.rel_path, p.creator, p.filename FROM photos p "
            "LEFT JOIN media_verdicts v ON v.rel_path = p.rel_path "
            f"WHERE {' AND '.join(where)} ORDER BY p.creator ASC, p.filename ASC"
        )
        if limit is not None:
            sql += " LIMIT ?"
            params.append(int(limit))

        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()

        pending: List[Dict[str, Any]] = []
        for row in rows:
            full = os.path.join(self.base_dir, row["creator"], row["filename"])
            # The index can outlive the file (external delete). Skipping here is
            # cheaper than a failed classify attempt per ghost row.
            if not os.path.isfile(full):
                continue
            pending.append(
                {
                    "rel_path": row["rel_path"],
                    "creator": row["creator"],
                    "filename": row["filename"],
                    "full_path": full,
                    "is_video": row["filename"].lower().endswith(VIDEO_EXTENSIONS),
                }
            )
        return pending

    def tier_histogram(self) -> Dict[str, int]:
        """Archive-wide tier distribution for /api/insights.

        A classifier that emits one value for most of the archive carries almost
        no information — that is how the v2 prompt shipped at 85% one value
        without anyone noticing. This makes it visible without a labelled set.
        """
        with self._lock:
            rows = self._conn.execute(
                "SELECT tier, COUNT(*) AS c FROM media_verdicts GROUP BY tier"
            ).fetchall()
        return {str(int(row["tier"])): int(row["c"]) for row in rows}

    def prompt_import_done(self) -> bool:
        return self._meta_get(_PROMPTS_IMPORTED_KEY) == "1"

    def mark_prompt_import_done(self) -> None:
        self._meta_set(_PROMPTS_IMPORTED_KEY, "1")

    def _migrate_identity_columns(self) -> None:
        """Add post_id/shortcode/source/added_at to existing DBs."""
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
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_photos_added ON photos(added_at)"
        )
        # Drives "re-score everything the old prompt judged" without a rescan.

    def _migrate_caption_search(self) -> None:
        """Backfill caption_search once from the sidecars.

        The column is added by `_migrate_identity_columns`, but an existing
        archive would then have it NULL on every row until someone happened to
        rebuild — i.e. search would silently keep missing captions. One pass of
        sidecar reads (about 0.7s over 4.5k files, per S8's measurement) is
        cheaper than telling the user to reindex.
        """
        if self._meta_get("caption_search_backfilled") == "1":
            return
        rows = self._conn.execute(
            "SELECT rel_path FROM photos WHERE caption_search IS NULL"
        ).fetchall()
        updates = []
        for row in rows:
            rel = row["rel_path"]
            full = os.path.join(self.base_dir, *rel.split("/"))
            updates.append((caption_search_blob(read_sidecar(full)), rel))
        if updates:
            self._conn.executemany(
                "UPDATE photos SET caption_search = ? WHERE rel_path = ?", updates
            )
        self._meta_set("caption_search_backfilled", "1")
        if updates:
            log.info("caption search backfilled for %d photos", len(updates))

    def _migrate_added_at(self) -> None:
        """Backfill added_at once from filesystem birth/ctime.

        Downloaders rewrite mtime to the post timestamp, so using mtime for the
        backfill would leave "newest" looking like a post-date sort. Birth/ctime
        is when the file landed on disk (download time on Windows).
        """
        if self._meta_get("added_at_backfilled") == "1":
            return
        rows = self._conn.execute(
            "SELECT rel_path FROM photos "
            "WHERE added_at IS NULL OR added_at = 0"
        ).fetchall()
        for row in rows:
            rel = row["rel_path"]
            full = os.path.join(self.base_dir, *rel.split("/"))
            added = file_added_at(full)
            if not added:
                # Last resort: keep a stable number so ORDER BY is defined.
                try:
                    added = float(os.path.getmtime(full))
                except OSError:
                    added = 0.0
            self._conn.execute(
                "UPDATE photos SET added_at = ? WHERE rel_path = ?",
                (added, rel),
            )
        self._meta_set("added_at_backfilled", "1")

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

        # Preserve when each path was first archived across rebuilds so
        # "newest" does not reshuffle the whole library after a rescan.
        with self._lock:
            prior_added = {
                row["rel_path"]: row["added_at"]
                for row in self._conn.execute(
                    "SELECT rel_path, added_at FROM photos "
                    "WHERE added_at IS NOT NULL AND added_at > 0"
                ).fetchall()
            }

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
                    added = prior_added.get(rel) or file_added_at(full) or mtime
                    rows.append(
                        (
                            rel,
                            creator,
                            filename,
                            taken,
                            mtime,
                            added,
                            fav,
                            has_p,
                            stale,
                            blob,
                            # Free here: `side` is the same single sidecar read
                            # the other four fields already share.
                            caption_search_blob(side),
                            post_id or None,
                            shortcode or None,
                            self._source_from_file(full, side),
                        )
                    )

        with self._lock:
            self._conn.execute("DELETE FROM photos")
            self._conn.executemany(
                "INSERT INTO photos("
                "rel_path, creator, filename, taken_at, mtime, added_at, "
                "favorite, has_prompt, prompt_stale, prompt_search, caption_search, "
                "post_id, shortcode, source"
                ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
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
        caption: Optional[str] = None,
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
                "caption_search, post_id, shortcode, source, added_at "
                "FROM photos WHERE rel_path = ?",
                (rel,),
            ).fetchone()
            fav = favorite if favorite is not None else (int(existing["favorite"]) if existing else 0)
            hp = has_prompt if has_prompt is not None else (int(existing["has_prompt"]) if existing else 0)
            st = prompt_stale if prompt_stale is not None else (int(existing["prompt_stale"]) if existing else 0)
            blob = prompt_search if prompt_search is not None else (existing["prompt_search"] if existing else "")
            # Caption precedence: explicit arg → the sidecar we already loaded →
            # whatever the row had. The last case matters: a favorite toggle
            # passes none of these and must not blank the caption index.
            if caption is not None:
                cap_blob = caption_search_blob({"caption": caption})
            elif side_meta is not None:
                cap_blob = caption_search_blob(side_meta)
            elif existing is not None:
                cap_blob = existing["caption_search"] or ""
            else:
                cap_blob = caption_search_blob(read_sidecar(full))
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
            # New downloads get "now" so they sort to the top of newest. Existing
            # rows keep their first-seen time so favorite toggles / prompt
            # updates do not reshuffle the gallery.
            if existing is not None and existing["added_at"]:
                added_at = float(existing["added_at"])
            else:
                added_at = time.time()
            self._conn.execute(
                "INSERT INTO photos("
                "rel_path, creator, filename, taken_at, mtime, added_at, "
                "favorite, has_prompt, prompt_stale, prompt_search, caption_search, "
                "post_id, shortcode, source"
                ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(rel_path) DO UPDATE SET "
                "creator=excluded.creator, filename=excluded.filename, "
                "taken_at=excluded.taken_at, mtime=excluded.mtime, "
                "added_at=COALESCE(photos.added_at, excluded.added_at), "
                "favorite=excluded.favorite, has_prompt=excluded.has_prompt, "
                "prompt_stale=excluded.prompt_stale, prompt_search=excluded.prompt_search, "
                "caption_search=excluded.caption_search, "
                "post_id=COALESCE(excluded.post_id, photos.post_id), "
                "shortcode=COALESCE(excluded.shortcode, photos.shortcode), "
                "source=excluded.source",
                (
                    rel,
                    creator,
                    filename,
                    taken_at,
                    mtime,
                    added_at,
                    fav,
                    hp,
                    st,
                    blob or "",
                    cap_blob or "",
                    post_id or None,
                    shortcode or None,
                    self._norm_platform(source),
                ),
            )
            self._conn.commit()

    def delete_photo(self, rel_path: str, *, drop_verdict: bool = True) -> None:
        """Drop a photo from the index.

        `drop_verdict=False` for a *soft* delete. Trashing 40 rejects and hitting
        Undo must give back 40 rejects, not 40 unclassified files that need the
        whole vision pass run again — and the verdict row is invisible while the
        photo row is gone, because every verdict query joins out from `photos`.
        Permanent removal (`TrashStore.purge`) is what really drops it.
        """
        rel = normalize_rel_path(rel_path)
        with self._lock:
            self._conn.execute("DELETE FROM photos WHERE rel_path = ?", (rel,))
            if drop_verdict:
                self._conn.execute(
                    "DELETE FROM media_verdicts WHERE rel_path = ?", (rel,)
                )
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
        photo = {
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
        # Only present when the caller joined media_verdicts (query_photos does;
        # rebuild's internal row reads do not).
        if "v_verdict" in keys and row["v_verdict"] != "unclassified":
            photo["verdict"] = {
                "verdict": row["v_verdict"],
                "tier": int(row["v_tier"] if row["v_tier"] is not None else -1),
                "manual": row["v_manual"] or None,
                "reason": row["v_reason"] or "",
                "media_kind": row["v_media_kind"] or "",
                "verdict_source": row["v_source"] or "",
                "confidence": row["v_confidence"],
                "prompt_version": row["v_prompt_version"] or "",
                "sheet_path": row["v_sheet_path"] or None,
                "error": row["v_error"] or None,
                "classified_at": row["v_classified_at"] or "",
            }
        return photo

    def list_creators(
        self,
        *,
        with_verdicts: bool = True,
        source: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Sidebar creator rollup, optionally scoped to one platform.

        `source` narrows `photo_count`, the cover and the verdict counters, and
        drops creators with no media from that platform. The per-creator
        `sources` map is deliberately NOT narrowed: the sidebar has to mark a
        folder as multi-source *while* a filter is active, which a filtered map
        cannot express.

        Provenance comes from `photos.source`, never from the folder-name
        suffix — `SCRAPE_FOLDER_SUFFIX=0` merges platforms into one bare folder,
        and any folder can also hold manual uploads.
        """
        from promptstudio.scraping.checkpoints import SyncCheckpoints

        sync = SyncCheckpoints().load()
        wanted = self._norm_platform(source) if source else None
        counts = self._verdict_counts_for_list(with_verdicts, wanted)
        by_creator = self._creator_source_rollup()

        creators = []
        for name, per_source in by_creator.items():
            picked = per_source.get(wanted) if wanted else None
            if wanted and not picked:
                continue
            # Unfiltered cover comes from the largest source, so a creator with
            # three X photos and 400 Instagram ones still shows an IG cover.
            # The COUNT, though, is the total when unfiltered — taking it from
            # the same `chosen` row would report the biggest source's count as
            # the folder's size.
            chosen = picked or max(per_source.values(), key=lambda s: s["n"])
            cover = chosen["cover"]
            count = int(picked["n"]) if picked else sum(s["n"] for s in per_source.values())
            entry = sync.get(name.lower()) or sync.get(name) or {}
            creators.append(
                {
                    "name": name,
                    "photo_count": count,
                    "sources": {src: int(v["n"]) for src, v in per_source.items()},
                    "cover_url": f"/media/{name}/{urllib.parse.quote(cover)}",
                    "cover_thumb_url": thumb_url(f"{name}/{cover}"),
                    "last_synced_at": entry.get("updated_at") or None,
                    "synced_count": entry.get("downloaded_count"),
                    **(counts.get(name) or _EMPTY_VERDICT_COUNTS),
                }
            )
        creators.sort(key=lambda c: (-c["photo_count"], c["name"]))
        return creators

    def _verdict_counts_for_list(
        self, with_verdicts: bool, source: Optional[str]
    ) -> Dict[str, Dict[str, int]]:
        if not with_verdicts:
            return {}
        try:
            from promptstudio.scraping.media_classifier import active_prompt_versions

            stale_versions = active_prompt_versions()
        except Exception:
            stale_versions = ()
        return self.creator_verdict_counts(
            stale_versions=stale_versions, source=source
        )

    def _creator_source_rollup(self) -> Dict[str, Dict[str, Dict[str, Any]]]:
        """`{creator: {source: {"n": count, "cover": filename}}}` in one scan.

        Grouped one level finer than the old creator-only rollup so a single
        query answers both "how many from this platform" and "which platforms
        does this folder hold". Sorting moves to Python — creator counts are in
        the hundreds, which is not where time goes.
        """
        with self._lock:
            rows = self._conn.execute(
                "SELECT creator, source, COUNT(*) AS n, MIN(filename) AS cover "
                "FROM photos GROUP BY creator, source"
            ).fetchall()
        out: Dict[str, Dict[str, Dict[str, Any]]] = {}
        for row in rows:
            src = self._norm_platform(row["source"])
            out.setdefault(row["creator"], {})[src] = {
                "n": int(row["n"] or 0),
                "cover": row["cover"],
            }
        return out

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
            "unclassified_total": self.unclassified_total(),
        }

    def unclassified_total(self) -> int:
        """Archive-wide count of media with no verdict row.

        The sidebar's per-creator `unclassified_count` is scoped to the active
        source filter, which is right for the sidebar and wrong for the navbar
        Classify All button: that job is archive-wide whatever is filtered, and
        reading the filtered sum made it claim "every creator is already
        classified" while another platform's backlog sat untouched.

        Anti-join on media_verdicts' primary key, so it is index probes rather
        than a second scan.
        """
        with self._lock:
            row = self._conn.execute(
                "SELECT COUNT(*) AS n FROM photos p "
                "LEFT JOIN media_verdicts v ON v.rel_path = p.rel_path "
                "WHERE v.rel_path IS NULL"
            ).fetchone()
        return int(row["n"] or 0)

    def query_photos(
        self,
        *,
        creator: Optional[str] = None,
        search: Optional[str] = None,
        unanalyzed: bool = False,
        favorite_only: bool = False,
        media_type: Optional[str] = None,
        verdict: Optional[str] = None,
        source: Optional[str] = None,
        sort: str = "name",
        limit: Optional[int] = None,
        offset: int = 0,
        reject_cut: Optional[int] = None,
    ) -> Tuple[List[Dict[str, Any]], int]:
        where: List[str] = []
        params: List[Any] = []
        # Every predicate is table-qualified because of the media_verdicts join:
        # both tables carry `rel_path` and `creator`, so a bare `creator = ?`
        # is an "ambiguous column" error rather than a silently wrong answer.
        if creator:
            where.append("p.creator = ?")
            params.append(creator)
        if source:
            where.append("p.source = ?")
            params.append(self._norm_platform(source))
        if favorite_only:
            where.append("p.favorite = 1")
        if unanalyzed:
            where.append("p.has_prompt = 0")
        if media_type == "video":
            video_likes = " OR ".join("LOWER(p.filename) LIKE ?" for _ in VIDEO_EXTENSIONS)
            where.append(f"({video_likes})")
            params.extend(f"%{ext}" for ext in VIDEO_EXTENSIONS)
        elif media_type == "photo":
            photo_likes = " OR ".join("LOWER(p.filename) LIKE ?" for _ in IMAGE_EXTENSIONS)
            where.append(f"({photo_likes})")
            params.extend(f"%{ext}" for ext in IMAGE_EXTENSIONS)

        cut = self._reject_cut(reject_cut)
        verdict_case = _VERDICT_CASE.format(cut=cut)
        if verdict in ("keep", "reject", "unclassified", "error"):
            where.append(f"{verdict_case} = ?")
            params.append(verdict)
        elif verdict == "unusable":
            # Tier 0 alone: no woman / man in frame / poster / unusable quality.
            # Split out from `reject` so a cautious cleanup pass can act on the
            # boundary that is a quality gate, not a taste call.
            where.append("v.manual IS NULL AND v.tier = 0")
        elif verdict == "modest":
            where.append("v.manual IS NULL AND v.tier = 1")

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
                # caption_search stays on LIKE even here: it is not in the FTS
                # index (that mirrors the prompts table), and it is the only
                # human-written text in the row — dropping it when the flag
                # flips would make search quietly worse, not faster.
                where.append(
                    "(LOWER(p.creator) LIKE ? OR LOWER(p.filename) LIKE ? "
                    "OR IFNULL(p.caption_search, '') LIKE ? OR p.rel_path IN "
                    "(SELECT rel_path FROM prompts_fts WHERE prompts_fts MATCH ?))"
                )
                params.extend([like, like, like, fts_query])
            else:
                where.append(
                    "(LOWER(p.creator) LIKE ? OR LOWER(p.filename) LIKE ? "
                    "OR IFNULL(p.caption_search, '') LIKE ? "
                    "OR IFNULL(p.prompt_search, '') LIKE ?)"
                )
                params.extend([like, like, like, like])

        where_sql = (" WHERE " + " AND ".join(where)) if where else ""
        sort = (sort or "name").lower()
        # newest/oldest = archive ingest time (download). Downloaders stamp
        # mtime to the remote post date, so mtime alone cannot mean "just got".
        # posted = Instagram/post chronology via mtime, falling back to added_at
        # (file birth/ctime at index time) when mtime is missing or zero.
        if sort == "newest":
            order = "ORDER BY IFNULL(p.added_at, p.mtime) DESC, p.filename ASC"
        elif sort == "oldest":
            order = "ORDER BY IFNULL(p.added_at, p.mtime) ASC, p.filename ASC"
        elif sort == "posted":
            order = (
                "ORDER BY CASE "
                "WHEN p.mtime IS NOT NULL AND p.mtime > 0 THEN p.mtime "
                "ELSE IFNULL(p.added_at, 0) END DESC, p.filename ASC"
            )
        elif sort == "posted_oldest":
            order = (
                "ORDER BY CASE "
                "WHEN p.mtime IS NOT NULL AND p.mtime > 0 THEN p.mtime "
                "ELSE IFNULL(p.added_at, 0) END ASC, p.filename ASC"
            )
        elif sort == "tier":
            # Harshest first: this is the review order, so the files most likely
            # to be deleted are the ones you see without scrolling. Errors (-1)
            # sort after tier 0 rather than before it — an unreadable file is a
            # retry, not a verdict.
            order = (
                "ORDER BY CASE WHEN v.tier IS NULL THEN 9 WHEN v.tier < 0 THEN 8 "
                "ELSE v.tier END ASC, p.filename ASC"
            )
        else:
            order = "ORDER BY p.creator ASC, p.filename ASC"

        join = " FROM photos p LEFT JOIN media_verdicts v ON v.rel_path = p.rel_path"
        select_cols = (
            "p.*, v.tier AS v_tier, v.manual AS v_manual, v.reason AS v_reason, "
            "v.media_kind AS v_media_kind, v.verdict_source AS v_source, "
            "v.confidence AS v_confidence, v.prompt_version AS v_prompt_version, "
            "v.sheet_path AS v_sheet_path, v.error AS v_error, "
            f"v.classified_at AS v_classified_at, {verdict_case} AS v_verdict"
        )
        with self._lock:
            total = self._conn.execute(
                f"SELECT COUNT(*) AS c{join}{where_sql}", params
            ).fetchone()["c"]
            sql = f"SELECT {select_cols}{join}{where_sql} {order}"
            page_params = list(params)
            if limit is not None:
                sql += " LIMIT ? OFFSET ?"
                page_params.extend([int(limit), max(0, int(offset))])
            rows = self._conn.execute(sql, page_params).fetchall()

        return [self._row_to_photo(r) for r in rows], int(total)
