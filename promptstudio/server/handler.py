"""PromptStudio HTTP API server."""

import functools
import http.server
import json
import os
import socketserver
import urllib.parse
from typing import Any, Dict, List, Optional

from promptstudio.comfy.client import ComfyJobManager, check_comfy_health
from promptstudio.config import (
    CREATOR_SCRAPE_QUEUE_ENABLED,
    DEFAULT_MAX_POSTS_PER_CREATOR,
    FOLLOWING_LIST_FILE,
    GLAM_SEXY_MIN,
    HOST,
    INCLUDE_VIDEOS_DEFAULT,
    MAX_PHOTOS_API_PAGE,
    MODEL_NAME,
    PORT,
    PROMPT_PIPELINE_VERSION,
    SAVED_DIR,
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
from promptstudio.scraping.downloader import InstagramDownloader
from promptstudio.scraping.sources.base import VALID_MODES, ScrapeOptions
from promptstudio.scraping.sync_manager import SyncManager
from promptstudio.server.multipart import parse_multipart_data
from promptstudio.storage.archive import ArchiveStore, ensure_creator_folder
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
_classify = ClassifyJobManager.get()
_styles = CreatorStyleStore()
_trash = TrashStore()
_comfy = ComfyJobManager.get()
_scrape_queue = CreatorScrapeQueue.get() if CREATOR_SCRAPE_QUEUE_ENABLED else None

OLLAMA_TAGS_URL = os.environ.get("OLLAMA_TAGS_URL", "http://localhost:11434/api/tags")

_following_cache: Dict[str, Any] = {"mtime": None, "accounts": []}

_TRUTHY = ("1", "true", "yes")


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


def _creator_queue_blocks_oneshot() -> Optional[Dict[str, Any]]:
    """If scrape queue has pending jobs and is not paused, block one-shot sync.

    Policy, not mutual exclusion — the Instagram lease in `SyncManager.start_job`
    is what prevents two syncs from actually overlapping. This exists so a
    one-shot request does not jump the queue's pacing, and being advisory is
    fine: the worst case is a job that the lease then refuses anyway.
    """
    if not CREATOR_SCRAPE_QUEUE_ENABLED:
        return None
    try:
        q = CreatorScrapeQueue.get()
        n = q.pending_count()
        if n > 0 and not q.is_paused():
            return {
                "status": "busy",
                "message": (
                    f"Creator scrape queue has {n} pending — "
                    "pause or empty the queue first"
                ),
                "creator_queue_depth": n,
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
        except (BrokenPipeError, ConnectionResetError):
            # Normal when a user seeks or closes a video mid-stream. Nothing to
            # send, and it is not worth a traceback.
            log.debug("client disconnected during %s %s", self.command, self.path)
            self.close_connection = True
            return None
        except Exception:
            log.exception("unhandled error in %s %s", self.command, self.path)
            self._send_json_500()
            return None

    return wrapper


class GalleryRequestHandler(http.server.SimpleHTTPRequestHandler):
    def handle_one_request(self):
        # Handler instances are reused across keep-alive requests, so this must
        # reset per request, not per connection.
        self._response_started = False
        return super().handle_one_request()

    def send_response(self, code, message=None):
        self._response_started = True
        return super().send_response(code, message)

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
        super().end_headers()

    def do_OPTIONS(self):
        self.send_response(200)
        self.end_headers()

    @_error_boundary
    def do_HEAD(self):
        """Support HEAD for media (range probing); body omitted in _serve_local_file."""
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path.startswith("/media/"):
            # do_GET uses self.command == "HEAD" to skip the body
            return self.do_GET()
        self.send_response(200)
        self.end_headers()

    def _send_json(self, data, status=200):
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(data).encode("utf-8"))

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
                full_path = _archive.resolve_path(rel_path)
                if full_path:
                    try:
                        # delete_photo owns cache/favorite/tombstone bookkeeping so
                        # restorable state is captured before it is cleared.
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
        return super().do_DELETE()

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
                    started = _sync.try_drain_creator_queue()
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
                q = CreatorScrapeQueue.get()
                if scope == "all_pending":
                    n = q.cancel_all_pending()
                    cancel_running = bool(data.get("cancel_running"))
                    if cancel_running:
                        _sync.request_cancel()
                    self._send_json(
                        {
                            "status": "ok",
                            "cancelled_pending": n,
                            "cancel_running": cancel_running,
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
                    _sync.request_cancel()
                    self._send_json({"status": "cancelling", "job_id": job_id})
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
            if isinstance(data, dict):
                reason = str(data.get("reason") or "Paused by user")
            CreatorScrapeQueue.get().pause(reason or "Paused by user")
            self._send_json({"status": "paused", "pause_reason": reason or "Paused by user"})
            return

        if path == "/api/scrape/resume":
            if not CREATOR_SCRAPE_QUEUE_ENABLED:
                self.send_error(404, "Creator scrape queue disabled")
                return
            CreatorScrapeQueue.get().resume()
            started = _sync.try_drain_creator_queue()
            self._send_json({"status": "resumed", "drain_started": started})
            return

        if path == "/api/sync/saved":
            blocked = _creator_queue_blocks_oneshot()
            if blocked:
                self._send_json(blocked, 409)
                return
            def job(log, on_rate_limit=None):
                dl = InstagramDownloader(
                    log=log,
                    on_rate_limit=on_rate_limit,
                    should_cancel=_sync.is_cancel_requested,
                )
                return dl.sync_saved_posts()

            # No is_running() pre-check: start_job takes the Instagram lease and
            # flips status under one lock, so it is the only answer that cannot
            # be stale by the time the job starts.
            if _sync.start_job("saved", job):
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
                # Prefer serial scrape queue: full feed + deep gap-fill (not glam top-50).
                # One-shot bounded path remains only when queue disabled or client forces oneshot.
                force_oneshot = data.get("oneshot", False)
                if isinstance(force_oneshot, str):
                    force_oneshot = force_oneshot.lower() in ("1", "true", "yes")
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
                        started = _sync.try_drain_creator_queue()
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
                max_posts = opts.resolved_max_posts()
                blocked = _creator_queue_blocks_oneshot()
                if blocked:
                    self._send_json(blocked, 409)
                    return

                def job(log, on_rate_limit=None):
                    dl = InstagramDownloader(
                        log=log,
                        on_rate_limit=on_rate_limit,
                        should_cancel=_sync.is_cancel_requested,
                    )
                    return dl.sync_creator_feed(
                        username,
                        max_posts=max_posts,
                        include_videos=include_videos,
                        mode=mode,
                        deep=deep,
                    )

                if _sync.start_job("creator", job):
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
                from promptstudio.config import DEFAULT_ACCOUNTS_PER_DAY

                data = self._read_json_body()
                max_accounts = int(
                    data.get("max_accounts", data.get("accounts_per_day", DEFAULT_ACCOUNTS_PER_DAY))
                )
                max_posts = int(data.get("max_posts", 20))
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
                    dl = InstagramDownloader(
                        log=log,
                        on_rate_limit=on_rate_limit,
                        should_cancel=_sync.is_cancel_requested,
                    )
                    return dl.sync_following(
                        max_accounts=max_accounts,
                        max_posts_per_account=max_posts,
                        keywords=keywords,
                        min_media_count=min_media_count,
                        include_videos=include_videos,
                    )

                if _sync.start_job("following", job):
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
                # No is_running() pre-checks against _batch/_classify: both take
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

        if path == "/api/classify/start":
            try:
                data = self._read_json_body()
                creator = (data.get("creator") or "").strip().lstrip("@")
                if not creator:
                    self.send_error(400, "creator required")
                    return
                force = bool(data.get("force", False))
                only_unscored = data.get("only_unscored", True)
                if isinstance(only_unscored, str):
                    only_unscored = only_unscored.lower() in ("1", "true", "yes")
                only_unscored = bool(only_unscored)
                include_videos = data.get("include_videos", INCLUDE_VIDEOS_DEFAULT)
                if isinstance(include_videos, str):
                    include_videos = include_videos.lower() in ("1", "true", "yes")
                include_videos = bool(include_videos)
                rescore_stale = _as_bool(data.get("rescore_stale"))
                limit = data.get("limit")
                limit = int(limit) if limit is not None else None
                result = _classify.start(
                    creator,
                    force=force,
                    include_videos=include_videos,
                    limit=limit,
                    only_unscored=only_unscored,
                    rescore_stale=rescore_stale,
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

                filename = os.path.basename(rel_path)
                cached = _prompt_cache.get(rel_path, filename) or {}
                variant = (data.get("variant") or "pro").lower()
                workflow = (data.get("workflow") or "").lower()
                if not workflow:
                    if variant in ("pro", "ref", "modeltoimage_pro"):
                        workflow = "pro"
                    elif variant in ("txt2img", "sdxl", "flux", "pony"):
                        workflow = "txt2img"
                    else:
                        workflow = "pro"

                exports = cached.get("exports") or {}
                positive = data.get("positive_prompt")
                negative = data.get("negative_prompt")
                use_mode_e = data.get("use_mode_e")
                if use_mode_e is None:
                    use_mode_e = workflow == "pro"
                use_mode_e = bool(use_mode_e)

                if use_mode_e and workflow == "pro":
                    from promptstudio.prompts.comfy_mode import build_mode_e_bundle

                    structured = cached.get("structured_vision")
                    if not isinstance(structured, dict):
                        structured = None
                    base_pos = positive or cached.get("positive_prompt") or ""
                    base_neg = negative or cached.get("negative_prompt") or ""
                    # Prefer cached Mode E export when client did not override text
                    if positive is None and exports.get("comfy_ref"):
                        positive = exports["comfy_ref"]
                        negative = (
                            exports.get("comfy_negative")
                            or exports.get("negative")
                            or base_neg
                        )
                        mode_meta = {"source": "exports", "anti_terms": []}
                    else:
                        bundle = build_mode_e_bundle(
                            positive=str(base_pos),
                            negative=str(base_neg),
                            structured=structured,
                        )
                        positive = bundle["positive"]
                        negative = bundle["negative"]
                        mode_meta = {
                            "source": bundle["source"],
                            "anti_terms": bundle["anti_terms"],
                        }
                else:
                    mode_meta = None
                    if not positive:
                        if variant == "flux":
                            positive = exports.get("flux") or cached.get("positive_prompt", "")
                        elif variant == "pony":
                            positive = exports.get("pony") or cached.get("positive_prompt", "")
                        else:
                            positive = (
                                exports.get("sdxl")
                                or cached.get("positive_prompt", "")
                            )
                    if not negative:
                        negative = (
                            exports.get("negative")
                            or cached.get("negative_prompt")
                            or "deformed, bad anatomy, blurry"
                        )
                if not str(positive).strip():
                    self.send_error(400, "No prompt available — generate one first")
                    return

                params = cached.get("parameters") or {}
                aspect = data.get("aspect_ratio") or params.get("aspect_ratio") or "4:5"
                if workflow == "pro":
                    from promptstudio.config import (
                        COMFYUI_DEFAULT_CFG,
                        COMFYUI_DEFAULT_DENOISE,
                        COMFYUI_DEFAULT_STEPS,
                    )

                    steps = int(
                        data.get("steps")
                        if data.get("steps") is not None
                        else COMFYUI_DEFAULT_STEPS
                    )
                    cfg = float(
                        data.get("cfg_scale")
                        if data.get("cfg_scale") is not None
                        else COMFYUI_DEFAULT_CFG
                    )
                    denoise = float(
                        data.get("denoise")
                        if data.get("denoise") is not None
                        else COMFYUI_DEFAULT_DENOISE
                    )
                else:
                    steps = int(data.get("steps") or params.get("steps") or 30)
                    cfg = float(data.get("cfg_scale") or params.get("cfg_scale") or 7.0)
                    denoise = None
                seed = data.get("seed")
                seed = int(seed) if seed is not None else None
                checkpoint = data.get("checkpoint") or None

                if _comfy.start(
                    source_rel=rel_path,
                    positive=str(positive),
                    negative=str(negative),
                    workflow=workflow,
                    aspect=str(aspect),
                    steps=steps,
                    cfg=cfg,
                    denoise=denoise,
                    seed=seed,
                    checkpoint=checkpoint,
                ):
                    payload = {
                        "status": "started",
                        "path": rel_path,
                        "variant": variant,
                        "workflow": workflow,
                        "denoise": denoise,
                        "steps": steps,
                        "cfg": cfg,
                        # The resolved seed, not the request's — `seed` is None
                        # whenever the client did not pin one, which is the
                        # default. ComfyJobManager.start() materialises it.
                        "seed": _comfy.get_status().get("seed"),
                        "use_mode_e": use_mode_e and workflow == "pro",
                        "positive_prompt": str(positive)[:400],
                        "negative_prompt": str(negative)[:300],
                    }
                    if mode_meta:
                        payload["mode_e"] = mode_meta
                    self._send_json(payload)
                else:
                    self._send_json(
                        {"status": "busy", "message": _comfy.last_refusal}, 409
                    )
            except (ValueError, json.JSONDecodeError):
                self.send_error(400, "Invalid JSON body")
            return

        return super().do_POST()

    @_error_boundary
    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        query = urllib.parse.parse_qs(parsed.query)

        if path == "/api/media/detail":
            # Reel/photo inspector: caption, glam, Instagram link (not vision prompts)
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
            glam_score = meta.get("glam_score")
            if glam_score is None:
                try:
                    from promptstudio.storage.db import ArchiveIndex

                    glam_score = ArchiveIndex.get().get_glam_score(rel_path)
                except Exception:
                    glam_score = -1
            try:
                glam_score = int(glam_score)
            except (TypeError, ValueError):
                glam_score = -1
            glam_block = meta.get("glam") if isinstance(meta.get("glam"), dict) else {}
            if not glam_block and isinstance(meta.get("glam_classify"), dict):
                glam_block = meta.get("glam_classify") or {}
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
            self._send_json(
                {
                    "rel_path": rel_path,
                    "filename": filename,
                    "creator": creator or meta.get("owner_username") or "",
                    "is_video": is_video,
                    "url": f"/media/{urllib.parse.quote(rel_path)}",
                    "thumb_url": thumb_url(rel_path),
                    "file_size": file_size,
                    "glam_score": glam_score,
                    "favorite": fav,
                    "caption": meta.get("caption") or "",
                    "shortcode": shortcode,
                    "post_url": post_url,
                    "post_id": meta.get("post_id") or "",
                    "taken_at": meta.get("taken_at") or "",
                    "downloaded_at": meta.get("downloaded_at") or "",
                    "carousel_index": meta.get("carousel_index", 0),
                    "source": meta.get("source") or "",
                    "glam": glam_block or None,
                }
            )
            return

        if path.startswith("/media/thumb/"):
            rel_path = urllib.parse.unquote(path[len("/media/thumb/") :])
            full_path = _archive.resolve_path(rel_path)
            if not full_path:
                self.send_error(404, "File not found")
                return
            from promptstudio.storage.thumbs import ensure_thumbnail, resolve_thumb_file

            thumb = ensure_thumbnail(full_path, rel_path) or resolve_thumb_file(rel_path)
            serve_path = thumb or full_path
            # Thumbs are small; still advertise ranges for consistency
            self._serve_local_file(
                serve_path,
                "image/jpeg",
                cache_control="public, max-age=86400",
            )
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
            self._send_json(_archive.list_creators())
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
                    "entries": entries,
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
            sexy_raw = (query.get("sexy", ["false"])[0] or "").lower()
            sexy_only = sexy_raw in ("1", "true", "yes")
            reject_raw = (query.get("reject", ["false"])[0] or "").lower()
            reject_only = reject_raw in ("1", "true", "yes")
            unscored_raw = (query.get("unscored", ["false"])[0] or "").lower()
            unscored_only = unscored_raw in ("1", "true", "yes")
            glam_min = None
            glam_max = None
            if sexy_only:
                glam_min = GLAM_SEXY_MIN
            glam_raw = query.get("glam_min", [None])[0]
            if glam_raw is not None and str(glam_raw).strip() != "":
                try:
                    glam_min = int(glam_raw)
                except ValueError:
                    pass
            glam_max_raw = query.get("glam_max", [None])[0]
            if glam_max_raw is not None and str(glam_max_raw).strip() != "":
                try:
                    glam_max = int(glam_max_raw)
                except ValueError:
                    pass
            media_type = (query.get("media_type", ["all"])[0] or "all").lower()
            if media_type not in ("video", "photo"):
                media_type = None
            sort = (query.get("sort", ["name"])[0] or "name").lower()
            if sort not in ("name", "newest", "oldest", "glam"):
                sort = "name"
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

            photos, total = _archive.query_photos(
                creator=creator,
                search=search,
                unanalyzed=unanalyzed,
                favorite_only=favorite_only,
                media_type=media_type,
                glam_min=glam_min,
                glam_max=glam_max,
                unscored_only=unscored_only,
                reject_only=reject_only,
                sort=sort,
                limit=limit,
                offset=offset,
            )
            photos = _prompt_cache.annotate_photos(photos)
            photos = _favorites.annotate_photos(photos)
            public_photos = [
                {k: v for k, v in p.items() if k != "full_path"} for p in photos
            ]
            self._send_json(
                {
                    "photos": public_photos,
                    "total": total,
                    "offset": offset,
                    "limit": limit,
                    "has_more": offset + len(public_photos) < total,
                    "sort": sort,
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
            creator = status.get("creator")
            if creator and not status.get("running"):
                # Include remaining unscored for resume UX
                try:
                    status["pending"] = len(
                        _classify.list_pending(
                            creator,
                            force=False,
                            include_videos=bool(status.get("include_videos", True)),
                        )
                    )
                except Exception:
                    status["pending"] = 0
                # Files scored by a superseded prompt. Without this the client
                # has no way to know rescore_stale would do anything.
                try:
                    from promptstudio.scraping.outfit_classifier import (
                        active_prompt_versions,
                    )
                    from promptstudio.storage.db import ArchiveIndex

                    status["stale"] = len(
                        ArchiveIndex.get().list_stale_glam(
                            active_prompt_versions(), creator=creator
                        )
                    )
                except Exception:
                    status["stale"] = 0
            elif status.get("running"):
                total = int(status.get("total") or 0)
                done = int(status.get("completed") or 0)
                status["pending"] = max(0, total - done)
            else:
                status["pending"] = 0
            # Keep the response shape stable — clients should not have to
            # distinguish "no stale files" from "we didn't look".
            status.setdefault("stale", 0)
            self._send_json(status)
            return

        if path == "/api/comfy/status":
            self._send_json(_comfy.get_status())
            return

        if path == "/api/generations":
            rel_path = query.get("path", [None])[0]
            if not rel_path:
                self.send_error(400, "path required")
                return
            rel_path = urllib.parse.unquote(rel_path)
            gens = _comfy.index.list_for(rel_path)
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
        log.info("PromptStudio running at http://localhost:%s (threaded)", port)
        log.info("Archive: %s", SAVED_DIR)
        httpd.serve_forever()
