# Agent Context & Workspace Guidelines

This document provides operational context, guidelines, and API specifications for AI coding agents working on the **PromptStudio** repository.

---

## 1. Project Overview & Objective

PromptStudio is a Python + HTML5/CSS3/JS web application that analyzes local creator photo archives (`~\Pictures\InstagramSaved`) and uses **Ollama Multimodal AI Vision (`moondream`)** to generate highly specific, photorealistic prompts for Stable Diffusion, Flux.1, Midjourney, and ComfyUI.

---

## 2. Core Architecture & Stack

- **Backend:** Python 3 (`server.py`) running on `http://localhost:5000`.
- **Vision Model Engine:** Ollama (`http://localhost:11434/api/generate`) with `moondream` model (`prompt_engine.py`).
- **Frontend:** Vanilla HTML5, Vanilla CSS3 (Glassmorphic dark mode theme), Vanilla JS (`index.html`, `style.css`, `app.js`).
- **Storage Location:** `~\Pictures\InstagramSaved` (organized by creator subfolders).
- **Prompt Cache:** `~\Pictures\InstagramSaved\prompts_cache.json`.

---

## 3. Key Operational Rules for AI Agents

1. **Obey User Requirements Strictly:**
   - Preserve creator folder organization (`~\Pictures\InstagramSaved\<creator_handle>`).
   - Do NOT resurrect legacy non-person filtering; user explicitly deleted the non-person folder.
   - Keep photo deletion behind the custom Confirmation Modal dialog (`#deleteConfirmModal`).

2. **Ollama Integration Safety:**
   - Ollama service runs on `http://localhost:11434`.
   - The primary vision model is `moondream` (or `moondream:latest`).
   - Images are payload-encoded as Base64 strings when querying Ollama.
   - Prompts MUST be cached in `prompts_cache.json` to prevent re-querying Ollama on every page render.

3. **Backend Server (`server.py`):**
   - Use standard Python library modules (`http.server`, `urllib.parse`, `json`, `os`, `re`).
   - Avoid deprecated modules like `cgi` (Python 3.14 compatible).
   - Server must allow cross-origin requests (`Access-Control-Allow-Origin: *`) and handle `DELETE` and `POST` methods cleanly.

4. **Frontend Aesthetics & UX:**
   - Maintain the dark glassmorphic design system in `style.css`.
   - Ensure all 1-click copy buttons (`Copy Prompt`, `Copy Negative`, `Copy Full Bundle`) trigger toast notifications (`showToast()`).
   - Lightbox modal MUST support keyboard navigation (`Left`/`Right` arrow keys, `Escape` key).

---

## 4. API Reference Summary

| Method | Endpoint | Description | Query / Body Params |
| :--- | :--- | :--- | :--- |
| `GET` | `/api/stats` | Returns aggregate photo & creator counts | None |
| `GET` | `/api/creators` | Returns list of creator folders with photo counts | None |
| `GET` | `/api/photos` | Query photos in gallery | `creator`, `search` |
| `GET` | `/api/prompt` | Fetch Ollama Vision prompt for photo | `path=creator/filename.jpg` |
| `DELETE` | `/api/photo` | Permanently deletes photo file from disk | `path=creator/filename.jpg` |
| `POST` | `/api/creator/create` | Creates a new creator subfolder | JSON: `{"name": "creator_name"}` |
| `POST` | `/api/photo/upload` | Uploads an image to creator subfolder | Multipart form: `creator`, `file` |

---

## 5. Development Commands

- **Start API Web Server:**
  ```powershell
  py server.py
  ```
- **Test Ollama Vision Engine directly:**
  ```powershell
  py prompt_engine.py
  ```
- **Check Ollama Server & Models:**
  ```powershell
  & "~\AppData\Local\Programs\Ollama\ollama.exe" list
  ```
