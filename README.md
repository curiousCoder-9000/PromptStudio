# 🪄 PromptStudio - AI Image Prompt Extractor & Creator Studio

**PromptStudio** is a dark-mode web application and **AI Vision Reverse-Engineering Engine** that analyzes local creator photo archives (`~/Pictures/InstagramSaved`) and uses **Ollama Multimodal AI Vision (`moondream`)** to generate highly specific, photorealistic prompts for Stable Diffusion, Flux.1, Midjourney, and ComfyUI.

---

## ✨ Key Features

- **🤖 Real Ollama AI Vision (`moondream`):** Inspects image pixels directly via Base64 payload to produce unique, authentic prompts for every photo.
- **💎 Dark Glassmorphic Web UI:** Built with HSL tailored colors (`#8b5cf6`, `#ec4899`, `#06b6d4`), Google Fonts (`Outfit`, `Inter`, `Fira Code`), and responsive masonry photo grids.
- **🔍 Interactive AI Prompt Inspector Lightbox:** Click any photo to inspect its positive prompt, negative prompt, visual hashtags (`#full-body`, `#bikini`, `#sunset`), and diffusion parameters (Sampler, Steps, CFG Scale, Aspect Ratio).
- **📋 1-Click Copy Buttons:** Copy Positive Prompt, Negative Prompt, or the Full Generation Bundle with instant toast notifications.
- **🗑️ Photo Deletion with Confirmation Modal:** Click the dustbin icon to safely delete unwanted photos from disk storage after confirming via modal.
- **📁 Creator Folder Creation & Image Uploads:** Create new creator subfolders and upload images (`.jpg`, `.png`, `.webp`) directly to your archive.
- **🔎 Prompt Descriptor Search:** Search your entire 1,135+ photo archive by prompt keywords (e.g. search `bikini`, `sunset`, `35mm`, `poolside`).

---

## 🛠️ Quickstart Guide

### 1. Prerequisites
- Python 3.10+ installed
- [Ollama](https://ollama.com) installed with `moondream` model:
  ```powershell
  ollama pull moondream
  ```

### 2. Launch the Application
Open PowerShell or Command Prompt in the repository folder:

```powershell
cd .
py server.py
```

### 3. Open Web UI
Navigate your browser to:
👉 **`http://localhost:5000`**

---

## 📚 Comprehensive Documentation Suite

- **[System Architecture](file:///./docs/architecture.md):** Architectural design, component specs, and data flow diagrams.
- **[API Reference Specification](file:///./docs/api.md):** Complete HTTP endpoint schemas, request/response JSON, and query parameters.
- **[Troubleshooting & Environment Setup](file:///./docs/troubleshooting.md):** Setup guide, Ollama service management, port conflicts, and cache maintenance.
- **[Agent Guidelines](file:///./docs/agent.md):** Operational guidelines and context for AI coding agents.
- **[Workspace Rules](file:///./AGENTS.md):** Root AGENTS rules file.
