# Troubleshooting

Agent map: [context.md](context.md).

## Environment

| Item | Value |
|------|--------|
| OS | Windows 10/11 |
| Python | 3.10+ (tested 3.14) |
| Deps | `instaloader`, `opencv-python-headless` (`requirements.txt`); Pillow optional for thumbs |
| Ollama | `http://localhost:11434` · default model **`qwen2.5vl:7b`** |
| ComfyUI | optional `http://127.0.0.1:8188` |
| Archive | `~/Pictures/InstagramSaved` |

```powershell
# Ollama up?
py -c "import urllib.request; print(urllib.request.urlopen('http://localhost:11434/api/tags').read().decode()[:300])"

# Pull model if missing
ollama pull qwen2.5vl:7b

# App
py server.py
```

Override model: `$env:OLLAMA_VISION_MODEL="moondream"` (or any installed vision model).

---

## Common issues

### UnicodeEncodeError (PowerShell)

```powershell
$env:PYTHONUTF8="1"
```

Avoid emoji in new `print()` paths when possible.

### Port 5000 in use

`ThreadingHTTPServer.allow_reuse_address = True`. Kill stale process:

```powershell
Get-NetTCPConnection -LocalPort 5000 -ErrorAction SilentlyContinue | Select-Object OwningProcess
# or
Get-Process -Name python* | Stop-Process -Force
```

### `ModuleNotFoundError: cgi`

Do **not** import `cgi`. Uploads use `promptstudio.server.multipart.parse_multipart_data`.

### Ollama down / wrong model

- Badge + `GET /api/health` show `ollama: false` or `model_ready: false`.
- Ensure model name matches `OLLAMA_VISION_MODEL` (or tag prefix).
- Batch analyze and single-prompt both require Ollama.

### Stale or wrong prompts

Cache: `~/Pictures/InstagramSaved/prompts_cache.json`.

```powershell
# Nuclear: wipe all cached prompts (user must confirm)
Remove-Item "$HOME\Pictures\InstagramSaved\prompts_cache.json" -Force
```

Or per-photo: `GET /api/prompt?path=…&refresh=true` / UI re-analyze.  
Stale flags when `vision_engine` or `pipeline_version` ≠ current (`v2-structured`).

### Gallery missing new files

```powershell
$env:PROMPTSTUDIO_REBUILD_INDEX="1"
py server.py
```

Index is `archive.db` next to media.

### Instagram rate-limit / abort

- Stop for the day; queue persists in `following_queue.json`.
- Keep app/browser IG closed during sync.
- Reuse Instaloader session; do not re-login each run.
- See [instagram_downloader.md](instagram_downloader.md).

### Comfy generate fails

- ComfyUI running; `GET /api/health` → `comfy: true`.
- Checkpoint `COMFYUI_CHECKPOINT` must exist in Comfy models.
- Pro workflow JSON: `promptstudio/comfy/workflows/modelToimage_pro.api.json`.

### Thumbs broken

OpenCV headless is enough for resize; if both Pillow and cv2 fail, `/media/thumb` falls back to full image.
