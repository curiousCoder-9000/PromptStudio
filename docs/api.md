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
| `GET` | `/api/creators` | Creator folders with counts + cover + sync meta + keep/reject counters. `?source=` scopes them |
| `GET` | `/api/creator/style` | Learned style prefix for a creator |
| `POST` | `/api/creator/style/rebuild` | Rebuild style from cached prompts |
| `GET` | `/api/following` | Accounts from local `following_list.json` |
| `GET` | `/api/photos` | Paginated (`offset`, `limit`, `creator`, `search`, `unanalyzed`, `favorite`, `media_type`, `verdict`, `source`, `sort`, `group`, `mode`, `collection`) |
| `GET` | `/api/duplicates` | Near-dup groups (`kind=phash\|embed\|all`). Favourites never `preselected` |
| `GET`/`POST`/`DELETE` | `/api/views` | Saved filter sets (F8) |
| `GET`/`POST`/`DELETE` | `/api/collections` | Cross-creator boards (C4) |
| `POST`/`DELETE` | `/api/collections/items` | `{id, paths}` add / remove members |
| `POST` | `/api/taste/train` | Embed + fit P(keep) (B2) |
| `GET` | `/api/taste/status` | Taste job snapshot |
| `POST` | `/api/taste/cancel` | Cooperative cancel |
| `GET` | `/api/media/detail` | Reel/photo inspector (`path`): caption, IG link — not vision prompts |
| `GET` | `/api/prompt` | Vision prompt bundle (`path`, optional `refresh`) — includes `history` |
| `PUT` | `/api/prompt` | Save edited positive/negative prompts + tags |
| `POST` | `/api/prompt/restore` | Restore a prior prompt from history |
| `POST` | `/api/prompt/mode-e` | Mode E rewrite (outfit/scene; optional `apply`) |
| `PUT` | `/api/favorite` | Toggle or set favorite flag |
| `POST` | `/api/prompt/batch` | Background batch analyze (`creator`, `force`, `limit`, `paths`) |
| `GET` | `/api/prompt/batch/status` | Batch job progress (cheap — snapshot, no archive scan) |
| `POST` | `/api/prompt/batch/cancel` | Cooperative cancel after the current photo |
| `POST` | `/api/classify/start` | Background keep/reject classify — one creator, or the whole archive |
| `GET` | `/api/classify/status` | Classify job progress + tier histogram |
| `POST` | `/api/classify/cancel` | Cooperative cancel after the current item |
| `POST` | `/api/classify/verdict` | Pin one file — or `rel_paths[]` — to keep/reject by hand (or clear) |
| `GET` | `/api/labels` | Taste-label counts, or `?path=` for one row |
| `PUT` | `/api/labels` | `{path, label}` where label is `1` keep / `-1` discard / `0` clear |
| `POST` | `/api/labels/seed` | Copy favorites → keep and trash → discard without overwriting |
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
| `POST` | `/api/sync/cancel` | Cooperative cancel. Optional `source` — omitted cancels every lane |
| `GET` | `/api/sync/status` | Sync job progress / abort / `creator_queue` summary |
| `POST` | `/api/scrape/enqueue` | Enqueue serial full/bounded scrape (Instagram / X / Reddit) |
| `GET` | `/api/sources` | Available scrape sources |
| `GET` | `/api/scrape/status` | Creator scrape queue + embedded sync status |
| `POST` | `/api/scrape/cancel` | Cancel pending job or all pending (`scope`) |
| `POST` | `/api/scrape/pause` | Pause drain. Optional `source` — omitted pauses every lane |
| `POST` | `/api/scrape/resume` | Resume + try drain. Optional `source` |
| `POST` | `/api/comfy/generate` | Queue ComfyUI Pro (ref) or txt2img |
| `GET` | `/api/comfy/status` | ComfyUI job progress |
| `POST` | `/api/comfy/batch` | Batch generate — `paths[]` or `/api/photos` filters |
| `GET` | `/api/comfy/batch/status` | Batch generate progress |
| `POST` | `/api/comfy/batch/cancel` | Cooperative cancel + in-flight interrupt |
| `GET` | `/api/workflows` | ComfyUI workflow registry — `name`, `label`, `kind` |
| `GET` | `/api/generations` | Saved generations for a source photo |
| `GET` | `/api/generations/list` | Outputs gallery — filter, sort, paginate |
| `PUT` | `/api/generation/rate` | Rate one output: `-1` discard · `0` unrated · `1` keep · `2` star |
| `DELETE` | `/api/generation` | Delete one output — **permanent, no trash** |
| `GET` | `/media/<path>` | Full-resolution image or video |
| `GET` | `/media/thumb/<path>` | Generated JPEG thumbnail |

---

## 2. Detailed specs

### `GET /api/stats`

```json
{ "total_photos": 1134, "total_videos": 82, "total_creators": 147,
  "prompts_ready": 420, "unclassified_total": 311,
  "trash_enabled": true, "trash_count": 3,
  "verdict_facets": {
    "total": 1216,
    "reject_max_tier": 1,
    "warn_above": 0.6,
    "counts": { "keep": 481, "reject": 331, "unclassified": 400,
                "error": 4, "unusable": 91, "modest": 240 },
    "shares": { "keep": 0.3956, "reject": 0.2722, "unclassified": 0.3289,
                "error": 0.0033, "unusable": 0.0748, "modest": 0.1974 }
  } }
```

`unclassified_total` is media with no `media_verdicts` row, **archive-wide and never
scoped** — it is what the navbar Classify All button counts, and that job ignores the
source filter. Reading the sidebar's per-creator `unclassified_count` instead (which
`/api/creators?source=` does narrow) made the button disable itself claiming everything
was classified while another platform's backlog was untouched.

`verdict_facets` is the **B4 pass rate** of every verdict filter: one grouped query for
all six buckets, from the same predicates `/api/photos?verdict=` filters with, so a
chip's badge can never describe a filter nobody is running. Rides on this route rather
than getting its own because the refresh points already match — app init, and the end of
a classify run. Archive-wide and never scoped for the same reason `unclassified_total`
is: saturation is a property of the classifier over everything it has judged, and a
share that moved as the user clicked between creators could not be compared against the
guard at all. `shares` are `null` on an empty archive (nothing measured yet is not the
same answer as measured at zero), and `warn_above` is served rather than hardcoded in
`app.js` so the badge, the panel and the pytest gate cannot drift apart
(`DISTRIBUTION_MAX_SHARE`).

All the counters are single indexed SQL aggregates. `prompts_ready` reads the
`has_prompt` column (maintained write-through by `PromptCache`) rather than
walking the archive and loading the prompt cache, which is what it used to do on
every call — and `/api/stats` runs on every app init.

### `GET /api/insights`

Phase 13 B1 quality dashboard. Read-only aggregates over data already on disk
(prompt `manual_edit` / `history`, the `generations` table).
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
    "rated": 17,
    "kept": 11,
    "discarded": 6,
    "starred": 3,
    "keep_rate": 0.6471,
    "unreproducible": 4,
    "by_prompt_version": {
      "Ollama (qwen2.5vl:7b) v2-structured": {
        "total": 20, "rated": 14, "kept": 10, "keep_rate": 0.7143
      }
    },
    "by_workflow":   { "pro": { "total": 24, "rated": 15, "kept": 11, "keep_rate": 0.7333 } },
    "by_checkpoint": { "juggernautXL_ragnarok.safetensors": { "…": "…" } },
    "by_mode_e":     { "on":  { "…": "…" }, "off": { "…": "…" } }
  }
}
```

`keep_rate = kept / rated`, **not** `kept / total` — an unrated output is not
evidence either way, and dividing by the total would make the number drift
toward zero as the archive grows instead of measuring anything. It is `null`
until something is rated, because `0.0` would read as a damning score for an
archive nobody has judged yet.

The four cuts are what make it actionable: one archive-wide rate says the loop
is or is not working, but not which half to change. `by_mode_e` is not in
[design_generation_loop.md](design_generation_loop.md) §3.3's list of three,
but §3.3 names "is Mode E worth it" as a question the cuts should answer and
none of the named three splits on it.

`unreproducible` counts rows with `seed < 0` — generations imported from the
pre-A0 JSON index, whose seed was never recorded and cannot be recovered. It is
the only measure of success criterion #1 ("100% of new rows reproducible").

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
    "error_rate": 0.0049,
    "saturation": {
      "what": "classified tier", "n": 812, "min_n": 100, "threshold": 0.6,
      "measured": true, "saturated": false,
      "top_bucket": "tier 2", "top_count": 318, "top_share": 0.3916,
      "message": "classified tier: tier 2 holds 39.2% of 812 (limit 60%)"
    }
  }
}
```

`top_tier_share` is the number to watch. Above ~0.6 the classifier is barely
discriminating whatever the prompt claims — the previous one shipped at 0.85 and
nothing was reading it. See [design_media_classifier.md](design_media_classifier.md) §5.

`saturation` is that number with the **B4 verdict** attached, from the one rule in
`insights.saturation_report`. `generations` carries the same block over rated outputs
only — `keep_rate`'s own denominator, because counting unrated rows as a bucket would
fire on every archive nobody has judged yet. `measured` is `false` below `min_n`
(`DISTRIBUTION_MIN_CLASSIFIED` / `DISTRIBUTION_MIN_RATED`), which is a different answer
from "measured and fine"; `message` names the bucket, its share and the denominator, so
the failing check in `tests/test_distribution_guard.py` tells you where to look.

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
| `bad_creator` | 400 | `creator` names an excluded folder (`_trash`, `_thumbs`, …) |

- **`creator` is optional. Omitted or `""` classifies the whole archive.** The scope
  is either one real creator or all of them; a `_`-prefixed folder is `bad_creator`.
  In the status payload `""` (archive-wide run) and `null` (no job) are different
  values — do not collapse them, or the UI renders "Classifying @" with no handle.
- `only_unclassified` (default true) visits never-classified media plus previously
  failed attempts. `force` (or `only_unclassified: false`) re-runs everything.
- `rescore_stale` adds media judged by a superseded prompt version. Without it a
  prompt bump never re-runs anything, and the only way to adopt it is a full
  rescore.
- Takes the `ollama` lease, so it is mutually exclusive with batch analyze — which
  matters more archive-wide, since the run is long.

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

Bulk form (U13 — rescue a reject pile without opening each card):

```json
{ "rel_paths": ["someone/a.jpg", "someone/b.jpg"], "verdict": "keep" }
```

Returns `{"status": "ok", "verdict": "keep", "updated": […], "missing": […],
"verdicts": { "<rel>": {…} }}`. Unclassified paths land in `missing` and are
not invented. Cap is `MAX_PHOTO_IDS_API` (default 10 000). Sending both
`rel_path` and `rel_paths` prefers the list. A one-item `rel_paths` still uses
the bulk shape, so existing single-path clients are unchanged.

The override is stored separately from the tier, so it survives a re-classify and
a soft delete + Undo.

### `GET /api/classify/sheet?rel_path=creator/reel.mp4`

The contact sheet JPEG the reel was judged from, or `404` if there is none
(photos never have one). Served from `_classify/` through `safe_join`, not the
media resolver — `_classify` is an `EXCLUDED_FOLDER`, so `/media/…` cannot reach
it, which is exactly why sheets live there.

### `GET` / `PUT` `/api/labels`

B3 taste labels for the preference model. `label` is `1` keep, `-1` discard, `0`
clears the row so "not judged yet" is the absence of a label.

`GET /api/labels` returns `{keep, discard, labelled, unlabeled}`.
`GET /api/labels?path=` returns one row or 404.
`PUT /api/labels` body `{path, label}`.

### `POST /api/labels/seed`

Copies `favorites.json` as keep and `_trash/` `rel_path`s as discard.
Existing labels are not overwritten. Returns inserted/skipped counts plus
the updated summary.

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

Each creator also carries `sources` — `{"instagram": 120, "x": 37}` — because a
folder can hold media from more than one platform (`SCRAPE_FOLDER_SUFFIX=0`, or
manual uploads). Provenance is `photos.source`, **never** the folder-name suffix.

`?source=<name>` scopes `photo_count`, the cover and every verdict counter to one
platform, and drops creators with nothing from it. `sources` stays **unfiltered**
so the sidebar can still mark a folder multi-source while a filter is active.
`all` and an empty value both mean unfiltered; anything unregistered is a **400**,
not a silent full result. See [design_source_filter.md](design_source_filter.md).

### `GET /api/health`

Probes Ollama at `http://localhost:11434/api/tags` (1.5s timeout).

```json
{
  "ollama": true,
  "model": "qwen2.5vl:7b",
  "model_ready": true,
  "models": ["qwen2.5vl:7b", "moondream:latest"],
  "leases": { "ollama": "batch_prompt", "instagram": null, "comfy": null },
  "instagram_backend": "instaloader",
  "instagram_cookies": { "mode": "none", "ready": false }
}
```

When Ollama is down: `{ "ollama": false, ... }`. Also includes `comfy` / `url` for ComfyUI reachability.

`instagram_backend` is `instaloader` or `gallery-dl` (`IG_BACKEND`).
`instagram_cookies` is `{mode, ready}` plus `browser` when cookies come from
`--cookies-from-browser`. Cookie values are never returned.

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

`workflow` is a **registry name** from [`GET /api/workflows`](#get-apiworkflows) — an
unknown one is a `400` naming what is available, not a silent fall-through to txt2img.
Two ship with the package:

- **`workflow: "pro"`** (default) — `kind: img2img`. Uploads the archive photo to ComfyUI and runs the `modelToimage_pro` graph (IPAdapter face+body, OpenPose, img2img denoise, FaceDetailer) from `promptstudio/comfy/workflows/pro/`. Checkpoint defaults to `juggernautXL_ragnarok.safetensors`.
- **`workflow: "txt2img"`** — `kind: txt2img`. Bare CheckpointLoader → EmptyLatent graph (what `variant` `sdxl` / `flux` / `pony` used to force).

Which defaults apply — `steps` / `cfg` / `denoise`, and whether Mode E runs — follows the
workflow's declared `kind`, not its name, so a third `img2img` entry behaves like `pro`
without a code change.

Outputs are saved under `_generations/<creator>/` and indexed in `generations_index.json`.

### `GET /api/comfy/status`

```json
{ "running": true, "progress": "Generating…", "source_path": "…", "result": null }
```

### `POST /api/comfy/batch`

Batch generate (A2). Selection is either `paths` — the gallery's multi-select —
or the same filter vocabulary `/api/photos` accepts. Every other key is a
generation override applied to all items, identical in meaning to
`/api/comfy/generate`.

```json
{
  "paths": ["creator/a.jpg", "creator/b.jpg"],
  "creator": "nina",
  "favorite": false,
  "media_type": "photo",
  "verdict": "keep",
  "source": "instagram",
  "limit": 50,
  "workflow": "pro",
  "seed": null
}
```

```json
{
  "status": "started",
  "batch_id": "9f2c1a7b40de",
  "pending": 47,
  "skipped_no_prompt": 3,
  "skipped_video": 0,
  "capped": false
}
```

- `409` with `status: "busy"` when the `comfy` lease is held — the message names
  the holder, whether that is a one-shot generate or another batch.
- `503` with `status: "offline"` when ComfyUI is unreachable.
- `200` with `status: "nothing_to_do"` when the selection resolved to nothing;
  the skip counts still come back, so "why did nothing happen" is answerable.

**Skips are counted, never fixed.** A photo with no prompt is reported, not
auto-analyzed — chaining the two jobs is out of scope (design §9). Videos are
skipped because img2img has no meaningful reference frame. `COMFY_BATCH_MAX`
(default 200) caps one enqueue and sets `capped`.

Every row written by the run carries `batch_id`, so
`GET /api/generations/list?batch_id=…` is the contact sheet for it.

### `GET /api/comfy/batch/status`

```json
{
  "running": true, "batch_id": "9f2c1a7b40de", "total": 47,
  "completed": 12, "failed": 1, "pending": 34, "current": "nina/x.jpg",
  "cancelled": false, "cancel_requested": false,
  "skipped_no_prompt": 3, "skipped_video": 0, "workflow": "pro"
}
```

`pending` is snapshotted at start and decremented per item — never recomputed
per poll, which would be a full archive scan every four seconds.

### `POST /api/comfy/batch/cancel`

```json
{ "status": "cancelling", "running": true }
```

Two-level. The cooperative flag drains the remaining queue; the item already on
the GPU is interrupted via ComfyUI's `/interrupt`, but **only** when our
`prompt_id` is the head of `/queue` — otherwise cancelling a PromptStudio batch
would kill an unrelated job started from the ComfyUI tab. The pending copy is
dropped by id either way.

This inverts `/api/prompt/batch/cancel`, which finishes the in-flight photo.
Both are right: a half-written prompt poisons the cache, whereas nothing here is
persisted until the image is downloaded.

`{ "status": "idle" }` when no batch is running.

### `GET /api/workflows`

The A4 workflow registry, for the generate picker.

```json
{
  "workflows": [
    { "name": "pro",     "label": "Pro (reference)",       "kind": "img2img" },
    { "name": "txt2img", "label": "Txt2img (no reference)", "kind": "txt2img" }
  ],
  "default": "pro"
}
```

A workflow is a directory of two files — `graph.json` (a ComfyUI **Export (API)**
dump, untouched) and `slots.json` (where this app's runtime values go). Built-ins
live in `promptstudio/comfy/workflows/`; the user's own live in
`COMFY_WORKFLOWS_DIR` (default `<archive>/_workflows`) and **shadow** a built-in
of the same name.

Node ids are deliberately not in the response: the client picks a name, the
server owns the injection. A directory that fails validation is left out of the
list and the reason logged — `pro` staying usable matters more than surfacing a
broken import here.

`default` is always one of `workflows`, so the picker can never preselect a name
that is not offered.

There is **no import route yet** (design §3.6's upload → propose → remap flow).
Its gate needs a running ComfyUI for `/object_info` validation, so it is deferred;
today a workflow is installed by dropping the two files into
`COMFY_WORKFLOWS_DIR`, and E1's `workflows` kind backs them up.

### `GET /api/generations?path=creator/file.jpg`

```json
{ "path": "…", "generations": [{ "primary_url": "/media/_generations/…", "files": [] }] }
```

Reads the legacy `generations_index.json`, unchanged for lightbox back-compat.
The `generations` table in `archive.db` is the source of truth for everything
added since A0 (full prompts, real seed, rating).

### `GET /api/generations/list`

The outputs gallery (A1). Mirrors `/api/photos`: same `offset` / `limit` /
`has_more` / `total` contract, so the existing paging works unchanged.

Query: `creator`, `workflow`, `checkpoint`, `batch_id`, `source` (a source
`rel_path`), `rating` (`-1|0|1|2`), `rated_only=1`, `since` / `until` (ISO or
`YYYY-MM-DD`; date-only `until` is inclusive of that day), `has_source=1|0`,
`sort`, `offset`, `limit`.

`rating=0` filters to the **unrated**; omitting the parameter means no filter.
`rated_only=1` is the different question — everything judged either way.

`sort` ∈ `newest` (default) · `oldest` · `rating` · `source`. An unrecognised
value falls back to `newest` rather than erroring: it is whitelisted against an
`ORDER BY` fragment, which cannot be parameterised, so a stale bookmark must not
be able to reach SQL or 500.

```json
{
  "generations": [{
    "gen_id": "8f3c…", "rel_path": "_generations/nina/x_gen_1.png",
    "url": "/media/_generations/…", "thumb_url": "/media/thumb/_generations/…",
    "source_rel": "nina/photo.jpg", "creator": "nina",
    "seed": 987654321, "seed_recorded": true,
    "workflow": "pro", "checkpoint": "…", "steps": 32, "cfg": 6.0,
    "denoise": 0.7, "mode_e": true, "prompt_version": "…",
    "positive_prompt": "…", "negative_prompt": "…",
    "rating": 2, "rated_at": "…", "batch_id": null, "created_at": "…",
    "has_source": true, "source_thumb_url": "/media/thumb/…"
  }],
  "total": 412, "offset": 0, "limit": 200, "has_more": true,
  "facets": { "creators": [], "workflows": [], "checkpoints": [] }
}
```

`seed_recorded` is `false` for rows imported from the pre-A0 JSON index, whose
seed was never written down (`seed = -1`). The UI must show "seed not recorded"
and disable regenerate-same-seed for those rather than offering a button that
cannot reproduce anything.

`facets` lists the values actually present, so a checkpoint since removed from
ComfyUI still appears as a filter for the outputs it produced.

### `DELETE /api/generation?gen_id=…`

**Permanent. Does not use `_trash/`** — the deliberate asymmetry with
`DELETE /api/photo`. Archive media is unrecoverable; a generation carries its
own seed, prompt and checkpoint and is reproducible by construction, so a
restore path would be dead weight. Say so in the confirm copy.

Row is dropped first, file second, and the file only through
`ArchiveStore.resolve_path` — a row whose `rel_path` escapes the archive drops
the row and unlinks nothing (`file_removed: false`). A row without its file is
recoverable; the reverse is not.

| Status | When |
|--------|------|
| `200` | `{"status":"deleted","gen_id":…,"rel_path":…,"file_removed":true}` |
| `400` | missing `gen_id` |
| `404` | unknown `gen_id` |

### `PUT /api/generation/rate`

```json
{ "gen_id": "8f3c…", "rating": 2 }
```

`rating` is one ordinal: `-1` discard · `0` unrated · `1` keep · `2` star.
Setting `0` clears `rated_at` — a withdrawn verdict must not keep counting as
rated.

| Status | When |
|--------|------|
| `200` | `{"status":"ok","gen_id":…,"rating":…}` |
| `400` | missing `gen_id`, or a rating off the scale. A **string** `"2"` is also 400 — the value is not coerced, so a client bug surfaces instead of storing something the caller did not mean |
| `404` | unknown `gen_id` |

Feeds `keep_rate` on [`GET /api/insights`](#get-apiinsights).

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

Query: `creator`, `search`, `mode` (`text` | `semantic` — C1 cosine over taste embeddings), `unanalyzed` (`1`/`true`), `favorite` (`1`/`true`), `media_type` (`photo` | `video` | omit/`all`), `verdict` (see below), `source` (`instagram` | `x` | `reddit` | omit/`all`), `path` (exact `rel_path`; for opening one photo that is not on the current gallery page), `label` (`unlabeled` | `keep` | `discard` — B3 taste labels), `collection` (board id), `sort` (`name` | `newest` | `oldest` | `posted` | `posted_oldest` | `tier` | `foryou`), `group` (`post` | omit), `ids` (`1` — return `{rel_path, favorite}` for the whole match set, not a gallery page), `offset` (default 0), `limit` (default/max from config, typically 300).

- `source` — ANDs with `creator`, so a merged folder can be split by platform. An unregistered value is a **400**.
- `group=post` — collapse a carousel into one post. See [Post grouping](#post-grouping) below; any other value is a **400**.
- `ids=1` — return `{paths: [{rel_path, favorite}], total, truncated}` for every
  file matching the same filters, capped at `MAX_PHOTO_IDS_API` (default 10 000).
  Used by review-mode "Select all N". Grouping is ignored: selection is per file.
  The gallery page cap does **not** apply.

- `newest` / `oldest` — archive ingest time (`added_at`; when the file was downloaded/indexed).
- `posted` / `posted_oldest` — remote post time (`mtime`, which downloaders stamp to the post date); falls back to `added_at` when `mtime` is missing or zero.
- `tier` — classify tier ascending (harshest first), then errors, then never-classified. The review-mode order.
- `foryou` — B2 `p_keep` descending; unscored rows sink. Train via `POST /api/taste/train`.

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
  "rows": 60,
  "offset": 0,
  "limit": 60,
  "has_more": true,
  "sort": "newest",
  "verdict": "",
  "group": ""
}
```

- `rows` is **the paging unit** — what to add to `offset` for the next page. Ungrouped it
  equals `photos.length`; grouped it is the number of *posts* on the page while `photos`
  still carries every slide. Paging by `photos.length` when grouped skips content.

- `verdict` is **absent** on rows that have never been classified — its presence is the "has a verdict" test.
- `verdict.verdict` is derived server-side from `tier` against `CLASSIFY_REJECT_MAX_TIER` (or from `manual` when set). Clients must not re-derive it; the threshold is configurable and would drift.

- `search` matches creator, filename, cached prompt text/tags, and the **post caption**
  (plus `author` on gallery-dl sources). The caption is the only human-written text in the
  archive — hashtags, location, brand names — and everything else in the index is
  model-generated, so `#ootd` or a place name only resolves through it.
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

#### Post grouping

`group=post` collapses an Instagram carousel (and the equivalent on X / Reddit) into one
post. It is a **view** of `photos.post_id`, which is already populated and indexed —
nothing on disk moves, and there is no ingest step.

The response stays flat: a post's slides come back adjacent and in slide order, tagged
with three extra fields, and the client draws one tile per `group_key`.

| Field | Meaning |
|-------|---------|
| `group_key` | `creator/post_id`, falling back to `rel_path` when there is no post id. Creator-scoped because the three sources share no id namespace |
| `group_count` | Slides in this post **after filtering** — `media_type=photo` on a carousel with a reel in it reports the stills only |
| `group_index` | 0-based position within the post |

```jsonc
{
  "photos": [
    { "rel_path": "nadia/c2_1.jpg",  "group_key": "nadia/c2", "group_count": 11, "group_index": 0, /* … */ },
    { "rel_path": "nadia/c2_2.jpg",  "group_key": "nadia/c2", "group_count": 11, "group_index": 1, /* … */ },
    { "rel_path": "nadia/alone.jpg", "group_key": "nadia/alone.jpg", "group_count": 1, "group_index": 0 }
  ],
  "total": 2,      // posts
  "rows": 2,       // posts on this page — add this to offset
  "group": "post"
}
```

Three things worth knowing:

- **`total` and `has_more` count posts, not files.** They drive an infinite-scroll
  sentinel; a file count against a post-rendering grid drifts one page at a time and
  silently skips content. `rows` exists so the client never has to guess the unit.
- A photo with no `post_id` is a **group of one**, so there is a single code path and no
  "carousel or not" branch anywhere.
- Slides are ordered naturally, not lexicographically — slide 2 precedes slide 10.
  `group_concat` has no defined order in SQLite, so this is done in Python.
- `sort=name` orders **posts by their group key**, not by the first slide's filename —
  it is the same expression as the grouping, which is what lets the query use the index
  (S10). Identical for a photo with no post id; a carousel sorts by its post id.

`LIMIT` applies to posts, so a page of 60 can return several hundred photos. That is the
point: the lightbox walks the slides the grid never drew, and they arrive as complete
photo rows (favourite, verdict, prompt state) rather than bare paths.

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

**409** if the **Instagram** lane has pending jobs and is not paused (fairness).
Lane-scoped: these routes are Instagram-only, so a queued Reddit job does not
block them.

### `POST /api/sync/cancel`

Cooperative cancel of a running job (`saved` / `creator` / `following` /
`creator_queue`). Body is optional:

```json
{ "source": "x" }
```

`source` cancels one lane. **No body cancels every lane** — a bare "stop" from
the user means stop everything, not whichever lane happens to be first.

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
                 "media_present": true,
                 "url": "/media/_trash/<id>/IMG_1.jpg",
                 "thumb_url": "/media/thumb/_trash/<id>/IMG_1.jpg" } ],
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

`lanes` is the real shape — one entry per source, each running at most one job.
The flat keys beside it are the **union** across lanes, kept so a pre-lane client
degrades sensibly: `paused` means *nothing* can run, `running_job` is whichever
lane started first, `pending_count` is the total.

```json
{
  "lanes": {
    "instagram": {
      "source": "instagram",
      "paused": false,
      "pause_reason": "",
      "paused_at": null,
      "pending": [],
      "pending_count": 0,
      "running_job": null,
      "depth": 0,
      "finished_session": 3,
      "stats": { "completed_today": 3, "downloaded_today": 41, "errors_today": 0 }
    },
    "x":      { "source": "x", "paused": true, "pause_reason": "cookies expired", "…": "…" },
    "reddit": { "source": "reddit", "paused": false, "…": "…" }
  },
  "paused": false,
  "pause_reason": "cookies expired",
  "pending": [],
  "pending_count": 0,
  "running_job": null,
  "running_jobs": [],
  "history": [],
  "stats": { "completed_today": 3, "downloaded_today": 41, "errors_today": 0 },
  "sync": { "running": false, "progress": "", "lanes": {}, "creator_queue": {} },
  "instagram_backend": "instaloader",
  "instagram_cookies": { "mode": "none", "ready": false }
}
```

A job's `position` is its place **within its own lane**. With lanes draining in
parallel, a global position predicts nothing about when a job starts.

### `POST /api/scrape/pause` / `POST /api/scrape/resume`

```json
{ "reason": "Paused by user", "source": "x" }
```

`source` is optional: **omitted (or `all`) means every lane** — what the global
button does. A named lane pauses only that platform, which is also what an
auto-pause does (an expired X cookie must not stop Instagram). Resume clears the
pause and tries to drain that lane. An unregistered `source` is a 400.

### `POST /api/scrape/cancel`

```json
{ "job_id": "csq_…", "scope": "job" }
```

`scope: all_pending` cancels pending jobs — in one lane with `source`, in all of
them without. Optional `cancel_running: true` also cancels the running job(s) in
that scope. Cancelling by `job_id` resolves the job's **own** lane, so it never
stops a different platform that happens to also be running.

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

Same convention as `/api/scrape/status`: `lanes` is authoritative, and the flat
keys are the **Instagram lane** (the primary one, and the only one a
single-source archive has).

```json
{
  "running": false,
  "job_type": "following",
  "progress": "Complete",
  "rate_limit_hits": 2,
  "consecutive_rate_limits": 0,
  "last_backoff_sec": 120,
  "result": { "downloaded": 15, "skipped": 40, "errors": 1, "rate_limit_hits": 2, "aborted": false },
  "lanes": {
    "instagram": { "source": "instagram", "running": false, "progress": "Complete", "…": "…" },
    "x":         { "source": "x", "running": true, "progress": "Downloading…", "…": "…" },
    "reddit":    { "source": "reddit", "running": false, "…": "…" }
  },
  "running_lanes": ["x"],
  "any_running": true,
  "creator_queue": { "enabled": true, "depth": 2, "lanes": {} }
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
