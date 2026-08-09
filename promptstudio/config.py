"""Central configuration for PromptStudio.

All secrets and machine-specific paths come from environment variables
(or a local `.env` file — never commit `.env`). See `.env.example`.
"""

from __future__ import annotations

import os
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


# Server
PORT = int(os.environ.get("PROMPTSTUDIO_PORT", "5000"))
HOST = os.environ.get("PROMPTSTUDIO_HOST", "")

# Local image archive (never store personal media inside the git repo)
SAVED_DIR = os.path.expanduser(
    os.environ.get("PROMPTSTUDIO_ARCHIVE", "~/Pictures/InstagramSaved")
)

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
ARCHIVE_DB_FILE = os.path.join(SAVED_DIR, "archive.db")

# Full-scrape ceiling (downloaded media units). 0 = unlimited.
FULL_SCRAPE_MAX_POSTS = int(os.environ.get("IG_FULL_SCRAPE_MAX_POSTS", "5000"))
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
DEFAULT_ACCOUNTS_PER_DAY = int(os.environ.get("IG_ACCOUNTS_PER_DAY", "20"))
RATE_LIMIT_BACKOFF_SEC = int(os.environ.get("IG_RATE_LIMIT_BACKOFF", "60"))
RATE_LIMIT_BACKOFF_MAX_SEC = int(os.environ.get("IG_RATE_LIMIT_BACKOFF_MAX", "300"))
DEFAULT_MAX_POSTS_PER_CREATOR = int(os.environ.get("IG_MAX_POSTS", "50"))
DEFAULT_MIN_MEDIA_COUNT = int(os.environ.get("IG_MIN_MEDIA_COUNT", "5"))
INCLUDE_VIDEOS_DEFAULT = _env_bool("IG_INCLUDE_VIDEOS", "1")
QUEUE_PRIORITY_DEFAULT = int(os.environ.get("IG_QUEUE_PRIORITY_DEFAULT", "10"))
POST_RANK_ENABLED = _env_bool("IG_POST_RANK", "1")
POST_SCAN_FACTOR = float(os.environ.get("IG_POST_SCAN_FACTOR", "3"))

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
GALLERY_DL_BIN = os.environ.get("GALLERY_DL_BIN", "gallery-dl")
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
# Video frame selection — used by `scraping/video_frames.py` for video
# thumbnails (`storage/thumbs.py`) and near-duplicate detection
# (`storage/dedupe.py`). These outlived the glam classifier that introduced
# them; the CLASSIFY_REEL_* names are kept so the frame ranker reads unchanged.
CLASSIFY_REEL_CANDIDATES = int(os.environ.get("CLASSIFY_REEL_CANDIDATES", "16"))
CLASSIFY_REEL_SKIP_HEAD_FRAC = float(os.environ.get("CLASSIFY_REEL_SKIP_HEAD_FRAC", "0.02"))
# 0.0 on purpose: the interesting frame is often in the final seconds.
CLASSIFY_REEL_SKIP_TAIL_FRAC = float(os.environ.get("CLASSIFY_REEL_SKIP_TAIL_FRAC", "0.0"))
CLASSIFY_REEL_MIN_BRIGHT = float(os.environ.get("CLASSIFY_REEL_MIN_BRIGHT", "22"))
CLASSIFY_REEL_MIN_SHARP = float(os.environ.get("CLASSIFY_REEL_MIN_SHARP", "35"))
# "Sharp enough" reference — frames at/above this stop earning extra rank, so an
# adequately sharp frame is not beaten by a razor-sharp static intro.
CLASSIFY_REEL_SHARP_REF = float(os.environ.get("CLASSIFY_REEL_SHARP_REF", "140"))
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
THUMB_MAX_SIZE = int(os.environ.get("PROMPTSTUDIO_THUMB_SIZE", "400"))

# Ollama vision engine
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434/api/generate")
OLLAMA_TEXT_URL = os.environ.get("OLLAMA_TEXT_URL", OLLAMA_URL)
MODEL_NAME = os.environ.get("OLLAMA_VISION_MODEL", "qwen2.5vl:7b")
REWRITE_MODEL_NAME = os.environ.get("OLLAMA_REWRITE_MODEL", MODEL_NAME)

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
COMFYUI_PRO_WORKFLOW = os.environ.get(
    "COMFYUI_PRO_WORKFLOW",
    os.path.join(_COMFY_PKG_DIR, "workflows", "modelToimage_pro.api.json"),
)
COMFYUI_DEFAULT_DENOISE = float(os.environ.get("COMFYUI_DENOISE", "0.70"))
COMFYUI_DEFAULT_STEPS = int(os.environ.get("COMFYUI_STEPS", "32"))
COMFYUI_DEFAULT_CFG = float(os.environ.get("COMFYUI_CFG", "6.0"))
GENERATIONS_DIR = os.path.join(SAVED_DIR, "_generations")
GENERATIONS_INDEX_FILE = os.path.join(SAVED_DIR, "generations_index.json")
