# PromptStudio Development Roadmap

Agent map: [context.md](context.md). Phased plan: **scrape → organize → analyze → search → generate**.

---

## Phase 1 — Structured scraping foundation ✅

**Goal:** Reliable Instagram ingest with clean module layout.

| Deliverable | Status |
|-------------|--------|
| `promptstudio/` package (`config`, `storage`, `scraping`, `server`) | Done |
| Sync **saved posts** (`scripts/download_instagram_saved.py`) | Done |
| Sync **single creator feed** (`scripts/download_creator_feed.py`) | Done |
| Sync **following list** bulk (`scripts/download_following.py`) | Done |
| Post **metadata sidecars** (`*.meta.json` per image) | Done |
| **Organize + dedupe** utilities in package | Done |
| API: `POST /api/sync/saved`, `POST /api/sync/creator`, `GET /api/sync/status` | Done |
| UI: Instagram Sync modal in navbar | Done |

---

## Phase 2 — Prompt pipeline & search ✅

**Goal:** Automate analysis and make the archive searchable by prompt content.

| Deliverable | Status |
|-------------|--------|
| Prompt cache keyed by `creator/filename` (backward compatible) | Done |
| `GET /api/photos?search=` searches prompt text + tags | Done |
| `GET /api/stats` includes `prompts_ready` | Done |
| `POST /api/prompt/batch` background batch generation | Done |
| UI: batch analyze button + sync status polling | Done |

---

## Phase 3 — Smarter scraping ✅

| Deliverable | Status |
|-------------|--------|
| Following sync UI (max accounts/posts + bio keywords) | Done |
| `promptstudio/scraping/filters.py` bio/media filters | Done |
| Biography field on `export_following_list.py` | Done |
| Resume checkpoints (`sync_state.json`) | Done |
| Exponential rate-limit backoff + SyncManager counters | Done |
| `group_by_post_id()` carousel metadata helper | Done |

---

## Phase 3b — Anti-ban following crawler (Phase A) ✅

| Deliverable | Status |
|-------------|--------|
| Persistent `following_queue.json` with daily budget | Done |
| Randomized post / account / batch pauses | Done |
| Hard abort on rate-limit streak or abuse signals | Done |
| CLI `--accounts-per-day` + abort exit code | Done |
| Sync UI default 20 accounts/day + abort status | Done |

---

## Phase 3c — Idempotent downloads ✅

| Deliverable | Status |
|-------------|--------|
| SQLite `post_id` / `shortcode` identity index | Done |
| Skip already-archived posts (not filename-only) | Done |
| Catch-up streak stop (`IG_CATCH_UP_STREAK`) | Done |
| Keep local when IG deletes; re-fetch after local delete | Done |
| Shortcode-aware archive dedupe | Done |

---

## Phase 4 — Prompt quality ✅
| Deliverable | Status |
|-------------|--------|
| Two-stage pipeline: structured vision JSON → intensity-aware rewrite | Done |
| Cache version bump (`pipeline_version: v2-structured`) | Done |
| Creator style blocks (`creator_styles.json`) | Done |
| Export variants: Flux / SDXL / Pony + UI copy buttons | Done |

---

## Phase 5 — Scale & UX ✅

| Deliverable | Status |
|-------------|--------|
| Thumbnails via `/media/thumb/...` (Pillow or OpenCV) | Done |
| Paginated gallery (`offset` / `limit` + infinite scroll) | Done |
| SQLite `archive.db` photo catalog (list/filter/sort) | Done |
| In-memory write-through prompt + favorites caches | Done |
| Per-creator **Classify unscored** job + Rejects review/delete UI | Done |

---

## Phase 6 — ComfyUI loop ✅

| Deliverable | Status |
|-------------|--------|
| `ThreadingHTTPServer` (gallery stays responsive during sync/batch/Comfy) | Done |
| Comfy client: Pro `modelToimage_pro` + txt2img fallback | Done |
| APIs: `/api/comfy/generate`, `/api/comfy/status`, `/api/generations` | Done |
| Mode E rewrite + denoise/steps/CFG/seed controls | Done |
| Side-by-side Original vs Generated in lightbox | Done |
| Health includes ComfyUI reachability | Done |

**CLIP?** Not needed for the generate → Comfy → compare loop. Tags + prompt search cover discovery. Defer until you want “find visually similar photos.”

---

## Phase 7 — Delete safety & optimistic gallery ✅

**Goal:** make the reject-review cleanup loop non-destructive and stop reloading the whole app on every mutation.

| Deliverable | Status |
|-------------|--------|
| `promptstudio/storage/trash.py` — `_trash/<entry_id>/` + `entry.json` manifest | Done |
| Restore returns file, sidecar, prompt bundle, favorite, index row, un-tombstones | Done |
| APIs: `GET /api/trash`, `POST /api/trash/restore`, `POST /api/trash/purge`, `?permanent=1` | Done |
| Trash modal (restore / delete forever / empty / purge expired) + nav count badge | Done |
| Undo toast after single + bulk delete (`trash_id` round trip) | Done |
| Optimistic gallery removal — no `initApp()`, scroll and loaded pages survive | Done |
| Bulk delete progress counter; confirm copy says "Move to Trash" + flags favorites | Done |

---

## Phase 8 — Frontend correctness pass ✅

**Goal:** stop the gallery from disagreeing with the active filters, and stop trusting third-party text.

| Deliverable | Status |
|-------------|--------|
| `escapeHtml()` applied to every dynamic `innerHTML` site (handles, filenames, tags, prompt history, IG `full_name`, queue rows) | Done |
| Search debounced 250 ms (`debounce()`), `Enter` bypasses it | Done |
| `AbortController` on photos / creator-style / following fetches — newest request wins | Done |
| Removed the `photosLoading` guard that silently dropped filter changes | Done |
| Offset committed only on response, so aborts can't corrupt paging | Done |
| Search spinner + `aria-busy` on the grid while fetching | Done |
| Video detection unified on `isVideoFilename()` | Done |

---

## Phase 9 — Test harness & CI ✅

**Goal:** stop verifying by hand. Cover the code where a silent failure is expensive.

| Deliverable | Status |
|-------------|--------|
| `pyproject.toml` — pytest + ruff config (bug-focused rules, not style churn) | Done |
| `requirements-dev.txt` | Done |
| 107 unit tests: path containment, Range parsing, multipart, filters, trash | Done |
| `tests/ui/` — 70 browser checks over CDP, no npm deps (Node 22+ `WebSocket`) | Done |
| `.github/workflows/ci.yml` — lint + tests on 3.10/3.13 + UI job | Done |
| **Fixed:** `resolve_path` prefix check let paths escape the archive | Done |

**Found by writing the tests:** `resolve_path` used `full.startswith(base)`, which is
not containment — with base `…/InstagramSaved`, the path `../InstagramSaved_backup/x.jpg`
normalized to a sibling directory and resolved. Every media route
(`/media/…`, `/api/media/detail`, `DELETE /api/photo`) goes through it, and CORS is `*`,
so a page in the browser could read files from a prefix-sharing sibling — and with soft
delete, move them. Now compared on path boundaries; `tests/test_paths.py` covers it.

---

## Phase 10 — Job feedback & control ✅

**Goal:** make long jobs interruptible and stop reporting progress by toast spam.

| Deliverable | Status |
|-------------|--------|
| Cancel for `BatchPromptManager` + `POST /api/prompt/batch/cancel` | Done |
| `#jobChipStack` — one chip per job kind, progress bar + cancel | Done |
| Batch + classify progress toasts dropped to start/finish only | Done |
| `/api/prompt/batch/status` no longer calls `list_uncached()` per poll | Done |
| Batch chip resumes after a browser refresh (jobs live server-side) | Done |

Cancel is cooperative and checked *between* items: the in-flight Ollama call
isn't interruptible, and abandoning it mid-write would poison the prompt cache.

---

## Phase 11 — Hot-path cost ✅

| Deliverable | Status |
|-------------|--------|
| `prompts_ready` from the indexed `has_prompt` column in one SQL aggregate | Done |
| `/api/stats` no longer walks the archive or loads the prompt cache | Done |
| `count_prompts_ready()` kept as the exact reference (asserted equal in tests) | Done |

---

## Phase 12 — Polish ✅

| Deliverable | Status |
|-------------|--------|
| View prefs (sort / media / grid / filter chips) persisted to `localStorage` | Done |
| `gridSize` promoted from a bare DOM class into state so it can be restored | Done |
| Restored prefs applied *before* the first `/api/photos` request | Done |
| Skeleton cards + `aria-busy` while the first page loads (not on append) | Done |
| Segmented sync-mode radio replaces 3 contradictory checkboxes | Done |
| `catch_up_only` finally reachable from the UI | Done |

Navigation state is deliberately **not** restored — selected creator, selection,
and reject-review mode all start clean, so a refresh can't drop you into a
destructive mode.

---

Phases 1–12 built the **acquisition** half. Phases 13–15 build the half that
justifies it. Source: [product_review.md](product_review.md) — the value chain is
`scrape → organize → analyze → search → generate`, and `scraping/` carries 3.1×
the LOC of `prompts/` + `comfy/` combined. Feature IDs below (A1, B2, …) map to
that document; S-numbers map to [review_backend_architecture.md](review_backend_architecture.md).

---

## Phase 12b — Backend durability, observability & dedupe ✅

**Goal:** stop losing derived state, make failures visible, and remove the
guesswork from job contention. Findings and measurements:
[review_backend_architecture.md](review_backend_architecture.md).

| Deliverable | ID | Status |
|-------------|----|--------|
| `storage/atomic.py` — atomic writes for all ten state files | S1 | Done |
| HTTP error boundary — JSON 500 + logged traceback, not a dropped socket | S2 | Done |
| `logging_setup.py` — rotating file log; every `print()` converted | S3 | Done |
| Prompts into a `prompts` table (JSON imported once, kept as rollback) — **7x faster saves**, filename-collision bug gone | S4 | Done |
| FTS5 index over prompt text — built and maintained, **search left on LIKE** | S5 | Built, off |
| `jobs.py` resource leases (`ollama` / `instagram` / `comfy`) — closes the start race | S6 | Done |
| WAL + `busy_timeout`; `read_sidecar()` — **4 reads/photo → 1**, rebuild 2.15s → 0.70s | S8 | Done |
| `storage/journal.py` — append-only run history + `GET /api/journal` | F2 | Done |
| Perceptual-hash near-duplicate detection + `scripts/find_duplicates.py` | F1a | Done |
| Router refactor — 36-branch if-chain into a route table | S7 | Deferred |
| Embeddings (SigLIP 2 + sqlite-vec) for semantic search / kNN pre-scoring | F1b | Todo |

**Two recommendations were reversed by measurement**, and both are recorded
rather than quietly dropped: FTS5 lost 3x to the LIKE scan on common query
terms, and incremental rebuild became pointless once the redundant sidecar reads
were removed. Neither is shipped on.

**S7 is deferred on purpose.** It is maintainability-only — no correctness or
performance payoff — and the review's own advice was to do it incrementally as
routes are touched rather than as a big-bang restructure.

---

## Phase 13 — Instrument, then close the loop 🔜

**Goal:** measure whether the pipeline works, stop losing what it produces, and
let the user see and judge output for the first time.

Theme A items are specified in
[design_generation_loop.md](design_generation_loop.md).

| Deliverable | ID | Status |
|-------------|----|--------|
| Quality dashboard — prompt edit rate, regenerate rate, score distribution per `prompt_version` (`GET /api/insights`) | B1 | **Done** |
| **Provenance** — record the seed actually used (`resolve_seed`, required builder arg, returned by the API, echoed in the UI) | A0 | **Done** |
| **Storage** — `generations` table in `archive.db`, full prompts, drop the 20-per-source cap, retire `resolve_archive_file` | A0 | Todo |
| **Atomic JSON writes everywhere** — `storage/atomic.py`, applied to all ten writers | S1 | **Done** |
| **Top-level error boundary** — unhandled route errors return JSON 500 instead of dropping the connection | S2 | **Done** |
| **`logging` + rotating file handler** — 34 `print()` converted; `except: pass` sweep still open | S3 | **Done** |
| `export --derived` / import — prompts, glam scores, favorites, styles, labels, generation index | E1 | Todo |
| Rate generations (keep / discard / ⭐) — `PUT /api/generation/rate` | A3 | Todo |
| Outputs gallery — `GET /api/generations/list`, filter by creator/date/checkpoint/rating, full provenance | A1 | Todo |

**B1 first.** `parameters.manual_edit` (`handler.py:358`) and prompt `history`
have been written since Phase 4 and read by nothing — edit rate is a free,
already-instrumented measure of prompt quality. Phase 13 is cheap to justify
only *after* it exists.

**A0 before A3 and A1.** `build_pro_workflow` resolves `seed=None` into a random
integer inside the builder (`comfy/client.py:218-220`) and never returns it, so
`_run_pro` records `seed: null` on every generation where the user did not tick
the seed lock — which is the default. Until that is fixed, the outputs gallery
would ship a provenance panel whose most important field is empty and a
"regenerate" button that cannot reproduce anything. `_save_outputs` also
truncates prompts to 500/300 chars and the index silently caps history at 20 per
source. Full evidence in
[design_generation_loop.md](design_generation_loop.md) §2.

**A3 before A1**, so the outputs gallery ships with ranking rather than gaining
it later.

---

## Phase 14 — Studio usable at archive scale 🔜

**Goal:** generation becomes a batch operation, and the single hardcoded
workflow stops being the ceiling.

| Deliverable | ID | Status |
|-------------|----|--------|
| Batch generate queue — `POST /api/comfy/batch` + status/cancel, `renderJobChip('generate', …)` | A2 | Todo |
| Custom `workflow_api.json` import + slot-mapping UI (prompt / negative / seed / image) | A4 | Todo |
| Post / carousel grouping in the gallery | C2 | Todo |
| Pass-rate badge per filter + CI failure when one bucket exceeds 60% of outputs | B4 | Todo |

Clone the `BatchPromptManager` shape for A2 — cooperative cancel, progress chip
and resume-after-refresh are already built and tested; only the runner changes.
A2 must respect the existing single-flight rule on the Comfy resource.

**A2 is the sixth job manager.** The lease half of
[review_backend_architecture.md](review_backend_architecture.md) S6 is **done**
(`promptstudio/jobs.py`), so A2 declares `comfy` and acquires it rather than
adding another round of pairwise `is_running()` checks. The `BackgroundJob`
base class is still deferred — A2 is the point where extracting it finally pays,
since it would be the fifth copy of the same singleton/status/cancel scaffolding.

C2 is near-free: `post_id` is in every sidecar and `group_by_post_id()` already
exists at `storage/metadata.py:100`; only the gallery ignores it.

B4 becomes standing policy from here — Phase 5's Sexy filter reached 92% pass
rate with nothing to notice.

---

## Phase 15 — Taste as the durable asset 🔜

**Goal:** stop hand-encoding a preference function in English. Learn it from the
labels the product has been discarding, and mine the same embeddings three ways.

| Deliverable | ID | Status |
|-------------|----|--------|
| Rapid labeling mode + `labels` table, seeded from `photos.favorite` and `_trash/` | B3 | Todo |
| SigLIP-2 embeddings cached in `archive.db` + logistic head → calibrated `P(keep)`, "For You" sort | B2 | Todo |
| Semantic search — text→image over sqlite-vec, `?search=…&mode=semantic` | C1 | Todo |
| Near-dup collapse — `phash` column first, embedding kNN second | C3 | Todo |
| Faceted attributes (setting / outfit / framing / pose) as columns + filter chips | C5 | Todo |
| Collections / saved views — cross-creator boards, saved filter sets | C4 | Todo |
| Activity view over the run journal (JSONL per job kind) | E2 | Todo |

**One embedding job serves B2, C1 and C3.** Build it once. This retires the
Phase 6 note ("CLIP? Not needed…") — that call was right for the Comfy loop and
wrong for everything else, because the cost side changed: sqlite-vec drops into
the `archive.db` that already exists, no server.

The VLM is **demoted, not removed** — it keeps `brief_reason` and the ~10–20% of
items near the decision boundary. Rationale in
[research_glam_classifier.md](research_glam_classifier.md) §3.5.

B3 gates B2 (labels are the training set) and is also
`design_reel_classifier_v2.md` P0 — one labeling harness, both consumers.
E2 depends on S3 landing in Phase 13. C5 rides free on a vision call already
being made.

---

## Interleaved engineering work

From [review_backend_architecture.md](review_backend_architecture.md), placed
rather than left floating:

| Item | Where |
|------|-------|
| S1 · S2 · S3 — atomic writes, error boundary, logging | Phase 13, as one "make failures visible" PR |
| S6 — `BackgroundJob` + resource lease | **Before A2** in Phase 14 |
| S4 · S5 — prompt cache → SQLite table, FTS5 search | Phase 15, folded into the storage PR that adds embeddings |
| S8 — `journal_mode=WAL`, `busy_timeout`, mtime-diffed `rebuild()` | Whichever storage PR lands first |
| S7 — route table, `handler.py` decomposition | Continuous, inside feature PRs only — never standalone |

`app.js` (5,074 lines) has the same monolith problem as `handler.py` and gets
the same treatment on the same terms.

---

## Deliberately not building

Recorded so it is not relitigated — full reasoning in
[product_review.md](product_review.md) §7.

- **Onboarding investment** (first-run wizard, doctor, demo mode, settings UI for
  the 89 env vars) — cut by owner. Revisit only if the distribution intent changes.
- **A fourth scrape source** — three is enough; more widens the gap Phases 13–15 close.
- **Classifier prompt tuning before the eval set exists** — that is what produced
  85% saturation.
- **Auth / multi-user / hosting** — ToS constraint makes it a dead end.
- **A general-purpose vector DB** — sqlite-vec in `archive.db`, no new process.

---

## Future (optional)

- FastAPI async job queue (partially covered by threaded HTTP + background jobs)
- Content-addressed thumbnails so renames and re-scores don't orphan `_thumbs/`

---

## Project layout

```
promptstudio/
├── config.py
├── storage/     archive db favorites metadata thumbs
├── scraping/    session downloader filters queue checkpoints
│                organizer sync_manager outfit_classifier
├── prompts/     cache engine styles batch comfy_mode
├── comfy/       client + workflows/modelToimage_pro.api.json
└── server/      handler multipart

scripts/                   # Thin CLI wrappers
server.py / prompt_engine.py
```

See [context.md](context.md) for the full task→file map.

---

## CLI quick reference

```powershell
py scripts/download_instagram_saved.py
py scripts/download_creator_feed.py roxeuoon --max-posts 50
py scripts/export_following_list.py
py scripts/download_following.py --accounts-per-day 20 --max-posts 30 --keywords ""
py scripts/organize_and_filter.py
py scripts/deduplicate.py
py server.py
```
