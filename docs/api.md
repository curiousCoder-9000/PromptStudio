# PromptStudio API Reference

Base: `http://localhost:5000`  
Agent map: [context.md](context.md). Routes implemented in `promptstudio/server/handler.py`.

---

## 1. Endpoints Overview

| Method | Endpoint | Description |
| :--- | :--- | :--- |
| `GET` | `/api/stats` | Photos, creators, `prompts_ready` |
| `GET` | `/api/insights` | Quality dashboard — prompt edit/regenerate rates, generation counts, classify tier distribution |
| `GET` | `/api/health` | Ollama + Comfy reachability + models + job leases |
| `GET` | `/api/journal` | Run history for a background job kind |
| `GET` | `/api/creators` | Creator folders with counts + cover + sync meta + keep/reject counters |
| `GET` | `/api/creator/style` | Learned style prefix for a creator |
| `POST` | `/api/creator/style/rebuild` | Rebuild style from cached prompts |
| `GET` | `/api/following` | Accounts from local `following_list.json` |
| `GET` | `/api/photos` | Paginated (`offset`, `limit`, `creator`, `search`, `unanalyzed`, `favorite`, `media_type`, `verdict`, `sort`) |
| `GET` | `/api/media/detail` | Reel/photo inspector (`path`): caption, IG link — not vision prompts |
| `GET` | `/api/prompt` | Vision prompt bundle (`path`, optional `refresh`) — includes `history` |
| `PUT` | `/api/prompt` | Save edited positive/negative prompts + tags |
| `POST` | `/api/prompt/restore` | Restore a prior prompt from history |
| `POST` | `/api/prompt/mode-e` | Mode E rewrite (outfit/scene; optional `apply`) |
| `PUT` | `/api/favorite` | Toggle or set favorite flag |
| `POST` | `/api/prompt/batch` | Background batch analyze (`creator`, `force`, `limit`, `paths`) |
| `GET` | `/api/prompt/batch/status` | Batch job progress (cheap — snapshot, no archive scan) |
| `POST` | `/api/prompt/batch/cancel` | Cooperative cancel after the current photo |
| `POST` | `/api/classify/start` | Background keep/reject classify for one creator |
| `GET` | `/api/classify/status` | Classify job progress + tier histogram |
| `POST` | `/api/classify/cancel` | Cooperative cancel after the current item |
| `POST` | `/api/classify/verdict` | Pin one file to keep/reject by hand (or clear) |
| `GET` | `/api/classify/sheet` | Contact sheet a reel was judged from (`rel_path`) |
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

### `GET /api/insights`

Phase 13 B1 quality dashboard. Read-only aggregates over data already on disk
(prompt `manual_edit` / `history`, `generations_index.json`).
No new scoring jobs.

```json
{
  "prompts": {
    "total": 420,
    "manual_edits": 38,
    "edit_rate": 0.0905,
    "with_history": 51,
    "regenerate_rate": 0.1214,
    "avg_history_depth": 0.18,
    "by_pipeline_version": { "v2-structured": 420 }
  },
  "generations": {
    "sources_with_gens": 12,
    "total_outputs": 28,
    "avg_per_source": 2.333,
    "sources_with_multiple": 5,
    "rated": 0,
    "keep_rate": null
  }
}
```

`keep_rate` stays null until A3 (rate outputs) lands.

A `classify` block reports the tier distribution over everything classified:

```json
{
  "classify": {
    "classified": 812,
    "errors": 4,
    "reject_max_tier": 1,
    "distribution": { "-1": 4, "0": 91, "1": 240, "2": 318, "3": 130, "4": 33 },
    "labels": { "0": "Unusable", "1": "Fully modest", "…": "…" },
    "reject_rate": 0.4077,
    "top_tier_share": 0.3916,
    "error_rate": 0.0049
  }
}
```

`top_tier_share` is the number to watch. Above ~0.6 the classifier is barely
discriminating whatever the prompt claims — the previous one shipped at 0.85 and
nothing was reading it. See [design_media_classifier.md](design_media_classifier.md) §5.

### `POST /api/classify/start`

```json
{ "creator": "someone", "only_unclassified": true, "include_videos": true,
  "rescore_stale": false, "force": false, "limit": null }
```

→ `200 {"status": "started", "pending": 40, "creator": "someone", …}`

| `status` | Code | Meaning |
|----------|-----:|---------|
| `started` | 200 | Job running; poll `/api/classify/status` |
| `nothing_to_do` | 200 | No pending media (`pending: 0`) |
| `busy` | 409 | Another job holds the `ollama` lease, or a classify is already running |
| `ollama_down` | 503 | Ollama unreachable |
| `bad_creator` | 400 | Missing/empty creator |

- `only_unclassified` (default true) visits never-classified media plus previously
  failed attempts. `force` (or `only_unclassified: false`) re-runs everything.
- `rescore_stale` adds media judged by a superseded prompt version. Without it a
  prompt bump never re-runs anything, and the only way to adopt it is a full
  rescore.
- Takes the `ollama` lease, so it is mutually exclusive with batch analyze.

### `GET /api/classify/status`

```json
{ "running": true, "creator": "someone", "total": 40, "completed": 12,
  "failed": 1, "kept": 8, "rejected": 3, "current": "someone/IMG_9.jpg",
  "cancelled": false, "cancel_requested": false,
  "tier_hist": { "-1": 1, "0": 2, "1": 1, "2": 5, "3": 3, "4": 0 },
  "top_tier_share": 0.4545, "error_rate": 0.0833,
  "reject_max_tier": 1, "model": "qwen2.5vl:7b",
  "tier_labels": { "0": "Unusable", "…": "…" },
  "started_at": "…", "finished_at": null, "error": null }
```

`pending` (remaining work) is included **only when idle** — during a run it is a
full re-query on every 3s poll for an answer that is already in `completed`.

### `POST /api/classify/cancel`

`{"status": "cancelling" | "idle", "running": bool}`. Cooperative: stops after
the item in flight, since a vision call is not interruptible.

### `POST /api/classify/verdict`

```json
{ "rel_path": "someone/IMG_9.jpg", "verdict": "keep" }
```

`verdict` is `keep`, `reject`, or `null` (clear the override and fall back to the
model's tier). Returns `{"status": "ok", "verdict": {…}}` with the refreshed
block, or `404 {"status": "not_classified"}` when the file has no verdict row —
there is no tier to override yet.

The override is stored separately from the tier, so it survives a re-classify and
a soft delete + Undo.

### `GET /api/classify/sheet?rel_path=creator/reel.mp4`

The contact sheet JPEG the reel was judged from, or `404` if there is none
(photos never have one). Served from `_classify/` through `safe_join`, not the
media resolver — `_classify` is an `EXCLUDED_FOLDER`, so `/media/…` cannot reach
it, which is exactly why sheets live there.

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

`GET /api/creators` includes `last_synced_at` and `synced_count` from `sync_state.json` when available,
plus per-creator classify counters: `keep_count`, `reject_count`, `unusable_count`
(tier 0), `modest_count` (tier 1), `unclassified_count`, `error_count`, `stale_count`
(judged by a superseded prompt version). All from one indexed `GROUP BY`.

### `GET /api/health`

Probes Ollama at `http://localhost:11434/api/tags` (1.5s timeout).

```json
{
  "ollama": true,
  "model": "qwen2.5vl:7b",
  "model_ready": true,
  "models": ["qwen2.5vl:7b", "moondream:latest"],
  "leases": { "ollama": "batch_prompt", "instagram": null, "comfy": null }
}
```

When Ollama is down: `{ "ollama": false, ... }`. Also includes `comfy` / `url` for ComfyUI reachability.

`leases` names the job holding each exclusive resource, or `null` if free — the
first thing to check when a job reports `busy` and nothing looks like it is
running. Owners: `batch_prompt`, `classify`, `sync`.

### `GET /api/journal?kind=<kind>&limit=20`

Run history for a background job. Without `kind`, lists the kinds present on
disk. Kinds: `batch_prompt`, `classify`, `sync`.

```json
{
  "kind": "sync",
  "limit": 20,
  "runs": [
    {
      "run_id": "sync_20260809T041626Z_a1b2",
      "kind": "sync",
      "creator": "someone",
      "total": 42,
      "started_at": "2026-08-09T04:16:26+00:00",
      "finished_at": "2026-08-09T04:19:02+00:00",
      "outcome": "ok",
      "duration_sec": 156.2,
      "items": 42,
      "failures": 2,
      "item_count": 42,
      "events": [{ "ts": "…", "name": "rate_limit", "backoff_sec": 60 }]
    }
  ]
}
```

Newest run first; an in-flight run has `finished_at: null`. Per-item records are
**counted** (`item_count`), not returned — a 4000-photo run must not become 4000
objects in a response. Read the raw lines at
`<archive>/_journal/<kind>.jsonl` when per-item detail is needed.

`outcome` is `ok` | `error` | `cancelled`.

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

Query: `creator`, `search`, `unanalyzed` (`1`/`true`), `favorite` (`1`/`true`), `media_type` (`photo` | `video` | omit/`all`), `verdict` (see below), `sort` (`name` | `newest` | `oldest` | `posted` | `posted_oldest` | `tier`), `offset` (default 0), `limit` (default/max from config, typically 300).

- `newest` / `oldest` — archive ingest time (`added_at`; when the file was downloaded/indexed).
- `posted` / `posted_oldest` — remote post time (`mtime`, which downloaders stamp to the post date); falls back to `added_at` when `mtime` is missing or zero.
- `tier` — classify tier ascending (harshest first), then errors, then never-classified. The review-mode order.

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
      "favorite": true,
      "verdict": {
        "verdict": "reject",
        "tier": 1,
        "manual": null,
        "reason": "crewneck sweater",
        "media_kind": "photo",
        "verdict_source": "image",
        "confidence": 0.82,
        "prompt_version": "v4-ordinal-frame-v7a",
        "sheet_path": null,
        "error": null,
        "classified_at": "2026-08-09T16:33:57+00:00"
      }
    }
  ],
  "total": 1134,
  "offset": 0,
  "limit": 60,
  "has_more": true,
  "sort": "newest",
  "verdict": ""
}
```

- `verdict` is **absent** on rows that have never been classified — its presence is the "has a verdict" test.
- `verdict.verdict` is derived server-side from `tier` against `CLASSIFY_REJECT_MAX_TIER` (or from `manual` when set). Clients must not re-derive it; the threshold is configurable and would drift.

- `search` matches creator, filename, and cached prompt text/tags.
- `unanalyzed=1` returns photos that need analysis (no cache entry or wrong `vision_engine`), same rule as batch.
- `favorite=1` returns only favorited photos (`favorites.json`).
- `sort=newest|oldest` uses `taken_at` from `*.meta.json`, then filename UTC stamp, then mtime.
- `has_prompt`: cache hit with current `vision_engine`.
- `prompt_stale`: entry exists but engine or `pipeline_version` is outdated.

`verdict=` values:

| Value | Rows returned |
|-------|---------------|
| `reject` | effective verdict is reject (`tier ≤ CLASSIFY_REJECT_MAX_TIER`, or `manual='reject'`) |
| `keep` | effective verdict is keep |
| `unusable` | raw tier 0 only, no manual override — the quality gate |
| `modest` | raw tier 1 only, no manual override — the taste call |
| `error` | classify was attempted and failed; retryable |
| `unclassified` | no verdict row at all |

`unusable` and `modest` are raw-tier views on purpose: they let a cautious cleanup
pass act on the boundary nobody argues about without touching the one that has
never been measured. A hand-kept file drops out of both.

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

`mode`: `bounded` (default, keyword rank/top-N) or `full` (stream entire feed).  
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
| `latest` | Catch-up only for existing folders; never keyword-rank; respects tombstones |
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
