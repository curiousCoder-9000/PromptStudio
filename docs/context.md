# PromptStudio — Agent Context Map

**Read this first.** Dense map so agents avoid scanning the whole tree.  
Detail on demand: [api.md](api.md) · [instagram_downloader.md](instagram_downloader.md) · [troubleshooting.md](troubleshooting.md) · [roadmap.md](roadmap.md)

**Do not load as agent context:** `docs/following_list.md`, `docs/following_classify_report.md` (generated data dumps).

---

## What it is

Local **Instagram photo archive studio**: scrape creators → store under `~/Pictures/InstagramSaved` → Ollama vision reverse-engineers SD/Flux prompts → optional ComfyUI img2img. Vanilla web UI at `:5000`.

| Layer | Tech | Entry |
|-------|------|-------|
| HTTP API + static UI | `ThreadingHTTPServer` | `server.py` → `promptstudio.server.handler` |
| Frontend | Vanilla HTML/CSS/JS | `index.html`, `style.css`, `app.js` |
| Vision prompts | Ollama (default `qwen2.5vl:7b`) | `promptstudio.prompts.engine` |
| Instagram | Instaloader + session file | `promptstudio.scraping.*` |
| Gallery index | SQLite `archive.db` | `promptstudio.storage.db` |
| ComfyUI (optional) | HTTP API `:8188` | `promptstudio.comfy.client` |

**Version:** package `2.0.0` · prompt pipeline `v2-structured` · Python **3.14+** on Windows.

---

## Run

```powershell
copy .env.example .env                # set PROMPTSTUDIO_ARCHIVE + INSTAGRAM_SESSION_USER
pip install -r requirements.txt
py server.py                          # http://localhost:5000
py prompt_engine.py [image.jpg]       # smoke-test vision
# Ollama: http://localhost:11434  ·  model from OLLAMA_VISION_MODEL
# Comfy:  http://127.0.0.1:8188   ·  optional
```

**Verify before claiming done** (`pip install -r requirements-dev.txt`):

```powershell
pytest                  # 107 unit tests, temp archive, no server needed
ruff check .            # must be clean — config in pyproject.toml
./tests/ui/run.sh       # 70 browser checks; needs Node 22+ and Chrome
```

Deps: `instaloader`, `opencv-python-headless`, `Pillow`, `python-dotenv` (`requirements.txt`).

---

## Package map (edit here)

```
promptstudio/
  config.py              # ALL env defaults, paths, pacing, models
  server/
    handler.py           # Every HTTP route lives here
    multipart.py         # cgi-free multipart upload parser
  storage/
    archive.py           # ArchiveStore façade (creators, photos, upload/delete)
    db.py                # ArchiveIndex SQLite catalog + query
    favorites.py         # favorites.json write-through
    metadata.py          # *.meta.json sidecars (post_id, shortcode)
    thumbs.py            # /media/thumb generation under _thumbs/
    trash.py             # TrashStore soft delete + restore under _trash/
  prompts/
    engine.py            # Ollama: structured vision → rewrite → exports
    cache.py             # prompts_cache.json in-memory write-through + history
    styles.py            # creator_styles.json style prefixes
    batch.py             # background BatchPromptManager
    comfy_mode.py        # Mode E (outfit/scene only) rewrite
  scraping/
    session.py           # Instaloader + session load
    downloader.py        # saved / creator / following sync (Instagram)
    results.py           # SyncResult — shared, no instaloader import
    filters.py           # bio keywords, min media, public-only (IG-only signals)
    queue.py             # following_queue.json daily budget
    creator_queue.py     # ad-hoc scrape jobs, keyed (source, username)
    checkpoints.py       # sync_state.json telemetry
    sync_manager.py      # single background sync job; dispatches by source
    organizer.py         # root organize + dedupe
    outfit_classifier.py # vision classify keep/unfollow
    sources/             # multi-source seam (see docs/multi_source_scraping.md)
      base.py            # NormalizedPost, SourceTarget, MediaSource, folder naming
      __init__.py        # lazy registry: instagram | x | reddit
      instagram_source.py  # wraps InstagramDownloader
      gallery_dl_source.py # X + Reddit via gallery-dl subprocess
  comfy/
    client.py            # ComfyJobManager, pro + txt2img workflows
    workflows/modelToimage_pro.api.json
scripts/                 # thin CLIs only — logic stays in package
server.py / prompt_engine.py   # shims
tests/
  conftest.py            # temp archive + cache reset (sets env BEFORE import)
  test_paths.py          # resolve_path containment (traversal regressions)
  test_byte_range.py     # Range header parsing (video scrubbing)
  test_multipart.py      # upload parser (no stdlib cgi)
  test_filters.py        # following filters + feed post ranking
  test_trash.py          # soft delete / restore / purge
  test_batch_job.py      # batch cancel / pending snapshot (vision mocked)
  test_stats.py          # prompts_ready indexed count vs exact walk
  test_sources.py        # target parsing, gallery-dl argv, metadata mapping, exit codes
  test_source_identity.py # platform-scoped tombstones + schema migration
  ui/                    # headless-Chrome suites over CDP (run.sh)
```

**Frontend:** `app.js` owns all UI state/API calls; `style.css` glassmorphism (`#8b5cf6`, `#ec4899`, `#06b6d4`); fonts Outfit / Inter / Fira Code.

**Frontend invariants:**
- **Escape before `innerHTML`.** Handles, captions, bios, filenames, and Ollama tags are all third-party text — run them through `escapeHtml()` (or use `textContent`). Numeric JSON fields get `Number(...)`.
- **User-typed input is debounced** (`debounce()`; `SEARCH_DEBOUNCE_MS` = 250) and **in-flight fetches are abortable** — `state.photosRequest` / `creatorStyleRequest` / `followingRequest` hold the current `AbortController`. Treat `err.name === 'AbortError'` as success-by-supersession, and only clear loading state if `state.xRequest === controller`.
- Video type detection goes through `isVideoFilename()` — do not inline extension lists.
- **Long jobs report progress in a chip, never a repeating toast.** `#jobChipStack` holds one chip per job kind (scrape / batch / classify); drive it with `renderJobChip(kind, {...})` and toast only on start and finish.
- **View prefs persist, navigation does not.** `PREF_FIELDS` + `saveViewPrefs()` keep sort/media/grid/filters in `localStorage`; selected creator, selection, and reject-review mode are deliberately *not* restored (landing in a destructive mode from a refresh is hostile). Call `saveViewPrefs()` in any new view-control handler.

---

## Data on disk (`PROMPTSTUDIO_ARCHIVE`, default `~/Pictures/InstagramSaved`)

| Path | Role |
|------|------|
| `<creator>/*.jpg\|png\|webp\|mp4` | Media (creator = IG handle) |
| `<creator>/*.meta.json` | Sidecar: `post_id`, `shortcode`, `caption`, `taken_at` |
| `archive.db` | SQLite gallery catalog |
| `prompts_cache.json` | Vision prompt bundles (keyed `creator/file`) |
| `favorites.json` | Favorite flags |
| `creator_styles.json` | Learned style prefixes |
| `sync_state.json` | Per-creator last shortcode / counts |
| `following_queue.json` | Multi-day following crawl budget |
| `sync_status.json` | Last sync job status |
| `generations_index.json` | Comfy outputs index |
| `_thumbs/` | JPEG thumbs |
| `_generations/` | Comfy outputs |
| `_classify/` | Classifier staging (excluded from gallery) |
| `_trash/<entry_id>/` | Soft-deleted media + sidecar + `entry.json` manifest |
| `_no_person_detected/` | Legacy exclude (do not resurrect filter UX) |

**Repo-root (not archive):** `following_list.json` (+ `.target`/`.lock`/`.freeze` during export), classify reports `following_classify_report.json`, `local_photo_classify_*.json`.

**Session:** `INSTALOADER_SESSION_DIR` for user `INSTAGRAM_SESSION_USER` (required in `.env` for scrape).

**Excluded folder names:** `_no_person_detected`, `_thumbs`, `_generations`, `_classify`, `_trash`.

---

## Config knobs (`promptstudio/config.py` + env)

| Env | Default | Meaning |
|-----|---------|---------|
| `PROMPTSTUDIO_PORT` | `5000` | HTTP port |
| `PROMPTSTUDIO_ARCHIVE` | `~/Pictures/InstagramSaved` | Archive root |
| `PROMPTSTUDIO_REBUILD_INDEX` | off | Force SQLite reindex on start |
| `OLLAMA_VISION_MODEL` | `qwen2.5vl:7b` | Vision model (**not** moondream) |
| `OLLAMA_REWRITE_MODEL` | same as vision | Stage-2 rewrite model |
| `OLLAMA_URL` | `…/api/generate` | Ollama generate |
| `COMFYUI_URL` | `http://127.0.0.1:8188` | Comfy API |
| `COMFYUI_CHECKPOINT` | `juggernautXL_ragnarok.safetensors` | Default ckpt |
| `IG_*` | see config | Anti-ban delays, daily cap 20, catch-up streak 3 |
| `IG_INCLUDE_VIDEOS` | `1` | Creator/following download reels |
| `IG_QUEUE_PRIORITY_KEEP` | `100` | Classify-keep queue priority |
| `IG_POST_RANK` | `1` | Rank feed posts by caption/reel signals |
| `IG_POST_SCAN_FACTOR` | `3` | Scan window = max_posts × factor |
| `GLAM_SEXY_MIN` | `2` | Gallery `sexy=1` minimum glam_score |
| `PROMPTSTUDIO_TRASH` | `1` | Soft delete to `_trash/` (`0` = immediate unlink) |
| `PROMPTSTUDIO_TRASH_DAYS` | `30` | Retention window for `purge expired` |
| `INSTAGRAM_SESSION_USER` | _(empty)_ | Instaloader session name — set in `.env` |

Pipeline id: `PROMPT_PIPELINE_VERSION = "v2-structured"`.  
Engine id string: `Ollama ({MODEL_NAME}) {PROMPT_PIPELINE_VERSION}` — used for cache freshness.

---

## Prompt pipeline (v2)

1. **Structured vision** — Ollama image → JSON fields: face, hair, body, clothing, pose, expression, lighting, background.
2. **Erotic rewrite** — text model + optional creator style prefix → positive paragraph.
3. **Exports** — `flux` / `sdxl` / `pony` / `negative` via `build_export_variants`.
4. **Mode E** (`comfy_mode.py`) — strip identity; outfit/scene only for IPAdapter ref generate.
5. **Cache** — write-through JSON; max 3 history snapshots on save/regenerate.

Stale when `vision_engine` or `pipeline_version` mismatch.

---

## HTTP API (compact)

Base: `http://localhost:5000`. Full schemas → [api.md](api.md).

| Method | Path | Notes |
|--------|------|-------|
| GET | `/api/health` | Ollama + Comfy reachability |
| GET | `/api/stats` | photos, creators, `prompts_ready` |
| GET | `/api/creators` | folders + sync badges |
| GET | `/api/photos` | `creator`, `search`, `unanalyzed`, `favorite`, `media_type` (`photo`/`video`), `sort`, `offset`, `limit` |
| GET/PUT | `/api/prompt` | get bundle / save edits |
| POST | `/api/prompt/restore` | history index |
| POST | `/api/prompt/mode-e` | Mode E rewrite; `apply` |
| POST | `/api/prompt/batch` | background analyze |
| GET | `/api/prompt/batch/status` | batch progress (snapshot; no archive scan) |
| POST | `/api/prompt/batch/cancel` | cooperative cancel after current photo |
| PUT | `/api/favorite` | toggle / set |
| DELETE | `/api/photo` | soft delete → `_trash/`; `permanent=1` unlinks |
| GET | `/api/trash` | trashed entries + size + retention |
| POST | `/api/trash/{restore,purge}` | restore by id(s) · purge id(s)/`all`/`expired` |
| POST | `/api/creator/create` | new folder |
| POST | `/api/photo/upload` | multipart `creator` + `file` |
| GET/POST | `/api/creator/style` · `/rebuild` | style prefix |
| GET | `/api/following` | local following_list.json |
| POST | `/api/sync/{saved,creator,following}` | background jobs |
| GET | `/api/sync/status` | running + abort + queue |
| POST | `/api/comfy/generate` | pro (default) or txt2img |
| GET | `/api/comfy/status` · `/api/generations` | job + history |
| GET | `/media/...` · `/media/thumb/...` | full / thumb |

CORS: `*`. Methods: GET, POST, PUT, DELETE, OPTIONS. Server is **threaded**.

---

## Scraping modes

| Mode | CLI | API |
|------|-----|-----|
| Saved posts | `scripts/download_instagram_saved.py` | `POST /api/sync/saved` |
| Creator feed | `scripts/download_creator_feed.py HANDLE` | `POST /api/sync/creator` |
| Following bulk | `scripts/download_following.py` | `POST /api/sync/following` |
| Export following | `scripts/export_following_list.py` | — |
| Classify following | `scripts/classify_following.py` | — (local vision, dry-run) |
| Prioritize queue | `scripts/prioritize_following_queue.py` | — (classify keep first) |
| Score local glam | `scripts/classify_local_photos.py` | gallery Sexy filter |
| Backfill glam DB | `scripts/backfill_glam_scores.py` | from classify report, no Ollama |

**Videos/reels:** default ON (`IG_INCLUDE_VIDEOS`, `include_videos` on API/UI). Use `--no-reels` to skip.  
**Queue priority:** `following_queue.json` `priority` + `reason`; `next_pending` highest first.  
**Post rank:** `IG_POST_RANK` + `IG_CAPTION_KEYWORDS` — prefer sexy captions/reels inside a feed.  
**Glam filter:** `GET /api/photos?sexy=1` or `glam_min=2`; sort `glam`.

Idempotent: skip by `post_id`/`shortcode` in DB + meta. Catch-up stop after `IG_CATCH_UP_STREAK` consecutive hits. Following uses daily queue + random pauses; abort after rate-limit streak or abuse phrases. Details → [instagram_downloader.md](instagram_downloader.md).

---

## Task → file cheat sheet

| Want to… | Touch |
|----------|--------|
| Add/change API route | `promptstudio/server/handler.py` (+ `app.js` if UI) |
| Change env/paths/defaults | `promptstudio/config.py` |
| Vision / rewrite / exports | `promptstudio/prompts/engine.py` |
| Mode E / Comfy prompt shaping | `promptstudio/prompts/comfy_mode.py` |
| Prompt cache schema | `promptstudio/prompts/cache.py` |
| Gallery query/sort/index | `promptstudio/storage/db.py`, `archive.py` |
| Soft delete / restore / purge | `promptstudio/storage/trash.py` (+ `archive.delete_photo`) |
| Download / rate-limit / filters | `scraping/downloader.py`, `filters.py`, `queue.py` |
| Add or change a scrape source | `scraping/sources/` — see `docs/multi_source_scraping.md` §7 |
| Comfy workflow wiring | `comfy/client.py`, `comfy/workflows/*.json` |
| UI behavior | `app.js` |
| UI chrome/theme | `style.css`, `index.html` |
| New CLI | thin wrapper in `scripts/` calling package |
| Add a test | `tests/test_*.py` (pytest, `store`/`make_photo` fixtures) or `tests/ui/` |

---

## Hard rules (agents)

1. **Never delete archive media** without user confirm via UI `#deleteConfirmModal` → `DELETE /api/photo`. Deletes are **soft** (`_trash/`) — only `permanent=1` or `/api/trash/purge` destroys data, and neither may be called without an explicit user action.
2. **No `cgi` module** — use `promptstudio.server.multipart`.
3. **Python 3.14+ Windows** compatible stdlib only for new code unless already a dep.
4. **Logic in `promptstudio/`**, not fat scripts.
5. **Do not reintroduce** non-person filter folder UX; excluded dirs stay excluded.
6. Lightbox a11y: ←/→/Esc. Keep glass dark theme + brand colors.
7. Prefer editing existing modules over new top-level files.

---

## Doc index

| File | When to open |
|------|----------------|
| **context.md** (this) | Always first — map + rules |
| [api.md](api.md) | Request/response shapes |
| [architecture.md](architecture.md) | Component diagram, data flow |
| [instagram_downloader.md](instagram_downloader.md) | Sync pacing, queue, resume |
| [troubleshooting.md](troubleshooting.md) | Ollama, ports, cache wipe |
| [roadmap.md](roadmap.md) | Phase history / future |
| [agent.md](agent.md) | Short ops checklist |
| Root [AGENTS.md](../AGENTS.md) | Injected workspace rules |
| [scripts/README.md](../scripts/README.md) | CLI examples |
