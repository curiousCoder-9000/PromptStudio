# AGENTS.md - PromptStudio Guidelines

This file specifies project rules, coding standards, and operational guidelines for AI agents working in this workspace.

## Project Scope & Stack

- **Name:** PromptStudio (AI Vision Image Prompt Extractor & Creator Studio)
- **Backend:** Python HTTP Server (`server.py`) running on `http://localhost:5000`
- **Vision Engine:** Ollama Multimodal Vision (`moondream`) on `http://localhost:11434` (`prompt_engine.py`)
- **Frontend:** Vanilla HTML5 / Vanilla CSS3 (Glassmorphism dark theme) / Vanilla JS (`index.html`, `style.css`, `app.js`)
- **Local Storage Archive:** `~\Pictures\InstagramSaved`

## Guidelines for AI Agents

1. **Preserve User Archive Data:**
   - Do NOT perform unconfirmed file deletions.
   - All photo deletions MUST go through the `DELETE /api/photo` endpoint after user confirms in `#deleteConfirmModal`.

2. **Ollama Vision Engine:**
   - Always verify Ollama server status at `http://localhost:11434/api/tags`.
   - Use `moondream` model for Base64 image captioning and photorealistic prompt generation.
   - Cache results in `~/Pictures/InstagramSaved/prompts_cache.json` (in-memory write-through; gallery uses `archive.db` SQLite catalog).

3. **Python Version Compatibility:**
   - Write Python code compatible with Python 3.14+ on Windows.
   - Do NOT import deprecated standard library modules such as `cgi`.

4. **Design System & Aesthetics:**
   - Maintain modern dark mode glassmorphism UI with HSL tailored colors (`#8b5cf6`, `#ec4899`, `#06b6d4`).
   - Use Google Fonts (`Outfit`, `Inter`, `Fira Code`).
   - Keep line lengths reasonable and maintain full keyboard accessibility in Lightbox modals (`Left`/`Right` arrow keys, `Esc` key).

## Reference Documentation

- [docs/architecture.md](file:///./docs/architecture.md) - Full Architecture & API Specification
- [docs/agent.md](file:///./docs/agent.md) - Agent Operational Context & Guidelines
- [docs/instagram_downloader.md](file:///./docs/instagram_downloader.md) - Instagram Downloader & Sync Guide
