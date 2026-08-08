# PromptStudio Development Roadmap

Agent map: [context.md](context.md). Phased plan: **scrape → organize → analyze → search → generate**.

---

## Phase 1 — Structured scraping foundation ✅

**Goal:** Reliable Instagram ingest with clean module layout.

| Deliverable | Status |
|-------------|--------|
| `promptstudio/` package (`config`, `storage`, `scraping`, `server`) | Done |
| Sync **saved posts** (`scripts/download_instagram_saved.py`) | Done |
| Sync **single creator feed** (`scripts/download_creator_feed.py`) | Done |
| Sync **following list** bulk (`scripts/download_following.py`) | Done |
| Post **metadata sidecars** (`*.meta.json` per image) | Done |
| **Organize + dedupe** utilities in package | Done |
| API: `POST /api/sync/saved`, `POST /api/sync/creator`, `GET /api/sync/status` | Done |
| UI: Instagram Sync modal in navbar | Done |

---

## Phase 2 — Prompt pipeline & search ✅

**Goal:** Automate analysis and make the archive searchable by prompt content.

| Deliverable | Status |
|-------------|--------|
| Prompt cache keyed by `creator/filename` (backward compatible) | Done |
| `GET /api/photos?search=` searches prompt text + tags | Done |
| `GET /api/stats` includes `prompts_ready` | Done |
| `POST /api/prompt/batch` background batch generation | Done |
| UI: batch analyze button + sync status polling | Done |

---

## Phase 3 — Smarter scraping ✅

| Deliverable | Status |
|-------------|--------|
| Following sync UI (max accounts/posts + bio keywords) | Done |
| `promptstudio/scraping/filters.py` bio/media filters | Done |
| Biography field on `export_following_list.py` | Done |
| Resume checkpoints (`sync_state.json`) | Done |
| Exponential rate-limit backoff + SyncManager counters | Done |
| `group_by_post_id()` carousel metadata helper | Done |

---

## Phase 3b — Anti-ban following crawler (Phase A) ✅

| Deliverable | Status |
|-------------|--------|
| Persistent `following_queue.json` with daily budget | Done |
| Randomized post / account / batch pauses | Done |
| Hard abort on rate-limit streak or abuse signals | Done |
| CLI `--accounts-per-day` + abort exit code | Done |
| Sync UI default 20 accounts/day + abort status | Done |

---

## Phase 3c — Idempotent downloads ✅

| Deliverable | Status |
|-------------|--------|
| SQLite `post_id` / `shortcode` identity index | Done |
| Skip already-archived posts (not filename-only) | Done |
| Catch-up streak stop (`IG_CATCH_UP_STREAK`) | Done |
| Keep local when IG deletes; re-fetch after local delete | Done |
| Shortcode-aware archive dedupe | Done |

---

## Phase 4 — Prompt quality ✅
| Deliverable | Status |
|-------------|--------|
| Two-stage pipeline: structured vision JSON → intensity-aware rewrite | Done |
| Cache version bump (`pipeline_version: v2-structured`) | Done |
| Creator style blocks (`creator_styles.json`) | Done |
| Export variants: Flux / SDXL / Pony + UI copy buttons | Done |

---

## Phase 5 — Scale & UX ✅

| Deliverable | Status |
|-------------|--------|
| Thumbnails via `/media/thumb/...` (Pillow or OpenCV) | Done |
| Paginated gallery (`offset` / `limit` + infinite scroll) | Done |
| SQLite `archive.db` photo catalog (list/filter/sort) | Done |
| In-memory write-through prompt + favorites caches | Done |
| Per-creator **Classify unscored** job + Rejects review/delete UI | Done |

---

## Phase 6 — ComfyUI loop ✅

| Deliverable | Status |
|-------------|--------|
| `ThreadingHTTPServer` (gallery stays responsive during sync/batch/Comfy) | Done |
| Comfy client: Pro `modelToimage_pro` + txt2img fallback | Done |
| APIs: `/api/comfy/generate`, `/api/comfy/status`, `/api/generations` | Done |
| Mode E rewrite + denoise/steps/CFG/seed controls | Done |
| Side-by-side Original vs Generated in lightbox | Done |
| Health includes ComfyUI reachability | Done |

**CLIP?** Not needed for the generate → Comfy → compare loop. Tags + prompt search cover discovery. Defer until you want “find visually similar photos.”

---

## Future (optional)

- CLIP embedding index for visual similarity (optional; not required for Comfy)
- FastAPI async job queue (partially covered by threaded HTTP + background jobs)
- Custom ComfyUI `workflow_api.json` import UI

---

## Project layout

```
promptstudio/
├── config.py
├── storage/     archive db favorites metadata thumbs
├── scraping/    session downloader filters queue checkpoints
│                organizer sync_manager outfit_classifier
├── prompts/     cache engine styles batch comfy_mode
├── comfy/       client + workflows/modelToimage_pro.api.json
└── server/      handler multipart

scripts/                   # Thin CLI wrappers
server.py / prompt_engine.py
```

See [context.md](context.md) for the full task→file map.

---

## CLI quick reference

```powershell
py scripts/download_instagram_saved.py
py scripts/download_creator_feed.py roxeuoon --max-posts 50
py scripts/export_following_list.py
py scripts/download_following.py --accounts-per-day 20 --max-posts 30 --keywords ""
py scripts/organize_and_filter.py
py scripts/deduplicate.py
py server.py
```
