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

## First look when something misbehaves

Since the observability sweep there are three places to check before reading code:

| Question | Where |
|----------|-------|
| What went wrong, with a stack trace | `<archive>/promptstudio.log` (rotating, 5 MB × 3) |
| Why did last night's job stop / drift | `<archive>/_journal/<kind>.jsonl` or `GET /api/journal?kind=sync` |
| Why does a job say "busy" when nothing is running | `GET /api/health` → `leases` |

```powershell
# Tail the log
Get-Content $env:USERPROFILE\Pictures\InstagramSaved\promptstudio.log -Tail 50 -Wait

# Last sync run: outcome, failures, counts
py -c "import json,urllib.request as u; print(json.dumps(json.load(u.urlopen('http://localhost:5000/api/journal?kind=sync&limit=1'))['runs'][0], indent=2))"

# Who holds the Ollama / Instagram lease
py -c "import json,urllib.request as u; print(json.load(u.urlopen('http://localhost:5000/api/health'))['leases'])"
```

A route that raises now returns a JSON **500** and logs the traceback with the
route — it no longer drops the connection, so "the app went offline" in the
browser genuinely means the server is down, not that a handler threw.

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

Instaloader’s first call is `Profile.from_username()` →
`/api/v1/users/web_profile_info/`. That endpoint 429s even on fresh sessions
(instaloader#2726). 429s are a rolling window, not a calendar-day reset.

- Pause the Instagram lane. Do **not** Resume while `IG_BACKEND` is still
  Instaloader — that retries `web_profile_info`.
- Switch: `IG_BACKEND=gallery-dl` plus `SCRAPE_COOKIES_FROM_BROWSER=brave`
  (or `IG_COOKIES_FILE`). Restart the server. gallery-dl is pinned to
  `user-strategy=search,web` and never calls `web_profile_info`.
- Close Brave/Chrome while scraping if cookie read fails (Chromium locks the
  Cookies SQLite). If `brave` fails: `chrome`, then a profile path, then
  cookies.txt.
- Keep the logged-in Instagram tab closed during a scrape — same rate budget.
- gallery-dl is the most reliable option in practice, not a guarantee.
  Auth/challenge/zero-download 429s still pause the Instagram lane.
- See [instagram_downloader.md](instagram_downloader.md).

### Comfy generate fails

- ComfyUI running; `GET /api/health` → `comfy: true`.
- Checkpoint `COMFYUI_CHECKPOINT` must exist in Comfy models.
- Pro workflow: `promptstudio/comfy/workflows/pro/` — `graph.json` (ComfyUI API export)
  plus `slots.json` (where the prompt, seed and parameters are injected).
- `GET /api/workflows` lists what the picker offers. A workflow missing from it failed
  validation — `<archive>/promptstudio.log` has the line, naming the slot or node id.
- To use your own graph: export it from ComfyUI with **Export (API)**, drop it and a
  `slots.json` into `<archive>/_workflows/<name>/`. A user entry shadows a built-in of
  the same name, so `<archive>/_workflows/pro/` overrides the shipped Pro graph.

### Thumbs broken

OpenCV headless is enough for resize; if both Pillow and cv2 fail, `/media/thumb` falls back to full image.

### Creator count keeps growing with folders you never followed

Expected, not a corruption bug. Three separate causes:

1. **Saved-post sync.** `sync_saved_posts` names the folder from `post.owner_username`
   (`scraping/downloader.py`) — the account that *made* the saved post, which is usually
   not one you follow. Real handles, unexpected folders.
2. **Test payloads.** `POST /api/creator/create` calls from UI testing create real folders
   (`test_nonexistent_xyz_ui_check2` and similar). Delete the folder and drop the entry
   from `creator_scrape_queue.json`.
3. **Underscore state dirs.** `_trash`, `_thumbs`, `_journal`, `_classify`, `_generations`
   are app state, not creators. They are in `EXCLUDED_FOLDERS` and never counted — you only
   see them when browsing the archive directly.

Only cause 1 is unresolved by design. Fixing it means either routing saved posts into a
generic `_saved/` folder, or only creating a creator folder when the owner is already a
known target in `following_list.json`. Neither is implemented.
