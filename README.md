# PromptStudio

Local **AI Vision Prompt Studio** for personal photo archives (especially Instagram creators).

Analyzes images under a local archive folder with **Ollama** multimodal vision, builds photorealistic prompts for Stable Diffusion / Flux / Midjourney / ComfyUI, and manages scrape → gallery → generate in one dark glass UI.

> **Privacy first.** This project is designed so **secrets, sessions, personal following lists, and media never belong in git**. Configure everything via `.env`.

## Features

- Ollama two-stage vision (`v2-structured`): structured JSON → rewrite → Flux/SDXL/Pony exports
- Glassmorphic gallery: search, favorites, sort, media type, infinite scroll, thumbs
- Lightbox: edit prompts, history restore, Mode E, optional ComfyUI generate
- Instagram sync: saved posts, creator feed, following bulk (anti-ban pacing + resume)
- X / Twitter and Reddit scraping via gallery-dl, into the same archive and gallery
- Safe delete: soft delete to `_trash/` with one-click **Undo** + Trash restore/purge
- Upload, creator folders, batch analyze, reel metadata panel

## Quickstart

### 1. Prerequisites

- Python **3.10+** (developed on 3.14 Windows)
- [Ollama](https://ollama.com) with a vision model:

```powershell
ollama pull qwen2.5vl:7b
```

### 2. Configure environment

```powershell
copy .env.example .env
# Edit .env — at minimum set:
#   PROMPTSTUDIO_ARCHIVE=C:\Users\YOU\Pictures\InstagramSaved
#   INSTAGRAM_SESSION_USER=your_instagram_username   # only if using scrape
```

### 3. Install & run

```powershell
pip install -r requirements.txt
py server.py
```

Open **http://localhost:5000**

Optional: ComfyUI at `http://127.0.0.1:8188` for the generate loop.

### Instagram login (optional scrape)

```powershell
pip install instaloader
instaloader --login YOUR_USERNAME
```

Set `INSTAGRAM_SESSION_USER` and (if needed) `INSTALOADER_SESSION_DIR` in `.env` to match where Instaloader stored `session-YOUR_USERNAME`.

## Configuration

| Variable | Purpose | Default |
|----------|---------|---------|
| `PROMPTSTUDIO_ARCHIVE` | Local media root | `~/Pictures/InstagramSaved` |
| `INSTAGRAM_SESSION_USER` | IG username for Instaloader | _(empty — required for scrape)_ |
| `OLLAMA_VISION_MODEL` | Vision model | `qwen2.5vl:7b` |
| `PROMPT_INTENSITY` | Prompt tone: `low` / `balanced` / `high` | `balanced` |
| `IG_CAPTION_KEYWORDS` | Caption rank keywords | fashion/model set |
| `PROMPTSTUDIO_TRASH` | Soft delete to `_trash/` (`0` = unlink now) | `1` |
| `PROMPTSTUDIO_TRASH_DAYS` | Trash retention for "Purge expired" | `30` |
| `COMFYUI_URL` | Optional ComfyUI | `http://127.0.0.1:8188` |
| `X_COOKIES_FILE` | cookies.txt for X scraping | _(empty — required for X)_ |
| `REDDIT_COOKIES_FILE` | cookies.txt for Reddit (optional) | _(empty)_ |
| `SCRAPE_FOLDER_SUFFIX` | Suffix non-IG folders (`nina__x`) | `1` |

Full list: **[`.env.example`](.env.example)**

`PROMPT_INTENSITY=high` enables more sensual rewrites for private use. The public default is balanced photorealistic fashion/portrait language.

## What must stay private (gitignored)

Do **not** commit:

- `.env` — credentials and personal paths
- Instaloader `session-*` files
- **Any `cookies.txt`** used for X / Reddit — these are session credentials
- `following_list.json`
- Your media archive (`PROMPTSTUDIO_ARCHIVE`)
- Generated docs dumps (`docs/following_list.md`, etc.)

Export helpers write local files only:

```powershell
py scripts/export_following_list.py
```

Use `following_list.example.json` as a shape reference.

## Development

```powershell
pip install -r requirements.txt -r requirements-dev.txt
pytest                    # unit tests (fast, no server needed)
ruff check .              # lint
./tests/ui/run.sh         # browser suites (needs Node 22+ and Chrome)
```

`pytest` runs against a throwaway archive in a temp directory — it never touches
`PROMPTSTUDIO_ARCHIVE`. The browser suites boot their own server on `:5099` and
tear it down afterwards; see [tests/ui/README.md](tests/ui/README.md).

CI runs lint + tests on Python 3.10 and 3.13, plus the UI suites
([.github/workflows/ci.yml](.github/workflows/ci.yml)).

## Docs

| Doc | Purpose |
|-----|---------|
| **[docs/context.md](docs/context.md)** | Agent/dev map — package, data, API |
| [docs/api.md](docs/api.md) | HTTP contracts |
| [docs/architecture.md](docs/architecture.md) | Components & flows |
| [docs/instagram_downloader.md](docs/instagram_downloader.md) | Sync & anti-ban |
| [docs/multi_source_scraping.md](docs/multi_source_scraping.md) | X / Reddit scraping via gallery-dl |
| [docs/troubleshooting.md](docs/troubleshooting.md) | Setup & failures |
| [docs/roadmap.md](docs/roadmap.md) | Phases done / optional next |
| [AGENTS.md](AGENTS.md) | Hard rules for AI agents |
| [scripts/README.md](scripts/README.md) | CLI wrappers |

## Layout

```
server.py                 # entry
.env.example              # public config template
promptstudio/             # application logic
  config.py               # loads .env + env vars
  server/ storage/ prompts/ scraping/ comfy/
scripts/                  # thin CLIs
index.html style.css app.js
docs/
```

## Security notes for public forks

1. **Never** put Instagram passwords, session cookies, or API tokens in the repo.
2. If a GitHub PAT or session ever lands in git remote URLs or history, **revoke it immediately** and rewrite history.
3. Media and following lists are personal data — keep them only under `PROMPTSTUDIO_ARCHIVE` and gitignored JSON files.
4. Respect Instagram’s terms of service and local laws when scraping; this tool is for personal archives.

## License

Use and modify for personal projects. You are responsible for how you use Instagram credentials, scraped content, and generated prompts.
