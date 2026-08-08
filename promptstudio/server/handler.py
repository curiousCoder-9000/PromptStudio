"""PromptStudio HTTP API server."""

import http.server
import json
import os
import socketserver
import urllib.parse
from typing import Any, Dict, List

from promptstudio.comfy.client import ComfyJobManager, check_comfy_health
from promptstudio.config import (
    FOLLOWING_LIST_FILE,
    HOST,
    MAX_PHOTOS_API_PAGE,
    MODEL_NAME,
    PORT,
    PROMPT_PIPELINE_VERSION,
    SAVED_DIR,
)
from promptstudio.prompts.batch import BatchPromptManager, count_prompts_ready
from promptstudio.prompts.cache import PromptCache
from promptstudio.prompts.engine import ENGINE_ID, build_export_variants, get_prompt_for_image
from promptstudio.scraping.downloader import InstagramDownloader
from promptstudio.scraping.sync_manager import SyncManager
from promptstudio.server.multipart import parse_multipart_data
from promptstudio.storage.archive import ArchiveStore
from promptstudio.storage.favorites import FavoritesStore
from promptstudio.storage.metadata import delete_metadata_for_image
from promptstudio.prompts.styles import CreatorStyleStore

_archive = ArchiveStore()
_prompt_cache = PromptCache()
_favorites = FavoritesStore()
_sync = SyncManager.get()
_batch = BatchPromptManager.get()
_styles = CreatorStyleStore()
_comfy = ComfyJobManager.get()

OLLAMA_TAGS_URL = os.environ.get("OLLAMA_TAGS_URL", "http://localhost:11434/api/tags")

_following_cache: Dict[str, Any] = {"mtime": None, "accounts": []}


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
    return result


class GalleryRequestHandler(http.server.SimpleHTTPRequestHandler):
    def end_headers(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, DELETE, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        super().end_headers()

    def do_OPTIONS(self):
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

    def do_DELETE(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/api/photo":
            rel_path = urllib.parse.parse_qs(parsed.query).get("path", [None])[0]
            if rel_path:
                rel_path = urllib.parse.unquote(rel_path)
                full_path = _archive.resolve_path(rel_path)
                if full_path:
                    try:
                        filename = _archive.delete_photo(rel_path)
                        _prompt_cache.delete(rel_path, filename)
                        # Index row cleared inside ArchiveStore.delete_photo → re-download allowed
                        delete_metadata_for_image(full_path)
                        _favorites.set_favorite(rel_path, False)
                        self._send_json({"status": "deleted", "filename": filename})
                        return
                    except OSError as e:
                        self.send_error(500, f"Error deleting file: {e}")
                        return
            self.send_error(404, "Photo not found")
            return
        return super().do_DELETE()

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

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path

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

        if path == "/api/sync/saved":
            if _sync.is_running():
                self._send_json({"status": "busy", "message": "Sync already running"}, 409)
                return

            def job(log, on_rate_limit=None):
                dl = InstagramDownloader(log=log, on_rate_limit=on_rate_limit)
                return dl.sync_saved_posts()

            if _sync.start_job("saved", job):
                self._send_json({"status": "started", "job_type": "saved"})
            else:
                self._send_json({"status": "busy"}, 409)
            return

        if path == "/api/sync/creator":
            try:
                data = self._read_json_body()
                username = data.get("username", "").strip()
                max_posts = int(data.get("max_posts", 50))
                if not username:
                    self.send_error(400, "username required")
                    return
                if _sync.is_running():
                    self._send_json({"status": "busy"}, 409)
                    return

                def job(log, on_rate_limit=None):
                    dl = InstagramDownloader(log=log, on_rate_limit=on_rate_limit)
                    return dl.sync_creator_feed(username, max_posts=max_posts)

                if _sync.start_job("creator", job):
                    self._send_json(
                        {"status": "started", "job_type": "creator", "username": username}
                    )
                else:
                    self._send_json({"status": "busy"}, 409)
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
                keywords_raw = data.get("keywords", "")
                if isinstance(keywords_raw, list):
                    keywords = keywords_raw
                elif isinstance(keywords_raw, str) and keywords_raw.strip():
                    keywords = [k.strip() for k in keywords_raw.split(",") if k.strip()]
                else:
                    keywords = None
                if _sync.is_running():
                    self._send_json({"status": "busy"}, 409)
                    return

                def job(log, on_rate_limit=None):
                    dl = InstagramDownloader(log=log, on_rate_limit=on_rate_limit)
                    return dl.sync_following(
                        max_accounts=max_accounts,
                        max_posts_per_account=max_posts,
                        keywords=keywords,
                        min_media_count=min_media_count,
                    )

                if _sync.start_job("following", job):
                    self._send_json(
                        {
                            "status": "started",
                            "job_type": "following",
                            "max_accounts": max_accounts,
                            "accounts_per_day": max_accounts,
                            "keywords": keywords,
                        }
                    )
                else:
                    self._send_json({"status": "busy"}, 409)
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
                if _batch.is_running():
                    self._send_json({"status": "busy"}, 409)
                    return
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
                    self._send_json({"status": "busy"}, 409)
            except (ValueError, json.JSONDecodeError):
                self.send_error(400, "Invalid JSON body")
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
                print(f"/api/prompt/mode-e bad request: {exc}")
                self.send_error(400, "Invalid JSON body")
            except Exception as exc:
                import traceback

                traceback.print_exc()
                print(f"/api/prompt/mode-e error: {exc}")
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
                if _comfy.is_running():
                    self._send_json({"status": "busy"}, 409)
                    return

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
                        "seed": seed,
                        "use_mode_e": use_mode_e and workflow == "pro",
                        "positive_prompt": str(positive)[:400],
                        "negative_prompt": str(negative)[:300],
                    }
                    if mode_meta:
                        payload["mode_e"] = mode_meta
                    self._send_json(payload)
                else:
                    self._send_json({"status": "busy"}, 409)
            except (ValueError, json.JSONDecodeError):
                self.send_error(400, "Invalid JSON body")
            return

        return super().do_POST()

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        query = urllib.parse.parse_qs(parsed.query)

        if path.startswith("/media/thumb/"):
            rel_path = urllib.parse.unquote(path[len("/media/thumb/") :])
            full_path = _archive.resolve_path(rel_path)
            if not full_path:
                self.send_error(404, "File not found")
                return
            from promptstudio.storage.thumbs import ensure_thumbnail, resolve_thumb_file

            thumb = ensure_thumbnail(full_path, rel_path) or resolve_thumb_file(rel_path)
            serve_path = thumb or full_path
            self.send_response(200)
            self.send_header("Content-Type", "image/jpeg")
            self.send_header("Cache-Control", "max-age=86400")
            self.end_headers()
            with open(serve_path, "rb") as f:
                self.wfile.write(f.read())
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
                self.send_response(200)
                self.send_header("Content-Type", content_type)
                self.send_header("Cache-Control", "max-age=3600")
                self.end_headers()
                with open(full_path, "rb") as f:
                    self.wfile.write(f.read())
                return
            self.send_error(404, "File not found")
            return

        if path == "/api/creators":
            self._send_json(_archive.list_creators())
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
            sort = (query.get("sort", ["name"])[0] or "name").lower()
            if sort not in ("name", "newest", "oldest"):
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
            stats = _archive.stats()
            stats["prompts_ready"] = count_prompts_ready()
            self._send_json(stats)
            return

        if path == "/api/sync/status":
            self._send_json(_sync.get_status())
            return

        if path == "/api/prompt/batch/status":
            status = _batch.get_status()
            status["pending"] = len(_batch.list_uncached())
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
        print(f"PromptStudio running at http://localhost:{port} (threaded)")
        print(f"Archive: {SAVED_DIR}")
        httpd.serve_forever()
