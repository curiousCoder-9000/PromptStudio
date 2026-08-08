"""Central configuration for PromptStudio."""

import os

# Server
PORT = int(os.environ.get("PROMPTSTUDIO_PORT", "5000"))
HOST = os.environ.get("PROMPTSTUDIO_HOST", "")

# Local image archive
SAVED_DIR = os.path.expanduser(
    os.environ.get("PROMPTSTUDIO_ARCHIVE", "~/Pictures/InstagramSaved")
)

# Instagram / Instaloader
SESSION_USER = os.environ.get("INSTAGRAM_SESSION_USER", "YOUR_INSTAGRAM_USERNAME")
INSTALOADER_SESSION_DIR = os.path.expanduser(
    os.environ.get("INSTALOADER_SESSION_DIR", "~/AppData/Local/Instaloader")
)
FOLLOWING_LIST_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "following_list.json",
)

# Archive index files
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
CREATOR_SCRAPE_QUEUE_ENABLED = os.environ.get("IG_CREATOR_SCRAPE_QUEUE", "1").lower() in (
    "1",
    "true",
    "yes",
)
AUTO_DRAIN_ON_START = os.environ.get("IG_AUTO_DRAIN_ON_START", "1").lower() in (
    "1",
    "true",
    "yes",
)
AUTO_DRAIN_ON_START_DELAY_SEC = float(os.environ.get("IG_AUTO_DRAIN_DELAY_SEC", "2"))
# Force full filesystem reindex on next server start when set to 1/true
REBUILD_INDEX = os.environ.get("PROMPTSTUDIO_REBUILD_INDEX", "").lower() in (
    "1",
    "true",
    "yes",
)
CREATOR_STYLES_FILE = os.path.join(SAVED_DIR, "creator_styles.json")
FAVORITES_FILE = os.path.join(SAVED_DIR, "favorites.json")
THUMB_DIR = os.path.join(SAVED_DIR, "_thumbs")
PROMPT_HISTORY_MAX = int(os.environ.get("PROMPT_HISTORY_MAX", "3"))

# Scraping behaviour (Phase A anti-ban pacing)
DOWNLOAD_DELAY_SEC = float(os.environ.get("IG_DOWNLOAD_DELAY", "2.5"))
POST_DELAY_MIN_SEC = float(os.environ.get("IG_POST_DELAY_MIN", "4"))
POST_DELAY_MAX_SEC = float(os.environ.get("IG_POST_DELAY_MAX", "12"))
ACCOUNT_PAUSE_MIN_SEC = float(os.environ.get("IG_ACCOUNT_PAUSE_MIN", "30"))
ACCOUNT_PAUSE_MAX_SEC = float(os.environ.get("IG_ACCOUNT_PAUSE_MAX", "120"))
BATCH_PAUSE_EVERY = int(os.environ.get("IG_BATCH_EVERY", "10"))
BATCH_PAUSE_MIN_SEC = float(os.environ.get("IG_BATCH_PAUSE_MIN", str(5 * 60)))
BATCH_PAUSE_MAX_SEC = float(os.environ.get("IG_BATCH_PAUSE_MAX", str(15 * 60)))
ABORT_RATE_LIMIT_STREAK = int(os.environ.get("IG_ABORT_RATE_LIMIT_STREAK", "3"))
# Stop crawling a creator after this many consecutive already-archived posts
CATCH_UP_STREAK = int(os.environ.get("IG_CATCH_UP_STREAK", "3"))
DEFAULT_ACCOUNTS_PER_DAY = int(os.environ.get("IG_ACCOUNTS_PER_DAY", "20"))
RATE_LIMIT_BACKOFF_SEC = int(os.environ.get("IG_RATE_LIMIT_BACKOFF", "60"))
RATE_LIMIT_BACKOFF_MAX_SEC = int(os.environ.get("IG_RATE_LIMIT_BACKOFF_MAX", "300"))
DEFAULT_MAX_POSTS_PER_CREATOR = int(os.environ.get("IG_MAX_POSTS", "50"))
DEFAULT_MIN_MEDIA_COUNT = int(os.environ.get("IG_MIN_MEDIA_COUNT", "5"))
# Download reels / video posts during creator + following sync (saved posts always include media)
INCLUDE_VIDEOS_DEFAULT = os.environ.get("IG_INCLUDE_VIDEOS", "1").lower() in (
    "1",
    "true",
    "yes",
)
# Queue priority boosts for classify-driven acquisition
QUEUE_PRIORITY_KEEP = int(os.environ.get("IG_QUEUE_PRIORITY_KEEP", "100"))
QUEUE_PRIORITY_UNSURE = int(os.environ.get("IG_QUEUE_PRIORITY_UNSURE", "40"))
QUEUE_PRIORITY_DEFAULT = int(os.environ.get("IG_QUEUE_PRIORITY_DEFAULT", "10"))
# Within a creator feed: scan more posts than we download, rank by glam signals
POST_RANK_ENABLED = os.environ.get("IG_POST_RANK", "1").lower() in ("1", "true", "yes")
POST_SCAN_FACTOR = float(os.environ.get("IG_POST_SCAN_FACTOR", "3"))
DEFAULT_CAPTION_KEYWORDS = [
    k.strip()
    for k in os.environ.get(
        "IG_CAPTION_KEYWORDS",
        "bikini,lingerie,swim,swimwear,onlyfans,boudoir,lingerie,bodysuit,"
        "cleavage,booty,thong,micro,sexy,glamour,glam,cosplay,fitness model,"
        "underboob,see through,mesh,stockings,heels,wet",
    ).split(",")
    if k.strip()
]
# Gallery sexy filter: glam_score >= this (0–3; -1 = unscored)
GLAM_SEXY_MIN = int(os.environ.get("GLAM_SEXY_MIN", "2"))
DEFAULT_BIO_KEYWORDS = [
    k.strip()
    for k in os.environ.get(
        "IG_BIO_KEYWORDS",
        "model,influencer,fitness,onlyfans,lingerie,bikini,glamour,actress,swimwear,boudoir,cosplay",
    ).split(",")
    if k.strip()
]

# Storage conventions
IMAGE_EXTENSIONS = (".jpg", ".jpeg", ".webp", ".png")
VIDEO_EXTENSIONS = (".mp4", ".webm")
MEDIA_EXTENSIONS = IMAGE_EXTENSIONS + VIDEO_EXTENSIONS
EXCLUDED_FOLDERS = {"_no_person_detected", "_thumbs", "_generations", "_classify"}
METADATA_SUFFIX = ".meta.json"
MAX_PHOTOS_API_PAGE = int(os.environ.get("PROMPTSTUDIO_PHOTO_PAGE", "300"))
THUMB_MAX_SIZE = int(os.environ.get("PROMPTSTUDIO_THUMB_SIZE", "400"))

# Ollama vision engine (also used by promptstudio.prompts.engine)
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434/api/generate")
OLLAMA_TEXT_URL = os.environ.get("OLLAMA_TEXT_URL", OLLAMA_URL)
MODEL_NAME = os.environ.get("OLLAMA_VISION_MODEL", "qwen2.5vl:7b")
REWRITE_MODEL_NAME = os.environ.get("OLLAMA_REWRITE_MODEL", MODEL_NAME)
EROTIC_INTENSITY = os.environ.get("PROMPT_EROTIC_INTENSITY", "high")
REALISM_BIAS = os.environ.get("PROMPT_REALISM_BIAS", "strong")
PROMPT_PIPELINE_VERSION = "v2-structured"
CREATOR_STYLE_MIN_PROMPTS = int(os.environ.get("CREATOR_STYLE_MIN", "5"))

# ComfyUI (optional local generator) — CLIP similarity deferred
COMFYUI_URL = os.environ.get("COMFYUI_URL", "http://127.0.0.1:8188").rstrip("/")
COMFYUI_CHECKPOINT = os.environ.get(
    "COMFYUI_CHECKPOINT", "juggernautXL_ragnarok.safetensors"
)
_COMFY_PKG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "comfy")
COMFYUI_PRO_WORKFLOW = os.environ.get(
    "COMFYUI_PRO_WORKFLOW",
    os.path.join(_COMFY_PKG_DIR, "workflows", "modelToimage_pro.api.json"),
)
# Defaults aligned with modelToimage_pro Mode E (rev 7)
COMFYUI_DEFAULT_DENOISE = float(os.environ.get("COMFYUI_DENOISE", "0.70"))
COMFYUI_DEFAULT_STEPS = int(os.environ.get("COMFYUI_STEPS", "32"))
COMFYUI_DEFAULT_CFG = float(os.environ.get("COMFYUI_CFG", "6.0"))
GENERATIONS_DIR = os.path.join(SAVED_DIR, "_generations")
GENERATIONS_INDEX_FILE = os.path.join(SAVED_DIR, "generations_index.json")
