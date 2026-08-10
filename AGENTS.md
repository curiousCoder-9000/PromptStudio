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
| Gallery index | `promptstudio/storage/db.py` |
| Background job contention | `promptstudio/jobs.py` |
| Why a job did that | `<archive>/_journal/`, `GET /api/journal` |
| Duplicate detection | `promptstudio/storage/dedupe.py` |
| Instagram sync | `promptstudio/scraping/downloader.py` |
| Add a scrape source | `promptstudio/scraping/sources/` |
| ComfyUI | `promptstudio/comfy/client.py` |
| Frontend | `app.js` |

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
