"""SQLite photo catalog for fast gallery list/filter/sort."""

from __future__ import annotations

import json
import os
import re
import sqlite3
import threading
import time
import urllib.parse
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple

from promptstudio.config import (
    ARCHIVE_DB_FILE,
    CLASSIFY_REJECT_MAX_TIER,
    DISTRIBUTION_MAX_SHARE,
    EXCLUDED_FOLDERS,
    FTS_SEARCH,
    GENERATIONS_INDEX_FILE,
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


def _verdict_predicate(name: str, cut: int) -> Tuple[str, List[Any]]:
    """SQL for one verdict filter, as (clause, params). Unknown name => no-op.

    Shared by `query_photos` and `verdict_facet_counts` so a chip's pass-rate
    badge cannot end up describing a different filter than the chip runs.

    `unusable` (tier 0) and `modest` (tier 1) are the two halves of `reject`,
    split because the 0 boundary is a quality gate anyone would accept while
    the 1 boundary is a taste call that has never been measured. Both ignore
    rows the user has overridden by hand — a manual verdict is not the
    classifier's output and must not be counted as evidence about it.
    """
    case = _VERDICT_CASE.format(cut=cut)
    if name in ("keep", "reject", "unclassified", "error"):
        return f"{case} = ?", [name]
    if name == "unusable":
        return "v.manual IS NULL AND v.tier = 0", []
    if name == "modest":
        return "v.manual IS NULL AND v.tier = 1", []
    return "", []

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

# ComfyUI outputs. One row per output *file*, not per job: a workflow with a
# batch node emits several images and each is independently rateable.
#
# `seed` is NOT NULL on purpose. The defect this table exists to close
# (design_generation_loop.md §2.1) was a seed resolved inside the graph builder
# and never returned, so every unlocked generation recorded `seed: null` and was
# unreproducible. The schema now refuses the row rather than trusting the caller.
# Legacy rows imported from JSON carry -1, an honest "never recorded" that is
# distinguishable from a real seed.
#
# `positive_prompt` is the full text. The JSON index truncated to 500/300 chars,
# which is enough to display and not enough to reproduce.
_GENERATIONS_SCHEMA = """
CREATE TABLE IF NOT EXISTS generations (
  id               INTEGER PRIMARY KEY AUTOINCREMENT,
  gen_id           TEXT NOT NULL UNIQUE,
  rel_path         TEXT NOT NULL UNIQUE,
  source_rel       TEXT NOT NULL,
  creator          TEXT NOT NULL,
  created_at       TEXT NOT NULL,
  batch_id         TEXT,
  workflow         TEXT NOT NULL,
  checkpoint       TEXT,
  seed             INTEGER NOT NULL,
  steps            INTEGER,
  cfg              REAL,
  denoise          REAL,
  mode_e           INTEGER NOT NULL DEFAULT 0,
  positive_prompt  TEXT NOT NULL,
  negative_prompt  TEXT,
  prompt_version   TEXT,
  rating           INTEGER NOT NULL DEFAULT 0,
  rated_at         TEXT,
  error            TEXT
);
-- Composite, not created_at alone. Paging orders by (created_at, id) — the id
-- tiebreaker is what stops two rows sharing a timestamp from swapping between
-- pages and being served twice or not at all. Against a created_at-only index
-- SQLite walks the index and then builds a temp b-tree for the last ORDER BY
-- term, which measured 33.3ms on a 50k-row deep page against 1.93ms here.
-- (At the 1k rows §4 actually gates on, both are under a millisecond.)
CREATE INDEX IF NOT EXISTS idx_gen_created_id ON generations(created_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS idx_gen_source ON generations(source_rel);
CREATE INDEX IF NOT EXISTS idx_gen_rating ON generations(rating);
CREATE INDEX IF NOT EXISTS idx_gen_batch ON generations(batch_id);
-- Superseded by the composite above, whose leading column serves every query
-- the old one did. Dropped by name rather than redefined: CREATE INDEX IF NOT
-- EXISTS is a no-op on an existing DB, so reusing the name would silently
-- leave older archives on the slower shape.
DROP INDEX IF EXISTS idx_gen_created;
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
    # Wall-clock (or filesystem birth) when the file entered this archive.
    # Downloaders often set mtime to the Instagram post date, so mtime cannot
    # drive "newest downloaded first".
    ("added_at", "REAL"),
    # Creator-written text (caption, author) — see caption_search_blob.
    ("caption_search", "TEXT"),
)

DEFAULT_SOURCE = "instagram"

_PROMPTS_IMPORTED_KEY = "prompts_imported_from_json"
_GENERATIONS_IMPORTED_KEY = "generations_imported_from_json"

# -1 discard · 0 unrated · 1 keep · 2 star. Deliberately cheap to press: an
# expensive rating UI collects no data, and no data is the whole problem A3
# exists to fix.
GENERATION_RATINGS = (-1, 0, 1, 2)
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


def _ratio(num: int, den: int) -> Optional[float]:
    """None, not 0.0, when the denominator is empty — "nothing measured yet"
    and "measured as zero" are different answers.

    Mirrors `insights._rate` (same 4-place rounding); duplicated rather than
    imported because `insights` imports this module.
    """
    if den <= 0:
        return None
    return round(num / den, 4)


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
            self._conn.executescript(_GENERATIONS_SCHEMA)
            self._init_fts()
            self._migrate_identity_columns()
            self._migrate_deleted_posts()
            self._migrate_added_at()
            self._migrate_caption_search()
            self._conn.commit()
        # After the schema commit, not inside it: the import writes rows, and a
        # failure here must leave a usable index rather than an app that will
        # not start. Best-effort for the same reason `_init_fts` is.
        try:
            self.import_generations_from_json()
        except Exception:
            log.exception("legacy generations import failed; continuing")

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

    # ── generations ──────────────────────────────────────────────────

    def record_generation(
        self,
        *,
        rel_path: str,
        source_rel: str,
        creator: str,
        workflow: str,
        seed: int,
        positive_prompt: str,
        negative_prompt: str = "",
        gen_id: Optional[str] = None,
        created_at: Optional[str] = None,
        batch_id: Optional[str] = None,
        checkpoint: Optional[str] = None,
        steps: Optional[int] = None,
        cfg: Optional[float] = None,
        denoise: Optional[float] = None,
        mode_e: bool = False,
        prompt_version: Optional[str] = None,
        rating: int = 0,
        error: Optional[str] = None,
    ) -> str:
        """Record one output file. Returns its `gen_id`.

        `seed` is required and stored as an integer — passing None raises here
        rather than writing an unreproducible row (design_generation_loop.md
        §2.1). Use -1 to mean "never recorded", which is what the legacy JSON
        import writes.

        Re-recording the same `rel_path` overwrites: a regenerate that lands on
        the same filename is a correction, not a second output. `rating` is
        deliberately *not* overwritten on conflict — the user's verdict outlives
        a metadata rewrite.
        """
        if seed is None:
            raise ValueError("record_generation requires a resolved seed")
        rel = normalize_rel_path(rel_path)
        gid = gen_id or uuid.uuid4().hex
        stamp = created_at or datetime.now(timezone.utc).isoformat()
        with self._lock:
            self._conn.execute(
                "INSERT INTO generations("
                "gen_id, rel_path, source_rel, creator, created_at, batch_id, "
                "workflow, checkpoint, seed, steps, cfg, denoise, mode_e, "
                "positive_prompt, negative_prompt, prompt_version, rating, error"
                ") VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(rel_path) DO UPDATE SET "
                "source_rel=excluded.source_rel, creator=excluded.creator, "
                "created_at=excluded.created_at, batch_id=excluded.batch_id, "
                "workflow=excluded.workflow, checkpoint=excluded.checkpoint, "
                "seed=excluded.seed, steps=excluded.steps, cfg=excluded.cfg, "
                "denoise=excluded.denoise, mode_e=excluded.mode_e, "
                "positive_prompt=excluded.positive_prompt, "
                "negative_prompt=excluded.negative_prompt, "
                "prompt_version=excluded.prompt_version, error=excluded.error",
                (
                    gid,
                    rel,
                    normalize_rel_path(source_rel),
                    creator,
                    stamp,
                    batch_id,
                    workflow,
                    checkpoint,
                    int(seed),
                    None if steps is None else int(steps),
                    None if cfg is None else float(cfg),
                    None if denoise is None else float(denoise),
                    1 if mode_e else 0,
                    positive_prompt or "",
                    negative_prompt or "",
                    prompt_version,
                    int(rating),
                    error,
                ),
            )
            self._conn.commit()
        return gid

    def rate_generation(self, gen_id: str, rating: int) -> bool:
        """Set the user's verdict on one output. False if `gen_id` is unknown.

        One ordinal rather than a keep flag plus a star flag: the two would let
        "starred but not kept" exist, which means nothing.

        Returning to 0 clears `rated_at` — a timestamp beside "unrated" claims a
        judgement that was explicitly withdrawn, and `rated` counts key off
        `rating != 0`.
        """
        if isinstance(rating, bool) or not isinstance(rating, int):
            raise ValueError(f"rating must be an int in {GENERATION_RATINGS}")
        if rating not in GENERATION_RATINGS:
            raise ValueError(f"rating must be one of {GENERATION_RATINGS}")
        stamp = None if rating == 0 else datetime.now(timezone.utc).isoformat()
        with self._lock:
            cur = self._conn.execute(
                "UPDATE generations SET rating = ?, rated_at = ? WHERE gen_id = ?",
                (rating, stamp, gen_id),
            )
            self._conn.commit()
            return cur.rowcount > 0

    # Whitelist, because `sort` arrives from a query string and goes into an
    # ORDER BY, which cannot be parameterised. An unknown value falls back to
    # newest rather than erroring — a stale bookmark should not 500.
    _GEN_SORTS = {
        "newest": "created_at DESC, id DESC",
        "oldest": "created_at ASC, id ASC",
        "rating": "rating DESC, created_at DESC",
        "source": "source_rel ASC, created_at DESC",
    }

    def _generations_where(
        self,
        *,
        creator: Optional[str],
        workflow: Optional[str],
        checkpoint: Optional[str],
        batch_id: Optional[str],
        source_rel: Optional[str],
        rating: Optional[int],
        rated_only: bool,
        since: Optional[str],
    ) -> Tuple[str, List[Any]]:
        where: List[str] = []
        params: List[Any] = []
        for column, value in (
            ("creator", creator),
            ("workflow", workflow),
            ("checkpoint", checkpoint),
            ("batch_id", batch_id),
        ):
            if value:
                where.append(f"{column} = ?")
                params.append(value)
        if source_rel:
            where.append("source_rel = ?")
            params.append(normalize_rel_path(source_rel))
        # `rating=0` means "the unrated"; `rated_only` means "everything I have
        # judged". Two different questions, so `rating is not None` rather than
        # a truthiness test — `if rating:` would silently drop the 0 case.
        if rating is not None:
            where.append("rating = ?")
            params.append(int(rating))
        if rated_only:
            where.append("rating != 0")
        if since:
            where.append("created_at >= ?")
            params.append(since)
        return (" WHERE " + " AND ".join(where)) if where else "", params

    def list_generations(
        self,
        *,
        creator: Optional[str] = None,
        workflow: Optional[str] = None,
        checkpoint: Optional[str] = None,
        batch_id: Optional[str] = None,
        source_rel: Optional[str] = None,
        rating: Optional[int] = None,
        rated_only: bool = False,
        since: Optional[str] = None,
        sort: str = "newest",
        limit: Optional[int] = None,
        offset: int = 0,
    ) -> Tuple[List[Dict[str, Any]], int]:
        """Outputs for the A1 gallery. Returns `(rows, total)`.

        `total` is the count *before* paging, matching `query_photos` — the
        frontend derives `has_more` from it, so a page-sized total would stop
        infinite scroll after the first page.
        """
        where_sql, params = self._generations_where(
            creator=creator,
            workflow=workflow,
            checkpoint=checkpoint,
            batch_id=batch_id,
            source_rel=source_rel,
            rating=rating,
            rated_only=rated_only,
            since=since,
        )
        order = self._GEN_SORTS.get(sort or "newest", self._GEN_SORTS["newest"])
        sql = f"SELECT * FROM generations{where_sql} ORDER BY {order}"
        page_params = list(params)
        if limit is not None:
            sql += " LIMIT ? OFFSET ?"
            page_params.extend([int(limit), max(0, int(offset))])
        with self._lock:
            total = self._conn.execute(
                f"SELECT COUNT(*) AS c FROM generations{where_sql}", params
            ).fetchone()["c"]
            rows = self._conn.execute(sql, page_params).fetchall()
        return [dict(r) for r in rows], int(total)

    def explain_generations_query(self, sort: str = "newest") -> str:
        """Query plan for the default gallery page — the §4 pagination gate is
        a claim about the plan, so it is checkable as one."""
        order = self._GEN_SORTS.get(sort or "newest", self._GEN_SORTS["newest"])
        with self._lock:
            rows = self._conn.execute(
                f"EXPLAIN QUERY PLAN SELECT * FROM generations "
                f"ORDER BY {order} LIMIT 50 OFFSET 900"
            ).fetchall()
        return " | ".join(str(r["detail"]) for r in rows)

    def delete_generation(self, gen_id: str) -> Optional[str]:
        """Drop one generation row. Returns its `rel_path`, or None if unknown.

        The row only — unlinking the file is the caller's job, because the
        containment check for "is this path inside the archive" lives in
        `ArchiveStore` and must not be duplicated here (the A0 lesson).
        """
        with self._lock:
            row = self._conn.execute(
                "SELECT rel_path FROM generations WHERE gen_id = ?", (gen_id,)
            ).fetchone()
            if not row:
                return None
            self._conn.execute("DELETE FROM generations WHERE gen_id = ?", (gen_id,))
            self._conn.commit()
            return str(row["rel_path"])

    # Tables the E1 bundle may round-trip. A whitelist, not a free-form table
    # name: the value reaches an unparameterisable position in the SQL, and
    # `photos` is deliberately absent — it is rebuilt from the media on disk,
    # so restoring it would resurrect rows for files that are not there.
    EXPORTABLE_TABLES = ("prompts", "media_verdicts", "phashes", "generations")

    def dump_table(self, table: str) -> List[Dict[str, Any]]:
        """Every row of one derived table, as plain dicts."""
        if table not in self.EXPORTABLE_TABLES:
            raise ValueError(f"table {table!r} is not exportable")
        with self._lock:
            rows = self._conn.execute(f"SELECT * FROM {table}").fetchall()
        # `id` is a local autoincrement with no meaning on another machine, and
        # carrying it would collide on import into a non-empty table.
        return [{k: v for k, v in dict(r).items() if k != "id"} for r in rows]

    def load_table(self, table: str, rows: Sequence[Dict[str, Any]]) -> int:
        """Upsert rows into one derived table. Returns the number applied.

        Idempotent by construction — every exportable table has a natural key
        (`rel_path`, or `gen_id` for generations), so a re-run of a half-finished
        restore overwrites rather than duplicating.
        """
        if table not in self.EXPORTABLE_TABLES:
            raise ValueError(f"table {table!r} is not exportable")
        if not rows:
            return 0
        applied = 0
        with self._lock:
            existing = {
                r[1] for r in self._conn.execute(f"PRAGMA table_info({table})").fetchall()
            }
            for row in rows:
                # Ignore columns this build does not have: an older archive
                # importing a newer bundle should restore what it understands
                # rather than failing outright on one unknown field.
                cols = [c for c in row if c in existing]
                if not cols:
                    continue
                placeholders = ",".join("?" for _ in cols)
                self._conn.execute(
                    f"INSERT OR REPLACE INTO {table}({','.join(cols)}) "
                    f"VALUES ({placeholders})",
                    [row[c] for c in cols],
                )
                applied += 1
            self._conn.commit()
        if table == "prompts":
            self.reindex_all_prompts()
        return applied

    def reindex_all_prompts(self) -> None:
        """Re-derive the photos.has_prompt / prompt_search columns and the FTS
        mirror after a bulk prompt write.

        `load_table` bypasses `prompt_set`, which is what normally keeps those
        in step — without this a restored archive looks like it has no prompts.
        """
        with self._lock:
            rows = self._conn.execute(
                "SELECT rel_path, payload FROM prompts"
            ).fetchall()
            for row in rows:
                try:
                    entry = json.loads(row["payload"])
                except (ValueError, TypeError):
                    continue
                self._reindex_prompt(str(row["rel_path"]), entry)
            self._conn.commit()

    def generation_facets(self) -> Dict[str, List[str]]:
        """Distinct workflows / checkpoints / creators present, for filter UI.

        Built from the data rather than the registry: a checkpoint the user has
        since removed from ComfyUI still has outputs worth filtering to.
        """
        out: Dict[str, List[str]] = {}
        with self._lock:
            for key, column in (
                ("creators", "creator"),
                ("workflows", "workflow"),
                ("checkpoints", "checkpoint"),
            ):
                rows = self._conn.execute(
                    f"SELECT DISTINCT {column} AS v FROM generations "
                    f"WHERE {column} IS NOT NULL AND {column} != '' ORDER BY v"
                ).fetchall()
                out[key] = [str(r["v"]) for r in rows]
        return out

    def generation_rating_summary(self) -> Dict[str, Any]:
        """Volume + keep-rate aggregates for `GET /api/insights` (B1).

        One pass for the totals, then one grouped pass per cut. The cuts are
        what make the number actionable: a single archive-wide keep rate says
        the loop is or is not working, but not which half to change.
        """
        def _slice(rows: Sequence[sqlite3.Row]) -> Dict[str, Dict[str, Any]]:
            out: Dict[str, Dict[str, Any]] = {}
            for row in rows:
                rated = int(row["rated"] or 0)
                kept = int(row["kept"] or 0)
                out[str(row["k"])] = {
                    "total": int(row["total"] or 0),
                    "rated": rated,
                    "kept": kept,
                    "keep_rate": _ratio(kept, rated),
                }
            return out

        # `rated` counts a withdrawn verdict (0) as unrated, matching
        # rate_generation clearing rated_at when it returns to 0.
        agg = (
            "COUNT(*) AS total, "
            "SUM(CASE WHEN rating != 0 THEN 1 ELSE 0 END) AS rated, "
            "SUM(CASE WHEN rating >= 1 THEN 1 ELSE 0 END) AS kept"
        )
        with self._lock:
            totals = self._conn.execute(
                f"SELECT {agg}, "
                "SUM(CASE WHEN rating = -1 THEN 1 ELSE 0 END) AS discarded, "
                "SUM(CASE WHEN rating = 2 THEN 1 ELSE 0 END) AS starred, "
                "SUM(CASE WHEN seed < 0 THEN 1 ELSE 0 END) AS unreproducible, "
                "COUNT(DISTINCT source_rel) AS sources "
                "FROM generations"
            ).fetchone()
            multi = self._conn.execute(
                "SELECT COUNT(*) AS c FROM ("
                "SELECT source_rel FROM generations "
                "GROUP BY source_rel HAVING COUNT(*) > 1)"
            ).fetchone()
            cuts = {}
            for name, expr in (
                ("by_prompt_version", "COALESCE(prompt_version, 'unknown')"),
                ("by_workflow", "COALESCE(workflow, 'unknown')"),
                ("by_checkpoint", "COALESCE(checkpoint, 'unknown')"),
                ("by_mode_e", "CASE WHEN mode_e = 1 THEN 'on' ELSE 'off' END"),
            ):
                cuts[name] = _slice(
                    self._conn.execute(
                        f"SELECT {expr} AS k, {agg} FROM generations GROUP BY k"
                    ).fetchall()
                )

        total = int(totals["total"] or 0)
        rated = int(totals["rated"] or 0)
        kept = int(totals["kept"] or 0)
        sources = int(totals["sources"] or 0)
        return {
            "total_outputs": total,
            "sources_with_gens": sources,
            "sources_with_multiple": int(multi["c"] or 0),
            "avg_per_source": round(total / sources, 3) if sources else 0.0,
            "rated": rated,
            "kept": kept,
            "discarded": int(totals["discarded"] or 0),
            "starred": int(totals["starred"] or 0),
            "keep_rate": _ratio(kept, rated),
            # Legacy rows imported with seed = -1. Success criterion #1 is
            # "100% of new rows reproducible" and nothing else measures it.
            "unreproducible": int(totals["unreproducible"] or 0),
            **cuts,
        }

    def import_generations_from_json(
        self, path: str = GENERATIONS_INDEX_FILE
    ) -> int:
        """One-time import of the pre-A0 `generations_index.json`.

        Returns the number of output files imported. Guarded by a meta key, the
        same shape as the prompts import — `ArchiveIndex` is constructed per
        process and this would otherwise re-run on every start.

        Legacy records carry `seed: null` because the seed was resolved inside
        the graph builder and never returned (§2.1). That value is gone and
        cannot be recovered, so those rows get **-1**: an honest "never
        recorded" that A1 can render as "seed not recorded" and refuse to
        regenerate from. Inventing a plausible seed would make the
        regenerate button confidently wrong.

        A malformed record is skipped, not fatal — the file is hand-editable
        and was written by a path that could die mid-run, and abandoning the
        import would lose the well-formed records after it.
        """
        if self._meta_get(_GENERATIONS_IMPORTED_KEY) == "1":
            return 0
        if not os.path.isfile(path):
            return 0
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError) as e:
            log.warning("generations index unreadable, skipping import: %s", e)
            return 0
        if not isinstance(data, dict):
            return 0

        imported = 0
        for source_rel, records in data.items():
            if not isinstance(records, list):
                continue
            for rec in records:
                if not isinstance(rec, dict):
                    continue
                files = rec.get("files")
                if not isinstance(files, list):
                    continue
                raw_seed = rec.get("seed")
                seed = -1 if raw_seed is None else int(raw_seed)
                for item in files:
                    if not isinstance(item, dict):
                        continue
                    rel = item.get("rel_path")
                    if not rel:
                        continue
                    rel = normalize_rel_path(str(rel))
                    # _generations/<creator>/<file> — fall back to the source's
                    # first segment for records written before that layout.
                    parts = rel.split("/")
                    creator = parts[1] if len(parts) > 2 else (
                        normalize_rel_path(source_rel).split("/", 1)[0]
                    )
                    try:
                        self.record_generation(
                            rel_path=rel,
                            source_rel=source_rel,
                            creator=creator,
                            workflow=str(rec.get("workflow") or "pro"),
                            seed=seed,
                            positive_prompt=rec.get("positive_prompt") or "",
                            negative_prompt=rec.get("negative_prompt") or "",
                            created_at=rec.get("created_at"),
                            checkpoint=rec.get("checkpoint"),
                            steps=rec.get("steps"),
                            cfg=rec.get("cfg"),
                            denoise=rec.get("denoise"),
                        )
                        imported += 1
                    except (ValueError, sqlite3.DatabaseError) as e:
                        log.warning("skipping legacy generation %s: %s", rel, e)

        self._meta_set(_GENERATIONS_IMPORTED_KEY, "1")
        log.info("imported %d generation(s) from %s", imported, path)
        return imported

    def list_generations_for(self, source_rel: str) -> List[Dict[str, Any]]:
        """Every generation from one source photo, newest first — unbounded.

        The JSON index it replaces kept `items[:20]` and dropped the rest
        silently, which is data loss that only becomes visible once something
        renders the history.
        """
        rel = normalize_rel_path(source_rel)
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM generations WHERE source_rel = ? "
                "ORDER BY created_at DESC, id DESC",
                (rel,),
            ).fetchall()
        return [dict(r) for r in rows]

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

    def verdict_facet_counts(self, *, cut: Optional[int] = None) -> Dict[str, Any]:
        """Share of the archive each verdict filter selects — the B4 pass rate.

        One grouped pass over `photos` for every filter, not one COUNT per
        chip: the review strip has five and the browse dropdown seven, and a
        round trip each is how a badge meant to be glanced at turns into a
        reason not to render it. The predicates come from
        `_verdict_predicate`, the same source `query_photos` filters with.

        **Archive-wide, never scoped** — same stance as `unclassified_total`,
        for a different reason. Saturation is a property of the classifier
        over everything it has judged; a share that moved as the user clicked
        between creators or platforms could not be compared against the 60%
        rule at all.

        `shares` are None on an empty archive: nothing measured yet is not the
        same answer as measured at zero.

        Measured (rule 13) at 20k photos / 16k verdicts: **12.6 ms**, taking
        `stats()` from ~15 ms to ~28 ms. Six `SUM(CASE …)` over one join scan —
        a COUNT per chip would pay that scan six times over.
        """
        cut_v = self._reject_cut(cut)
        selects = ["COUNT(*) AS total"]
        params: List[Any] = []
        for name in VERDICT_FILTERS:
            clause, clause_params = _verdict_predicate(name, cut_v)
            selects.append(f"SUM(CASE WHEN {clause} THEN 1 ELSE 0 END) AS n_{name}")
            params.extend(clause_params)

        with self._lock:
            row = self._conn.execute(
                "SELECT " + ", ".join(selects) + " FROM photos p "
                "LEFT JOIN media_verdicts v ON v.rel_path = p.rel_path",
                params,
            ).fetchone()

        total = int(row["total"] or 0)
        counts = {name: int(row[f"n_{name}"] or 0) for name in VERDICT_FILTERS}
        return {
            "total": total,
            "reject_max_tier": cut_v,
            # Served rather than hardcoded in app.js: the badge, /api/insights
            # and the pytest gate all have to mean the same 0.6.
            "warn_above": DISTRIBUTION_MAX_SHARE,
            "counts": counts,
            "shares": {name: _ratio(n, total) for name, n in counts.items()},
        }

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

    def stats(self) -> Dict[str, Any]:
        """Gallery counters for /api/stats — all indexed, no filesystem walk.

        `prompts_ready` reads the `has_prompt` column (idx_photos_prompt), which
        PromptCache maintains write-through via update_prompt_flags. The old
        count_prompts_ready() iterated every photo in the archive and loaded the
        whole prompt cache on each call, and /api/stats is on the init path.

        `verdict_facets` rides along rather than getting its own route: the
        pass-rate badges want the same refresh points this already has (app
        init, and the end of a classify run), and B4 is only useful if the
        number is on screen without asking for it.
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
            "verdict_facets": self.verdict_facet_counts(),
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
        if verdict:
            # Same predicate the pass-rate badge counts with — see
            # `_verdict_predicate`. Tier 0 alone is "no woman / man in frame /
            # poster / unusable quality", split from `reject` so a cautious
            # cleanup pass can act on the boundary that is a quality gate
            # rather than a taste call.
            clause, clause_params = _verdict_predicate(verdict, cut)
            if clause:
                where.append(clause)
                params.extend(clause_params)

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
