# AGENTS.md — PromptStudio

Injected workspace rules. **Full map:** [docs/context.md](docs/context.md) (read first; keeps later tasks cheap).

## Stack (current)

| Piece | Detail |
|-------|--------|
| App | PromptStudio — local IG archive + Ollama vision prompts + optional ComfyUI |
| Server | `server.py` → `promptstudio.server.handler` · `http://localhost:5000` (threaded) |
| Vision | Ollama `OLLAMA_VISION_MODEL` default **`qwen2.5vl:7b`** · pipeline `v2-structured` |
| Frontend | `index.html` / `style.css` / `app.js` (glass dark: `#8b5cf6` `#ec4899` `#06b6d4`) |
| Archive | `~/Pictures/InstagramSaved` (`PROMPTSTUDIO_ARCHIVE`) · catalog `archive.db` (photos · prompts · phashes) |
| Observability | `<archive>/promptstudio.log` · run history `<archive>/_journal/<kind>.jsonl` |
| Package | All logic under `promptstudio/` · `scripts/` = thin CLIs only |

## Hard rules

1. **Archive safety:** never delete media without user confirm → UI `#deleteConfirmModal` → `DELETE /api/photo`. Deletes are **soft** (move to `_trash/`, restorable). Only `permanent=1` / `POST /api/trash/purge` destroy data — never call either without an explicit user action.
2. **No `cgi`:** multipart via `promptstudio.server.multipart`. Target **Python 3.14+** on Windows.
3. **Config single source:** `promptstudio/config.py` (+ `.env` / env vars). Never hardcode usernames, secrets, or archive paths. Never commit `.env`, sessions, `following_list.json`, or classify dumps.
4. **Routes live in** `promptstudio/server/handler.py`. Prefer package modules over new root files.
5. **Do not resurrect** non-person filter UX; keep `EXCLUDED_FOLDERS` behavior.
6. **UI:** preserve glassmorphism + keyboard lightbox (`←`/`→`/`Esc`).
7. **Never interpolate third-party text into `innerHTML` unescaped** — use `escapeHtml()` or `textContent`. Debounce typed input; give user-driven fetches an `AbortController`.
8. **Verify, don't assert.** `pytest` + `ruff check .` must pass before claiming done; run `./tests/ui/run.sh` for frontend changes. Add a test with the fix.
9. **Never `open(path, "w")` for state.** Use `promptstudio.storage.atomic.atomic_write_json` — a truncated file reads as empty and every loader here swallows the parse error, so a partial write is silent total loss.
10. **No `print()`.** `log = get_logger(__name__)` from `promptstudio.logging_setup`.
11. **Cross-job exclusion is a lease** (`promptstudio/jobs.py`), never an `is_running()` check on another manager — polling then starting is a race. Declare the resource, acquire, release in a `finally`.
12. **Measure before optimising, and report the number.** Two "obvious" wins in this codebase turned out to be losses under measurement (FTS5 search, incremental rebuild) — both are recorded in `docs/review_backend_architecture.md`.

## Where to look

| Task | File |
|------|------|
| Any API change | `promptstudio/server/handler.py` |
| Vision / prompts | `promptstudio/prompts/engine.py` |
| Gallery index | `promptstudio/storage/db.py` |
| Background job contention | `promptstudio/jobs.py` |
| Why a job did that | `<archive>/_journal/`, `GET /api/journal` |
| Duplicate detection | `promptstudio/storage/dedupe.py` |
| Instagram sync | `promptstudio/scraping/downloader.py` |
| ComfyUI | `promptstudio/comfy/client.py` |
| Frontend | `app.js` |

## Docs (token budget)

| Load | Skip unless needed |
|------|--------------------|
| [docs/context.md](docs/context.md) | `docs/following_list.md`, `docs/following_classify_report.md` (data dumps) |
| [docs/api.md](docs/api.md) for schemas | Full `app.js` / `style.css` unless UI task |
| [docs/instagram_downloader.md](docs/instagram_downloader.md) for sync | Entire `scripts/` tree for non-scrape work |
| [docs/review_backend_architecture.md](docs/review_backend_architecture.md) for backend decisions + measurements | — |
