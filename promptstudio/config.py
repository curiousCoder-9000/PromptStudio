"""Central configuration for PromptStudio.

All secrets and machine-specific paths come from environment variables
(or a local `.env` file — never commit `.env`). See `.env.example`.
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path


def _load_dotenv() -> None:
    """Load repo-root `.env` if python-dotenv is installed."""
    root = Path(__file__).resolve().parent.parent
    env_path = root / ".env"
    try:
        from dotenv import load_dotenv
    except ImportError:
        # Minimal fallback: KEY=VALUE lines, no export/quotes fancy parsing
        if not env_path.is_file():
            return
        try:
            for line in env_path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, val = line.partition("=")
                key = key.strip()
                val = val.strip().strip('"').strip("'")
                if key and key not in os.environ:
                    os.environ[key] = val
        except OSError:
            pass
        return
    load_dotenv(env_path, override=False)


_load_dotenv()


def _env_bool(name: str, default: str = "0") -> bool:
    return os.environ.get(name, default).lower() in ("1", "true", "yes", "on")


def _env_csv(name: str, default: str) -> list[str]:
    raw = os.environ.get(name, default)
    return [k.strip() for k in raw.split(",") if k.strip()]


def _env_num(name: str, default: float, cast=float):
    """Numeric knob where **blank also means the default** (rule 14's trap).

    `int(os.environ.get(NAME, "1"))` raises on a set-but-empty var, which is
    exactly what `.env.example` shipping `NAME=` produces.
    """
    raw = (os.environ.get(name) or "").strip()
    if not raw:
        return cast(default)
    try:
        return cast(raw)
    except ValueError:
        return cast(default)


# Server
PORT = int(os.environ.get("PROMPTSTUDIO_PORT", "5000"))

LOOPBACK_HOSTS = ("127.0.0.1", "::1", "localhost")


def resolve_host(raw: str | None) -> str:
    """Bind address, defaulting to loopback — including when the value is blank.

    There is no auth and CORS is "*", so a non-loopback bind hands the whole
    archive, and `DELETE /api/photo`, to every host that can reach the port.
    The empty case matters: `.env.example` shipped `PROMPTSTUDIO_HOST=`, and
    `os.environ.get(name, "127.0.0.1")` returns `""` for a set-but-empty var
    rather than the default — and `""` binds INADDR_ANY. Set the var explicitly
    to expose the server on purpose.
    """
    return (raw or "").strip() or "127.0.0.1"


HOST = resolve_host(os.environ.get("PROMPTSTUDIO_HOST"))

# Local image archive (never store personal media inside the git repo)
DEFAULT_ARCHIVE_DIR = "~/Pictures/InstagramSaved"
ARCHIVE_DB_NAME = "archive.db"


def resolve_archive_dir(raw: str | None) -> str:
    """Archive root, defaulting when the value is unset **or blank**.

    Same trap as `resolve_host`: a set-but-empty `PROMPTSTUDIO_ARCHIVE=` makes
    `os.environ.get(name, default)` return `""`, and `""` expands to the
    process working directory — which for a scrape means writing someone's
    media into the git repo. Named so anything that needs to resolve an
    archive other than this process's own (the E5a distribution guard reads
    the developer's real archive while pytest runs against a temp one) does it
    the same way instead of hardcoding the default.
    """
    return os.path.expanduser((raw or "").strip() or DEFAULT_ARCHIVE_DIR)


def archive_db_file(archive_dir: str | None = None) -> str:
    return os.path.join(resolve_archive_dir(archive_dir), ARCHIVE_DB_NAME)


SAVED_DIR = resolve_archive_dir(os.environ.get("PROMPTSTUDIO_ARCHIVE"))

# Instagram / Instaloader — no default username (must set for scrape features)
SESSION_USER = (
    os.environ.get("INSTAGRAM_SESSION_USER")
    or os.environ.get("IG_SESSION_USER")
    or ""
).strip()
_default_session_dir = (
    "~/AppData/Local/Instaloader"
    if os.name == "nt"
    else "~/.config/instaloader"
)
INSTALOADER_SESSION_DIR = os.path.expanduser(
    os.environ.get("INSTALOADER_SESSION_DIR", _default_session_dir)
)
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FOLLOWING_LIST_FILE = os.path.join(_REPO_ROOT, "following_list.json")

# Archive index files (under the archive directory)
PROMPT_CACHE_FILE = os.path.join(SAVED_DIR, "prompts_cache.json")
SYNC_STATUS_FILE = os.path.join(SAVED_DIR, "sync_status.json")
SYNC_STATE_FILE = os.path.join(SAVED_DIR, "sync_state.json")
FOLLOWING_QUEUE_FILE = os.path.join(SAVED_DIR, "following_queue.json")
CREATOR_SCRAPE_QUEUE_FILE = os.path.join(SAVED_DIR, "creator_scrape_queue.json")
ARCHIVE_DB_FILE = os.path.join(SAVED_DIR, ARCHIVE_DB_NAME)
# Written when Instagram flags automation; start_job / InstagramSource refuse
# until `until`. Not secrets — just a timestamp.
IG_COOLDOWN_FILE = os.path.join(SAVED_DIR, "ig_cooldown.json")

# Full-scrape ceiling (downloaded media units). Hard-capped separately so a
# UI/API "full" request cannot walk thousands of posts in one sitting.
FULL_SCRAPE_MAX_POSTS = int(_env_num("IG_FULL_SCRAPE_MAX_POSTS", 80, int))
CREATOR_SCRAPE_HISTORY_MAX = int(os.environ.get("IG_SCRAPE_HISTORY_MAX", "50"))
CREATOR_SCRAPE_MAX_PENDING = int(os.environ.get("IG_SCRAPE_MAX_PENDING", "50"))
CREATOR_SCRAPE_QUEUE_ENABLED = _env_bool("IG_CREATOR_SCRAPE_QUEUE", "1")
AUTO_DRAIN_ON_START = _env_bool("IG_AUTO_DRAIN_ON_START", "1")
AUTO_DRAIN_ON_START_DELAY_SEC = float(os.environ.get("IG_AUTO_DRAIN_DELAY_SEC", "2"))
REBUILD_INDEX = _env_bool("PROMPTSTUDIO_REBUILD_INDEX", "")
CREATOR_STYLES_FILE = os.path.join(SAVED_DIR, "creator_styles.json")
FAVORITES_FILE = os.path.join(SAVED_DIR, "favorites.json")
THUMB_DIR = os.path.join(SAVED_DIR, "_thumbs")
PROMPT_HISTORY_MAX = int(os.environ.get("PROMPT_HISTORY_MAX", "3"))

# Soft delete — UI deletes move media to _trash/ so they can be restored.
# Set PROMPTSTUDIO_TRASH=0 to go back to immediate unlink.
TRASH_ENABLED = _env_bool("PROMPTSTUDIO_TRASH", "1")
TRASH_DIR = os.path.join(SAVED_DIR, "_trash")
TRASH_RETENTION_DAYS = int(os.environ.get("PROMPTSTUDIO_TRASH_DAYS", "30"))

# Scraping behaviour (anti-ban pacing)
DOWNLOAD_DELAY_SEC = float(os.environ.get("IG_DOWNLOAD_DELAY", "2.5"))
POST_DELAY_MIN_SEC = float(os.environ.get("IG_POST_DELAY_MIN", "4"))
POST_DELAY_MAX_SEC = float(os.environ.get("IG_POST_DELAY_MAX", "12"))
ACCOUNT_PAUSE_MIN_SEC = float(os.environ.get("IG_ACCOUNT_PAUSE_MIN", "30"))
ACCOUNT_PAUSE_MAX_SEC = float(os.environ.get("IG_ACCOUNT_PAUSE_MAX", "120"))
BATCH_PAUSE_EVERY = int(os.environ.get("IG_BATCH_EVERY", "10"))
BATCH_PAUSE_MIN_SEC = float(os.environ.get("IG_BATCH_PAUSE_MIN", str(5 * 60)))
BATCH_PAUSE_MAX_SEC = float(os.environ.get("IG_BATCH_PAUSE_MAX", str(15 * 60)))
ABORT_RATE_LIMIT_STREAK = int(os.environ.get("IG_ABORT_RATE_LIMIT_STREAK", "3"))
CATCH_UP_STREAK = int(os.environ.get("IG_CATCH_UP_STREAK", "3"))
DEFAULT_ACCOUNTS_PER_DAY = int(_env_num("IG_ACCOUNTS_PER_DAY", 8, int))
RATE_LIMIT_BACKOFF_SEC = int(os.environ.get("IG_RATE_LIMIT_BACKOFF", "60"))
RATE_LIMIT_BACKOFF_MAX_SEC = int(os.environ.get("IG_RATE_LIMIT_BACKOFF_MAX", "300"))
DEFAULT_MAX_POSTS_PER_CREATOR = int(_env_num("IG_MAX_POSTS", 24, int))
DEFAULT_MIN_MEDIA_COUNT = int(os.environ.get("IG_MIN_MEDIA_COUNT", "5"))
INCLUDE_VIDEOS_DEFAULT = _env_bool("IG_INCLUDE_VIDEOS", "0")
QUEUE_PRIORITY_DEFAULT = int(os.environ.get("IG_QUEUE_PRIORITY_DEFAULT", "10"))
POST_RANK_ENABLED = _env_bool("IG_POST_RANK", "1")
POST_SCAN_FACTOR = float(os.environ.get("IG_POST_SCAN_FACTOR", "3"))

# Instagram fetch backend. Instaloader stays the default so existing session
# files keep working; gallery-dl + browser cookies is the practical way around
# web_profile_info 429s. Read live so tests can flip IG_BACKEND without reload.
_IG_BACKEND_ALIASES = {
    "instaloader": "instaloader",
    "il": "instaloader",
    "gallery-dl": "gallery-dl",
    "gallerydl": "gallery-dl",
    "gdl": "gallery-dl",
}


def instagram_backend() -> str:
    """`instaloader` or `gallery-dl`. Unknown values fall back to instaloader."""
    raw = (os.environ.get("IG_BACKEND") or "instaloader").strip().lower()
    return _IG_BACKEND_ALIASES.get(raw, "instaloader")


def instagram_cookies_file() -> str:
    """Netscape cookies.txt for the gallery-dl Instagram backend."""
    return os.path.expanduser((os.environ.get("IG_COOKIES_FILE") or "").strip())


def scrape_cookies_from_browser() -> str:
    """`--cookies-from-browser` value (brave, chrome, chrome:Default, …)."""
    return (os.environ.get("SCRAPE_COOKIES_FROM_BROWSER") or "").strip()


def instagram_cookies_info() -> dict:
    """Health/status snapshot — never includes cookie values."""
    path = instagram_cookies_file()
    if path and os.path.isfile(path):
        return {"mode": "file", "ready": True}
    browser = scrape_cookies_from_browser()
    if browser:
        return {"mode": "browser", "browser": browser, "ready": True}
    return {"mode": "none", "ready": False}


# gallery-dl's own Instagram sleep-request default is 6–12s. The shared
# SCRAPE_SLEEP_REQUEST=1.5 is for X/Reddit and is too fast for this account.
def ig_gdl_sleep_sec() -> float:
    return float(_env_num("IG_GDL_SLEEP", 3.0))


def ig_gdl_sleep_request_sec() -> float:
    return float(_env_num("IG_GDL_SLEEP_REQUEST", 8.0))


def ig_gdl_sleep_429_sec() -> float:
    return float(_env_num("IG_GDL_SLEEP_429", 180.0))


def ig_posts_hard_cap() -> int:
    """Max Instagram posts one run may request. 0 disables the cap."""
    return int(_env_num("IG_POSTS_HARD_CAP", 80, int))


def clamp_ig_posts(n: int) -> int:
    """Fold a requested ceiling onto the Instagram hard cap.

    `<=0` means "caller did not set a limit" and becomes the cap (or 0 if the
    cap itself is disabled). A positive request is min()'d against the cap.
    """
    try:
        requested = int(n)
    except (TypeError, ValueError):
        requested = 0
    cap = ig_posts_hard_cap()
    if requested <= 0:
        return cap
    if cap > 0:
        return min(requested, cap)
    return requested


def ig_cooldown_hours() -> float:
    """How long to sit out after Instagram flags automation."""
    return float(_env_num("IG_COOLDOWN_HOURS", 72.0))

# ---------------------------------------------------------------------------
# Multi-source scraping (X / Reddit via gallery-dl)
# ---------------------------------------------------------------------------
# Archive folders stay one level deep (storage/db.py parses creator as the first
# path segment), so non-Instagram sources are disambiguated by folder *suffix*
# instead of a nested <source>/<creator> path. Instagram keeps bare handles so
# the existing archive is untouched.
FOLDER_SUFFIX_NON_DEFAULT = _env_bool("SCRAPE_FOLDER_SUFFIX", "1")
FOLDER_SUFFIX_SEP = os.environ.get("SCRAPE_FOLDER_SUFFIX_SEP", "__")

# gallery-dl binary. Left as a bare name so a venv/PATH install just works.
# On Windows a pip --user install often puts gallery-dl.exe in
# %APPDATA%\Python\PythonXY\Scripts, which is not on PATH — resolve at
# spawn time via resolve_gallery_dl_cmd() rather than assuming which().
GALLERY_DL_BIN = os.environ.get("GALLERY_DL_BIN", "gallery-dl")


def resolve_gallery_dl_cmd(configured: str | None = None) -> list[str]:
    """Argv prefix that actually launches gallery-dl.

    A custom `GALLERY_DL_BIN` (or a test fake) is used as-is. The default
    name `gallery-dl` is resolved in order: on Windows, same-interpreter
    `python -m gallery_dl` (the pip `gallery-dl.exe` shim dies with
    STATUS_DLL_INIT_FAILED / 0xC0000142 when spawned from a background
    thread); then PATH, this interpreter's Scripts dir, the Windows user
    Scripts dir, then `python -m gallery_dl` if the package is importable.
    """
    raw = (configured if configured is not None else GALLERY_DL_BIN) or "gallery-dl"
    raw = str(raw).strip() or "gallery-dl"
    if raw not in ("gallery-dl", "gallery_dl"):
        return [os.path.expanduser(raw)]

    # Prefer the module form on Windows before any .exe. The distlib/pip
    # console launcher is a separate process that has to init a console
    # and load pythonXY.dll; from a scrape-lane thread that fails as
    # 0xC0000142, which we used to misread as "no extractor".
    if os.name == "nt":
        try:
            import gallery_dl
        except ImportError:
            pass
        else:
            return [sys.executable, "-m", "gallery_dl"]

    found = shutil.which("gallery-dl") or shutil.which("gallery-dl.exe")
    if found:
        return [found]

    exe_name = "gallery-dl.exe" if os.name == "nt" else "gallery-dl"
    py_dir = Path(sys.executable).resolve().parent
    appdata = Path(os.environ.get("APPDATA") or "")
    py_tag = f"Python{sys.version_info.major}{sys.version_info.minor}"
    for candidate in (
        py_dir / "Scripts" / exe_name,
        py_dir / exe_name,
        appdata / "Python" / py_tag / "Scripts" / exe_name,
    ):
        if candidate.is_file():
            return [str(candidate)]

    try:
        import gallery_dl  # noqa: F401
    except ImportError:
        return ["gallery-dl"]
    return [sys.executable, "-m", "gallery_dl"]


GALLERY_DL_TIMEOUT_SEC = int(os.environ.get("GALLERY_DL_TIMEOUT", str(2 * 60 * 60)))
# Optional cookie files (Netscape format). X needs one; Reddit does not.
GALLERY_DL_COOKIES_X = os.path.expanduser(os.environ.get("X_COOKIES_FILE", ""))
GALLERY_DL_COOKIES_REDDIT = os.path.expanduser(
    os.environ.get("REDDIT_COOKIES_FILE", "")
)
# Or pull cookies straight from a browser profile: "firefox", "chrome:Default".
GALLERY_DL_COOKIES_FROM_BROWSER = os.environ.get("SCRAPE_COOKIES_FROM_BROWSER", "")
# Extra raw gallery-dl args, whitespace-split (escape hatch for per-site tuning).
GALLERY_DL_EXTRA_ARGS = os.environ.get("GALLERY_DL_EXTRA_ARGS", "")

# Pacing. Defaults are deliberately gentler than gallery-dl's own so a first run
# on a cookie-authenticated account doesn't look like a scraper.
SCRAPE_SLEEP_SEC = float(os.environ.get("SCRAPE_SLEEP", "2.0"))
SCRAPE_SLEEP_REQUEST_SEC = float(os.environ.get("SCRAPE_SLEEP_REQUEST", "1.5"))
SCRAPE_SLEEP_429_SEC = float(os.environ.get("SCRAPE_SLEEP_429", "90"))
SCRAPE_RETRIES = int(os.environ.get("SCRAPE_RETRIES", "3"))
# gallery-dl's own catch-up: stop after N consecutive already-downloaded files.
SCRAPE_ABORT_AFTER_KNOWN = int(os.environ.get("SCRAPE_ABORT_AFTER_KNOWN", "0"))

# ---------------------------------------------------------------------------
# Per-lane pacing (docs/design_scrape_lanes.md §8)
# ---------------------------------------------------------------------------
# The IG_* pauses above are Instagram anti-ban constants: 30-120s between
# creators and a 5-15 minute pause every 10 jobs. Non-Instagram sources used to
# inherit them because there was one global worker — pure dead time, since
# gallery-dl already self-paces via SCRAPE_SLEEP / SCRAPE_SLEEP_REQUEST.
#
# Each knob is overridable per source with an explicit env var, so a lane that
# does start getting rate-limited can be slowed without touching Instagram:
#   SCRAPE_ACCOUNT_PAUSE_MIN_X=20  SCRAPE_BATCH_EVERY_REDDIT=25
_GALLERY_DL_LANE_PACING = {
    "account_pause_min": 2.0,
    "account_pause_max": 6.0,
    "batch_every": 0,          # 0 disables the long batch pause entirely
    "batch_pause_min": 0.0,
    "batch_pause_max": 0.0,
}

_INSTAGRAM_LANE_PACING = {
    "account_pause_min": ACCOUNT_PAUSE_MIN_SEC,
    "account_pause_max": ACCOUNT_PAUSE_MAX_SEC,
    "batch_every": BATCH_PAUSE_EVERY,
    "batch_pause_min": BATCH_PAUSE_MIN_SEC,
    "batch_pause_max": BATCH_PAUSE_MAX_SEC,
}


def _lane_pacing(source: str, key: str) -> float:
    """One pacing value for one lane, env override winning.

    Instagram keeps the IG_* defaults untouched. Everything else gets the
    gentle gallery-dl profile unless explicitly overridden.
    """
    name = (source or "instagram").strip().lower() or "instagram"
    override = os.environ.get(f"SCRAPE_{key.upper()}_{name.upper()}")
    if override not in (None, ""):
        try:
            return float(override)
        except ValueError:
            pass
    table = _INSTAGRAM_LANE_PACING if name == "instagram" else _GALLERY_DL_LANE_PACING
    return float(table[key])


def account_pause_range_for(source: str) -> tuple:
    """(min, max) seconds to wait between two jobs in the same lane."""
    lo = max(0.0, _lane_pacing(source, "account_pause_min"))
    return lo, max(lo, _lane_pacing(source, "account_pause_max"))


def batch_pause_range_for(source: str) -> tuple:
    lo = max(0.0, _lane_pacing(source, "batch_pause_min"))
    return lo, max(lo, _lane_pacing(source, "batch_pause_max"))


def batch_pause_every_for(source: str) -> int:
    """Jobs between long batch pauses. 0 disables them for this lane."""
    return int(_lane_pacing(source, "batch_every"))

# X: pull the media timeline (photos/videos only) rather than the full timeline.
X_MEDIA_TIMELINE_ONLY = _env_bool("X_MEDIA_TIMELINE_ONLY", "1")
X_INCLUDE_RETWEETS = _env_bool("X_INCLUDE_RETWEETS", "0")

# Public-safe fashion/model defaults — override in .env for personal filters
DEFAULT_CAPTION_KEYWORDS = _env_csv(
    "IG_CAPTION_KEYWORDS",
    "model,fashion,beauty,portrait,style,fitness,cosplay,editorial,swimwear,runway,glamour",
)
DEFAULT_BIO_KEYWORDS = _env_csv(
    "IG_BIO_KEYWORDS",
    "model,influencer,fitness,glamour,actress,swimwear,cosplay,fashion,beauty,photographer",
)
# ── Media keep/reject classifier (scraping/media_classifier.py) ─────────
# Ollama request shape. One vision call per photo; one per reel contact sheet.
CLASSIFY_MAX_EDGE = int(os.environ.get("CLASSIFY_MAX_EDGE", "768"))
# Contact sheets carry 9 panels, so they need a larger edge than a single frame.
CLASSIFY_SHEET_MAX_EDGE = int(os.environ.get("CLASSIFY_SHEET_MAX_EDGE", "1368"))
CLASSIFY_NUM_CTX = int(os.environ.get("CLASSIFY_NUM_CTX", "8192"))
CLASSIFY_NUM_PREDICT = int(os.environ.get("CLASSIFY_NUM_PREDICT", "400"))
CLASSIFY_TIMEOUT = float(os.environ.get("CLASSIFY_TIMEOUT", "180"))
CLASSIFY_RETRIES = int(os.environ.get("CLASSIFY_RETRIES", "2"))
CLASSIFY_KEEP_ALIVE = os.environ.get("CLASSIFY_KEEP_ALIVE", "30m")
# JSON-schema constrained decoding (Ollama `format`). Off => legacy regex scrape.
CLASSIFY_STRUCTURED = _env_bool("CLASSIFY_STRUCTURED", "1")
# Tiers 0..N are rejects; N+1..4 are keeps. The 0-4 tier is what gets persisted,
# so moving this re-thresholds the whole archive with no re-classify. Default 1
# = discard the unusable (tier 0) *and* the fully-modest (tier 1). Set to 0 for
# a cleanup-only pass: the 1<->2 boundary has never been measured, and the one
# boundary that was (2<->3) came back at 0.576 recall.
CLASSIFY_REJECT_MAX_TIER = int(os.environ.get("CLASSIFY_REJECT_MAX_TIER", "1"))
# Reel contact sheets are kept on disk so the review UI can show what the model
# actually looked at. `_classify` is in EXCLUDED_FOLDERS, so they stay out of
# the gallery, the creator list and every rebuild.
CLASSIFY_SHEET_DIR = os.path.join(SAVED_DIR, "_classify")

# ── B4 distribution guard (platform rule, not a classifier one-off) ──
#
# A bucket holding more than this share of a distribution makes every filter
# built on it close to a no-op: the previous classifier shipped with 85% of the
# archive on one tier and the Sexy filter admitting ~92%, and nothing noticed
# for three prompt versions. One number, read by the pass-rate badges
# (`ArchiveIndex.verdict_facet_counts`), by `/api/insights`, and by the
# `tests/test_distribution_guard.py` gate — so the UI warning and the failing
# check can never disagree about where the line is.
DISTRIBUTION_MAX_SHARE = _env_num("DISTRIBUTION_MAX_SHARE", 0.6)
# Below these counts the guard reports "not measured" instead of a verdict.
# Classified: a classify run walks the archive creator-by-creator
# (`list_unclassified` orders by creator, then filename), so its first slice is
# one or two creators and a single creator's style can saturate a tier without
# the classifier being wrong. 100 spans several creators, and puts the 60% line
# about ±10 points outside sampling noise instead of ±25.
DISTRIBUTION_MIN_CLASSIFIED = _env_num("DISTRIBUTION_MIN_CLASSIFIED", 100, int)
# Rated generations: these are entered by hand, one keypress at a time, so the
# classified threshold would keep the generation half of the rule inert for
# months — which is how a guard quietly becomes decorative. 30 is roughly one
# rating sitting, and on the 3-value scale (discard / keep / star) a uniform
# rater trips 60% about 0.2% of the time.
DISTRIBUTION_MIN_RATED = _env_num("DISTRIBUTION_MIN_RATED", 30, int)

# Video frame selection — used by `scraping/video_frames.py` for the classifier,
# for video thumbnails (`storage/thumbs.py`) and for near-duplicate detection
# (`storage/dedupe.py`).
CLASSIFY_REEL_CANDIDATES = int(os.environ.get("CLASSIFY_REEL_CANDIDATES", "16"))
# Vision calls a single reel may spend: the sheet, plus at most one confirm pass.
CLASSIFY_REEL_VISION_MAX = int(os.environ.get("CLASSIFY_REEL_VISION_MAX", "2"))
CLASSIFY_REEL_SKIP_HEAD_FRAC = float(os.environ.get("CLASSIFY_REEL_SKIP_HEAD_FRAC", "0.02"))
# 0.0 on purpose: the interesting frame is often in the final seconds.
CLASSIFY_REEL_SKIP_TAIL_FRAC = float(os.environ.get("CLASSIFY_REEL_SKIP_TAIL_FRAC", "0.0"))
CLASSIFY_REEL_MIN_BRIGHT = float(os.environ.get("CLASSIFY_REEL_MIN_BRIGHT", "22"))
CLASSIFY_REEL_MIN_SHARP = float(os.environ.get("CLASSIFY_REEL_MIN_SHARP", "35"))
# "Sharp enough" reference — frames at/above this stop earning extra rank, so an
# adequately sharp frame is not beaten by a razor-sharp static intro.
CLASSIFY_REEL_SHARP_REF = float(os.environ.get("CLASSIFY_REEL_SHARP_REF", "140"))
# Confidence band that triggers a full-resolution confirm re-read of the peak
# frame. Outside it the first answer stands.
CLASSIFY_REEL_UNCERTAIN_LO = float(os.environ.get("CLASSIFY_REEL_UNCERTAIN_LO", "0.45"))
CLASSIFY_REEL_UNCERTAIN_HI = float(os.environ.get("CLASSIFY_REEL_UNCERTAIN_HI", "0.65"))
# Whole-reel contact sheet: one vision call sees the entire timeline, so a
# reveal in the final seconds is judged. Off => rank frames and score the best.
CLASSIFY_REEL_SHEET = _env_bool("CLASSIFY_REEL_SHEET", "1")
CLASSIFY_REEL_SHEET_PANELS = int(os.environ.get("CLASSIFY_REEL_SHEET_PANELS", "9"))
CLASSIFY_REEL_SHEET_PANEL_W = int(os.environ.get("CLASSIFY_REEL_SHEET_PANEL_W", "256"))
# Skin-tone fraction weight in the frame ranker. 0 disables the term.
CLASSIFY_REEL_SKIN_WEIGHT = float(os.environ.get("CLASSIFY_REEL_SKIN_WEIGHT", "1.0"))
# HSV histogram correlation below this between neighbouring samples => shot cut.
CLASSIFY_REEL_CUT_THRESHOLD = float(os.environ.get("CLASSIFY_REEL_CUT_THRESHOLD", "0.45"))

# Storage conventions
IMAGE_EXTENSIONS = (".jpg", ".jpeg", ".webp", ".png")
VIDEO_EXTENSIONS = (".mp4", ".webm")
MEDIA_EXTENSIONS = IMAGE_EXTENSIONS + VIDEO_EXTENSIONS
EXCLUDED_FOLDERS = {
    "_no_person_detected",
    "_thumbs",
    "_generations",
    "_classify",
    "_trash",
    "_journal",
    "_eval",
    "_workflows",
}
METADATA_SUFFIX = ".meta.json"

# FTS5 prompt search. Off by default: measured slower than the LIKE scan for
# common query terms at current archive sizes (see docs/review_backend_
# architecture.md S5). The index is maintained either way, so this is a flip.
FTS_SEARCH = _env_bool("PROMPTSTUDIO_FTS_SEARCH", "0")

# ── Run journal (append-only JSONL history of background jobs) ───
JOURNAL_ENABLED = _env_bool("PROMPTSTUDIO_JOURNAL", "1")
JOURNAL_DIR = os.path.join(SAVED_DIR, "_journal")
JOURNAL_MAX_BYTES = int(os.environ.get("PROMPTSTUDIO_JOURNAL_MAX_BYTES", str(10 * 1024 * 1024)))
JOURNAL_BACKUPS = int(os.environ.get("PROMPTSTUDIO_JOURNAL_BACKUPS", "3"))

# ── Logging ──────────────────────────────────────────────────────
# Lives beside the archive, not in the repo, so it survives a checkout and
# never lands in git. Set PROMPTSTUDIO_LOG_FILE="" to disable file logging.
LOG_LEVEL = os.environ.get("PROMPTSTUDIO_LOG_LEVEL", "INFO").strip().upper()
LOG_FILE = os.environ.get(
    "PROMPTSTUDIO_LOG_FILE", os.path.join(SAVED_DIR, "promptstudio.log")
)
LOG_MAX_BYTES = int(os.environ.get("PROMPTSTUDIO_LOG_MAX_BYTES", str(5 * 1024 * 1024)))
LOG_BACKUPS = int(os.environ.get("PROMPTSTUDIO_LOG_BACKUPS", "3"))
LOG_CONSOLE = _env_bool("PROMPTSTUDIO_LOG_CONSOLE", "1")
MAX_PHOTOS_API_PAGE = int(os.environ.get("PROMPTSTUDIO_PHOTO_PAGE", "300"))
# Path-list for "select all N" — not a gallery page, so the page cap does not
# apply. 10k short strings is a small JSON payload; past that the UI must say
# the selection was truncated rather than pretend the whole pile is covered.
MAX_PHOTO_IDS_API = int(os.environ.get("PROMPTSTUDIO_PHOTO_IDS", "10000"))
THUMB_MAX_SIZE = int(os.environ.get("PROMPTSTUDIO_THUMB_SIZE", "400"))
# Thumbnails used to be created inside `GET /media/thumb/`, and only there —
# nothing generated them at ingest, so `_thumbs/` covered 20% of a 61k catalog
# and the newest 500 files were 91% unthumbed. A first page of "newest" was
# then 60 simultaneous Pillow decodes on the six connections a browser opens,
# and for a reel, an unbounded frame-ranking pass over the whole timeline.
#
# These workers own that encode instead. The request thread submits and waits
# briefly; the archive-wide backlog is `scripts/backfill_thumbnails.py`.
# 0 = no workers, generate inline on the request thread (the old behaviour,
# kept as an escape hatch rather than a code path anyone should choose).
#
# Scaled off the CPU rather than fixed at 1: Pillow releases the GIL inside the
# decode, so these do overlap, and the miss path is what a first browse hits on
# an archive that predates thumbnail-at-ingest (run the backfill CLI and it
# stops mattering). Capped at 4 — the point is to stop competing with the API
# for threads, not to saturate the machine behind a gallery scroll.
THUMB_WORKERS = _env_num(
    "PROMPTSTUDIO_THUMB_WORKERS",
    min(4, max(2, (os.cpu_count() or 4) // 2)),
    int,
)
# How long a tile request waits for a worker before giving up and sending the
# placeholder. Long enough to cover a still on a warm filesystem, short enough
# that a reel's frame-ranking pass cannot hold a connection hostage.
THUMB_WAIT_SEC = _env_num("PROMPTSTUDIO_THUMB_WAIT", 2.0, float)

# Read-only SQLite connections for the gallery reads, alongside the single
# writer. WAL lets a reader run while a write is in flight, but that only helps
# if the reader is a *different* connection — the whole index used to share one,
# behind a process-wide RLock, so `/api/photos` queued behind every classify
# verdict and scrape upsert. `busy_timeout=5000` meant a write could stall the
# grid for five seconds (docs/review_gallery_performance.md §6).
#
# 0 disables the pool and sends every read back through the writer, which is
# the pre-P1 behaviour and the escape hatch if a platform's SQLite refuses a
# read-only handle on a WAL database.
DB_READERS = _env_num("PROMPTSTUDIO_DB_READERS", 4, int)

# Ollama vision engine
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434/api/generate")
OLLAMA_TEXT_URL = os.environ.get("OLLAMA_TEXT_URL", OLLAMA_URL)
MODEL_NAME = os.environ.get("OLLAMA_VISION_MODEL", "qwen2.5vl:7b")
REWRITE_MODEL_NAME = os.environ.get("OLLAMA_REWRITE_MODEL", MODEL_NAME)
# B2/C1/C3. Blank = hashed n-grams over vision JSON + prompt (no extra model,
# no new dep). Set to an Ollama embedding tag to switch the vectors.
OLLAMA_EMBED_MODEL = os.environ.get("OLLAMA_EMBED_MODEL", "").strip()
OLLAMA_EMBED_URL = os.environ.get(
    "OLLAMA_EMBED_URL", "http://127.0.0.1:11434/api/embed"
)
TASTE_EMBED_DIM = int(os.environ.get("TASTE_EMBED_DIM", "256"))

# Prompt tone: balanced (default) | low | high
# Accept legacy PROMPT_EROTIC_INTENSITY for existing private .env files
PROMPT_INTENSITY = (
    os.environ.get("PROMPT_INTENSITY")
    or os.environ.get("PROMPT_EROTIC_INTENSITY")
    or "balanced"
).strip().lower()
# Back-compat alias used by older code paths
EROTIC_INTENSITY = PROMPT_INTENSITY
REALISM_BIAS = os.environ.get("PROMPT_REALISM_BIAS", "strong").strip().lower()
PROMPT_PIPELINE_VERSION = "v2-structured"
CREATOR_STYLE_MIN_PROMPTS = int(os.environ.get("CREATOR_STYLE_MIN", "5"))

# ComfyUI (optional)
COMFYUI_URL = os.environ.get("COMFYUI_URL", "http://127.0.0.1:8188").rstrip("/")
COMFYUI_CHECKPOINT = os.environ.get(
    "COMFYUI_CHECKPOINT", "juggernautXL_ragnarok.safetensors"
)
_COMFY_PKG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "comfy")
# A4 workflow registry (see comfy/registry.py). `pro` and `txt2img` ship in the
# package so a fresh checkout can generate with an empty archive; the user's own
# ComfyUI exports live beside the archive, where E1 backs them up and a checkout
# cannot lose them. A user entry shadows a built-in of the same name.
COMFY_BUILTIN_WORKFLOWS_DIR = os.path.join(_COMFY_PKG_DIR, "workflows")
# `or`, not a two-arg get: a set-but-empty COMFY_WORKFLOWS_DIR would otherwise
# resolve to "" and point the registry at the process CWD (hard rule 14's trap,
# applied to a path instead of a host).
COMFY_WORKFLOWS_DIR = (
    os.environ.get("COMFY_WORKFLOWS_DIR", "").strip() or os.path.join(SAVED_DIR, "_workflows")
)
COMFYUI_DEFAULT_DENOISE = float(os.environ.get("COMFYUI_DENOISE", "0.70"))
COMFYUI_DEFAULT_STEPS = int(os.environ.get("COMFYUI_STEPS", "32"))
COMFYUI_DEFAULT_CFG = float(os.environ.get("COMFYUI_CFG", "6.0"))
GENERATIONS_DIR = os.path.join(SAVED_DIR, "_generations")
GENERATIONS_INDEX_FILE = os.path.join(SAVED_DIR, "generations_index.json")
# How many generations to keep per source photo in the legacy JSON index.
# 0 = unbounded, and it is the default: the old hardcoded 20 silently discarded
# history that nothing had yet rendered. The SQLite table is never capped by
# this — it exists only to bound the rollback file while it is still written.
GENERATIONS_KEEP_PER_SOURCE = int(os.environ.get("GENERATIONS_KEEP_PER_SOURCE", "0"))
# A2 batch generate. The cap is a guard against a mis-clicked "select all" on a
# 4,000-photo archive turning into a week of GPU time, not a capacity limit.
COMFY_BATCH_MAX = int(os.environ.get("COMFY_BATCH_MAX", "200"))
# Per-item ceiling. Was hardcoded at 900 in _run_pro; a batch needs it
# configurable because one wedged item should not eat the whole run's evening.
COMFY_BATCH_ITEM_TIMEOUT = int(os.environ.get("COMFY_BATCH_ITEM_TIMEOUT", "900"))
