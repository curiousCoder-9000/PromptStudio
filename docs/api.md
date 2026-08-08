# PromptStudio API Reference Specification

Base URL: `http://localhost:5000`

---

## 1. Endpoints Overview

| Method | Endpoint | Description |
| :--- | :--- | :--- |
| `GET` | `/api/stats` | Photos, creators, `prompts_ready` |
| `GET` | `/api/health` | Ollama reachability + installed models |
| `GET` | `/api/creators` | Creator folders with counts + cover |
| `GET` | `/api/creator/style` | Learned style prefix for a creator |
| `POST` | `/api/creator/style/rebuild` | Rebuild style from cached prompts |
| `GET` | `/api/following` | Accounts from local `following_list.json` |
| `GET` | `/api/photos` | Paginated photos (`offset`, `limit`, `creator`, `search`, `unanalyzed`, `favorite`, `sort`) |
| `GET` | `/api/prompt` | Vision prompt bundle (`path`, optional `refresh`) — includes `history` |
| `PUT` | `/api/prompt` | Save edited positive/negative prompts + tags |
| `POST` | `/api/prompt/restore` | Restore a prior prompt from history |
| `PUT` | `/api/favorite` | Toggle or set favorite flag |
| `POST` | `/api/prompt/batch` | Background batch analyze (`creator`, `force`, `limit`, `paths`) |
| `GET` | `/api/prompt/batch/status` | Batch job progress |
| `DELETE` | `/api/photo` | Delete photo + cache + thumb |
| `POST` | `/api/creator/create` | Create creator folder |
| `POST` | `/api/photo/upload` | Multipart upload |
| `POST` | `/api/sync/saved` | Sync Instagram saved posts |
| `POST` | `/api/sync/creator` | Sync one creator feed |
| `POST` | `/api/sync/following` | Bulk sync from following list |
| `POST` | `/api/comfy/generate` | Queue ComfyUI Pro (ref) or txt2img |
| `GET` | `/api/comfy/status` | ComfyUI job progress |
| `GET` | `/api/generations` | Saved generations for a source photo |
| `GET` | `/media/<path>` | Full-resolution image |
| `GET` | `/media/thumb/<path>` | Generated JPEG thumbnail |

---

## 2. Detailed specs

### `GET /api/stats`

```json
{ "total_photos": 1134, "total_creators": 147, "prompts_ready": 420 }
```

`GET /api/creators` includes `last_synced_at` and `synced_count` from `sync_state.json` when available.

### `GET /api/health`

Probes Ollama at `http://localhost:11434/api/tags` (1.5s timeout).

```json
{
  "ollama": true,
  "model": "qwen2.5vl:7b",
  "model_ready": true,
  "models": ["qwen2.5vl:7b", "moondream:latest"]
}
```

When Ollama is down: `{ "ollama": false, ... }`. Also includes `comfy` / `url` for ComfyUI reachability.

### `POST /api/prompt/mode-e`

Rewrite prompts for Comfy **Mode E** (outfit/scene only; identity from reference image).

```json
{
  "path": "creator/file.jpg",
  "positive_prompt": "optional override",
  "negative_prompt": "optional override",
  "apply": false
}
```

Returns `positive_prompt`, `negative_prompt`, `anti_terms`, `source` (`structured` | `stripped` | `fallback`). When `apply: true`, saves into the prompt cache.

Pro generate (`POST /api/comfy/generate`) defaults `use_mode_e: true` and also accepts `denoise`, `steps`, `cfg_scale`, `seed`.


Requires ComfyUI at `COMFYUI_URL` (default `http://127.0.0.1:8188`).

```json
{
  "path": "creator/file.jpg",
  "workflow": "pro",
  "variant": "pro",
  "positive_prompt": "optional override",
  "negative_prompt": "optional override",
  "denoise": 0.70,
  "steps": 32,
  "cfg_scale": 6.0,
  "seed": null,
  "checkpoint": null
}
```

- **`workflow: "pro"`** (default) — uploads the archive photo to ComfyUI and runs `modelToimage_pro` (IPAdapter face+body, OpenPose, img2img denoise, FaceDetailer) using `promptstudio/comfy/workflows/modelToimage_pro.api.json`. Checkpoint defaults to `juggernautXL_ragnarok.safetensors`.
- **`workflow: "txt2img"`** — legacy bare CheckpointLoader → EmptyLatent graph (used when `variant` is `sdxl` / `flux` / `pony`).

Outputs are saved under `_generations/<creator>/` and indexed in `generations_index.json`.

### `GET /api/comfy/status`

```json
{ "running": true, "progress": "Generating…", "source_path": "…", "result": null }
```

### `GET /api/generations?path=creator/file.jpg`

```json
{ "path": "…", "generations": [{ "primary_url": "/media/_generations/…", "files": [] }] }
```

### `GET /api/following`

Query: `search` (username / full_name / bio), `limit` (default 100, max 500).

Reads local `following_list.json` (no live Instagram call).

```json
{
  "accounts": [
    {
      "username": "gretadelmonte",
      "full_name": "Greta Delmonte",
      "biography": "…",
      "is_private": false,
      "media_count": 50,
      "followers_count": 609879
    }
  ],
  "total": 120
}
```

### `GET /api/photos`

Query: `creator`, `search`, `unanalyzed` (`1`/`true`), `favorite` (`1`/`true`), `sort` (`name` | `newest` | `oldest`), `offset` (default 0), `limit` (default/max from config, typically 300).

```json
{
  "photos": [
    {
      "filename": "roxeuoon_2021-09-14_13-33-42_UTC.jpg",
      "creator": "roxeuoon",
      "rel_path": "roxeuoon/roxeuoon_2021-09-14_13-33-42_UTC.jpg",
      "url": "/media/roxeuoon/...",
      "thumb_url": "/media/thumb/roxeuoon/...",
      "has_prompt": true,
      "prompt_stale": false,
      "favorite": true
    }
  ],
  "total": 1134,
  "offset": 0,
  "limit": 60,
  "has_more": true,
  "sort": "newest"
}
```

- `search` matches creator, filename, and cached prompt text/tags.
- `unanalyzed=1` returns photos that need analysis (no cache entry or wrong `vision_engine`), same rule as batch.
- `favorite=1` returns only favorited photos (`favorites.json`).
- `sort=newest|oldest` uses `taken_at` from `*.meta.json`, then filename UTC stamp, then mtime.
- `has_prompt`: cache hit with current `vision_engine`.
- `prompt_stale`: entry exists but engine or `pipeline_version` is outdated.

### `PUT /api/favorite`

```json
{ "path": "creator/file.jpg" }
```

Toggles favorite. Or set explicitly with `"favorite": true|false`.

### `POST /api/prompt/restore`

```json
{ "path": "creator/file.jpg", "index": 0 }
```

Restores history entry `index` (0 = most recent prior version). Max 3 history snapshots kept on regenerate/save.

### `GET /api/creator/style`

Query: `creator=handle`

```json
{
  "creator": "roxeuoon",
  "style_prefix": "roxeuoon look, soft light, …",
  "top_terms": ["soft", "light"],
  "sample_count": 12,
  "exists": true
}
```

### `POST /api/creator/style/rebuild`

```json
{ "creator": "roxeuoon" }
```

Returns `{ "status": "ok", "style": {…} }` or `insufficient_data` when too few cached prompts.

### `GET /api/prompt`

Query: `path=creator/file.jpg`, `refresh=true` to force regenerate.

Response includes `positive_prompt`, `negative_prompt`, `parameters` (`vision_engine`, `pipeline_version`), `visual_tags`, `structured_vision`, and `exports` (`flux`, `sdxl`, `pony`, `negative`).

### `PUT /api/prompt`

Save manual edits without re-running vision.

```json
{
  "path": "roxeuoon/file.jpg",
  "positive_prompt": "...",
  "negative_prompt": "...",
  "visual_tags": ["optional"]
}
```

Updates cache, sets `parameters.manual_edit: true`, rebuilds `exports`, returns the full prompt bundle.

### `POST /api/sync/following`

```json
{
  "max_accounts": 20,
  "accounts_per_day": 20,
  "max_posts": 20,
  "min_media_count": 5,
  "keywords": "model,lingerie"
}
```

`max_accounts` and `accounts_per_day` are aliases (default **20**). The run also respects the persistent daily budget in `following_queue.json`.

`keywords` may be a comma string or JSON array. Omit / empty uses config defaults. CLI `--keywords ""` disables the keyword filter (API must send an explicit empty list `[]` to disable — omit still means defaults).

Response / status `result` may include:

```json
{
  "downloaded": 15,
  "skipped": 40,
  "errors": 1,
  "rate_limit_hits": 2,
  "aborted": false,
  "abort_reason": "",
  "accounts_processed": 5,
  "queue_summary": {
    "pending": 80,
    "done": 5,
    "accounts_today": 5,
    "remaining_today": 15,
    "daily_cap": 20
  }
}
```

### `GET /api/sync/status`

```json
{
  "running": false,
  "job_type": "following",
  "progress": "Complete",
  "rate_limit_hits": 2,
  "consecutive_rate_limits": 0,
  "last_backoff_sec": 120,
  "result": { "downloaded": 15, "skipped": 40, "errors": 1, "rate_limit_hits": 2, "aborted": false }
}
```
### `POST /api/prompt/batch`

```json
{ "creator": "roxeuoon", "force": false, "limit": 50 }
```

Or re-analyze specific photos:

```json
{ "paths": ["creator/a.jpg", "creator/b.jpg"], "force": true }
```

### `GET /media/thumb/<creator>/<file>`

Creates/caches a JPEG under `~/Pictures/InstagramSaved/_thumbs/` (Pillow preferred, OpenCV fallback).
