# Design: Theme A — close the generation loop

| Field | Value |
|-------|--------|
| **Status** | Draft — accepted for implementation as roadmap **Phase 13–14** |
| **Date** | 2026-08-09 |
| **Problem** | The product acquires 4,400 images and can render them one at a time, through a lightbox, with one hardcoded workflow, into a directory nobody can browse. The user's verdict on the output is never captured. |
| **Source** | [`product_review.md`](product_review.md) Theme A · [`roadmap.md`](roadmap.md) Phases 13–14 |
| **Owner modules** | `comfy/client.py`, `storage/db.py`, `server/handler.py`, `app.js` |
| **Features** | A0 (new — provenance) · A1 outputs gallery · A2 batch generate · A3 rate outputs · A4 workflow registry |

---

## 1. TL;DR

Theme A is not four independent features. It is one feature — *the archive becomes a
studio* — with a prerequisite nobody has noticed:

> **The default generation path does not record the seed it used.**

`build_pro_workflow` resolves `seed=None` into a random integer **inside the builder**
(`comfy/client.py:218-220`) and never returns it. `_run_pro` then writes
`extra={"seed": seed}` from its own still-`None` parameter (`:450`). Unless the user
ticks the seed lock, **every generation in the archive is unreproducible**, and A1's
entire value proposition is provenance.

So the order is:

```
A0  fix provenance + move the index into SQLite   ← blocking, no UI
A3  rate outputs                                   ← smallest, unblocks metrics
A1  outputs gallery                                ← the visible payoff
A2  batch generate                                 ← needs S6 first
A4  workflow registry                              ← deletes the pro special-case
```

A0 is new and does not appear in the product review. It exists because writing this
design against the code turned up three defects that make A1 either wrong or
impossible, all in the same 60 lines.

---

## 2. Evidence

### 2.1 The seed is discarded on the default path 🔴 — **FIXED 2026-08-09**

```python
# comfy/client.py:204-220 — build_pro_workflow
def build_pro_workflow(*, image_name, positive, negative, seed=None, ...):
    workflow = copy.deepcopy(load_pro_workflow_template())
    if seed is None:
        seed = random.randint(0, 2**32 - 1)   # ← local; never escapes
    seed = int(seed)
```

```python
# comfy/client.py:440-452 — _run_pro
saved = self._save_outputs(
    source_rel, outputs, positive, negative,
    extra={"workflow": "pro", "denoise": denoise, "steps": steps,
           "cfg": cfg, "seed": seed,          # ← _run_pro's param, still None
           "reference": image_name},
)
```

`build_txt2img_workflow` has the identical shape at `:93-94`. The UI defaults the seed
lock to off, so this is the **normal** case, not an edge case.

Consequence: "regenerate this exact image", "regenerate with one parameter changed",
and any seed-level comparison are all impossible for the existing corpus. A1 would ship
a provenance panel whose most important field reads `null`.

**Fix (A0):** resolve the seed in the *runner*, before building. Builders become pure
functions of their arguments.

### 2.2 The generations index cannot support a gallery 🟠

`GenerationsIndex` (`comfy/client.py:261-292`) is a JSON dict, `source_rel → [records]`.

| Property | Value | Why it blocks A1/A2 |
|---|---|---|
| `add()` cost | `load()` → insert → `save()` — **full file rewrite per generation** | A2 batch of 50 rewrites the whole file 50 times |
| Ordering | per-source list only | "all generations, newest first" requires loading and merging every key |
| Retention | `items[:20]` at `:291` — **silently truncates** | A1 would display a history that the store has been quietly deleting |
| Rating | no field | A3 has nowhere to write |
| Prompt fidelity | `positive[:500]`, `negative[:300]` at `:601-602` | provenance panel shows a truncated prompt, and "regenerate" reproduces the wrong thing |

The 20-cap is the sharpest of these: it is data loss that only becomes visible once
someone builds the view that would have shown it.

### 2.3 ComfyUI jobs cannot be cancelled 🟠

`ComfyJobManager` is the only one of the five job managers with **no `_cancel` Event and
no `cancel()`** — compare `BatchPromptManager.cancel` (`prompts/batch.py:59`),
`ClassifyJobManager`, `SyncManager`, `CreatorScrapeQueue`. `_wait_for_images` polls to a
900-second deadline (`:437`) with no escape.

For a one-shot lightbox generate that is merely annoying. For A2 — a queue of 50 — it is
disqualifying.

Worth stating the contrast the repo already documents elsewhere: batch analyze is *not*
interruptible mid-item because "the Ollama call isn't interruptible, and abandoning it
mid-write would poison the prompt cache" (`roadmap.md` Phase 10). **Comfy is different.**
Nothing is written until images come back from `/view`, and ComfyUI exposes
`POST /interrupt` plus `POST /queue` deletion. In-flight cancel here is safe and should
be real, not cooperative-only.

### 2.4 `resolve_archive_file` repeats a bug that was already fixed once 🟡

```python
# comfy/client.py:251-258
full = os.path.normpath(os.path.join(SAVED_DIR, rel))
if not full.startswith(os.path.normpath(SAVED_DIR)):   # ← prefix, not containment
    raise ValueError("Invalid path")
```

This is exactly the defect `roadmap.md` Phase 9 records finding in `resolve_path` — with
base `…/InstagramSaved`, the path `../InstagramSaved_backup/x.jpg` shares the prefix and
passes. `ArchiveStore.resolve_path` (`storage/archive.py:158-173`) was fixed to compare
on path boundaries and carries a comment explaining why.

**Currently not reachable through HTTP** — `handler.py:1085` calls the *correct*
`_archive.resolve_path` before ever entering the Comfy path. But it is a latent duplicate
of a known bug in a module that A2 and A4 give new callers.

**Fix (A0):** delete `resolve_archive_file` and call `ArchiveStore().resolve_path`. One
containment check in the codebase, not two.

### 2.5 What already works and should not be rebuilt ✅

- **Thumbnails for generations work today.** `/media/thumb/` resolves through
  `_archive.resolve_path` (`handler.py:1318`), and `_generations/` lives *under* the
  archive root — so `/media/thumb/_generations/<creator>/<file>.png` already produces a
  cached JPEG. A1's grid needs **no server change** for thumbs.
- **The job chip is generic.** `renderJobChip(kind, {...})` (`app.js:4826`) keys off
  `elements[`${kind}JobChip*`]`. A2 needs new DOM ids following the convention and zero
  new chip logic.
- **`_generations` is already in `EXCLUDED_FOLDERS`** (`config.py:214-217`), so outputs
  never leak into the photo gallery or the index. Keep that.

---

## 3. Target architecture

```
                      ┌──────────────── A4 workflow registry ────────────────┐
                      │ <archive>/_workflows/<name>/{graph.json, slots.json} │
                      │ 'pro' and 'txt2img' are just the first two entries   │
                      └───────────────────────┬──────────────────────────────┘
                                              │ slot map
 photos ──selection──► A2 ComfyBatchManager ──┼──► ComfyJobManager._run(one)
            (BackgroundJob, resource='comfy') │        │ resolve seed  ← A0
                      │                       │        │ upload ref
                      │ batch_id              │        │ queue → poll → download
                      ▼                       │        ▼
              chip: renderJobChip('generate') │   _generations/<creator>/<file>
                                              │        │
                                              │        ▼
                                  A0  generations table in archive.db
                                      (one row per output file, full prompt,
                                       actual seed, batch_id, rating)
                                              │
                          ┌───────────────────┴───────────────────┐
                          ▼                                       ▼
                A1 Outputs gallery                      A3 rating (-1/0/1/2)
                filter · sort · compare · reuse         → keep-rate metric → B1
```

### 3.1 A0 — `generations` table

New table in the existing `archive.db`, following the additive-migration pattern at
`storage/db.py:75`. **One row per output file**, not per job — a workflow with a batch
node emits several images and each is independently rateable.

```sql
CREATE TABLE IF NOT EXISTS generations (
  id               INTEGER PRIMARY KEY,
  gen_id           TEXT NOT NULL UNIQUE,   -- stable public id (rating, delete, regenerate)
  rel_path         TEXT NOT NULL UNIQUE,   -- _generations/<creator>/<file>
  source_rel       TEXT NOT NULL,          -- archive photo this came from
  creator          TEXT NOT NULL,
  created_at       TEXT NOT NULL,
  batch_id         TEXT,                   -- groups one A2 run; NULL for one-shots
  workflow         TEXT NOT NULL,          -- 'pro' | 'txt2img' | <registry name>
  checkpoint       TEXT,
  seed             INTEGER NOT NULL,       -- the seed ACTUALLY used
  steps            INTEGER, cfg REAL, denoise REAL,
  mode_e           INTEGER NOT NULL DEFAULT 0,
  positive_prompt  TEXT NOT NULL,          -- full text, not truncated
  negative_prompt  TEXT,
  prompt_version   TEXT,                   -- ENGINE_ID at generation time
  rating           INTEGER NOT NULL DEFAULT 0,   -- -1 discard · 0 unrated · 1 keep · 2 star
  rated_at         TEXT,
  error            TEXT
);
CREATE INDEX idx_gen_created   ON generations(created_at DESC);
CREATE INDEX idx_gen_source    ON generations(source_rel);
CREATE INDEX idx_gen_rating    ON generations(rating);
CREATE INDEX idx_gen_batch     ON generations(batch_id);
```

`seed` is `NOT NULL` deliberately — it makes §2.1 impossible to reintroduce. The schema
enforces the invariant rather than trusting the caller.

**Migration.** On first run, import `generations_index.json` into the table: existing
records get `seed = -1` (honest "unknown", distinguishable from a real seed),
`rating = 0`, and their truncated prompts as-is. Keep writing the JSON file for one
release so a rollback is possible, then drop it. `GenerationsIndex.list_for` stays as a
thin read-through so the existing lightbox route (`/api/generations`) is untouched.

**Why this diverges from the roadmap.** `roadmap.md` puts the JSON→SQLite move (S4) in
Phase 15. That is the right place for `prompts_cache.json` — thousands of entries, a
migration with real risk. The generations index is two orders of magnitude smaller and
A1 cannot be built on it. Moving this one file early is not the same project.

### 3.2 A0 — provenance fixes

| Change | File | Note |
|---|---|---|
| Resolve seed in `_run_pro` / `_run_txt2img` before building | `comfy/client.py:396,458` | Builders become pure; `seed` param becomes required in `build_*_workflow` |
| Record full prompts | `:601-602` | Drop `[:500]` / `[:300]` |
| Record `prompt_version`, `mode_e`, `checkpoint` | `:598-607` | `mode_e` currently only exists in the handler's `mode_meta` and is never persisted |
| Delete `resolve_archive_file`, use `ArchiveStore().resolve_path` | `:251-258` | §2.4 |
| Replace `print(f"ComfyUI job error: …")` with `log.exception` | `:384` | The module already imports `get_logger` |

### 3.3 A3 — rating

One ordinal, not a boolean and a star flag:

| value | meaning | UI |
|---:|---|---|
| `-1` | discard — didn't work | `X` |
| `0` | unrated (default) | — |
| `1` | keep | `✓` |
| `2` | star — the reason you generated at all | `★` |

`PUT /api/generation/rate` with `{gen_id, rating}`. Keyboard on the A1 grid and in the
lightbox: `1`/`2`/`0`/`x`. Deliberately cheap to press — an expensive rating UI collects
no data.

**Derived metric, straight into B1:**
`keep_rate = count(rating >= 1) / count(rating != 0)` — the first end-to-end read on
whether the prompt engine produces anything the user wants. Report it per
`prompt_version`, per `workflow`, and per `checkpoint`; those three cuts answer "did the
v2-structured pipeline actually help" and "is Mode E worth it", neither of which is
currently answerable.

### 3.4 A1 — outputs gallery

A sibling nav section to the photo gallery, not a modal.

- **Grid** — `/media/thumb/_generations/…` (works today, §2.5).
- **Filters** — creator · rating · workflow · checkpoint · date range · `batch_id` ·
  *has source* (so a future pure-txt2img run doesn't break the layout).
- **Sort** — newest · rating · source photo.
- **Card** — output thumb, rating control, source thumb badge.
- **Detail** — source vs output side by side (reuse the existing `#mediaCompare` /
  `#generatedPane` machinery at `app.js:1878`), full prompt bundle, every parameter, and
  three actions:
  - **Copy parameters** — into the lightbox controls
  - **Regenerate — same seed** (change one parameter, hold everything else)
  - **Regenerate — new seed** (same prompt, different roll)

  Both regenerate actions are only meaningful because of A0. That is the whole argument
  for doing A0 first.

**Delete is permanent, no trash.** Archive media is unrecoverable, so `DELETE
/api/photo` soft-deletes. A generation with a recorded seed, prompt, and checkpoint is
**reproducible by construction** — routing it through `_trash/` would add a restore path
for something that can be regenerated in 30 seconds. Confirm dialog, then unlink + delete
the row. State this in the confirm copy so the asymmetry with photo delete is not
surprising.

### 3.5 A2 — batch generate

Built on the `BackgroundJob` base and `ResourceRegistry` from
[`review_backend_architecture.md`](review_backend_architecture.md) **S6**, declaring
`resource = "comfy"`. `roadmap.md` Phase 14 sequences S6 first for exactly this reason:
A2 is the sixth job manager, and adding it as a sixth hand-rolled singleton means another
round of pairwise `is_running()` checks in `handler.py`.

**Selection** — `paths[]` (from gallery multi-select) or `creator` + the same filter
vocabulary `/api/photos` already accepts (`favorite`, `glam_min`, `media_type`) +
`limit`. Reuse the query, don't invent a second filter language.

**Semantics**

- One `batch_id` for the run; every row carries it, so A1 can show a run as a contact sheet.
- Per-item seed is rolled independently unless pinned.
- Items with no prompt are **skipped and counted**, not auto-analyzed. Chaining batch
  analyze into batch generate is a much bigger job-composition question; report
  `skipped_no_prompt` and let the user run batch analyze first.
- Photos only — videos have no meaningful img2img reference here.

**Cancel is two-level** (§2.3):

1. Between items — cooperative, drains the remaining queue.
2. In-flight — `POST /interrupt` and drop the queued prompt from `/queue`.

Both are safe because nothing is persisted until `_download_image` returns. This is the
opposite of the batch-analyze decision, and the code comment should say why rather than
leaving a future reader to assume the inconsistency is an oversight.

**Progress** — `renderJobChip('generate', {...})`, new `#generateJobChip*` ids in
`index.html` matching the `batch`/`classify` naming. Server-side status means the chip
survives a refresh, same as the other two.

### 3.6 A4 — workflow registry

Today the pro graph is addressed through seven module-level constants
(`comfy/client.py:36-42`) and a hand-written injector (`build_pro_workflow:204-248`).
A4 replaces both with data.

```
<archive>/_workflows/<name>/graph.json    # ComfyUI "Export (API)" output
<archive>/_workflows/<name>/slots.json    # where runtime values go
```

```json
{
  "name": "flux_ref", "label": "Flux (reference)", "kind": "img2img",
  "slots": {
    "positive":        {"node": "6",  "field": "text"},
    "negative":        {"node": "7",  "field": "text"},
    "image":           {"node": "4",  "field": "image"},
    "seed":            [{"node": "9", "field": "seed"},
                        {"node": "22","field": "seed"}],
    "steps":           {"node": "9",  "field": "steps"},
    "cfg":             {"node": "9",  "field": "cfg"},
    "denoise":         {"node": "9",  "field": "denoise"},
    "checkpoint":      {"node": "1",  "field": "ckpt_name"},
    "filename_prefix": {"node": "11", "field": "filename_prefix"}
  }
}
```

`seed` accepts a list, which generalises the existing FaceDetailer special-case at
`:240-243` instead of preserving it.

**`pro` and `txt2img` become the first two registry entries**, shipped under
`comfy/workflows/` with slot maps that reproduce today's behaviour exactly. A4 therefore
*deletes* `PRO_NODE_*` and `build_pro_workflow`'s injector rather than adding a second
code path beside them. That is the test of whether A4 is designed right.

**Import flow** — upload `graph.json` → server reads node `class_type`s and proposes
slots (`CLIPTextEncode` → positive/negative, `LoadImage` → image, `KSampler*` →
seed/steps/cfg/denoise, `CheckpointLoaderSimple` → checkpoint, `SaveImage` →
filename_prefix) → user confirms or remaps in a small form → validate → save.

**Validation** — required slots for the declared `kind` are present; every referenced
node id exists in the graph; every `class_type` is known to the running ComfyUI via
`GET /object_info`. Reject with the specific missing slot, never a generic failure.

Add `_workflows` to `EXCLUDED_FOLDERS` (`config.py:214`), and to E1's export set — an
imported workflow is derived state the user would hate to re-create.

---

## 4. Phased plan

### A0 — Provenance and storage *(blocking; no user-visible change)*

- ✅ **Seed resolved once in `ComfyJobManager.start()`**, before the thread spawns, and
  passed to the graph, `_status`, and the saved record. `resolve_seed()` is the single
  place it can be materialised; both builders now take a required `seed: int` so the
  defect cannot reappear. `/api/comfy/generate` returns the resolved value instead of
  the request's, and `app.js` fills the seed box with it (that echo block existed but was
  empty — it was waiting on a value the server never sent).
- ✅ `log.exception` in the job runner, replacing `print`.
- ✅ Tests: `tests/test_comfy_seed.py` — 11 cases, including graph-seed == recorded-seed
  through a mocked ComfyUI, and the value landing in `generations_index.json`. Verified
  to fail against the pre-fix behaviour.
- ✅ `generations` table + additive migration + JSON import (`seed = -1` for legacy rows).
  Import is guarded by a `meta` key like the prompts one and runs from `ArchiveIndex`
  construction, so "first run" needs no CLI step. A malformed legacy record is skipped
  rather than abandoning the import.
- ✅ Stop truncating prompts; persist `prompt_version`, `mode_e`, `checkpoint`. All three
  are threaded `handler → start() → runner → _save_outputs`; the first two were computed
  at the call site and discarded.
- ✅ Delete `resolve_archive_file`; use `ArchiveStore.resolve_path` (§2.4).
- ✅ `GENERATIONS_KEEP_PER_SOURCE` (default `0` = unbounded) replaces the hardcoded
  `items[:20]` in the JSON index. The table is never capped by it.
- ✅ Tests: `tests/test_generations_store.py` (9) · `tests/test_generation_records.py` (7);
  the containment cases in `tests/test_path_containment.py` retargeted from the deleted
  helper onto the job entry point.

**Gate:** every new row has a real `seed` ✅; a mocked generate → read-back reproduces the
exact graph ✅; storage half ✅.

**Found while building it — a filename collision that predates the table.** Output names
were `<base>_gen_<YYYYmmdd_HHMMSS>_<n>`, a *second*-resolution stamp, so two generations of
the same source photo inside one second produced byte-identical paths and the second
overwrote the first on disk. The JSON index recorded both records pointing at one file, so
it looked like history while being loss. With `rel_path` UNIQUE the table would have
collapsed them into one row and made it visible — the stamp is now microsecond-resolution.
This is not in §2; it was surfaced by the "two generations in the same second" test written
for the 20-cap.

**Not done, and deliberately:** the `/api/comfy/generate` pass-through of `mode_e` and
`prompt_version` has no Python test. This repo has no HTTP-route harness — routes are
covered by `tests/ui/` — so the seam is tested at `ComfyJobManager.start()`, one call below
the route. Pre-existing gap, not introduced here.

**Note on the existing corpus.** Everything generated before this fix still has
`seed: null`. That is unrecoverable — the value was never written down. The migration
marks those rows `seed = -1` rather than inventing one, and A1 must show
"seed not recorded" and disable regenerate-same-seed for them.

### A3 — Rating ✅

- ✅ `rating` / `rated_at` columns (already in the A0 schema), `PUT /api/generation/rate`.
  Returning to `0` clears `rated_at`: a timestamp beside "unrated" claims a judgement
  that was explicitly withdrawn, and `rated` counts key off `rating != 0`.
- ✅ Rating control in the lightbox generated-pane, with `1` / `2` / `0` / `x`. The key
  handler runs *after* `handleTriageKey` — in review mode `X` is a reject sweep and must
  not be reinterpreted — and is inert unless the compare pane is actually open.
- ✅ `keep_rate` on `GET /api/insights`, plus `by_prompt_version` / `by_workflow` /
  `by_checkpoint` / `by_mode_e`.
- ✅ Tests: `tests/test_generation_rating.py` (15) · `tests/test_api_generation_rate.py` (9) ·
  `tests/ui/test_generation_rating.js` (19) · 7 rewritten/added in `tests/test_insights.py`.

**Gate:** rating survives restart ✅; `keep_rate` computes over a seeded fixture ✅.

**Three things this needed that §3.3 did not mention.**

1. **The record had no `gen_id`.** The lightbox rates what it just generated and has only
   the job status to work from, so `_save_outputs` now stamps each file entry with its id.
   Without it the control had nothing to send.
2. **`GET /api/generations` had to move onto the table.** It read
   `generations_index.json`, which has no rating column — so a rated output reopened with
   an empty control, which reads as "the rating was lost". It now serves from the table
   (one row per file becomes one record with a single-entry `files` list; the lightbox
   reads `gens[0]` either way). The JSON remains the rollback parachute, no longer a read
   path.
3. **Two quick presses raced.** The optimistic write rolled back on failure without
   checking whether anything had moved on, so a late 4xx from the first press overwrote
   the second press's value. Now guarded on `(gen_id, rating)` still being current — the
   same stale-response rule the gallery fetches use. **Found by the browser suite**, not
   by reasoning: the keyboard test's fire-and-forget calls landed mid-assertion and left
   the rating on a third value.

**`by_mode_e` is a divergence.** §3.3 lists three cuts and separately names "is Mode E
worth it" as a question they answer — but none of the three splits on `mode_e`, which is
its own column. Added as a fourth cut rather than leaving the stated question unanswerable.

**Also added:** `unreproducible` (rows with `seed < 0`) on the same block. Success
criterion #1 is "100% of new rows reproducible" and nothing measured it.

**A test-harness gap closed on the way.** Routes had no Python coverage at all — they were
reachable only from `tests/ui/` over CDP — so status-code mapping (400 vs 404 vs 200) was
untested. `tests/conftest.py` now has an `api` fixture: the real handler on a real socket,
port 0, session-scoped. A1 adds three more routes and A2 three more; they inherit it.

### A1 — Outputs gallery

- `GET /api/generations/list` — filter/sort/paginate, mirroring `/api/photos` query
  vocabulary and its `offset`/`limit`/`has_more` response shape.
- Outputs nav section, grid, detail view, compare, copy-params, both regenerate actions.
- `DELETE /api/generation` (permanent, confirmed).
- Keep `GET /api/generations?path=` untouched for the lightbox.

**Gate:** 1,000 generations paginate without a full-table scan; regenerate-same-seed
produces a byte-identical image on a fixed checkpoint.

### A2 — Batch generate *(after S6)*

- `ComfyBatchManager` on `BackgroundJob`, `resource="comfy"`.
- `POST /api/comfy/batch`, `GET /api/comfy/batch/status`, `POST /api/comfy/batch/cancel`.
- Real in-flight cancel via `/interrupt` + `/queue`.
- `#generateJobChip*` + `renderJobChip('generate', …)`; batch contact sheet in A1.

**Gate:** 50-item batch runs unattended; cancel stops within one item; a Comfy restart
mid-batch fails that item and continues; chip resumes after browser refresh.

### A4 — Workflow registry

- Registry loader + slot-map injector; `pro`/`txt2img` migrated onto it and the constants
  deleted.
- Import + validate UI; workflow picker in lightbox and batch.
- `_workflows` excluded and exported.

**Gate:** `pro` through the registry produces a graph byte-identical to today's
`build_pro_workflow` output for the same inputs — a regression fixture, not a judgement.

---

## 5. Success criteria

| # | Metric | Target |
|---|--------|--------|
| 1 | Generations with a recorded, reproducible seed | **100%** of new rows |
| 2 | Regenerate-same-seed reproduces the source image | byte-identical on a pinned checkpoint |
| 3 | Generations retained per source photo | unbounded (was silently 20) |
| 4 | Outputs gallery page load, 1,000 rows | < 300 ms server-side |
| 5 | Unattended batch of 50 | completes or reports per-item failure; no manual step |
| 6 | Batch cancel latency | ≤ 1 item, in-flight interrupted |
| 7 | `keep_rate` available, sliced by `prompt_version` / `workflow` / `checkpoint` | yes |
| 8 | Existing one-shot lightbox generate | behaviour unchanged (regression gate) |
| 9 | `pro` graph via registry vs `build_pro_workflow` | byte-identical (regression gate) |

(1) and (7) are the two that matter. (1) is the defect nobody had noticed; (7) is the
first time the product can answer whether any of its AI work is producing what the user
wants.

---

## 6. API changes

| Method | Endpoint | Note |
|---|---|---|
| `GET` | `/api/generations/list` | **new** — `creator`, `rating`, `workflow`, `checkpoint`, `batch_id`, `source`, `since`, `sort`, `offset`, `limit` |
| `PUT` | `/api/generation/rate` | **new** — `{gen_id, rating}` where rating ∈ {-1,0,1,2} |
| `DELETE` | `/api/generation` | **new** — `gen_id`; permanent, no trash (§3.4) |
| `POST` | `/api/comfy/batch` | **new** — `paths[]` or `creator`+filters, `limit`, workflow params |
| `GET` | `/api/comfy/batch/status` | **new** — snapshot; `pending` never recomputed per poll |
| `POST` | `/api/comfy/batch/cancel` | **new** — cooperative + in-flight interrupt |
| `GET` | `/api/workflows` | **new** (A4) — registry list |
| `POST` | `/api/workflows/import` | **new** (A4) — graph + slot map, validated |
| `GET` | `/api/generations?path=` | **unchanged** — lightbox back-compat |
| `POST` | `/api/comfy/generate` | **unchanged** contract; response gains real `seed` |

Update [`api.md`](api.md) in the same PR as each route — the existing doc is accurate and
should stay that way.

## 7. Config changes

| Env | Default | Meaning |
|-----|---------|---------|
| `COMFY_BATCH_MAX` | `200` | Cap on one batch enqueue |
| `COMFY_BATCH_ITEM_TIMEOUT` | `900` | Per-item ceiling (today's hardcoded `900` at `:437`) |
| `COMFY_WORKFLOWS_DIR` | `<archive>/_workflows` | A4 registry root |
| `GENERATIONS_KEEP_PER_SOURCE` | `0` | `0` = unbounded; replaces the hardcoded `[:20]` |

## 8. Risks

| Risk | Mitigation |
|------|------------|
| JSON→SQLite migration loses history | Import first, keep writing JSON for one release, `seed = -1` marks legacy rows honestly rather than faking a value |
| Legacy rows are unreproducible and A1 looks broken | Show "seed not recorded" explicitly on those rows; regenerate-same-seed disabled with a tooltip, not silently wrong |
| A2 lands before S6 and adds a sixth ad-hoc singleton | Sequenced in `roadmap.md` Phase 14; if S6 slips, A2 slips with it |
| `/interrupt` kills a job that is not ours | Track our `prompt_id` and only interrupt when it matches the head of `/queue` |
| Imported workflow references nodes the local ComfyUI lacks | Validate every `class_type` against `GET /object_info` at import, name the missing node |
| Unbounded generations fill the disk | `GENERATIONS_KEEP_PER_SOURCE`, plus rating makes bulk-delete of `rating = -1` a one-click cleanup |
| Batch saturates the GPU during interactive use | Single `comfy` lease (S6) already serialises it; pause/resume deferred to §9 |

## 9. Deliberately out of scope

- **Chaining batch analyze → batch generate.** Report `skipped_no_prompt` instead. Job
  composition is a separate design.
- **Pause/resume for the generate queue.** `CreatorScrapeQueue` has it because IG scrapes
  run for hours against a rate limit; a Comfy batch is minutes against local hardware.
  Cancel is enough. Revisit if batches routinely exceed an hour.
- **Video / animatediff workflows.** A4 makes them possible; a `kind: "video"` slot
  schema and the output handling are their own design.
- **Upscale and inpaint passes.** Same argument — registry entries once A4 exists.
- **Sharing or exporting generations externally.** E1 covers derived-state backup; a
  publish path is out of scope for a local tool (see `product_review.md` §3).
