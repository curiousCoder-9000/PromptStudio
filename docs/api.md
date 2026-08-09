# PromptStudio API Reference

Base: `http://localhost:5000`  
Agent map: [context.md](context.md). Routes implemented in `promptstudio/server/handler.py`.

---

## 1. Endpoints Overview

| Method | Endpoint | Description |
| :--- | :--- | :--- |
| `GET` | `/api/stats` | Photos, creators, `prompts_ready` |
| `GET` | `/api/health` | Ollama + Comfy reachability + models + job leases |
| `GET` | `/api/journal` | Run history for a background job kind |
| `GET` | `/api/creators` | Creator folders with counts + cover + sync meta |
| `GET` | `/api/creator/style` | Learned style prefix for a creator |
| `POST` | `/api/creator/style/rebuild` | Rebuild style from cached prompts |
| `GET` | `/api/following` | Accounts from local `following_list.json` |
| `GET` | `/api/photos` | Paginated (`offset`, `limit`, `creator`, `search`, `unanalyzed`, `favorite`, `sexy`, `glam_min`, `media_type`, `sort`) |
| `GET` | `/api/prompt` | Vision prompt bundle (`path`, optional `refresh`) — includes `history` |
| `PUT` | `/api/prompt` | Save edited positive/negative prompts + tags |
| `POST` | `/api/prompt/restore` | Restore a prior prompt from history |
| `POST` | `/api/prompt/mode-e` | Mode E rewrite (outfit/scene; optional `apply`) |
| `PUT` | `/api/favorite` | Toggle or set favorite flag |
| `POST` | `/api/prompt/batch` | Background batch analyze (`creator`, `force`, `limit`, `paths`) |
| `GET` | `/api/prompt/batch/status` | Batch job progress (cheap — snapshot, no archive scan) |
| `POST` | `/api/prompt/batch/cancel` | Cooperative cancel after the current photo |
| `POST` | `/api/classify/start` | Background glam classify for one creator (`creator`, `only_unscored`, `force`, `include_videos`, `limit`, `rescore_stale`) |
| `GET` | `/api/classify/status` | Classify job progress (`kept` / `rejected` / `failed`, `pending`, `stale`, `score_hist`) |
| `POST` | `/api/classify/cancel` | Cooperative cancel after current item |
| `DELETE` | `/api/photo` | Soft delete → `_trash/` (`permanent=1` to unlink) |
| `GET` | `/api/trash` | List trashed entries (`limit`, `offset`) + size/retention |
| `POST` | `/api/trash/restore` | Restore by `id` or `ids` |
| `POST` | `/api/trash/purge` | Permanent remove: `id` / `ids` / `all` / `expired` (+`days`) |
| `POST` | `/api/creator/create` | Create creator folder |
| `POST` | `/api/photo/upload` | Multipart upload |
| `POST` | `/api/sync/saved` | Sync Instagram saved posts |
| `POST` | `/api/sync/creator` | Sync one creator feed (`mode`, `deep` optional) |
| `POST` | `/api/sync/following` | Bulk sync from following list |
| `POST` | `/api/sync/cancel` | Cooperative cancel of running IG job |
| `GET` | `/api/sync/status` | Sync job progress / abort / `creator_queue` summary |
| `POST` | `/api/scrape/enqueue` | Enqueue serial full/bounded scrape (Instagram / X / Reddit) |
| `GET` | `/api/sources` | Available scrape sources |
| `GET` | `/api/scrape/status` | Creator scrape queue + embedded sync status |
| `POST` | `/api/scrape/cancel` | Cancel pending job or all pending (`scope`) |
| `POST` | `/api/scrape/pause` | Pause queue drain |
| `POST` | `/api/scrape/resume` | Resume queue + try drain |
| `POST` | `/api/comfy/generate` | Queue ComfyUI Pro (ref) or txt2img |
| `GET` | `/api/comfy/status` | ComfyUI job progress |
| `GET` | `/api/generations` | Saved generations for a source photo |
| `GET` | `/media/<path>` | Full-resolution image or video |
| `GET` | `/media/thumb/<path>` | Generated JPEG thumbnail |

---

## 2. Detailed specs

### `GET /api/stats`

```json
{ "total_photos": 1134, "total_creators": 147, "prompts_ready": 420,
  "trash_enabled": true, "trash_count": 3 }
```

All three counters are single indexed SQL aggregates. `prompts_ready` reads the
`has_prompt` column (maintained write-through by `PromptCache`) rather than
walking the archive and loading the prompt cache, which is what it used to do on
every call — and `/api/stats` runs on every app init.

### `GET /api/prompt/batch/status`

```json
{ "running": true, "total": 540, "completed": 128, "failed": 3,
  "current": "creator/IMG_9.jpg", "pending": 409,
  "cancelled": false, "cancel_requested": false,
  "started_at": "…", "finished_at": null, "error": null }
```

`pending` is a snapshot taken at job start and decremented as work completes —
never recomputed per request. `cancel_requested` flips as soon as
`POST /api/prompt/batch/cancel` lands; `cancelled` is set once the runner
actually stops, which happens after the in-flight photo finishes (the Ollama
call isn't interruptible, and a partial write would poison the prompt cache).

`GET /api/creators` includes `last_synced_at` and `synced_count` from `sync_state.json` when available.

### `GET /api/health`

Probes Ollama at `http://localhost:11434/api/tags` (1.5s timeout).

```json
{
  "ollama": true,
  "model": "qwen2.5vl:7b",
  "model_ready": true,
  "models": ["qwen2.5vl:7b", "moondream:latest"],
  "leases": { "ollama": "classify", "instagram": null, "comfy": null }
}
```

When Ollama is down: `{ "ollama": false, ... }`. Also includes `comfy` / `url` for ComfyUI reachability.

`leases` names the job holding each exclusive resource, or `null` if free — the
first thing to check when a job reports `busy` and nothing looks like it is
running. Owners: `classify`, `batch_prompt`, `sync`.

### `GET /api/journal?kind=<kind>&limit=20`

Run history for a background job. Without `kind`, lists the kinds present on
disk. Kinds: `classify`, `batch_prompt`, `sync`.

```json
{
  "kind": "classify",
  "limit": 20,
  "runs": [
    {
      "run_id": "classify_20260809T041626Z_a1b2",
      "kind": "classify",
      "creator": "someone",
      "total": 42,
      "started_at": "2026-08-09T04:16:26+00:00",
      "finished_at": "2026-08-09T04:19:02+00:00",
      "outcome": "ok",
      "duration_sec": 156.2,
      "items": 42,
      "failures": 2,
      "item_count": 42,
      "score_hist": { "-1": 2, "0": 1, "1": 4, "2": 9, "3": 26 },
      "top_score_share": 0.65,
      "unscored_rate": 0.0476,
      "events": [{ "ts": "…", "name": "rate_limit", "backoff_sec": 60 }]
    }
  ]
}
```

Newest run first; an in-flight run has `finished_at: null`. Per-item records are
**counted** (`item_count`), not returned — a 4000-photo run must not become 4000
objects in a response. Read the raw lines at
`<archive>/_journal/<kind>.jsonl` when per-item detail is needed.

`outcome` is `ok` | `error` | `cancelled`. `top_score_share` is the distribution
guard: a classifier emitting one value for most of the archive carries almost no
information, which is how an 85%-glam-3 prompt shipped unnoticed.

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

### `POST /api/comfy/generate`

Requires ComfyUI at `COMFYUI_URL` (default `http://127.0.0.1:8188`). Defaults `use_mode_e: true`; also accepts `denoise`, `steps`, `cfg_scale`, `seed`.

```json
{
  "path": "creator/file.jpg",
  "workflow": "pro",
  "variant": "pro",
  "positive_prompt": "optional override",
  "negative_prompt": "optional override",
  "use_mode_e": true,
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

Query: `creator`, `search`, `unanalyzed` (`1`/`true`), `favorite` (`1`/`true`), `sexy` (`1`/`true` → `glam_score >= GLAM_SEXY_MIN` default 2), `reject` (`1`/`true` → scored non-keep, `glam_score` 0–1), `unscored` (`1`/`true` → `glam_score = -1`), `glam_min` / `glam_max` (int), `media_type` (`photo` | `video` | omit/`all`), `sort` (`name` | `newest` | `oldest` | `glam`), `offset` (default 0), `limit` (default/max from config, typically 300).

Each photo may include `glam_score` (`-1` unscored, `0` none, `1` woman, `2` sexy, `3` sexy+figure).

`GET /api/creators` also returns `scored_count`, `unscored_count`, `reject_count` for sidebar badges.

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

### `POST /api/sync/creator`

```json
{
  "username": "roxeuoon",
  "max_posts": 50,
  "include_videos": true,
  "mode": "bounded",
  "deep": true
}
```

`include_videos` defaults to config `INCLUDE_VIDEOS_DEFAULT` / `IG_INCLUDE_VIDEOS` (true unless set off).

`mode`: `bounded` (default, glam rank/top-N) or `full` (stream entire feed).  
`deep` (full only): `true` = catch-up off (true archive); `false` = catch-up on.

**409** if creator scrape queue has pending jobs and is not paused (fairness).

### `POST /api/sync/cancel`

Cooperative cancel of whatever IG job is running (`saved` / `creator` / `following` / `creator_queue`).

```json
{ "status": "cancelling" }
```

or `{ "status": "idle" }` when nothing is running.

### `POST /api/scrape/enqueue`

Serial creator scrape queue (never parallel with any other scrape job). Creates
folder + enqueues.

```json
{
  "username": "roxeuoon",
  "source": "instagram",
  "mode": "full",
  "deep": true,
  "max_posts": null,
  "include_videos": true,
  "priority": 0
}
```

Defaults: `source=instagram`, `mode=full`, `deep=true`. Status: `started` |
`queued` | `already_pending` | `already_running`.

`source` accepts `instagram` (default), `x` (aliases: `twitter`), `reddit`.
Omitting it preserves the pre-multi-source behaviour exactly.

`username` is interpreted per source, and the response echoes what was resolved:

| Source | Accepted input | Fetches | Archive folder |
|--------|----------------|---------|----------------|
| `instagram` | `handle`, `@handle` | profile feed | `handle` |
| `x` | `handle`, `@handle`, `x.com/handle` | `/media` timeline | `handle__x` |
| `reddit` | `r/sub`, `sub`, `u/user`, full URL | subreddit or user submissions | `r_sub__reddit` / `u_user__reddit` |

Response adds `source`, `target_url`, `folder`, `folder_created`.

Queue identity is `(source, username)`, so the same handle can be queued for
Instagram and X simultaneously without one being rejected as a duplicate.

Errors: `400` for an unknown source or a handle that is invalid for its platform.

### `GET /api/sources`

Available scrape sources, for populating a picker.

```json
{
  "sources": [
    { "name": "instagram", "label": "Instagram" },
    { "name": "x", "label": "X / Twitter" },
    { "name": "reddit", "label": "Reddit" }
  ],
  "default": "instagram"
}
```

`mode` values:

| mode | Behavior |
|------|----------|
| `full` | Stream entire feed; `deep=true` = catch-up **off** (true archive) |
| `latest` | Catch-up only for existing folders; never glam-rank; respects tombstones |
| `bounded` | Glam rank + top-N (following bulk style) |

**Deletes:** `DELETE /api/photo` records a tombstone (`deleted_posts` in `archive.db`) so future sync never re-downloads that shortcode/post_id. Restoring from the Trash clears that tombstone again.

---

## Trash (soft delete)

`DELETE /api/photo?path=<rel>` moves media to `_trash/<entry_id>/` unless `PROMPTSTUDIO_TRASH=0` or `permanent=1` is passed.

```json
{ "status": "trashed", "filename": "IMG_1.jpg",
  "rel_path": "creator/IMG_1.jpg", "trash_id": "20260808T191204Z-a1b2c3" }
```

`status` is `"deleted"` and `trash_id` is `null` for a permanent delete. The UI uses `trash_id` to offer **Undo**.

Each entry directory holds the media file, its `.meta.json` sidecar, and an `entry.json` manifest capturing `favorite`, `prompt_bundle`, `post_id`/`shortcode`, `tombstoned`, and `taken_at` — so `POST /api/trash/restore` puts back the file, sidecar, prompt bundle, favorite flag, and index row, and clears the tombstone.

### `GET /api/trash`

```json
{ "entries": [ { "id": "20260808T191204Z-a1b2c3", "rel_path": "creator/IMG_1.jpg",
                 "creator": "creator", "filename": "IMG_1.jpg",
                 "deleted_at": "2026-08-08T19:12:04+00:00", "file_size": 244118,
                 "favorite": false, "prompt_bundle": null, "tombstoned": true,
                 "media_present": true } ],
  "total": 1, "offset": 0, "limit": 100,
  "count": 1, "bytes": 244118, "retention_days": 30 }
```

### `POST /api/trash/restore`

Body: `{"id": "<entry_id>"}` or `{"ids": ["<id>", …]}`.

```json
{ "status": "ok", "restored": 1, "failed": 0,
  "results": [ { "status": "restored", "id": "…", "rel_path": "creator/IMG_1.jpg" } ] }
```

Per-entry `status`: `restored` · `not_found` · `conflict` (a file already occupies the target path — the trash entry is kept, never overwritten) · `error`.

### `POST /api/trash/purge`

Body: `{"id"}` / `{"ids"}` / `{"all": true}` / `{"expired": true, "days": 30}`. Returns `{"status": "ok", "purged": N}`. Purging is irreversible.

`GET /api/stats` also reports `trash_enabled` and `trash_count`.

### `GET /api/scrape/status`

```json
{
  "paused": false,
  "pause_reason": "",
  "pending": [],
  "pending_count": 0,
  "running_job": null,
  "history": [],
  "stats": { "completed_today": 0, "downloaded_today": 0, "errors_today": 0 },
  "sync": { "running": false, "progress": "", "creator_queue": {} }
}
```

### `POST /api/scrape/pause` / `POST /api/scrape/resume`

Pause stops auto-drain (hard rate-limit abort also pauses). Resume clears pause and tries drain.

### `POST /api/scrape/cancel`

```json
{ "job_id": "csq_…", "scope": "job" }
```

`scope: all_pending` cancels all pending; optional `cancel_running: true` also requests IG cancel.

### `POST /api/sync/following`

```json
{
  "max_accounts": 20,
  "accounts_per_day": 20,
  "max_posts": 20,
  "min_media_count": 5,
  "keywords": "model,lingerie",
  "include_videos": true
}
```

`max_accounts` and `accounts_per_day` are aliases (default **20**). The run also respects the persistent daily budget in `following_queue.json`. Pending accounts are processed **highest `priority` first** (see `prioritize_following_queue.py`).

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
