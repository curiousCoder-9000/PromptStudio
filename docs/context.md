# PromptStudio — Agent Context Map

**Read this first.** Dense map so agents avoid scanning the whole tree.  
Detail on demand: [api.md](api.md) · [instagram_downloader.md](instagram_downloader.md) · [troubleshooting.md](troubleshooting.md) · [roadmap.md](roadmap.md)

**Hard rules live in [AGENTS.md](../AGENTS.md)** (auto-loaded) — not restated here.

**Do not load as agent context:** `docs/archive/` (shipped design docs, history only) ·
`docs/following_list.md` (generated data dump stub).

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
pytest                  # unit tests — temp archive, no server needed
ruff check .            # must be clean — config in pyproject.toml
./tests/ui/run.sh       # browser suites; needs Node 22+ and Chrome
```

Deps: `instaloader`, `opencv-python-headless`, `Pillow`, `python-dotenv` (`requirements.txt`).

---

## Package map (edit here)

```
promptstudio/
  config.py              # ALL env defaults, paths, pacing, models
  logging_setup.py       # lazy logging config; handlers on the promptstudio logger
  jobs.py                # LeaseRegistry — exclusive ollama/instagram/comfy leases
  insights.py            # archive stats/aggregates behind /api/insights
  server/
    handler.py           # Every HTTP route lives here
    multipart.py         # cgi-free multipart upload parser
  storage/
    archive.py           # ArchiveStore façade (creators, photos, upload/delete)
    atomic.py            # atomic_write_json — ALL derived-state writes go here
    db.py                # ArchiveIndex: photos + prompts + phashes + tombstones
    dedupe.py            # perceptual hash + near-duplicate grouping
    paths.py             # resolve_path containment — all media path resolution
    journal.py           # append-only JSONL run history per job kind
    favorites.py         # favorites.json write-through
    metadata.py          # *.meta.json sidecars (post_id, shortcode)
    thumbs.py            # /media/thumb generation under _thumbs/
    trash.py             # TrashStore soft delete + restore under _trash/
  prompts/
    engine.py            # Ollama: structured vision → rewrite → exports
    cache.py             # prompts table in archive.db (JSON imported once) + history
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
    video_frames.py      # reel frame extraction / cover-image pick
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
tests/                   # one test_<concern>.py per module; `ls tests/` for the current set
  conftest.py            # temp archive + cache reset (sets env BEFORE import)
  # security / protocol: test_paths, test_path_containment, test_byte_range, test_multipart
  # storage:            test_atomic_write, test_prompt_store, test_trash, test_dedupe,
  #                     test_sort_newest, test_index_sidecar_reads, test_journal, test_stats
  # jobs / runtime:     test_job_leases, test_batch_job, test_logging_and_errors, test_insights
  # scraping:           test_filters, test_sources, test_source_dispatch,
  #                     test_source_identity, test_scrape_options
  # comfy:              test_comfy_seed
  ui/                    # headless-Chrome suites over CDP (run.sh)
```

**Frontend:** `app.js` owns all UI state/API calls; `style.css` glassmorphism (`#8b5cf6`, `#ec4899`, `#06b6d4`); fonts Outfit / Inter / Fira Code.

**Frontend invariants:**
- **Escape before `innerHTML`.** Handles, captions, bios, filenames, and Ollama tags are all third-party text — run them through `escapeHtml()` (or use `textContent`). Numeric JSON fields get `Number(...)`.
- **User-typed input is debounced** (`debounce()`; `SEARCH_DEBOUNCE_MS` = 250) and **in-flight fetches are abortable** — `state.photosRequest` / `creatorStyleRequest` / `followingRequest` hold the current `AbortController`. Treat `err.name === 'AbortError'` as success-by-supersession, and only clear loading state if `state.xRequest === controller`.
- Video type detection goes through `isVideoFilename()` — do not inline extension lists.
- **Long jobs report progress in a chip, never a repeating toast.** `#jobChipStack` holds one chip per job kind (scrape / batch / classify); drive it with `renderJobChip(kind, {...})` and toast only on start and finish.
- **View prefs persist, navigation does not.** `PREF_FIELDS` + `saveViewPrefs()` keep sort/media/grid/filters in `localStorage`; selected creator, selection, and review mode (`state.reviewMode`) are deliberately *not* restored (landing in a destructive mode from a refresh is hostile). Call `saveViewPrefs()` in any new view-control handler.

---

## Data on disk (`PROMPTSTUDIO_ARCHIVE`, default `~/Pictures/InstagramSaved`)

| Path | Role |
|------|------|
| `<creator>/*.jpg\|png\|webp\|mp4` | Media (creator = IG handle) |
| `<creator>/*.meta.json` | Sidecar: `post_id`, `shortcode`, `caption`, `taken_at` |
| `archive.db` | SQLite catalog: `photos` (incl. `prompt_search` model text + `caption_search` creator text), `prompts`, `prompts_fts`, `phashes`, `media_verdicts`, `deleted_posts` (WAL) |
| `prompts_cache.json` | **Legacy.** Imported into `archive.db` once, then left as a rollback snapshot — no longer updated |
| `favorites.json` | Favorite flags |
| `creator_styles.json` | Learned style prefixes |
| `sync_state.json` | Per-creator last shortcode / counts |
| `following_queue.json` | Multi-day following crawl budget |
| `sync_status.json` | Last sync job status |
| `generations_index.json` | Comfy outputs index |
| `promptstudio.log` | Rotating app log (`PROMPTSTUDIO_LOG_FILE=` to disable) |
| `_journal/<kind>.jsonl` | Append-only run history: `batch_prompt`, `classify`, `sync` |
| `_classify/<creator>/*.sheet.jpg` | Reel contact sheets — the input a verdict was made from, shown in triage |
| `_thumbs/` | JPEG thumbs |
| `_generations/` | Comfy outputs |
| `_trash/<entry_id>/` | Soft-deleted media + sidecar + `entry.json` manifest |
| `_no_person_detected/` | Legacy exclude (do not resurrect filter UX) |

**Repo-root (not archive):** `following_list.json` (+ `.target`/`.lock`/`.freeze` during export).

**Session:** `INSTALOADER_SESSION_DIR` for user `INSTAGRAM_SESSION_USER` (required in `.env` for scrape).

**Excluded folder names:** `_no_person_detected`, `_thumbs`, `_generations`, `_classify`, `_trash`, `_journal`.
(`_classify/` holds saved reel contact sheets; being excluded is what keeps them out of the gallery, the creator list and every rebuild — and it is why `GET /api/classify/sheet` has to serve them rather than `/media/`.)

---

## Config knobs (`promptstudio/config.py` + env)

| Env | Default | Meaning |
|-----|---------|---------|
| `PROMPTSTUDIO_PORT` | `5000` | HTTP port |
| `PROMPTSTUDIO_HOST` | `127.0.0.1` | Bind address. **Blank also means loopback.** No auth + CORS `*`, so any other value exposes the archive and `DELETE /api/photo` to the network |
| `PROMPTSTUDIO_ARCHIVE` | `~/Pictures/InstagramSaved` | Archive root |
| `PROMPTSTUDIO_REBUILD_INDEX` | off | Force SQLite reindex on start |
| `OLLAMA_VISION_MODEL` | `qwen2.5vl:7b` | Vision model (**not** moondream) |
| `OLLAMA_REWRITE_MODEL` | same as vision | Stage-2 rewrite model |
| `OLLAMA_URL` | `…/api/generate` | Ollama generate |
| `COMFYUI_URL` | `http://127.0.0.1:8188` | Comfy API |
| `COMFYUI_CHECKPOINT` | `juggernautXL_ragnarok.safetensors` | Default ckpt |
| `IG_*` | see config | Anti-ban delays, daily cap 20, catch-up streak 3 |
| `IG_INCLUDE_VIDEOS` | `1` | Creator/following download reels |
| `IG_POST_RANK` | `1` | Rank feed posts by caption/reel signals |
| `IG_POST_SCAN_FACTOR` | `3` | Scan window = max_posts × factor |
| `CLASSIFY_REJECT_MAX_TIER` | `1` | Tiers `0..N` are rejects. Only the tier is stored, so changing this re-thresholds the archive with no re-classify |
| `CLASSIFY_REEL_SHEET` | `1` | Score reels from a whole-timeline contact sheet (`0` = ranked frames) |
| `CLASSIFY_*` | see `.env.example` | Vision request shape, reel contact sheet, frame ranking, retries |
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
| GET | `/api/photos` | `creator`, `search` (prompts **+ caption**), `unanalyzed`, `favorite`, `media_type` (`photo`/`video`), `verdict`, `sort` (incl. `tier`), `offset`, `limit` |
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
| Prioritize queue | `scripts/prioritize_following_queue.py` | — |

**Videos/reels:** default ON (`IG_INCLUDE_VIDEOS`, `include_videos` on API/UI). Use `--no-reels` to skip.  
**Queue priority:** `following_queue.json` `priority` + `reason`; `next_pending` highest first.  
**Post rank:** `IG_POST_RANK` + `IG_CAPTION_KEYWORDS` — prefer matching captions/reels inside a feed.

Idempotent: skip by `post_id`/`shortcode` in DB + meta. Catch-up stop after `IG_CATCH_UP_STREAK` consecutive hits. Following uses daily queue + random pauses; abort after rate-limit streak or abuse phrases. Details → [instagram_downloader.md](instagram_downloader.md).

---

## Task → file cheat sheet

| Want to… | Touch |
|----------|--------|
| Add/change API route | `promptstudio/server/handler.py` (+ `app.js` if UI) |
| Change env/paths/defaults | `promptstudio/config.py` |
| Vision / rewrite / exports | `promptstudio/prompts/engine.py` |
| Mode E / Comfy prompt shaping | `promptstudio/prompts/comfy_mode.py` |
| Prompt cache schema | `promptstudio/prompts/cache.py` (+ `prompts` table in `storage/db.py`) |
| Gallery query/sort/index | `promptstudio/storage/db.py`, `archive.py` |
| Soft delete / restore / purge | `promptstudio/storage/trash.py` (+ `archive.delete_photo`) |
| Download / rate-limit / filters | `scraping/downloader.py`, `filters.py`, `queue.py` |
| Add or change a scrape source | `scraping/sources/` — see `docs/multi_source_scraping.md` §7 |
| Comfy workflow wiring | `comfy/client.py`, `comfy/workflows/*.json` |
| UI behavior | `app.js` |
| UI chrome/theme | `style.css`, `index.html` |
| New CLI | thin wrapper in `scripts/` calling package |
| Writing any state file | `storage/atomic.py` — never bare `open(path, "w")` |
| Cross-job exclusion | `promptstudio/jobs.py` leases — not `is_running()` checks |
| Debugging a long job | `<archive>/_journal/<kind>.jsonl`, `GET /api/journal` |
| Duplicate detection | `storage/dedupe.py`, `scripts/find_duplicates.py` |
| Add a test | `tests/test_*.py` (pytest, `store`/`make_photo` fixtures) or `tests/ui/` |

---

## Hard rules (agents)

Single source: **[AGENTS.md](../AGENTS.md)** — auto-loaded via `CLAUDE.md`, so they are
already in context. Deliberately not duplicated here; the copy that used to live in this
section drifted four rules behind.

---

## Doc index

| File | When to open |
|------|----------------|
| **context.md** (this) | Always first — map, data layout, task→file table |
| Root [AGENTS.md](../AGENTS.md) | Hard rules (auto-loaded) |
| [api.md](api.md) | Request/response shapes |
| [architecture.md](architecture.md) | Component diagram, data flow |
| [instagram_downloader.md](instagram_downloader.md) | Sync pacing, queue, resume |
| [multi_source_scraping.md](multi_source_scraping.md) | X / Reddit via gallery-dl |
| [troubleshooting.md](troubleshooting.md) | Ollama, ports, cache wipe, known bugs |
| [review_backend_architecture.md](review_backend_architecture.md) | Backend decisions + measurements |
| [roadmap.md](roadmap.md) | Phase history / future |
| [product_review.md](product_review.md) | Product themes A/B/C/E, accepted backlog + sequence |
| [review_ui_product.md](review_ui_product.md) | UI/UX gaps (U1–U11), the loopback-bind correction, Stage-1 fix log |
| [backlog_features.md](backlog_features.md) | F1–F8 in detail — captions, archive-wide classify, duplicates UI, activity view |
| [backlog_engineering.md](backlog_engineering.md) | E1–E5 — pollers, `app.js` ownership, runtime reject-cut, verification gaps |
| [design_generation_loop.md](design_generation_loop.md) | Theme A spec — generations table, rating, outputs gallery, batch, workflow registry |
| [scripts/README.md](../scripts/README.md) · [tests/ui/README.md](../tests/ui/README.md) | CLI examples · browser suites |
| `archive/` | **Do not load.** Shipped/superseded designs, kept for history |
