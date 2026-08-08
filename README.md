# PromptStudio

Local **AI Vision Prompt Studio** for Instagram creator photo archives.

Analyzes images under `~/Pictures/InstagramSaved` with **Ollama** (default `qwen2.5vl:7b`), builds photorealistic prompts for Stable Diffusion / Flux / Midjourney / ComfyUI, and manages scrape → gallery → generate in one dark glass UI.

## Features

- Ollama two-stage vision (`v2-structured`): structured JSON → erotic rewrite → Flux/SDXL/Pony exports
- Glassmorphic gallery: search prompts/tags, favorites, sort, media type, infinite scroll, thumbs
- Lightbox: edit prompts, history restore, Mode E, ComfyUI pro generate (side-by-side)
- Instagram sync: saved posts, creator feed, following bulk (anti-ban pacing + resume)
- Safe delete, upload, creator folders, batch analyze

## Quickstart

**Prereqs:** Python 3.10+ (dev on 3.14 Windows), [Ollama](https://ollama.com) with vision model:

```powershell
ollama pull qwen2.5vl:7b
pip install -r requirements.txt
py server.py
```

Open **http://localhost:5000**

Optional: ComfyUI at `http://127.0.0.1:8188` for generate loop.

## Docs (start here)

| Doc | Purpose |
|-----|---------|
| **[docs/context.md](docs/context.md)** | **Agent/dev map** — package, data, API, task→file (read first) |
| [docs/api.md](docs/api.md) | HTTP contracts |
| [docs/architecture.md](docs/architecture.md) | Components & flows |
| [docs/instagram_downloader.md](docs/instagram_downloader.md) | Sync & anti-ban |
| [docs/troubleshooting.md](docs/troubleshooting.md) | Setup & failures |
| [docs/roadmap.md](docs/roadmap.md) | Phases done / optional next |
| [AGENTS.md](AGENTS.md) | Hard rules for AI agents |
| [scripts/README.md](scripts/README.md) | CLI wrappers |

## Layout

```
server.py                 # entry
promptstudio/             # all application logic
  config.py server/ storage/ prompts/ scraping/ comfy/
scripts/                  # thin CLIs
index.html style.css app.js
docs/
```
