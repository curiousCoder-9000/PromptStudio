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

## Phase 7 — Delete safety & optimistic gallery ✅

**Goal:** make the reject-review cleanup loop non-destructive and stop reloading the whole app on every mutation.

| Deliverable | Status |
|-------------|--------|
| `promptstudio/storage/trash.py` — `_trash/<entry_id>/` + `entry.json` manifest | Done |
| Restore returns file, sidecar, prompt bundle, favorite, index row, un-tombstones | Done |
| APIs: `GET /api/trash`, `POST /api/trash/restore`, `POST /api/trash/purge`, `?permanent=1` | Done |
| Trash modal (restore / delete forever / empty / purge expired) + nav count badge | Done |
| Undo toast after single + bulk delete (`trash_id` round trip) | Done |
| Optimistic gallery removal — no `initApp()`, scroll and loaded pages survive | Done |
| Bulk delete progress counter; confirm copy says "Move to Trash" + flags favorites | Done |

---

## Phase 8 — Frontend correctness pass ✅

**Goal:** stop the gallery from disagreeing with the active filters, and stop trusting third-party text.

| Deliverable | Status |
|-------------|--------|
| `escapeHtml()` applied to every dynamic `innerHTML` site (handles, filenames, tags, prompt history, IG `full_name`, queue rows) | Done |
| Search debounced 250 ms (`debounce()`), `Enter` bypasses it | Done |
| `AbortController` on photos / creator-style / following fetches — newest request wins | Done |
| Removed the `photosLoading` guard that silently dropped filter changes | Done |
| Offset committed only on response, so aborts can't corrupt paging | Done |
| Search spinner + `aria-busy` on the grid while fetching | Done |
| Video detection unified on `isVideoFilename()` | Done |

---

## Phase 9 — Test harness & CI ✅

**Goal:** stop verifying by hand. Cover the code where a silent failure is expensive.

| Deliverable | Status |
|-------------|--------|
| `pyproject.toml` — pytest + ruff config (bug-focused rules, not style churn) | Done |
| `requirements-dev.txt` | Done |
| 107 unit tests: path containment, Range parsing, multipart, filters, trash | Done |
| `tests/ui/` — 70 browser checks over CDP, no npm deps (Node 22+ `WebSocket`) | Done |
| `.github/workflows/ci.yml` — lint + tests on 3.10/3.13 + UI job | Done |
| **Fixed:** `resolve_path` prefix check let paths escape the archive | Done |

**Found by writing the tests:** `resolve_path` used `full.startswith(base)`, which is
not containment — with base `…/InstagramSaved`, the path `../InstagramSaved_backup/x.jpg`
normalized to a sibling directory and resolved. Every media route
(`/media/…`, `/api/media/detail`, `DELETE /api/photo`) goes through it, and CORS is `*`,
so a page in the browser could read files from a prefix-sharing sibling — and with soft
delete, move them. Now compared on path boundaries; `tests/test_paths.py` covers it.

---

## Phase 10 — Job feedback & control ✅

**Goal:** make long jobs interruptible and stop reporting progress by toast spam.

| Deliverable | Status |
|-------------|--------|
| Cancel for `BatchPromptManager` + `POST /api/prompt/batch/cancel` | Done |
| `#jobChipStack` — one chip per job kind, progress bar + cancel | Done |
| Batch + classify progress toasts dropped to start/finish only | Done |
| `/api/prompt/batch/status` no longer calls `list_uncached()` per poll | Done |
| Batch chip resumes after a browser refresh (jobs live server-side) | Done |

Cancel is cooperative and checked *between* items: the in-flight Ollama call
isn't interruptible, and abandoning it mid-write would poison the prompt cache.

---

## Phase 11 — Hot-path cost ✅

| Deliverable | Status |
|-------------|--------|
| `prompts_ready` from the indexed `has_prompt` column in one SQL aggregate | Done |
| `/api/stats` no longer walks the archive or loads the prompt cache | Done |
| `count_prompts_ready()` kept as the exact reference (asserted equal in tests) | Done |

---

## Phase 12 — Polish ✅

| Deliverable | Status |
|-------------|--------|
| View prefs (sort / media / grid / filter chips) persisted to `localStorage` | Done |
| `gridSize` promoted from a bare DOM class into state so it can be restored | Done |
| Restored prefs applied *before* the first `/api/photos` request | Done |
| Skeleton cards + `aria-busy` while the first page loads (not on append) | Done |
| Segmented sync-mode radio replaces 3 contradictory checkboxes | Done |
| `catch_up_only` finally reachable from the UI | Done |

Navigation state is deliberately **not** restored — selected creator, selection,
and reject-review mode all start clean, so a refresh can't drop you into a
destructive mode.

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
