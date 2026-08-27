# AGENTS.md — PromptStudio

Workspace rules, auto-loaded via [CLAUDE.md](CLAUDE.md).
**Full map:** [docs/context.md](docs/context.md) — read first; keeps later tasks cheap.

## Stack (current)

| Piece | Detail |
|-------|--------|
| App | PromptStudio — local IG archive + Ollama vision prompts + optional ComfyUI |
| Server | `server.py` → `promptstudio.server.handler` · `http://localhost:5000` (threaded) |
| Vision | Ollama `OLLAMA_VISION_MODEL` default **`qwen2.5vl:7b`** · pipeline `v2-structured` |
| Frontend | `index.html` / `style.css` / `app.js` (glass dark: `#8b5cf6` `#ec4899` `#06b6d4`) |
| Archive | `~/Pictures/InstagramSaved` (`PROMPTSTUDIO_ARCHIVE`) · catalog `archive.db` (photos · prompts · phashes · media_verdicts) |
| Observability | `<archive>/promptstudio.log` · run history `<archive>/_journal/<kind>.jsonl` |
| Package | All logic under `promptstudio/` · `scripts/` = thin CLIs only |

## Hard rules

These are the single source of truth — other docs point here rather than restating them.

1. **Archive safety:** never delete media without user confirm → UI `#deleteConfirmModal` → `DELETE /api/photo`. Deletes are **soft** (move to `_trash/`, restorable). Only `permanent=1` / `POST /api/trash/purge` destroy data — never call either without an explicit user action. No archive bulk-delete scripts without an explicit user ask.
2. **No `cgi`:** multipart via `promptstudio.server.multipart`. Target **Python 3.14+** on Windows; new code sticks to stdlib unless the dep already exists.
3. **Config single source:** `promptstudio/config.py` (+ `.env` / env vars). Never hardcode usernames, secrets, or archive paths. Never commit `.env`, sessions, `following_list.json`, or classify dumps.
4. **Routes live in** `promptstudio/server/handler.py` — the sole HTTP switchboard. Prefer package modules over new root files.
5. **Do not resurrect** non-person filter UX; keep `EXCLUDED_FOLDERS` behavior.
6. **UI:** preserve glassmorphism + keyboard lightbox (`←`/`→`/`Esc`).
7. **Never interpolate third-party text into `innerHTML` unescaped** — use `escapeHtml()` or `textContent`. Debounce typed input; give user-driven fetches an `AbortController`.
8. **Verify, don't assert.** `pytest` + `ruff check .` must pass before claiming done; run `./tests/ui/run.sh` for frontend changes. Add a test with the fix.
9. **Never `open(path, "w")` for state.** Use `promptstudio.storage.atomic.atomic_write_json` — a truncated file reads as empty and every loader here swallows the parse error, so a partial write is silent total loss.
10. **No `print()`.** `log = get_logger(__name__)` from `promptstudio.logging_setup`.
11. **Cross-job exclusion is a lease** (`promptstudio/jobs.py`), never an `is_running()` check on another manager — polling then starting is a race. Declare the resource, acquire, release in a `finally`.
12. **Classify stores the tier, never the verdict.** `media_verdicts.tier` is the
    measurement; keep/reject is derived against `CLASSIFY_REJECT_MAX_TIER` at query
    time. Storing the collapsed answer is what made the previous classifier cost a
    full-archive rescore per change of mind. Never add a `verdict` column.
13. **Measure before optimising, and report the number.** Two "obvious" wins in this codebase turned out to be losses under measurement (FTS5 search, incremental rebuild) — both recorded in [docs/review_backend_architecture.md](docs/review_backend_architecture.md).
14. **Loopback only, and blank means loopback.** There is no auth and CORS is `*`, so any
    non-loopback bind hands the archive and `DELETE /api/photo` to the whole network. Use
    `config.resolve_host()`; never `os.environ.get("PROMPTSTUDIO_HOST", "…")` directly — a
    set-but-empty var returns `""`, not the default, and `""` binds every interface. Same
    trap for any future host/port/origin knob.
15. **The UI must work offline.** Fonts and icons are vendored in `assets/`
    (`scripts/vendor_web_assets.py`). No CDN `<link>`, `<script src>` or `@import` in
    `index.html` — this is a local-first app and most of its buttons are icon-only.
16. **IG sync:** multi-day pacing; stop on abort; never password-login every run.
17. **Every score and every filter declares its distribution** (B4, standing policy since
    Phase 14). Ship a new one and you ship two things with it: its pass rate on screen
    where it is used, and a case in `tests/test_distribution_guard.py` — hand
    `insights.saturation_report()` a bucket→count mapping over the metric's **own**
    denominator (never a "not judged yet" bucket) and a minimum N. The previous
    classifier put 85% of the archive on one tier for three prompt versions; the number
    that would have caught it existed the whole time and nothing failed when it moved.

## Where to look

| Task | File |
|------|------|
| Any API change | `promptstudio/server/handler.py` |
| A new score or filter | `promptstudio/insights.py` `saturation_report` + `tests/test_distribution_guard.py` (rule 17) |
| Vision / prompts | `promptstudio/prompts/engine.py` |
| Keep/reject classify | `promptstudio/scraping/media_classifier.py` · job in `classify_job.py` |
| Setting a verdict from anywhere in the UI | `applyManualVerdict(photo, value)` + `patchCardVerdict(photo)` in `app.js`. The lightbox and the card are two callers of one function on purpose — never re-implement the patch, and never refetch (it drops the row out from under the cursor) |
| Gallery index | `promptstudio/storage/db.py` |
| Gallery feels slow | [`docs/review_gallery_performance.md`](docs/review_gallery_performance.md) — §11 for what already shipped; measure first; do not flip FTS5 |
| Thumbnails | `promptstudio/storage/thumb_queue.py` (ingest + workers) · `thumbs.py` (encode) · `scripts/backfill_thumbnails.py`. **Never** encode on the `/media/thumb/` request thread |
| Background job contention | `promptstudio/jobs.py` |
| A read that must not queue behind a write | `promptstudio/storage/db.py` `_read()` — **never** `self._lock` for a SELECT |
| Gallery DOM / windowing | `app.js` `syncGalleryWindow` + `buildPhotoCard`. `state.photos` is the model; do not derive truth from mounted cards |
| Why a job did that | `<archive>/_journal/`, `GET /api/journal` |
| Duplicate detection | `promptstudio/storage/dedupe.py` |
| Instagram sync | `promptstudio/scraping/downloader.py` (Instaloader) · `instagram_source.py` (`IG_BACKEND=gallery-dl`) |
| Add a scrape source | `promptstudio/scraping/sources/` |
| Add a ComfyUI workflow | `promptstudio/comfy/registry.py` · `comfy/workflows/<name>/` |
| ComfyUI | `promptstudio/comfy/client.py` |
| Frontend | `app.js` |
| Layout budget · focus rings · icon glyphs | `tests/ui/test_layout_and_a11y.js` — measured, not read; see [`docs/review_ui_product.md`](docs/review_ui_product.md) §10–11 |
| A new overlay, or anything about focus | `openDialog`/`closeDialog` in `app.js` — never assign `style.display` on a dialog directly. `tests/ui/test_dialogs_and_aria.js` · [`docs/review_ui_product.md`](docs/review_ui_product.md) §13 |
| A new toggle button | `setToggleState()` in `app.js` — sets the `active` class and `aria-pressed` together |
| Anything in `index.html`, or a new browser suite | `tests/test_markup_structure.py` — tag balance, one `<main>` and whose child it is, unique ids, `aria-labelledby` resolution, user-visible copy, and the suites' own regex escaping |
| A new navbar button | It goes in one of the two groups either side of `.nav-divider`: labelled if it starts a job, `icon-only` + `aria-label` if it navigates. The bar has ~340px of slack at 1280px; spend it and it wraps to two rows again ([`docs/review_ui_product.md`](docs/review_ui_product.md) §14) |

## Runtime checks

```powershell
# Ollama reachable
py -c "import urllib.request; print(urllib.request.urlopen('http://localhost:11434/api/tags', timeout=2).read()[:200])"
py server.py            # app on :5000
py prompt_engine.py     # vision smoke test
```

Health: `GET /api/health` → `ollama`, `model` (`qwen2.5vl:7b` default), `model_ready`, `comfy`, `leases`.

## Docs (token budget)

Load only what the task needs.

| Doc | Open when |
|-----|-----------|
| [docs/context.md](docs/context.md) | **Always first** — package map, data layout, task→file table |
| [docs/api.md](docs/api.md) | Request/response schemas |
| [docs/architecture.md](docs/architecture.md) | Component diagram, request/sync flows |
| [docs/instagram_downloader.md](docs/instagram_downloader.md) | IG sync pacing, queue, resume |
| [docs/multi_source_scraping.md](docs/multi_source_scraping.md) | X / Reddit via gallery-dl |
| [docs/troubleshooting.md](docs/troubleshooting.md) | Ollama, ports, cache wipe, known bugs |
| [docs/review_backend_architecture.md](docs/review_backend_architecture.md) | Backend decisions + measurements |
| [docs/review_gallery_performance.md](docs/review_gallery_performance.md) | Live 61k-archive gallery latency — SQL, thumbs, HTTP/1.0; do this before FTS/windowing |
| [docs/product_review.md](docs/product_review.md) | Product themes, accepted backlog |
| [docs/review_ui_product.md](docs/review_ui_product.md) | UI/UX gaps U1–U11 + Stage-1 fix log |
| [docs/backlog_features.md](docs/backlog_features.md) · [docs/backlog_engineering.md](docs/backlog_engineering.md) | F1–F8 · E1–E5, picked up directly |
| [docs/roadmap.md](docs/roadmap.md) | Phase history; 13–15 planned |
| [docs/design_generation_loop.md](docs/design_generation_loop.md) | Active spec for Phases 13–14 |
| [docs/design_source_filter.md](docs/design_source_filter.md) | Source as a view filter — provenance comes from `photos.source` |
| [docs/design_scrape_lanes.md](docs/design_scrape_lanes.md) | Per-source scrape lanes — concurrency, cancel, pause, pacing |
| [scripts/README.md](scripts/README.md) · [tests/ui/README.md](tests/ui/README.md) | CLI examples · browser suites |

**Never load:** `docs/archive/` (shipped/superseded design docs, ~117KB — history only),
`docs/following_list.md` (generated data dump stub), full `app.js` / `style.css` unless it is a UI task,
the whole `scripts/` tree for non-scrape work.

## Stale doc traps

| Wrong (old docs) | Current |
|------------------|---------|
| Vision model `moondream` | `qwen2.5vl:7b` (`OLLAMA_VISION_MODEL`) |
| Multipart in `server.py` via `cgi` | `promptstudio.server.multipart` |
| Monolithic `server.py` | Thin shim → `handler.py` |
| `opencv-python` only | `opencv-python-headless` (+ optional Pillow) |
| Prompts in `prompts_cache.json` | `prompts` table in `archive.db`; JSON is a stale rollback snapshot |
| Gallery is fine at 4.4k with `content-visibility` | Live archive is **61k**; SQL+thumbs dominate first paint. [`docs/review_gallery_performance.md`](docs/review_gallery_performance.md) |
| `query_photos` selects `p.*` and orders by `IFNULL(added_at, mtime)` | Named columns + bare indexed ORDER BY. Both sort fallbacks are paid at **write** time (`upsert_photo`) |
| `GET /media/thumb/` creates the thumbnail | It serves one. `thumb_queue` workers create them at ingest; a miss gets a placeholder, never the original |
| Server is HTTP/1.0, so a response need not be framed | `protocol_version = "HTTP/1.1"`. Every response **must** send `Content-Length` (or be a 304), or the client hangs |
| Grid `verdict` carries `media_kind` / `verdict_source` / `classified_at` | Slimmed out. `GET /api/media/detail` has the full row |
| A `:focus-visible` ring can be a `box-shadow` | Any component setting its own `box-shadow` later at the same specificity silently cancels it — this is what hid focus on every `.btn-secondary`. The ring is an `outline` |
| A sticky bar can bleed over its scroller's padding with a negative margin | `top: 0` resolves against the **scroller's padding box**, so sticky clamps it back down. `.inspector-panel` carries no vertical padding; its sticky header/footer supply it. `.gallery-header` is fine — its scroller is the document |
| Any `fa-*` class name works offline | Only names in the vendored **Free** set. A Pro name (`fa-sparkles`, `fa-image-slash`) renders at **width 0** — an invisible icon, not a fallback box. `tests/test_offline_assets.py` enforces |
| One SQLite connection behind one `RLock` | Writer + N `mode=ro` readers. Reads go through `_read()`; a SELECT on `self._conn` re-introduces the contention |
| The grid holds a card per loaded photo | It mounts a window (~21 at 1280×800). An absent card is normal — every `[data-rel-path]` patch site must stay `if (card)` |
| A photo card is a `div` you click | It is `tabindex="0"` with an `aria-label` and an Enter/Space handler. It carries **no** `role="button"` on purpose — that role has presentational children and would hide the per-card delete from AT |
| Triage lives in the lightbox | Since U16 it is on the card too, in review mode only: Keep/Reject **replace** the prompt hint (`cardTriageRowHtml`), and K/R/X work on a focused tile. `X` differs between the two on purpose — the lightbox soft-deletes with Undo, the grid opens `#deleteConfirmModal`, because one keystroke on a grid fires at whatever last had focus |
| Pressing the active Keep/Reject does nothing | It hands the photo back to the model (`verdict: "auto"`, which the API already maps to null). Two directions per control keeps a third button off a 200px tile |
| Escape covers every overlay | It covers the ones listed in the one prioritised chain in `app.js`. `#insightsModal` and `#activityModal` were missing from it for three phases and the chain still *read* complete |
| An element's tag can be changed on its own line | Both ends, or the parser unwinds. `<div class="creator-list">` → `<nav …>` with the `</div>` left behind cost `<main>` its place in the `.workspace` grid and put the gallery under the sidebar. `tests/test_markup_structure.py` parses `index.html` — tag balance, one `<main>` *and* whose child it is, unique ids, `aria-labelledby` resolution |
| Ollama being down is the badge's business | It sets `body.ollama-offline`, which the grid reads to stop advertising "Click for AI Prompt". Both hint variants ship and CSS picks one — the health poller flips the class every 30s and does **not** re-render the grid, so deciding from `state.ollamaOnline` at card-mount time goes stale |
| `getComputedStyle(el, pseudo)` reads the pseudo-element | Not for UA shadow pseudo-elements. `::-webkit-calendar-picker-indicator` answers with the **host**'s style, so a browser check on it always passes. `::file-selector-button` does work. Assert the unreachable ones in `tests/test_markup_structure.py` |
| A regex in a browser suite is written once | It is written **twice** — `Session.eval` interpolates into a template literal, so `/\s+/` must be `/\\s+/` in the file or it collapses to `/s+/` and eats every letter s. No backticks in an eval body either, comments included |
| A dialog can be shown with `style.display = 'flex'` | Use `openDialog()`. Raw assignment skips the focus hand-off, the return stack and Tab containment — and focus must be set on the **next frame**, since a `display:none` subtree has no geometry and `focus()` on it silently no-ops |
