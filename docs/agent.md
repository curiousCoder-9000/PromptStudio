# Agent ops checklist

Primary map: **[context.md](context.md)**. Workspace rules: **[AGENTS.md](../AGENTS.md)**.

## Before coding

1. Confirm task surface from [context.md](context.md) task→file table — do not load whole repo.
2. Config/defaults: `promptstudio/config.py` only.
3. Routes: `promptstudio/server/handler.py` is the sole HTTP switchboard.

## Runtime checks

```powershell
# Ollama
py -c "import urllib.request; print(urllib.request.urlopen('http://localhost:11434/api/tags', timeout=2).read()[:200])"
# App
py server.py
# Vision smoke
py prompt_engine.py
```

Health: `GET /api/health` → `ollama`, `model` (`qwen2.5vl:7b` default), `model_ready`, `comfy`.

## Safety

- Deletions: user modal only → `DELETE /api/photo`.
- IG sync: multi-day pacing; stop on abort; never password-login every run.
- No archive bulk-delete scripts without explicit user ask.

## Stale doc traps (fixed in context)

| Wrong (old docs) | Current |
|------------------|---------|
| Vision model `moondream` | `qwen2.5vl:7b` (`OLLAMA_VISION_MODEL`) |
| Multipart in `server.py` via `cgi` | `promptstudio.server.multipart` |
| Monolithic `server.py` | Thin shim → `handler.py` |
| `opencv-python` only | `opencv-python-headless` (+ optional Pillow) |

## Skip loading

- `docs/following_list.md`, `docs/following_classify_report.md` — generated tables, not design docs.
- Full `style.css` / `app.js` unless UI task.
