"""PromptStudio HTTP API server."""

import functools
import http.server
import json
import os
import socketserver
import urllib.parse
from typing import Any, Dict, List, Optional

from promptstudio.comfy.batch import ComfyBatchManager
from promptstudio.comfy.client import ComfyJobManager, check_comfy_health
from promptstudio.comfy.params import NoPromptError, resolve_generation_params
from promptstudio.comfy.registry import WorkflowError
from promptstudio.config import (
    CLASSIFY_REJECT_MAX_TIER,
    CREATOR_SCRAPE_QUEUE_ENABLED,
    DEFAULT_MAX_POSTS_PER_CREATOR,
    FOLLOWING_LIST_FILE,
    HOST,
    INCLUDE_VIDEOS_DEFAULT,
    MAX_PHOTO_IDS_API,
    MAX_PHOTOS_API_PAGE,
    MODEL_NAME,
    PORT,
    PROMPT_PIPELINE_VERSION,
    SAVED_DIR,
    THUMB_WAIT_SEC,
    TRASH_ENABLED,
)
from promptstudio.jobs import LEASES
from promptstudio.logging_setup import get_logger
from promptstudio.prompts.batch import BatchPromptManager
from promptstudio.prompts.cache import PromptCache
from promptstudio.prompts.engine import ENGINE_ID, build_export_variants, get_prompt_for_image
from promptstudio.prompts.styles import CreatorStyleStore
from promptstudio.scraping.classify_job import ClassifyJobManager
from promptstudio.scraping.creator_queue import CreatorScrapeQueue
from promptstudio.scraping.media_classifier import TIER_LABELS
from promptstudio.scraping.sources.base import VALID_MODES, ScrapeOptions
from promptstudio.scraping.sync_manager import SyncManager
from promptstudio.server.multipart import parse_multipart_data
from promptstudio.storage.archive import ArchiveStore, ensure_creator_folder
from promptstudio.storage.db import LABEL_FILTERS, SEARCH_MODES, VERDICT_FILTERS
from promptstudio.storage.favorites import FavoritesStore
from promptstudio.storage.journal import list_kinds as list_journal_kinds
from promptstudio.storage.journal import read_runs as read_journal_runs
from promptstudio.storage.trash import TrashStore

log = get_logger(__name__)

_archive = ArchiveStore()
_prompt_cache = PromptCache()
_favorites = FavoritesStore()
_sync = SyncManager.get()
_batch = BatchPromptManager.get()
_styles = CreatorStyleStore()
_trash = TrashStore()
_comfy = ComfyJobManager.get()
_comfy_batch = ComfyBatchManager.get()
_classify = ClassifyJobManager.get()
_scrape_queue = CreatorScrapeQueue.get() if CREATOR_SCRAPE_QUEUE_ENABLED else None

# Prefer 127.0.0.1 over localhost: on Windows, localhost often resolves to ::1
# first while Ollama may only be listening on IPv4, which makes /api/health hang
# for the full urllib timeout and the UI looks offline on every boot.
OLLAMA_TAGS_URL = os.environ.get(
    "OLLAMA_TAGS_URL", "http://127.0.0.1:11434/api/tags"
)

_following_cache: Dict[str, Any] = {"mtime": None, "accounts": []}

_TRUTHY = ("1", "true", "yes")
_FALSY = ("0", "false", "no")
_TASTE_JOB = None


def _taste_job():
    global _TASTE_JOB
    if _TASTE_JOB is None:
        from promptstudio.taste import TasteJob

        _TASTE_JOB = TasteJob.get()
    return _TASTE_JOB


def _trash_entry_urls(entry: Dict[str, Any]) -> Dict[str, Any]:
    """Attach /media URLs so the trash modal can render thumbnails."""
    from promptstudio.storage.thumbs import thumb_url

    eid = str(entry.get("id") or "")
    filename = str(entry.get("filename") or "")
    if not eid or not filename or not entry.get("media_present"):
        entry.setdefault("url", "")
        entry.setdefault("thumb_url", "")
        return entry
    rel = f"_trash/{eid}/{filename}"
    entry["url"] = f"/media/{urllib.parse.quote(rel)}"
    entry["thumb_url"] = thumb_url(rel)
    return entry


def _as_bool(value: Any, *, default: bool = False) -> bool:
    """Coerce a JSON/query value to bool, accepting "1"/"true"/"yes" strings.

    Clients send booleans as JSON bools from `app.js` and as strings from query
    params and older callers, so every flag has to accept both.
    """
    if value is None:
        return default
    if isinstance(value, str):
        return value.strip().lower() in _TRUTHY
    return bool(value)


# Keys A2 accepts. An allowlist rather than `**data`: `plan()` forwards every
# unrecognised key into the generation parameters, so a typo in the request
# body would silently become a workflow override instead of a 400.
_BATCH_SELECTION_KEYS = (
    "creator",
    "media_type",
    "verdict",
    "source",
)
_BATCH_OVERRIDE_KEYS = (
    "variant",
    "workflow",
    "positive_prompt",
    "negative_prompt",
    "use_mode_e",
    "aspect_ratio",
    "steps",
    "cfg_scale",
    "denoise",
    "checkpoint",
    "seed",
)


def _batch_generate_args(data: Dict[str, Any]) -> Dict[str, Any]:
    """Request body → `ComfyBatchManager.start()` kwargs."""
    args: Dict[str, Any] = {}
    raw_paths = data.get("paths")
    if isinstance(raw_paths, list):
        paths = [str(p).strip() for p in raw_paths if str(p).strip()]
        if paths:
            args["paths"] = paths
    for key in _BATCH_SELECTION_KEYS:
        value = data.get(key)
        if value:
            args[key] = str(value).strip()
    if data.get("favorite") is not None:
        args["favorite"] = _as_bool(data.get("favorite"))
    if data.get("limit") is not None:
        args["limit"] = int(data["limit"])
    for key in _BATCH_OVERRIDE_KEYS:
        if data.get(key) is not None:
            args[key] = data[key]
    return args


class _BadSource(ValueError):
    """An unrecognised ?source= value. Carries its own 400 message."""


def _parse_body_source(data: Dict[str, Any]) -> Optional[str]:
    """Read an optional `source` from a JSON body. None means "every lane"."""
    from promptstudio.scraping.sources import known_sources, normalize_source

    raw = str(data.get("source") or "").strip().lower()
    if not raw or raw == "all":
        return None
    name = normalize_source(raw)
    if name not in known_sources():
        raise _BadSource(
            f"Unknown source '{raw}'. Known: {', '.join(sorted(known_sources()))}, all"
        )
    return name


def _parse_source_filter(query: Dict[str, List[str]]) -> Optional[str]:
    """Read `?source=` as a filter, or None for "every source".

    Empty and `all` both mean unfiltered. Anything else must be a registered
    source: silently returning the whole archive when the caller asked for X is
    the failure mode that looks like success, so an unknown value is a 400.
    """
    from promptstudio.scraping.sources import known_sources, normalize_source

    raw = (query.get("source", [""])[0] or "").strip().lower()
    if not raw or raw == "all":
        return None
    name = normalize_source(raw)
    if name not in known_sources():
        raise _BadSource(
            f"Unknown source '{raw}'. Known: {', '.join(sorted(known_sources()))}, all"
        )
    return name


def _parse_has_source(query: Dict[str, List[str]]) -> Optional[bool]:
    """Tri-state: True / False filter, or None for "any".

    Empty is unfiltered. `has_source=0` is a real filter (pure txt2img rows),
    so this cannot collapse to `_as_bool` — that treats missing as False.
    """
    raw = (query.get("has_source", [""])[0] or "").strip().lower()
    if not raw:
        return None
    if raw in _TRUTHY:
        return True
    if raw in _FALSY:
        return False
    return None


def _expand_post_groups(reps: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Flatten grouped gallery rows into their slides, adjacent and in order.

    The grid draws one tile per `group_key`, but the lightbox walks the whole
    post — so the slides the grid never drew still have to arrive, as real
    photo rows. Returning only the representative plus a list of paths would
    leave slide 2 with no favourite, no verdict and no cached prompt.

    Costs one extra query per page, and only when the page actually contains a
    carousel: a group of one is already in hand.
    """
    extra = {
        rel
        for rep in reps
        for rel in (rep.get("group_members") or ())
        if rel != rep["rel_path"]
    }
    members = _archive.photos_for_rel_paths(sorted(extra)) if extra else {}

    slides: List[Dict[str, Any]] = []
    for rep in reps:
        key = rep.get("group_key") or rep["rel_path"]
        count = int(rep.get("group_count") or 1)
        for position, rel in enumerate(rep.get("group_members") or [rep["rel_path"]]):
            row = rep if rel == rep["rel_path"] else members.get(rel)
            if row is None:
                continue  # indexed row vanished between the two reads
            slide = dict(row)
            slide.pop("group_members", None)
            slide["group_key"] = key
            slide["group_count"] = count
            slide["group_index"] = position
            slides.append(slide)
    return slides


def _instagram_cooldown_block() -> Optional[Dict[str, Any]]:
    """409 body when Instagram is sitting out an automation warning."""
    from promptstudio.scraping.ig_cooldown import block_message, status

    msg = block_message()
    if not msg:
        return None
    return {
        "status": "cooldown",
        "message": msg,
        "instagram_cooldown": status(),
    }


def _creator_queue_blocks_oneshot(source: str = "instagram") -> Optional[Dict[str, Any]]:
    """If this lane has pending jobs and is not paused, block one-shot sync.

    Policy, not mutual exclusion — the per-source lease in
    `SyncManager.start_job` is what prevents two syncs from actually
    overlapping. This exists so a one-shot request does not jump the queue's
    pacing, and being advisory is fine: the worst case is a job that the lease
    then refuses anyway.

    Lane-scoped since lanes shipped. The one-shot routes are all Instagram, and
    a queued *Reddit* job has nothing to do with Instagram's pacing — blocking
    on it was only ever an artefact of there being one global worker.
    """
    if not CREATOR_SCRAPE_QUEUE_ENABLED:
        return None
    try:
        q = CreatorScrapeQueue.get()
        n = q.pending_count(source)
        if n > 0 and not q.is_paused(source):
            return {
                "status": "busy",
                "message": (
                    f"The {source} scrape queue has {n} pending — "
                    "pause or empty it first"
                ),
                "creator_queue_depth": n,
                "source": source,
            }
    except Exception:
        # Never block a sync because the queue file could not be read, but do
        # not hide it either — this used to be a bare `pass`.
        log.warning("creator queue check failed; allowing one-shot", exc_info=True)
    return None


def _load_following_accounts() -> List[Dict[str, Any]]:
    if not os.path.isfile(FOLLOWING_LIST_FILE):
        _following_cache["mtime"] = None
        _following_cache["accounts"] = []
        return []
    try:
        mtime = os.path.getmtime(FOLLOWING_LIST_FILE)
        if _following_cache["mtime"] == mtime and _following_cache["accounts"]:
            return _following_cache["accounts"]
        with open(FOLLOWING_LIST_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        accounts = data if isinstance(data, list) else []
        _following_cache["mtime"] = mtime
        _following_cache["accounts"] = accounts
        return accounts
    except Exception:
        return []


def _check_ollama_health(timeout: float = 1.5) -> Dict[str, Any]:
    import urllib.request

    result: Dict[str, Any] = {
        "ollama": False,
        "model": MODEL_NAME,
        "model_ready": False,
        "models": [],
    }
    try:
        req = urllib.request.Request(OLLAMA_TAGS_URL, method="GET")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
        models = []
        for m in payload.get("models") or []:
            name = m.get("name") or m.get("model")
            if name:
                models.append(name)
        model_ready = any(
            name == MODEL_NAME or name.startswith(MODEL_NAME.split(":")[0])
            for name in models
        )
        result.update(
            {
                "ollama": True,
                "model": MODEL_NAME,
                "model_ready": model_ready,
                "models": models,
            }
        )
    except Exception:
        pass
    result.update(check_comfy_health(timeout=timeout))
    # Who holds each exclusive resource — the first thing to look at when
    # a job reports busy and nothing appears to be running.
    result["leases"] = LEASES.snapshot()
    from promptstudio.config import instagram_backend, instagram_cookies_info

    result["instagram_backend"] = instagram_backend()
    result["instagram_cookies"] = instagram_cookies_info()
    return result


def _error_boundary(fn):
    """Turn an unhandled route exception into a logged JSON 500.

    Without this the exception escapes to ``socketserver.handle_error``, which
    prints a traceback and drops the socket. The browser then sees a network
    failure indistinguishable from "server is down", so ``app.js`` reports the
    app as offline instead of surfacing a bug.
    """

    @functools.wraps(fn)
    def wrapper(self, *args, **kwargs):
        try:
            return fn(self, *args, **kwargs)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            # Normal when a user seeks or closes a video mid-stream. Nothing to
            # send, and it is not worth a traceback. ConnectionAbortedError is
            # the Windows cousin (WinError 10053).
            log.debug("client disconnected during %s %s", self.command, self.path)
            self.close_connection = True
            return None
        except Exception:
            log.exception("unhandled error in %s %s", self.command, self.path)
            self._send_json_500()
            return None

    return wrapper


class GalleryRequestHandler(http.server.SimpleHTTPRequestHandler):
    # A first page of the gallery is 1 JSON request and 60 `/media/thumb/`
    # GETs. `BaseHTTPRequestHandler` defaults to HTTP/1.0, which has no
    # keep-alive, so those 60 tiles were 60 TCP handshakes against a browser
    # that would happily have reused ~6 connections.
    #
    # This is not the one-line change it looks like. HTTP/1.1 keep-alive means
    # the client stops using "connection closed" as the end of the body, so
    # **every** response now has to frame itself. `_send_json` sent no
    # Content-Length at all, and neither did `do_OPTIONS`, the `do_HEAD`
    # fallback or the 416 branch — each one would have left the client blocked
    # waiting for an EOF on a socket the server was keeping open. They all send
    # one now, and `parse_request` below closes the connection rather than risk
    # the other half of the trade: an unread request body being parsed as the
    # next request line.
    protocol_version = "HTTP/1.1"

    # Keep-alive holds a thread per idle connection, so idle ones have to be
    # reaped. `StreamRequestHandler.setup` turns this into
    # `socket.settimeout`, and `handle_one_request` already treats a read
    # timeout as "close the connection" — the browser silently reconnects.
    # Per socket operation, not cumulative, so a slow `/api/prompt` behind
    # Ollama is unaffected.
    timeout = 60

    def handle_one_request(self):
        # Handler instances are reused across keep-alive requests, so these must
        # reset per request, not per connection.
        self._response_started = False
        self._connection_header_sent = False
        try:
            return super().handle_one_request()
        except (ConnectionResetError, ConnectionAbortedError, BrokenPipeError):
            # A browser reaping an idle keep-alive connection is *normal* — it
            # just closes, and the blocking `readline` waiting for the next
            # request line raises. Under HTTP/1.0 this never happened, because
            # the server closed first after every response.
            #
            # Left unhandled it escapes to `socketserver.handle_error`, which
            # prints a full traceback per closed tab. The access log is where
            # real faults are meant to be visible, so filling it with routine
            # disconnects is how the next real one gets missed.
            # ConnectionAbortedError is the Windows cousin (WinError 10053).
            self.close_connection = True
            log.debug("client closed the connection (%s)", self.client_address)
            return None

    def parse_request(self):
        """Refuse to keep a connection alive past a request that carried a body.

        Several routes answer 400/404 before reading the body — an unknown
        `POST /api/nope`, a missing `id`, a bad `rel_path`. Under HTTP/1.0 the
        close swallowed whatever was left in the socket. Under keep-alive those
        bytes become the next request line, and the connection desynchronises
        into an unexplainable 400 on whatever the browser asked for next.

        Closing is the cheap side of the trade: only POST/PUT carry bodies, and
        they were paying for a fresh connection already. The 60 thumbnail GETs
        that this whole change exists for are unaffected.
        """
        ok = super().parse_request()
        if ok and (
            self.headers.get("Content-Length")
            or self.headers.get("Transfer-Encoding")
        ):
            self.close_connection = True
        return ok

    def send_header(self, keyword, value):
        # Tracked so `end_headers` does not append a second `Connection: close`
        # behind `send_error`, which sends its own.
        if keyword.lower() == "connection":
            self._connection_header_sent = True
        return super().send_header(keyword, value)

    def send_response(self, code, message=None):
        self._response_started = True
        return super().send_response(code, message)

    def log_message(self, fmt, *args):
        """Access log that must never break the response.

        ``BaseHTTPRequestHandler.log_message`` writes to ``sys.stderr``. On
        Windows a detached or invalidated console raises
        ``OSError: [Errno 22] Invalid argument`` inside ``send_response`` →
        ``log_request``, which aborts the response before any body is written
        (browser shows connection closed / blank page). Do not call ``super()``
        — route through the package logger only.
        """
        try:
            log.debug("%s - %s", self.address_string(), fmt % args)
        except Exception:
            pass

    def _send_json_500(self) -> None:
        """Best-effort 500. Silent if headers already went out."""
        if getattr(self, "_response_started", False):
            # Mid-body failure: the status line is already committed, so the
            # only honest signal left is closing the connection.
            self.close_connection = True
            return
        try:
            self._send_json({"error": "internal server error"}, status=500)
        except Exception:
            self.close_connection = True

    def end_headers(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, DELETE, OPTIONS")
        # Range is required so browsers can probe partial media when CORS applies
        self.send_header(
            "Access-Control-Allow-Headers",
            "Content-Type, Range",
        )
        self.send_header(
            "Access-Control-Expose-Headers",
            "Accept-Ranges, Content-Range, Content-Length",
        )
        # Under HTTP/1.1 a client assumes the connection is reusable unless
        # told otherwise, so a server that decides to close has to say so —
        # otherwise the browser pipelines its next request into a socket that
        # is already going away. `close_connection` is set by `parse_request`
        # (bodies), by a client `Connection: close`, and by `send_error`.
        if self.close_connection and not getattr(
            self, "_connection_header_sent", False
        ):
            self.send_header("Connection", "close")
        super().end_headers()

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Content-Length", "0")
        self.end_headers()

    @_error_boundary
    def do_HEAD(self):
        """Support HEAD for media (range probing); body omitted in _serve_local_file."""
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path.startswith("/media/"):
            # do_GET uses self.command == "HEAD" to skip the body
            return self.do_GET()
        self.send_response(200)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _send_json(self, data, status=200):
        # Encoded before the status line goes out, for two reasons: the body
        # length is a required header under HTTP/1.1 keep-alive, and a
        # `json.dumps` failure now happens while `_response_started` is still
        # False, so the error boundary can still send a clean 500 instead of
        # having to abandon a half-written response.
        body = json.dumps(data).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_json_body(self):
        length = int(self.headers.get("Content-Length", 0))
        if not length:
            return {}
        return json.loads(self.rfile.read(length).decode("utf-8"))

    @staticmethod
    def _parse_byte_range(range_header: str, file_size: int):
        """
        Parse a single HTTP Range header value into (start, end) inclusive.
        Returns None if the header is missing/unusable; raises ValueError if unsatisfiable.
        """
        if not range_header:
            return None
        raw = range_header.strip()
        if not raw.lower().startswith("bytes="):
            return None
        spec = raw.split("=", 1)[1].strip()
        # Only honor the first range (browsers use single ranges for media)
        spec = spec.split(",")[0].strip()
        if "-" not in spec:
            raise ValueError("invalid range")
        start_s, end_s = spec.split("-", 1)
        if start_s == "" and end_s == "":
            raise ValueError("empty range")
        if start_s == "":
            # suffix: last N bytes
            suffix = int(end_s)
            if suffix <= 0:
                raise ValueError("bad suffix")
            if suffix >= file_size:
                return 0, file_size - 1
            return file_size - suffix, file_size - 1
        start = int(start_s)
        if start < 0 or start >= file_size:
            raise ValueError("start out of bounds")
        if end_s == "":
            end = file_size - 1
        else:
            end = int(end_s)
            if end < start:
                raise ValueError("end before start")
            end = min(end, file_size - 1)
        return start, end

    def _serve_thumb(self, rel_path: str, full_path: str) -> None:
        """One gallery tile. Serves a file; does not create one.

        The old body of this route was `ensure_thumbnail(...) or full_path`, so
        a page of "newest" after a scrape meant sixty Pillow decodes inside
        sixty HTTP requests (449 ms each on the live archive), a whole-timeline
        frame-ranking pass for every reel, and — when that failed — a 3.5 MB
        original squeezed into a 220 px box.

        Now: hit, or hand it to the worker pool and wait a bounded moment, or
        placeholder. `no-store` on the placeholder is the part that keeps this
        honest — the grey tile must not be what the browser cache remembers
        this path by, or the file would look thumbless until a hard reload.
        """
        from promptstudio.storage import thumb_queue
        from promptstudio.storage.thumbs import (
            PLACEHOLDER_CONTENT_TYPE,
            PLACEHOLDER_GIF,
            ensure_thumbnail,
            resolve_thumb_file,
        )

        thumb = resolve_thumb_file(rel_path)
        if not thumb:
            queue = thumb_queue.get()
            if queue.workers:
                event = queue.submit(rel_path, full_path)
                if event is not None:
                    event.wait(THUMB_WAIT_SEC)
                thumb = resolve_thumb_file(rel_path)
            else:
                # THUMB_WORKERS=0 — the pre-P0.3 behaviour, on the request
                # thread, for an environment that will not have a background
                # thread. Documented in config.py as an escape hatch.
                thumb = ensure_thumbnail(full_path, rel_path)

        if not thumb:
            self.send_response(200)
            self.send_header("Content-Type", PLACEHOLDER_CONTENT_TYPE)
            self.send_header("Content-Length", str(len(PLACEHOLDER_GIF)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(PLACEHOLDER_GIF)
            return

        # Thumbs are small; still advertise ranges for consistency
        self._serve_local_file(
            thumb,
            "image/jpeg",
            cache_control="public, max-age=86400",
        )

    def _serve_local_file(
        self,
        full_path: str,
        content_type: str,
        cache_control: str = "public, max-age=3600",
    ) -> None:
        """
        Stream a local file with HTTP Range support (206 Partial Content).

        Chrome/Edge refuse to scrub HTML5 video unless the server advertises
        Accept-Ranges and answers Range requests with 206 + Content-Range.
        Serving the whole file as 200 without ranges makes currentTime seeks fail.
        """
        try:
            file_size = os.path.getsize(full_path)
        except OSError:
            self.send_error(404, "File not found")
            return

        range_header = self.headers.get("Range")
        try:
            byte_range = self._parse_byte_range(range_header or "", file_size)
        except ValueError:
            self.send_response(416)
            self.send_header("Content-Range", f"bytes */{file_size}")
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return

        chunk_size = 64 * 1024

        if byte_range is None:
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Content-Length", str(file_size))
            self.send_header("Cache-Control", cache_control)
            self.end_headers()
            if self.command == "HEAD":
                return
            with open(full_path, "rb") as f:
                while True:
                    data = f.read(chunk_size)
                    if not data:
                        break
                    self.wfile.write(data)
            return

        start, end = byte_range
        length = end - start + 1
        self.send_response(206)
        self.send_header("Content-Type", content_type)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Range", f"bytes {start}-{end}/{file_size}")
        self.send_header("Content-Length", str(length))
        self.send_header("Cache-Control", cache_control)
        self.end_headers()
        if self.command == "HEAD":
            return
        with open(full_path, "rb") as f:
            f.seek(start)
            remaining = length
            while remaining > 0:
                data = f.read(min(chunk_size, remaining))
                if not data:
                    break
                self.wfile.write(data)
                remaining -= len(data)

    @_error_boundary
    def do_DELETE(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/api/photo":
            query = urllib.parse.parse_qs(parsed.query)
            rel_path = query.get("path", [None])[0]
            permanent = (query.get("permanent", ["0"])[0] or "").lower() in (
                "1",
                "true",
                "yes",
            )
            if rel_path:
                rel_path = urllib.parse.unquote(rel_path)
                try:
                    # delete_photo owns containment, missing-file ghosts, and
                    # cache/favorite/tombstone bookkeeping. resolve_path requires
                    # the bytes to still be on disk, which is exactly the case
                    # a ghost catalog row fails — so do not 404 on that here.
                    result = _archive.delete_photo(rel_path, permanent=permanent)
                    if not result:
                        self.send_error(404, "Photo not found")
                        return
                    self._send_json(
                        {
                            "status": "deleted" if result["permanent"] else "trashed",
                            "filename": result["filename"],
                            "rel_path": result["rel_path"],
                            "trash_id": result["trash_id"],
                        }
                    )
                    return
                except OSError as e:
                    self.send_error(500, f"Error deleting file: {e}")
                    return
            self.send_error(404, "Photo not found")
            return

        if parsed.path == "/api/views":
            query = urllib.parse.parse_qs(parsed.query)
            try:
                view_id = int(query.get("id", ["0"])[0] or 0)
            except ValueError:
                self.send_error(400, "id required")
                return
            from promptstudio.storage.db import ArchiveIndex

            if not ArchiveIndex.get().delete_saved_view(view_id):
                self.send_error(404, "View not found")
                return
            self._send_json({"status": "ok", "id": view_id})
            return

        if parsed.path == "/api/collections":
            query = urllib.parse.parse_qs(parsed.query)
            try:
                cid = int(query.get("id", ["0"])[0] or 0)
            except ValueError:
                self.send_error(400, "id required")
                return
            from promptstudio.storage.db import ArchiveIndex

            if not ArchiveIndex.get().delete_collection(cid):
                self.send_error(404, "Collection not found")
                return
            self._send_json({"status": "ok", "id": cid})
            return

        if parsed.path == "/api/collections/items":
            try:
                data = self._read_json_body()
            except json.JSONDecodeError:
                self.send_error(400, "Invalid JSON body")
                return
            try:
                cid = int(data.get("id") or 0)
            except (TypeError, ValueError):
                cid = 0
            paths = data.get("paths") or []
            if not cid or not isinstance(paths, list):
                self.send_error(400, "id and paths required")
                return
            from promptstudio.storage.db import ArchiveIndex

            removed = ArchiveIndex.get().remove_collection_items(cid, paths)
            self._send_json({"status": "ok", "removed": removed})
            return

        if parsed.path == "/api/generation":
            from promptstudio.storage.db import ArchiveIndex

            query = urllib.parse.parse_qs(parsed.query)
            gen_id = (query.get("gen_id", [""])[0] or "").strip()
            if not gen_id:
                self.send_error(400, "gen_id required")
                return
            index = ArchiveIndex.get()
            # Permanent, no trash — unlike DELETE /api/photo. Archive media is
            # unrecoverable; a generation carries its own seed, prompt and
            # checkpoint, so it is reproducible by construction and a restore
            # path would be dead weight. The confirm copy says so.
            rel = index.delete_generation(gen_id)
            if rel is None:
                self.send_error(404, "Generation not found")
                return
            # Row first, file second, and the file only through the shared
            # containment check: the row is ours but it is still data, and a
            # hand-edited or migrated rel_path must not become an arbitrary
            # unlink. A row without its file is recoverable; the reverse is not.
            full_path = _archive.resolve_path(rel)
            removed = False
            if full_path:
                try:
                    os.remove(full_path)
                    removed = True
                    from promptstudio.storage.thumbs import resolve_thumb_file

                    thumb = resolve_thumb_file(rel)
                    if thumb and os.path.isfile(thumb):
                        os.remove(thumb)
                except OSError as e:
                    log.warning("removing generation %s: %s", rel, e)
            else:
                log.warning("generation %s is outside the archive; row only", rel)
            self._send_json(
                {
                    "status": "deleted",
                    "gen_id": gen_id,
                    "rel_path": rel,
                    "file_removed": removed,
                }
            )
            return

        # Not super().do_DELETE() — SimpleHTTPRequestHandler has no such method,
        # so the AttributeError hit the error boundary and reported a mistyped
        # URL as a server fault. GET and PUT already answered 404 here.
        self.send_error(404, "Not found")
        return

    @_error_boundary
    def do_PUT(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/api/prompt":
            try:
                data = self._read_json_body()
            except json.JSONDecodeError:
                self.send_error(400, "Invalid JSON body")
                return

            rel_path = (data.get("path") or "").strip()
            if not rel_path:
                self.send_error(400, "path required")
                return

            rel_path = urllib.parse.unquote(rel_path)
            full_path = _archive.resolve_path(rel_path)
            if not full_path:
                self.send_error(404, "Photo not found")
                return

            filename = os.path.basename(rel_path)
            existing = _prompt_cache.get(rel_path, filename) or {}
            positive = data.get("positive_prompt")
            negative = data.get("negative_prompt")
            if positive is None:
                positive = existing.get("positive_prompt", "")
            if negative is None:
                negative = existing.get("negative_prompt", "")
            if not isinstance(positive, str) or not isinstance(negative, str):
                self.send_error(400, "positive_prompt and negative_prompt must be strings")
                return

            visual_tags = data.get("visual_tags")
            if visual_tags is None:
                visual_tags = existing.get("visual_tags") or []
            if not isinstance(visual_tags, list):
                self.send_error(400, "visual_tags must be a list")
                return

            params = dict(existing.get("parameters") or {})
            params["vision_engine"] = params.get("vision_engine") or ENGINE_ID
            params["pipeline_version"] = params.get("pipeline_version") or PROMPT_PIPELINE_VERSION
            params["manual_edit"] = True
            if "sampler" not in params:
                params["sampler"] = "DPM++ 2M Karras"
            if "steps" not in params:
                params["steps"] = 30
            if "cfg_scale" not in params:
                params["cfg_scale"] = 7.0
            if "aspect_ratio" not in params:
                params["aspect_ratio"] = "4:5"

            updated = dict(existing)
            updated["positive_prompt"] = positive
            updated["negative_prompt"] = negative
            updated["visual_tags"] = visual_tags
            updated["parameters"] = params
            structured = existing.get("structured_vision")
            updated["exports"] = build_export_variants(
                positive,
                negative,
                structured=structured if isinstance(structured, dict) else None,
            )
            # Drop history key from payload so set() can push correctly from existing
            updated.pop("history", None)
            _prompt_cache.set(rel_path, updated, push_history=True)
            self._send_json(_prompt_cache.get(rel_path, filename) or updated)
            return

        if parsed.path == "/api/favorite":
            try:
                data = self._read_json_body()
            except json.JSONDecodeError:
                self.send_error(400, "Invalid JSON body")
                return
            rel_path = (data.get("path") or "").strip()
            if not rel_path:
                self.send_error(400, "path required")
                return
            rel_path = urllib.parse.unquote(rel_path)
            if not _archive.resolve_path(rel_path):
                self.send_error(404, "Photo not found")
                return
            if "favorite" in data:
                fav = bool(data.get("favorite"))
                _favorites.set_favorite(rel_path, fav)
            else:
                fav = _favorites.toggle(rel_path)
            self._send_json({"status": "ok", "path": rel_path, "favorite": fav})
            return

        if parsed.path == "/api/generation/rate":
            try:
                data = self._read_json_body()
            except json.JSONDecodeError:
                self.send_error(400, "Invalid JSON body")
                return
            gen_id = (data.get("gen_id") or "").strip()
            if not gen_id:
                self.send_error(400, "gen_id required")
                return
            from promptstudio.storage.db import ArchiveIndex

            try:
                # Passed through unconverted on purpose: int("2") would quietly
                # accept a string and int(1.9) would round a nonsense value into
                # range. rate_generation is the one place the scale is defined.
                rating = data.get("rating")
                ok = ArchiveIndex.get().rate_generation(gen_id, rating)
            except ValueError as e:
                self.send_error(400, str(e))
                return
            if not ok:
                self.send_error(404, "Generation not found")
                return
            self._send_json({"status": "ok", "gen_id": gen_id, "rating": rating})
            return

        if parsed.path == "/api/labels":
            try:
                data = self._read_json_body()
            except json.JSONDecodeError:
                self.send_error(400, "Invalid JSON body")
                return
            rel_path = (data.get("path") or data.get("rel_path") or "").strip()
            if not rel_path:
                self.send_error(400, "path required")
                return
            rel_path = urllib.parse.unquote(rel_path)
            from promptstudio.storage.db import ArchiveIndex

            try:
                ok = ArchiveIndex.get().set_label(rel_path, data.get("label"))
            except ValueError as e:
                self.send_error(400, str(e))
                return
            if not ok:
                self.send_error(404, "Photo not found")
                return
            row = ArchiveIndex.get().get_label(rel_path)
            self._send_json({"status": "ok", "path": rel_path, "label": row})
            return

        self.send_error(404, "Not found")
        return

    @_error_boundary
    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path

        if path == "/api/trash/restore":
            try:
                data = self._read_json_body()
            except json.JSONDecodeError:
                self.send_error(400, "Invalid JSON body")
                return
            ids = data.get("ids")
            if not isinstance(ids, list):
                ids = [data.get("id")] if data.get("id") else []
            ids = [str(i).strip() for i in ids if str(i or "").strip()]
            if not ids:
                self.send_error(400, "id or ids required")
                return
            results = [_trash.restore(entry_id) for entry_id in ids]
            restored = [r for r in results if r.get("status") == "restored"]
            self._send_json(
                {
                    "status": "ok",
                    "restored": len(restored),
                    "failed": len(results) - len(restored),
                    "results": results,
                }
            )
            return

        if path == "/api/trash/purge":
            try:
                data = self._read_json_body()
            except json.JSONDecodeError:
                self.send_error(400, "Invalid JSON body")
                return
            if data.get("all"):
                self._send_json({"status": "ok", "purged": _trash.empty()})
                return
            if data.get("expired"):
                days = data.get("days")
                try:
                    days = int(days) if days is not None else None
                except (TypeError, ValueError):
                    days = None
                self._send_json({"status": "ok", "purged": _trash.purge_expired(days)})
                return
            ids = data.get("ids")
            if not isinstance(ids, list):
                ids = [data.get("id")] if data.get("id") else []
            ids = [str(i).strip() for i in ids if str(i or "").strip()]
            if not ids:
                self.send_error(400, "id, ids, all, or expired required")
                return
            purged = sum(1 for entry_id in ids if _trash.purge(entry_id))
            self._send_json(
                {"status": "ok", "purged": purged, "failed": len(ids) - purged}
            )
            return

        if path == "/api/creator/create":
            try:
                data = self._read_json_body()
                name = _archive.create_creator(data.get("name", ""))
                self._send_json({"status": "created", "name": name})
            except ValueError:
                self.send_error(400, "Invalid creator handle name")
            except OSError as e:
                self.send_error(500, f"Error creating creator folder: {e}")
            return

        if path == "/api/photo/upload":
            try:
                fields, files = parse_multipart_data(self.rfile, self.headers)
                creator_name = fields.get("creator", "").strip()
                file_obj = files.get("file")
                if not creator_name or not file_obj:
                    self.send_error(400, "Creator handle and file are required")
                    return
                filename = _archive.save_upload(
                    creator_name, file_obj["filename"], file_obj["content"]
                )
                self._send_json(
                    {"status": "uploaded", "creator": creator_name, "filename": filename}
                )
            except OSError as e:
                self.send_error(500, f"Error uploading image: {e}")
            return

        if path == "/api/sync/cancel":
            if _sync.request_cancel():
                self._send_json({"status": "cancelling"})
            else:
                self._send_json({"status": "idle"})
            return

        if path == "/api/scrape/enqueue":
            if not CREATOR_SCRAPE_QUEUE_ENABLED:
                self.send_error(404, "Creator scrape queue disabled")
                return
            try:
                data = self._read_json_body()
                username = (data.get("username") or "").strip().lstrip("@")
                if not username:
                    self.send_error(400, "username required")
                    return
                # Defaults to instagram so existing clients are unaffected.
                source_name = (data.get("source") or "instagram").strip().lower()
                mode = (data.get("mode") or "full").strip().lower()
                if mode not in VALID_MODES:
                    self.send_error(400, "mode must be full, bounded, or latest")
                    return
                # Raw request values only — CreatorScrapeQueue.enqueue resolves
                # them through ScrapeOptions.normalize. Deriving `deep` and
                # `max_posts` here as well just produced an answer the queue
                # overwrote, so the two could drift without anything failing.
                max_posts = data.get("max_posts")
                max_posts = int(max_posts) if max_posts not in (None, "") else None
                include_videos = (
                    bool(data.get("include_videos"))
                    if "include_videos" in data
                    else INCLUDE_VIDEOS_DEFAULT
                )
                priority = int(data.get("priority") or 0)
                # Resolving the target here (rather than in the queue) validates
                # the handle for its platform and yields the real archive folder,
                # which for non-Instagram sources is suffixed (e.g. nina__x).
                from promptstudio.scraping.sources import get_source

                target = get_source(source_name).parse_target(username)
                if target.source == "instagram":
                    cooled = _instagram_cooldown_block()
                    if cooled:
                        self._send_json(cooled, 409)
                        return
                folder = ensure_creator_folder(target.folder)
                q = CreatorScrapeQueue.get()
                out = q.enqueue(
                    username,
                    mode=mode,
                    deep=_as_bool(data.get("deep", True), default=True),
                    max_posts=max_posts,
                    include_videos=include_videos,
                    priority=priority,
                    folder_name=folder["name"],
                    folder_created=folder["created"],
                    # catch_up_only keeps a true "latest" (streak stop + low
                    # ceiling); without it, latest is upgraded to full+deep.
                    catch_up_only=_as_bool(data.get("catch_up_only", False)),
                    source=target.source,
                )
                started = False
                if out["status"] == "queued":
                    # Only this job's lane: draining the others here would
                    # report "started" for work the caller did not enqueue.
                    started = _sync.try_drain_creator_queue(target.source)
                    if started:
                        out["status"] = "started"
                self._send_json(
                    {
                        **out,
                        "source": target.source,
                        "target_url": target.url,
                        "folder": folder["name"],
                        "folder_created": folder["created"],
                    }
                )
            except ValueError as e:
                self.send_error(400, str(e))
            except (json.JSONDecodeError, TypeError):
                self.send_error(400, "Invalid JSON body")
            return

        if path == "/api/scrape/cancel":
            if not CREATOR_SCRAPE_QUEUE_ENABLED:
                self.send_error(404, "Creator scrape queue disabled")
                return
            try:
                data = self._read_json_body()
                scope = (data.get("scope") or "job").strip().lower()
                try:
                    source = _parse_body_source(data)
                except _BadSource as e:
                    self.send_error(400, str(e))
                    return
                q = CreatorScrapeQueue.get()
                if scope == "all_pending":
                    n = q.cancel_all_pending(source)
                    cancel_running = bool(data.get("cancel_running"))
                    if cancel_running:
                        _sync.request_cancel(source)
                    self._send_json(
                        {
                            "status": "ok",
                            "cancelled_pending": n,
                            "cancel_running": cancel_running,
                            "source": source or "all",
                        }
                    )
                    return
                job_id = (data.get("job_id") or "").strip()
                if not job_id:
                    self.send_error(400, "job_id required")
                    return
                job = q.get_job(job_id)
                if not job:
                    self.send_error(404, "job not found")
                    return
                if job.get("status") == "pending":
                    q.cancel_pending(job_id)
                    self._send_json({"status": "cancelled", "job_id": job_id})
                    return
                if job.get("status") == "running":
                    # The job names its own lane — cancelling by job_id must not
                    # stop a different platform that happens to also be running.
                    _sync.request_cancel(job.get("source") or "instagram")
                    self._send_json(
                        {
                            "status": "cancelling",
                            "job_id": job_id,
                            "source": job.get("source") or "instagram",
                        }
                    )
                    return
                self._send_json(
                    {"status": "already_terminal", "job_id": job_id, "job_status": job.get("status")}
                )
            except (ValueError, json.JSONDecodeError):
                self.send_error(400, "Invalid JSON body")
            return

        if path == "/api/scrape/pause":
            if not CREATOR_SCRAPE_QUEUE_ENABLED:
                self.send_error(404, "Creator scrape queue disabled")
                return
            try:
                data = self._read_json_body()
            except Exception:
                data = {}
            reason = ""
            source = None
            if isinstance(data, dict):
                reason = str(data.get("reason") or "Paused by user")
                try:
                    source = _parse_body_source(data)
                except _BadSource as e:
                    self.send_error(400, str(e))
                    return
            # source=None pauses every lane, which is what the global button
            # means. A named source pauses only that lane.
            CreatorScrapeQueue.get().pause(
                reason or "Paused by user", source=source
            )
            self._send_json(
                {
                    "status": "paused",
                    "pause_reason": reason or "Paused by user",
                    "source": source or "all",
                }
            )
            return

        if path == "/api/scrape/resume":
            if not CREATOR_SCRAPE_QUEUE_ENABLED:
                self.send_error(404, "Creator scrape queue disabled")
                return
            try:
                data = self._read_json_body()
            except Exception:
                data = {}
            source = None
            if isinstance(data, dict):
                try:
                    source = _parse_body_source(data)
                except _BadSource as e:
                    self.send_error(400, str(e))
                    return
            CreatorScrapeQueue.get().resume(source)
            started = _sync.try_drain_creator_queue(source)
            self._send_json(
                {
                    "status": "resumed",
                    "drain_started": started,
                    "source": source or "all",
                }
            )
            return

        if path == "/api/sync/saved":
            cooled = _instagram_cooldown_block()
            if cooled:
                self._send_json(cooled, 409)
                return
            blocked = _creator_queue_blocks_oneshot()
            if blocked:
                self._send_json(blocked, 409)
                return
            def job(log, on_rate_limit=None):
                from promptstudio.scraping.sources.base import SourceContext
                from promptstudio.scraping.sources.instagram_source import run_saved

                ctx = SourceContext(
                    save_dir=SAVED_DIR,
                    log=log,
                    should_cancel=lambda: _sync.is_cancel_requested("instagram"),
                    on_rate_limit=on_rate_limit,
                )
                return run_saved(ctx)

            # No is_running() pre-check: start_job takes the Instagram lease and
            # flips status under one lock, so it is the only answer that cannot
            # be stale by the time the job starts.
            if _sync.start_job("saved", job, source="instagram"):
                self._send_json({"status": "started", "job_type": "saved"})
            else:
                self._send_json({"status": "busy", "message": _sync.last_refusal}, 409)
            return

        if path == "/api/sync/creator":
            try:
                data = self._read_json_body()
                username = (data.get("username") or "").strip().lstrip("@")
                if not username:
                    self.send_error(400, "username required")
                    return
                # Prefer serial scrape queue: full feed + deep gap-fill (not a top-N slice).
                # One-shot bounded path remains only when queue disabled or client forces oneshot.
                force_oneshot = data.get("oneshot", False)
                if isinstance(force_oneshot, str):
                    force_oneshot = force_oneshot.lower() in ("1", "true", "yes")
                cooled = _instagram_cooldown_block()
                if cooled:
                    self._send_json(cooled, 409)
                    return
                if CREATOR_SCRAPE_QUEUE_ENABLED and _scrape_queue and not force_oneshot:
                    if "include_videos" in data:
                        include_videos = bool(data.get("include_videos"))
                    else:
                        include_videos = INCLUDE_VIDEOS_DEFAULT
                    folder = ensure_creator_folder(username)
                    q = CreatorScrapeQueue.get()
                    out = q.enqueue(
                        username,
                        mode="full",
                        deep=True,
                        max_posts=None,
                        include_videos=include_videos,
                        priority=0,
                        folder_name=folder["name"],
                        folder_created=folder["created"],
                    )
                    started = False
                    if out["status"] == "queued":
                        # This route is Instagram-only.
                        started = _sync.try_drain_creator_queue("instagram")
                        if started:
                            out["status"] = "started"
                    self._send_json(
                        {
                            **out,
                            "job_type": "creator_queue",
                            "username": username,
                            "include_videos": include_videos,
                            "mode": "full",
                            "deep": True,
                            "folder": folder["name"],
                            "folder_created": folder["created"],
                            "routed": "scrape_queue",
                        }
                    )
                    return

                # Same resolution the queue path uses — a one-shot "latest" is
                # upgraded to a deep full walk rather than stopping at 50.
                opts = ScrapeOptions.normalize(
                    data.get("mode"),
                    deep=_as_bool(data.get("deep", True), default=True),
                    max_posts=int(data.get("max_posts", DEFAULT_MAX_POSTS_PER_CREATOR)),
                    include_videos=(
                        bool(data.get("include_videos"))
                        if "include_videos" in data
                        else INCLUDE_VIDEOS_DEFAULT
                    ),
                )
                mode = opts.mode
                deep = opts.deep
                include_videos = opts.include_videos
                blocked = _creator_queue_blocks_oneshot()
                if blocked:
                    self._send_json(blocked, 409)
                    return

                def job(log, on_rate_limit=None):
                    from promptstudio.scraping.sources import get_source
                    from promptstudio.scraping.sources.base import SourceContext

                    source = get_source("instagram")
                    target = source.parse_target(username)
                    ctx = SourceContext(
                        save_dir=SAVED_DIR,
                        log=log,
                        should_cancel=lambda: _sync.is_cancel_requested("instagram"),
                        on_rate_limit=on_rate_limit,
                    )
                    return source.run(target, opts, ctx)

                if _sync.start_job("creator", job, source="instagram"):
                    self._send_json(
                        {
                            "status": "started",
                            "job_type": "creator",
                            "username": username,
                            "include_videos": include_videos,
                            "mode": mode,
                            "deep": deep,
                            "routed": "oneshot",
                        }
                    )
                else:
                    self._send_json(
                        {"status": "busy", "message": _sync.last_refusal}, 409
                    )
            except (ValueError, json.JSONDecodeError):
                self.send_error(400, "Invalid JSON body")
            return

        if path == "/api/sync/following":
            try:
                from promptstudio.config import DEFAULT_ACCOUNTS_PER_DAY, clamp_ig_posts

                cooled = _instagram_cooldown_block()
                if cooled:
                    self._send_json(cooled, 409)
                    return
                data = self._read_json_body()
                max_accounts = int(
                    data.get("max_accounts", data.get("accounts_per_day", DEFAULT_ACCOUNTS_PER_DAY))
                )
                max_posts = clamp_ig_posts(int(data.get("max_posts", 20)))
                min_media_count = int(data.get("min_media_count", 5))
                if "include_videos" in data:
                    include_videos = bool(data.get("include_videos"))
                else:
                    include_videos = INCLUDE_VIDEOS_DEFAULT
                keywords_raw = data.get("keywords", "")
                if isinstance(keywords_raw, list):
                    keywords = keywords_raw
                elif isinstance(keywords_raw, str) and keywords_raw.strip():
                    keywords = [k.strip() for k in keywords_raw.split(",") if k.strip()]
                else:
                    keywords = None
                blocked = _creator_queue_blocks_oneshot()
                if blocked:
                    self._send_json(blocked, 409)
                    return

                def job(log, on_rate_limit=None):
                    from promptstudio.scraping.sources.base import SourceContext
                    from promptstudio.scraping.sources.instagram_source import (
                        run_following,
                    )

                    ctx = SourceContext(
                        save_dir=SAVED_DIR,
                        log=log,
                        should_cancel=lambda: _sync.is_cancel_requested("instagram"),
                        on_rate_limit=on_rate_limit,
                    )
                    return run_following(
                        ctx,
                        max_accounts=max_accounts,
                        max_posts_per_account=max_posts,
                        keywords=keywords,
                        min_media_count=min_media_count,
                        include_videos=include_videos,
                    )

                if _sync.start_job("following", job, source="instagram"):
                    self._send_json(
                        {
                            "status": "started",
                            "job_type": "following",
                            "max_accounts": max_accounts,
                            "accounts_per_day": max_accounts,
                            "keywords": keywords,
                            "include_videos": include_videos,
                        }
                    )
                else:
                    self._send_json(
                        {"status": "busy", "message": _sync.last_refusal}, 409
                    )
            except (ValueError, json.JSONDecodeError):
                self.send_error(400, "Invalid JSON body")
            return

        if path == "/api/prompt/batch":
            try:
                data = self._read_json_body()
                creator = data.get("creator") or None
                force = bool(data.get("force", False))
                limit = data.get("limit")
                limit = int(limit) if limit is not None else None
                paths_raw = data.get("paths")
                paths = None
                if isinstance(paths_raw, list):
                    paths = [str(p).strip() for p in paths_raw if str(p).strip()]
                    if not paths:
                        paths = None
                # No is_running() pre-check against _batch: it takes
                # the same Ollama lease, and start_batch decides under it.
                pending = _batch.list_uncached(creator=creator, force=force, paths=paths)
                if limit:
                    pending = pending[:limit]
                if not pending:
                    self._send_json({"status": "nothing_to_do", "pending": 0})
                    return
                if _batch.start_batch(
                    creator=creator, force=force, limit=limit, paths=paths
                ):
                    self._send_json(
                        {
                            "status": "started",
                            "pending": len(pending),
                            "creator": creator,
                            "paths": len(paths) if paths else None,
                        }
                    )
                else:
                    self._send_json(
                        {
                            "status": "busy",
                            "message": _batch.last_refusal or "Batch already running",
                        },
                        409,
                    )
            except (ValueError, json.JSONDecodeError):
                self.send_error(400, "Invalid JSON body")
            return

        if path == "/api/prompt/batch/cancel":
            if _batch.cancel():
                self._send_json({"status": "cancelling"})
            else:
                self._send_json({"status": "idle", "message": "No batch job running"})
            return

        if path == "/api/labels/seed":
            from promptstudio.storage.db import ArchiveIndex

            favs = _favorites.load()
            trash_entries, _total = _trash.list_entries()
            trash_paths = [
                str(e.get("rel_path") or "").strip()
                for e in trash_entries
                if str(e.get("rel_path") or "").strip()
            ]
            result = ArchiveIndex.get().seed_labels(
                keep_paths=list(favs),
                discard_paths=trash_paths,
            )
            result["status"] = "ok"
            result["counts"] = ArchiveIndex.get().label_counts()
            self._send_json(result)
            return

        if path == "/api/views":
            try:
                data = self._read_json_body()
            except json.JSONDecodeError:
                self.send_error(400, "Invalid JSON body")
                return
            from promptstudio.storage.db import ArchiveIndex

            try:
                row = ArchiveIndex.get().create_saved_view(
                    str(data.get("name") or ""),
                    data.get("filters") if isinstance(data.get("filters"), dict) else {},
                )
            except ValueError as e:
                self.send_error(400, str(e))
                return
            self._send_json({"status": "ok", "view": row})
            return

        if path == "/api/collections":
            try:
                data = self._read_json_body()
            except json.JSONDecodeError:
                self.send_error(400, "Invalid JSON body")
                return
            from promptstudio.storage.db import ArchiveIndex

            try:
                row = ArchiveIndex.get().create_collection(str(data.get("name") or ""))
            except ValueError as e:
                self.send_error(400, str(e))
                return
            self._send_json({"status": "ok", "collection": row})
            return

        if path == "/api/collections/items":
            try:
                data = self._read_json_body()
            except json.JSONDecodeError:
                self.send_error(400, "Invalid JSON body")
                return
            try:
                cid = int(data.get("id") or 0)
            except (TypeError, ValueError):
                cid = 0
            paths = data.get("paths") or []
            if not cid or not isinstance(paths, list):
                self.send_error(400, "id and paths required")
                return
            from promptstudio.storage.db import ArchiveIndex

            try:
                result = ArchiveIndex.get().add_collection_items(cid, paths)
            except KeyError:
                self.send_error(404, "Collection not found")
                return
            result["status"] = "ok"
            result["id"] = cid
            self._send_json(result)
            return

        if path == "/api/taste/train":
            try:
                data = self._read_json_body()
            except json.JSONDecodeError:
                data = {}
            force = _as_bool((data or {}).get("force"))
            job = _taste_job()
            if job.start(force=force):
                self._send_json({"status": "started", **job.get_status()})
            else:
                self._send_json(
                    {
                        "status": "busy",
                        "message": job.last_refusal or "Taste training already running",
                    },
                    409,
                )
            return

        if path == "/api/taste/cancel":
            job = _taste_job()
            if job.cancel():
                self._send_json({"status": "cancelling"})
            else:
                self._send_json({"status": "idle", "message": "No taste job running"})
            return

        if path == "/api/classify/start":
            try:
                data = self._read_json_body()
                # No creator means the whole archive. Requiring one capped
                # coverage at whatever the user remembered to run folder by
                # folder, while batch analyze has always been archive-wide.
                creator = (data.get("creator") or "").strip().lstrip("@")
                limit = data.get("limit")
                result = _classify.start(
                    creator,
                    force=_as_bool(data.get("force")),
                    include_videos=_as_bool(
                        data.get("include_videos"), default=INCLUDE_VIDEOS_DEFAULT
                    ),
                    limit=int(limit) if limit is not None else None,
                    only_unclassified=_as_bool(
                        data.get("only_unclassified"), default=True
                    ),
                    rescore_stale=_as_bool(data.get("rescore_stale")),
                )
                status = result.get("status")
                code = 200
                if status == "busy":
                    code = 409
                elif status == "ollama_down":
                    code = 503
                elif status == "bad_creator":
                    code = 400
                self._send_json(result, code)
            except (ValueError, json.JSONDecodeError):
                self.send_error(400, "Invalid JSON body")
            return

        if path == "/api/classify/cancel":
            ok = _classify.cancel()
            self._send_json(
                {
                    "status": "cancelling" if ok else "idle",
                    "running": _classify.is_running(),
                }
            )
            return

        if path == "/api/classify/verdict":
            # Manual override: pins a file to keep/reject regardless of tier, and
            # survives re-classify. `verdict: null` hands it back to the model.
            # `rel_paths` is the bulk form (U13) — one transaction, same
            # unclassified-is-missing contract as the single-path call.
            try:
                data = self._read_json_body()
            except json.JSONDecodeError:
                self.send_error(400, "Invalid JSON body")
                return
            raw_list = data.get("rel_paths")
            bulk = isinstance(raw_list, list)
            if bulk:
                paths = [
                    str(p).strip().replace("\\", "/")
                    for p in raw_list
                    if str(p).strip()
                ]
            else:
                rel_path = (data.get("rel_path") or "").strip().replace("\\", "/")
                paths = [rel_path] if rel_path else []
            if not paths:
                self.send_error(400, "rel_path or rel_paths required")
                return
            if len(paths) > MAX_PHOTO_IDS_API:
                self.send_error(
                    400, f"rel_paths exceeds {MAX_PHOTO_IDS_API} (got {len(paths)})"
                )
                return
            raw = data.get("verdict")
            value = None if raw in (None, "", "auto") else str(raw).strip().lower()
            if value not in (None, "keep", "reject"):
                self.send_error(400, "verdict must be keep, reject, or null")
                return
            from promptstudio.storage.db import ArchiveIndex

            index = ArchiveIndex.get()
            result = index.set_manual_verdicts(paths, value)
            updated = result["updated"]
            missing = result["missing"]
            if not bulk:
                if not updated:
                    self._send_json(
                        {
                            "status": "not_classified",
                            "message": "classify this creator before overriding",
                            "rel_path": paths[0],
                        },
                        404,
                    )
                    return
                self._send_json(
                    {
                        "status": "ok",
                        "rel_path": paths[0],
                        "verdict": index.get_verdict(paths[0]),
                    }
                )
                return
            verdicts = index.verdicts_for(updated) if updated else {}
            self._send_json(
                {
                    "status": "ok",
                    "verdict": value,
                    "updated": updated,
                    "missing": missing,
                    "verdicts": verdicts,
                }
            )
            return

        if path == "/api/creator/style/rebuild":
            try:
                data = self._read_json_body()
                creator = (data.get("creator") or "").strip().lstrip("@")
                if not creator:
                    self.send_error(400, "creator required")
                    return
                entry = _styles.rebuild_for_creator(creator)
                if not entry:
                    self._send_json(
                        {
                            "status": "insufficient_data",
                            "creator": creator,
                            "message": "Need more cached prompts to build a style",
                        }
                    )
                    return
                self._send_json({"status": "ok", "style": entry})
            except (ValueError, json.JSONDecodeError):
                self.send_error(400, "Invalid JSON body")
            return

        if path == "/api/prompt/restore":
            try:
                data = self._read_json_body()
                rel_path = (data.get("path") or "").strip()
                index = int(data.get("index", 0))
                if not rel_path:
                    self.send_error(400, "path required")
                    return
                rel_path = urllib.parse.unquote(rel_path)
                if not _archive.resolve_path(rel_path):
                    self.send_error(404, "Photo not found")
                    return
                restored = _prompt_cache.restore_history(rel_path, index)
                if not restored:
                    self.send_error(404, "History entry not found")
                    return
                self._send_json(restored)
            except (ValueError, json.JSONDecodeError):
                self.send_error(400, "Invalid JSON body")
            return

        if path == "/api/prompt/mode-e":
            try:
                data = self._read_json_body()
                rel_path = (data.get("path") or "").strip()
                if not rel_path:
                    self.send_error(400, "path required")
                    return
                rel_path = urllib.parse.unquote(rel_path)
                if not _archive.resolve_path(rel_path):
                    self.send_error(404, "Photo not found")
                    return
                from promptstudio.prompts.comfy_mode import build_mode_e_bundle

                filename = os.path.basename(rel_path)
                cached = _prompt_cache.get(rel_path, filename) or {}
                positive = data.get("positive_prompt")
                negative = data.get("negative_prompt")
                if positive is None:
                    positive = cached.get("positive_prompt") or ""
                if negative is None:
                    negative = cached.get("negative_prompt") or ""
                structured = cached.get("structured_vision")
                if not isinstance(structured, dict):
                    structured = None
                bundle = build_mode_e_bundle(
                    positive=str(positive),
                    negative=str(negative),
                    structured=structured,
                )
                apply = bool(data.get("apply"))
                result = {
                    "path": rel_path,
                    "positive_prompt": bundle["positive"],
                    "negative_prompt": bundle["negative"],
                    "anti_terms": list(bundle["anti_terms"]),
                    "source": bundle["source"],
                    "clothing_keys": list(bundle["clothing_keys"]),
                    "applied": False,
                }
                if apply:
                    updated = dict(cached)
                    updated["positive_prompt"] = bundle["positive"]
                    updated["negative_prompt"] = bundle["negative"]
                    updated["exports"] = build_export_variants(
                        bundle["positive"],
                        bundle["negative"],
                        structured=structured,
                    )
                    params = dict(updated.get("parameters") or {})
                    params["mode_e_applied"] = True
                    updated["parameters"] = params
                    updated.pop("history", None)
                    _prompt_cache.set(rel_path, updated, push_history=True)
                    result["applied"] = True
                    # Avoid nesting huge cache blobs; return exports only
                    saved = _prompt_cache.get(rel_path, filename) or updated
                    result["exports"] = (saved.get("exports") or {})
                self._send_json(result)
            except (ValueError, json.JSONDecodeError, TypeError) as exc:
                log.warning("/api/prompt/mode-e bad request: %s", exc)
                self.send_error(400, "Invalid JSON body")
            except Exception as exc:
                import traceback

                traceback.print_exc()
                log.exception("/api/prompt/mode-e failed")
                try:
                    self._send_json({"status": "error", "message": str(exc)}, 500)
                except Exception:
                    pass
            return

        if path == "/api/comfy/generate":
            try:
                data = self._read_json_body()
                rel_path = (data.get("path") or "").strip()
                if not rel_path:
                    self.send_error(400, "path required")
                    return
                rel_path = urllib.parse.unquote(rel_path)
                if not _archive.resolve_path(rel_path):
                    self.send_error(404, "Photo not found")
                    return
                if not check_comfy_health().get("comfy"):
                    self._send_json(
                        {"status": "offline", "message": "ComfyUI is not reachable"},
                        503,
                    )
                    return
                # No is_running() pre-check: _comfy.start() takes the ComfyUI
                # lease and flips status under one lock. Building the prompt
                # below is cheap, so there is nothing to short-circuit for.

                # Prompt selection, Mode E and the numeric defaults all live in
                # comfy/params.py, because A2's batch runner makes exactly the
                # same decisions and two copies would drift.
                try:
                    params = resolve_generation_params(rel_path, data)
                except NoPromptError as exc:
                    self.send_error(400, str(exc))
                    return
                except WorkflowError as exc:
                    # An unnamed or misspelled workflow. 400 with the registry's
                    # own message, which names what is available — the generic
                    # "Invalid JSON body" below would be a lie about the fault.
                    self.send_error(400, str(exc))
                    return

                if _comfy.start(
                    source_rel=rel_path,
                    positive=params.positive,
                    negative=params.negative,
                    workflow=params.workflow,
                    aspect=params.aspect,
                    steps=params.steps,
                    cfg=params.cfg,
                    denoise=params.denoise,
                    seed=params.seed,
                    checkpoint=params.checkpoint,
                    # Both were computed here and thrown away. Without them the
                    # generations table cannot answer "did Mode E help" or "which
                    # prompt engine produced the winners" (design §3.3).
                    mode_e=params.mode_e,
                    prompt_version=params.prompt_version,
                ):
                    payload = {
                        "status": "started",
                        "path": rel_path,
                        "variant": params.variant,
                        "workflow": params.workflow,
                        "denoise": params.denoise,
                        "steps": params.steps,
                        "cfg": params.cfg,
                        # The resolved seed, not the request's — `seed` is None
                        # whenever the client did not pin one, which is the
                        # default. ComfyJobManager.start() materialises it.
                        "seed": _comfy.get_status().get("seed"),
                        "use_mode_e": params.mode_e,
                        "positive_prompt": params.positive[:400],
                        "negative_prompt": params.negative[:300],
                    }
                    if params.mode_meta:
                        payload["mode_e"] = params.mode_meta
                    self._send_json(payload)
                else:
                    self._send_json(
                        {"status": "busy", "message": _comfy.last_refusal}, 409
                    )
            except (ValueError, json.JSONDecodeError):
                self.send_error(400, "Invalid JSON body")
            return

        if path == "/api/comfy/batch":
            try:
                data = self._read_json_body()
            except (ValueError, json.JSONDecodeError):
                self.send_error(400, "Invalid JSON body")
                return
            if not check_comfy_health().get("comfy"):
                self._send_json(
                    {"status": "offline", "message": "ComfyUI is not reachable"}, 503
                )
                return
            # No is_running() pre-check, same as the one-shot route: start()
            # takes the ComfyUI lease and flips status under one lock.
            try:
                result = _comfy_batch.start(**_batch_generate_args(data))
            except WorkflowError as exc:
                self.send_error(400, str(exc))
                return
            self._send_json(result, 409 if result.get("status") == "busy" else 200)
            return

        if path == "/api/comfy/batch/cancel":
            ok = _comfy_batch.cancel()
            self._send_json(
                {
                    "status": "cancelling" if ok else "idle",
                    "running": _comfy_batch.is_running(),
                }
            )
            return

        # Same as do_DELETE: there is no SimpleHTTPRequestHandler.do_POST.
        self.send_error(404, "Not found")
        return

    @_error_boundary
    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        query = urllib.parse.parse_qs(parsed.query)

        if path == "/api/media/detail":
            # Reel/photo inspector: caption, Instagram link (not vision prompts)
            rel_path = (query.get("path", [""])[0] or "").strip().replace("\\", "/")
            if not rel_path:
                self.send_error(400, "path required")
                return
            full_path = _archive.resolve_path(rel_path)
            if not full_path:
                self.send_error(404, "File not found")
                return
            from promptstudio.config import VIDEO_EXTENSIONS
            from promptstudio.storage.metadata import load_post_metadata
            from promptstudio.storage.thumbs import thumb_url

            filename = os.path.basename(full_path)
            creator = rel_path.split("/")[0] if "/" in rel_path else ""
            ext = os.path.splitext(filename)[1].lower()
            is_video = ext in VIDEO_EXTENSIONS or ext == ".mov"
            meta = load_post_metadata(full_path) or {}
            try:
                file_size = os.path.getsize(full_path)
            except OSError:
                file_size = 0
            fav = False
            try:
                fav = bool(_favorites.is_favorite(rel_path))
            except Exception:
                fav = False
            shortcode = meta.get("shortcode") or ""
            post_url = meta.get("post_url") or (
                f"https://www.instagram.com/p/{shortcode}/" if shortcode else ""
            )
            # Prefer reel URL shape when video + shortcode
            if is_video and shortcode and "/p/" in post_url:
                post_url = f"https://www.instagram.com/reel/{shortcode}/"
            from promptstudio.storage.db import ArchiveIndex

            verdict = ArchiveIndex.get().get_verdict(rel_path)
            if verdict:
                verdict["tier_label"] = TIER_LABELS.get(
                    int(verdict.get("tier", -1)), "Unknown"
                )
            self._send_json(
                {
                    "verdict": verdict or None,
                    "rel_path": rel_path,
                    "filename": filename,
                    "creator": creator or meta.get("owner_username") or "",
                    "is_video": is_video,
                    "url": f"/media/{urllib.parse.quote(rel_path)}",
                    "thumb_url": thumb_url(rel_path),
                    "file_size": file_size,
                    "favorite": fav,
                    "caption": meta.get("caption") or "",
                    "shortcode": shortcode,
                    "post_url": post_url,
                    "post_id": meta.get("post_id") or "",
                    "taken_at": meta.get("taken_at") or "",
                    "downloaded_at": meta.get("downloaded_at") or "",
                    "carousel_index": meta.get("carousel_index", 0),
                    "source": meta.get("source") or "",
                }
            )
            return

        if path == "/api/classify/sheet":
            # The contact sheet the reel was actually judged from. Served from
            # _classify/ through safe_join rather than the archive resolver:
            # _classify is an EXCLUDED_FOLDER, so the media route cannot reach
            # it — which is exactly why it is safe to keep sheets there.
            from promptstudio.scraping.media_classifier import sheet_full_path

            rel_path = (query.get("rel_path", [""])[0] or "").replace("\\", "/")
            sheet = sheet_full_path(rel_path) if rel_path else ""
            if not sheet or not os.path.isfile(sheet):
                self.send_error(404, "No contact sheet for this file")
                return
            self._serve_local_file(
                sheet, "image/jpeg", cache_control="public, max-age=86400"
            )
            return

        if path.startswith("/media/thumb/"):
            rel_path = urllib.parse.unquote(path[len("/media/thumb/") :])
            full_path = _archive.resolve_path(rel_path)
            if not full_path:
                self.send_error(404, "File not found")
                return
            self._serve_thumb(rel_path, full_path)
            return

        if path.startswith("/media/"):
            rel_path = urllib.parse.unquote(path[7:])
            full_path = _archive.resolve_path(rel_path)
            if full_path:
                ext = os.path.splitext(full_path)[1].lower()
                content_type = "image/jpeg"
                if ext == ".webp":
                    content_type = "image/webp"
                elif ext == ".png":
                    content_type = "image/png"
                elif ext == ".mp4":
                    content_type = "video/mp4"
                elif ext == ".webm":
                    content_type = "video/webm"
                elif ext == ".mov":
                    content_type = "video/quicktime"
                # CRITICAL: byte-range support so browser can scrub video currentTime
                self._serve_local_file(
                    full_path,
                    content_type,
                    cache_control="public, max-age=3600",
                )
                return
            self.send_error(404, "File not found")
            return

        if path == "/api/creators":
            try:
                source = _parse_source_filter(query)
            except _BadSource as e:
                self.send_error(400, str(e))
                return
            self._send_json(_archive.list_creators(source=source))
            return

        if path == "/api/trash":
            try:
                limit = int(query.get("limit", ["100"])[0] or 100)
            except ValueError:
                limit = 100
            limit = max(1, min(limit, 500))
            try:
                offset = int(query.get("offset", ["0"])[0] or 0)
            except ValueError:
                offset = 0
            entries, total = _trash.list_entries(limit=limit, offset=max(0, offset))
            self._send_json(
                {
                    "entries": [_trash_entry_urls(dict(e)) for e in entries],
                    "total": total,
                    "offset": max(0, offset),
                    "limit": limit,
                    **_trash.stats(),
                }
            )
            return

        if path == "/api/following":
            search = (query.get("search", [""])[0] or "").strip().lower()
            try:
                limit = int(query.get("limit", ["100"])[0] or 100)
            except ValueError:
                limit = 100
            limit = max(1, min(limit, 500))
            accounts = _load_following_accounts()
            if search:
                filtered = []
                for acct in accounts:
                    blob = " ".join(
                        [
                            str(acct.get("username") or ""),
                            str(acct.get("full_name") or ""),
                            str(acct.get("biography") or ""),
                        ]
                    ).lower()
                    if search in blob or search.lstrip("@") in str(
                        acct.get("username") or ""
                    ).lower():
                        filtered.append(acct)
                accounts = filtered
            total = len(accounts)
            self._send_json({"accounts": accounts[:limit], "total": total})
            return

        if path == "/api/photos":
            creator = query.get("creator", [None])[0]
            search = query.get("search", [None])[0]
            unanalyzed_raw = (query.get("unanalyzed", ["false"])[0] or "").lower()
            unanalyzed = unanalyzed_raw in ("1", "true", "yes")
            favorite_raw = (query.get("favorite", ["false"])[0] or "").lower()
            favorite_only = favorite_raw in ("1", "true", "yes")
            media_type = (query.get("media_type", ["all"])[0] or "all").lower()
            if media_type not in ("video", "photo"):
                media_type = None
            verdict = (query.get("verdict", [""])[0] or "").strip().lower()
            if verdict not in VERDICT_FILTERS:
                verdict = None
            label = (query.get("label", [""])[0] or "").strip().lower()
            if label not in LABEL_FILTERS:
                label = None
            try:
                source = _parse_source_filter(query)
            except _BadSource as e:
                self.send_error(400, str(e))
                return
            sort = (query.get("sort", ["name"])[0] or "name").lower()
            if sort not in (
                "name",
                "newest",
                "oldest",
                "posted",
                "posted_oldest",
                "tier",
                "foryou",
            ):
                sort = "name"
            search_mode = (query.get("mode", ["text"])[0] or "text").lower()
            if search_mode not in SEARCH_MODES:
                search_mode = "text"
            collection_raw = (query.get("collection", [""])[0] or "").strip()
            collection_id = None
            if collection_raw:
                try:
                    collection_id = int(collection_raw)
                except ValueError:
                    self.send_error(400, "collection must be an id")
                    return
            # Collapse a carousel into one tile. Anything other than `post` is
            # a 400 rather than a silent fallback: ungrouped rows against a
            # client that believes it is paging in posts drifts a page at a
            # time and ends up skipping content.
            group = (query.get("group", [""])[0] or "").strip().lower()
            if group not in ("", "post"):
                self.send_error(400, f"Unknown group '{group}'. Known: post")
                return
            try:
                offset = int(query.get("offset", ["0"])[0] or 0)
            except ValueError:
                offset = 0
            try:
                limit = int(query.get("limit", [str(MAX_PHOTOS_API_PAGE)])[0] or MAX_PHOTOS_API_PAGE)
            except ValueError:
                limit = MAX_PHOTOS_API_PAGE
            limit = max(1, min(limit, MAX_PHOTOS_API_PAGE))
            offset = max(0, offset)
            ids_only = (query.get("ids", ["0"])[0] or "").lower() in _TRUTHY

            path_exact = (query.get("path", [""])[0] or "").strip() or None
            if ids_only:
                # Whole-pile path list for "select all N". Not a gallery page,
                # so the page cap does not apply — grouping is ignored because
                # selection is per file.
                paths, total = _archive.query_photos(
                    creator=creator,
                    search=search,
                    unanalyzed=unanalyzed,
                    favorite_only=favorite_only,
                    media_type=media_type,
                    verdict=verdict,
                    source=source,
                    path=path_exact,
                    label=label,
                    sort=sort,
                    limit=MAX_PHOTO_IDS_API,
                    offset=0,
                    group_posts=False,
                    search_mode=search_mode,
                    collection_id=collection_id,
                    paths_only=True,
                )
                self._send_json(
                    {
                        "paths": paths,
                        "total": total,
                        "truncated": total > len(paths),
                        "verdict": verdict or "",
                        "source": source or "",
                    }
                )
                return
            photos, total = _archive.query_photos(
                creator=creator,
                search=search,
                unanalyzed=unanalyzed,
                favorite_only=favorite_only,
                media_type=media_type,
                verdict=verdict,
                source=source,
                path=path_exact,
                label=label,
                sort=sort,
                limit=limit,
                offset=offset,
                group_posts=group == "post",
                search_mode=search_mode,
                collection_id=collection_id,
            )
            # What the caller must advance `offset` by. Grouped, `total` counts
            # posts while `photos` carries every slide, so neither array length
            # nor total is the paging unit — this is.
            rows = len(photos)
            if group == "post":
                photos = _expand_post_groups(photos)
            photos = _prompt_cache.annotate_photos(photos)
            photos = _favorites.annotate_photos(photos)
            public_photos = [
                {k: v for k, v in p.items() if k != "full_path"} for p in photos
            ]
            self._send_json(
                {
                    "photos": public_photos,
                    "total": total,
                    "rows": rows,
                    "offset": offset,
                    "limit": limit,
                    "has_more": offset + rows < total,
                    "sort": sort,
                    "verdict": verdict or "",
                    "source": source or "",
                    "group": group,
                    "mode": search_mode,
                }
            )
            return

        if path == "/api/creator/style":
            creator = (query.get("creator", [""])[0] or "").strip().lstrip("@")
            if not creator:
                self.send_error(400, "creator required")
                return
            styles = _styles.load()
            entry = styles.get(creator)
            if not entry:
                self._send_json(
                    {
                        "creator": creator,
                        "style_prefix": "",
                        "top_terms": [],
                        "sample_count": 0,
                        "exists": False,
                    }
                )
                return
            self._send_json({**entry, "exists": True})
            return

        if path == "/api/health":
            self._send_json(_check_ollama_health())
            return

        if path == "/api/sources":
            from promptstudio.scraping.sources import source_info

            self._send_json({"sources": source_info(), "default": "instagram"})
            return

        if path == "/api/workflows":
            # A4 registry, for the generate picker. Only name/label/kind: the
            # client has no business knowing node ids, and shipping them would
            # invite a second injector in JavaScript.
            from promptstudio.comfy import registry

            entries = [spec.summary() for spec in registry.list_workflows()]
            self._send_json(
                {
                    "workflows": entries,
                    "default": registry.default_workflow([e["name"] for e in entries]),
                }
            )
            return

        if path == "/api/prompt":
            rel_path = query.get("path", [None])[0]
            force_refresh = query.get("refresh", ["false"])[0].lower() in ("true", "1", "yes")
            if rel_path:
                rel_path = urllib.parse.unquote(rel_path)
                full_path = _archive.resolve_path(rel_path)
                if full_path:
                    creator = os.path.basename(os.path.dirname(full_path))
                    prompt_data = get_prompt_for_image(
                        full_path, creator, force_refresh=force_refresh, rel_path=rel_path
                    )
                    self._send_json(prompt_data)
                    return
            self.send_error(404, "Prompt not found")
            return

        if path == "/api/stats":
            # prompts_ready comes from the indexed has_prompt column now — see
            # ArchiveIndex.stats(). count_prompts_ready() remains as an exact
            # cache-derived fallback for CLIs and index repair.
            stats = _archive.stats()
            stats["trash_enabled"] = TRASH_ENABLED
            stats["trash_count"] = _trash.count_entries()
            self._send_json(stats)
            return

        if path == "/api/insights":
            # B1 quality dashboard — prompt edit rate and generation counts.
            # Read-only aggregates over data already on disk; no new writes.
            from promptstudio.insights import compute_insights

            self._send_json(compute_insights())
            return

        if path == "/api/journal":
            # Run history for background jobs. Without ?kind, lists what exists.
            kind = (query.get("kind", [""])[0] or "").strip()
            if not kind:
                self._send_json({"kinds": list_journal_kinds()})
                return
            if kind not in list_journal_kinds():
                self._send_json({"kind": kind, "runs": [], "kinds": list_journal_kinds()})
                return
            try:
                limit = int(query.get("limit", ["20"])[0] or 20)
            except ValueError:
                limit = 20
            limit = max(1, min(limit, 200))
            self._send_json(
                {"kind": kind, "limit": limit, "runs": read_journal_runs(kind, limit=limit)}
            )
            return

        if path == "/api/duplicates":
            from promptstudio.storage.db import ArchiveIndex
            from promptstudio.storage.dedupe import review_groups
            from promptstudio.taste import embed_model_name, embedding_near_dup_groups

            kind = (query.get("kind", ["all"])[0] or "all").strip().lower()
            if kind not in ("phash", "embed", "all"):
                self.send_error(400, "kind must be phash, embed, or all")
                return
            index = ArchiveIndex.get()
            groups: List[Dict[str, Any]] = []
            if kind in ("phash", "all"):
                groups.extend(review_groups(index))
            if kind in ("embed", "all"):
                phash_paths = {m["rel_path"] for g in groups for m in g.get("members") or []}
                embeddings = index.all_embeddings(model=embed_model_name())
                embed_clusters = embedding_near_dup_groups(
                    embeddings, exclude=phash_paths
                )
                lookup = index.photos_for_rel_paths(
                    [p for g in embed_clusters for p in g]
                )
                from promptstudio.storage.dedupe import _same_post, pick_review_keeper

                for cluster in embed_clusters:
                    photos: List[Dict[str, Any]] = []
                    for rel in cluster:
                        photo = lookup.get(rel)
                        if not photo:
                            continue
                        full = photo.get("full_path") or ""
                        try:
                            size = os.path.getsize(full) if full and os.path.isfile(full) else 0
                        except OSError:
                            size = 0
                        photos.append({**photo, "file_size": size})
                    if len(photos) < 2 or _same_post(photos):
                        continue
                    keeper = pick_review_keeper(photos)
                    members = []
                    for photo in photos:
                        rel = photo["rel_path"]
                        members.append(
                            {
                                "rel_path": rel,
                                "filename": photo.get("filename") or "",
                                "creator": photo.get("creator") or "",
                                "url": photo.get("url") or "",
                                "thumb_url": photo.get("thumb_url") or "",
                                "favorite": bool(photo.get("favorite")),
                                "file_size": int(photo.get("file_size") or 0),
                                "post_id": photo.get("post_id"),
                                "keeper": rel == keeper,
                                "preselected": (not photo.get("favorite"))
                                and rel != keeper,
                            }
                        )
                    groups.append(
                        {
                            "kind": "embed",
                            "keeper": keeper,
                            "size": len(members),
                            "members": members,
                        }
                    )
            public = []
            for g in groups:
                public.append(
                    {
                        **g,
                        "members": [
                            {k: v for k, v in m.items() if k != "full_path"}
                            for m in g.get("members") or []
                        ],
                    }
                )
            self._send_json(
                {
                    "groups": public,
                    "total_groups": len(public),
                    "total_members": sum(int(g.get("size") or 0) for g in public),
                }
            )
            return

        if path == "/api/views":
            from promptstudio.storage.db import ArchiveIndex

            self._send_json({"views": ArchiveIndex.get().list_saved_views()})
            return

        if path == "/api/collections":
            from promptstudio.storage.db import ArchiveIndex

            self._send_json({"collections": ArchiveIndex.get().list_collections()})
            return

        if path == "/api/taste/status":
            job = _taste_job()
            from promptstudio.storage.db import ArchiveIndex

            weights = ArchiveIndex.get().get_taste_weights()
            self._send_json(
                {
                    **job.get_status(),
                    "model": (weights or {}).get("model") or job.get_status().get("model"),
                    "labelled": (weights or {}).get("labelled"),
                    "trained_at": (weights or {}).get("trained_at"),
                }
            )
            return

        if path == "/api/labels":
            from promptstudio.storage.db import ArchiveIndex

            index = ArchiveIndex.get()
            rel = (query.get("path", [""])[0] or "").strip()
            if rel:
                row = index.get_label(urllib.parse.unquote(rel))
                if not row:
                    self.send_error(404, "No label")
                    return
                self._send_json(row)
                return
            self._send_json(index.label_counts())
            return

        if path == "/api/sync/status":
            self._send_json(_sync.get_status())
            return

        if path == "/api/scrape/status":
            if not CREATOR_SCRAPE_QUEUE_ENABLED:
                self.send_error(404, "Creator scrape queue disabled")
                return
            snap = CreatorScrapeQueue.get().status_snapshot()
            snap["enabled"] = True
            snap["sync"] = _sync.get_status()
            from promptstudio.config import instagram_backend, instagram_cookies_info

            snap["instagram_backend"] = instagram_backend()
            snap["instagram_cookies"] = instagram_cookies_info()
            from promptstudio.scraping.ig_cooldown import status as cooldown_status

            snap["instagram_cooldown"] = cooldown_status()
            self._send_json(snap)
            return

        if path == "/api/prompt/batch/status":
            # `pending` comes from the job's own snapshot. It used to call
            # list_uncached(), a full archive query + prompt-cache load, on
            # every 4s poll for the whole run.
            self._send_json(_batch.get_status())
            return

        if path == "/api/classify/status":
            status = _classify.get_status()
            status["reject_max_tier"] = int(CLASSIFY_REJECT_MAX_TIER)
            status["tier_labels"] = {str(k): v for k, v in TIER_LABELS.items()}
            creator = status.get("creator")
            if creator and not status.get("running"):
                # Remaining work for resume UX. Only when idle — during a run
                # this is a full re-query on every 3s poll for no new answer.
                try:
                    status["pending"] = len(
                        _classify.list_pending(
                            creator,
                            include_videos=bool(status.get("include_videos", True)),
                        )
                    )
                except Exception:
                    status["pending"] = None
            self._send_json(status)
            return

        if path == "/api/comfy/batch/status":
            self._send_json(_comfy_batch.get_status())
            return

        if path == "/api/comfy/status":
            self._send_json(_comfy.get_status())
            return

        if path == "/api/generations/list":
            from promptstudio.storage.db import ArchiveIndex
            from promptstudio.storage.thumbs import thumb_url

            try:
                offset = max(0, int(query.get("offset", ["0"])[0] or 0))
            except ValueError:
                offset = 0
            try:
                limit = int(query.get("limit", [str(MAX_PHOTOS_API_PAGE)])[0]
                            or MAX_PHOTOS_API_PAGE)
            except ValueError:
                limit = MAX_PHOTOS_API_PAGE
            limit = max(1, min(limit, MAX_PHOTOS_API_PAGE))

            def _q(name):
                return (query.get(name, [""])[0] or "").strip() or None

            rating = _q("rating")
            try:
                # "" means no filter; "0" means the *unrated*, which is a real
                # filter — so this cannot collapse to a truthiness test.
                rating = None if rating is None else int(rating)
            except ValueError:
                self.send_error(400, "rating must be an integer")
                return

            index = ArchiveIndex.get()
            rows, total = index.list_generations(
                creator=_q("creator"),
                workflow=_q("workflow"),
                checkpoint=_q("checkpoint"),
                batch_id=_q("batch_id"),
                source_rel=_q("source"),
                rating=rating,
                rated_only=(query.get("rated_only", [""])[0] or "") in ("1", "true"),
                since=_q("since"),
                until=_q("until"),
                has_source=_parse_has_source(query),
                sort=(query.get("sort", ["newest"])[0] or "newest"),
                limit=limit,
                offset=offset,
            )
            out = []
            for row in rows:
                rel = row["rel_path"]
                item = dict(row)
                item["url"] = "/media/" + "/".join(
                    urllib.parse.quote(part) for part in rel.split("/")
                )
                item["thumb_url"] = thumb_url(rel)
                item["mode_e"] = bool(row["mode_e"])
                # -1 is the legacy "never recorded" marker from the A0 import.
                # Surfaced as a flag so the UI can disable regenerate-same-seed
                # instead of offering a button that cannot reproduce anything.
                item["seed_recorded"] = int(row["seed"]) >= 0
                src = (row["source_rel"] or "").strip()
                item["has_source"] = bool(src)
                item["source_thumb_url"] = thumb_url(src) if src else ""
                out.append(item)
            self._send_json(
                {
                    "generations": out,
                    "total": total,
                    "offset": offset,
                    "limit": limit,
                    "has_more": offset + len(out) < total,
                    "facets": index.generation_facets(),
                }
            )
            return

        if path == "/api/generations":
            rel_path = query.get("path", [None])[0]
            if not rel_path:
                self.send_error(400, "path required")
                return
            rel_path = urllib.parse.unquote(rel_path)
            from promptstudio.storage.db import ArchiveIndex

            # Served from the `generations` table, not generations_index.json.
            # The table is the source of truth since A0 — the JSON is a rollback
            # parachute — and it is the only place a rating exists, which the
            # lightbox needs to show a verdict after it reopens.
            #
            # Shape is kept: one row per output file becomes one record with a
            # single-entry `files` list. A multi-image job therefore arrives as
            # several records rather than one with several files; the lightbox
            # reads `gens[0]` and its primary either way.
            rows = ArchiveIndex.get().list_generations_for(rel_path)
            gens = []
            for row in rows:
                rel = row["rel_path"]
                url = "/media/" + "/".join(
                    urllib.parse.quote(part) for part in rel.split("/")
                )
                gens.append(
                    {
                        "created_at": row["created_at"],
                        "primary_url": url,
                        "primary_rel": rel,
                        "files": [
                            {
                                "filename": os.path.basename(rel),
                                "rel_path": rel,
                                "url": url,
                                "gen_id": row["gen_id"],
                                "rating": row["rating"],
                            }
                        ],
                        "gen_id": row["gen_id"],
                        "rating": row["rating"],
                        "seed": row["seed"],
                        "workflow": row["workflow"],
                        "checkpoint": row["checkpoint"],
                        "steps": row["steps"],
                        "cfg": row["cfg"],
                        "denoise": row["denoise"],
                        "mode_e": bool(row["mode_e"]),
                        "prompt_version": row["prompt_version"],
                        "positive_prompt": row["positive_prompt"],
                        "negative_prompt": row["negative_prompt"],
                    }
                )
            self._send_json({"path": rel_path, "generations": gens})
            return

        return super().do_GET()


class ThreadingHTTPServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
    daemon_threads = True
    allow_reuse_address = True


def run_server(port: int = PORT, host: str = HOST):
    os.chdir(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
    _archive.ensure_ready()
    with ThreadingHTTPServer((host, port), GalleryRequestHandler) as httpd:
        # Log the address actually bound, not a hardcoded "localhost" — the old
        # line said localhost while binding every interface, which hid the
        # exposure rather than reporting it.
        shown = "localhost" if host in ("127.0.0.1", "::1") else host
        log.info("PromptStudio running at http://%s:%s (threaded)", shown, port)
        if host not in ("127.0.0.1", "::1"):
            log.warning(
                "Bound to %s — no auth and CORS is '*', so every host that can "
                "reach this port can read and delete the archive.",
                host,
            )
        log.info("Archive: %s", SAVED_DIR)
        httpd.serve_forever()
