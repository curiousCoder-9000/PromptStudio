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
| Per-creator **Classify unscored** job + Rejects review/delete UI | Superseded — removed in `1cc0f44`, rebuilt as Phase 12c |

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
| Batch progress toasts dropped to start/finish only | Done |
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

## Phase 12c — Keep/reject classification, rebuilt ✅

**Goal:** get the per-creator "classify this folder, then let me delete the
failures" loop back, without re-importing the flaw that killed the first one.

| Deliverable | Status |
|-------------|--------|
| `media_verdicts` table — stores the **0–4 tier only**; keep/reject derived at query time | Done |
| `scraping/media_classifier.py` — ordinal photos + whole-timeline reel contact sheets | Done |
| `scraping/classify_job.py` — `ollama` lease, cooperative cancel, run journal | Done |
| APIs: `/api/classify/{start,status,cancel,verdict,sheet}`, `?verdict=` on `/api/photos` | Done |
| Sidebar keep/reject/to-do meter + job chip | Done |
| Review mode: verdict badges, reject tinting, `Unusable`/`Modest` split, select-non-favourites | Done |
| Triage lightbox: tier, reason, contact sheet, `K`/`R`/`X` keys, manual override | Done |
| Per-run tier histogram + `top_tier_share` in the journal and `/api/insights` | Done |

Two properties make this different from the Phase 5 version, and both are the
direct lesson of its removal:

1. **Only the measurement is stored.** `CLASSIFY_REJECT_MAX_TIER` re-thresholds
   the whole archive at query time. The old `glam_score` baked the policy into a
   column, so six changes of mind cost six full-archive rescores and produced six
   incomparable archives.
2. **The distribution is surfaced.** `top_tier_share` per run and archive-wide.
   The v2 prompt shipped at 85% on one value — a filter that is a no-op — and
   nothing was measuring it.

Scope is deliberately **triage, not ranking**. "Show me my best 50" still needs
the learned-preference work in `design_sexy_score_v2.md`; the manual-override
column here is what produces the labelled pairs that design needs. See
[design_media_classifier.md](design_media_classifier.md).

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

## Phase 12d — UI hardening (Stage 1) ✅

**Goal:** fix what a UI review found that neither earlier review covered — starting with
one finding that invalidates a documented assumption.
Source: [review_ui_product.md](review_ui_product.md).

| Deliverable | ID | Status |
|-------------|----|--------|
| **Bind to loopback**, including when `PROMPTSTUDIO_HOST` is set-but-empty; log the address actually bound and warn on exposure | §0 | Done |
| Fonts + Font Awesome vendored into `assets/`; both CDN links dropped; `scripts/vendor_web_assets.py` regenerates and `--check`s them | U1 | Done |
| Classify tier distribution rendered in the Insights modal — reject rate, `top_tier_share` with a saturation warning, per-tier bars, separate failure row | U3 | Done |
| Six pollers pause on a hidden tab and resume with an immediate refresh | E1 | Done |
| `tests/test_bind_host.py` (8) · `tests/test_offline_assets.py` (5) · `tests/ui/test_insights_and_pollers.js` (27) | — | Done |

**§0 is the one that mattered.** `product_review.md` §3 accepted "no auth, CORS `*`" as
correct *for localhost-only*. `HOST` defaulted to `""`, which binds every interface, and the
startup log printed "localhost" regardless. The empty case is the whole bug — `.env.example`
shipped `PROMPTSTUDIO_HOST=`, and `os.environ.get(name, default)` returns `""` for a
set-but-empty variable, so changing the default alone would have fixed nothing for anyone
who copied the template. Now AGENTS.md rule 14.

**U3 is the one that stung.** `top_tier_share` — the metric
[design_media_classifier.md](design_media_classifier.md) §5 calls "the one number to check
after the first real run" — was computed, journalled and served over HTTP while
`renderInsights` read only `data.prompts` and `data.generations`. Exactly the blind spot
Phase 12c existed to close.

Stage 2 (captions searchable, tier as a browse axis, archive-wide classify, gallery
virtualization) is tracked in [backlog_features.md](backlog_features.md) and
[backlog_engineering.md](backlog_engineering.md).

---

## Phase 12e — UI hardening (Stage 2) ✅

**Goal:** surface work that was already built but unreachable, and pay down the
one render cost that scales with archive size.
Source: [review_ui_product.md](review_ui_product.md) §6.

| Deliverable | ID | Status |
|-------------|----|--------|
| `scripts/benchmark_queries.py` — times the gallery hot paths on a synthetic archive, same methodology as S5 | E5b | Done |
| **Captions searchable** — `caption_search` column, populated with no extra file reads, backfilled for existing archives | F1 | Done |
| **Tier + verdict as browse axes** — `sort=tier` in the dropdown, verdict filter outside review mode, persisted as a view pref | F5/U4 | Done |
| **Archive-wide classify** — `creator=""` everywhere, navbar `Classify All (N)`, review mode over all creators | F2/U5 | Done |
| **Gallery render cost** — `content-visibility` + `IntersectionObserver` paging | U2 | Done |
| `tests/test_caption_search.py` (17) · `tests/test_classify_all_creators.py` (12) · `tests/ui/test_browse_and_paging.js` (17) | — | Done |

**Measured, per rule 13.** `content-visibility` at 780 cards: full re-render
69.2 ms → **27.2 ms**, grid-size relayout 18.5 ms → **2.7 ms**, and
`scrollHeight` unchanged at 93,091 px — the last number is the one that mattered,
because `contain-intrinsic-size` getting it wrong would move the scrollbar.
Captions cost **+1.1 ms** on worst-case search at 4,400 rows.

**Not done:** true windowing. The DOM-node count is unchanged; this buys layout
and paint, not memory. Windowing would break selection, keyboard nav and Ctrl-F,
so it waits for a measurement saying the node count itself is the problem.

**F1 diverged from its own proposal** and the doc was corrected rather than the
code bent to match: captions went into a separate `caption_search` column, not
into `prompt_search`, because the caption is fixed for the life of the file while
the prompt blob is rewritten on every regenerate.

---

## Instagram gallery-dl backend (opt-in) ✅

`IG_BACKEND=gallery-dl` keeps Instaloader in tree and switches the existing
`instagram` source (same folders, same `photos.source`) onto gallery-dl +
browser cookies / `IG_COOKIES_FILE`. `user-strategy` is pinned to `search,web`
so we never call `web_profile_info`. Default remains Instaloader.

---

## Phase 12f — Multi-source: sources, filter, lanes ✅

**Goal:** make the archive genuinely multi-platform — scrape beyond Instagram,
browse by provenance, and stop one platform's scrape from serialising or
cancelling another's.

Specs: [design_source_filter.md](design_source_filter.md) ·
[design_scrape_lanes.md](design_scrape_lanes.md) ·
reference [multi_source_scraping.md](multi_source_scraping.md).

| Deliverable | Where | Status |
|-------------|-------|--------|
| `MediaSource` interface + `_REGISTRY`; X and Reddit via **gallery-dl** as a subprocess | `737c409` | Done |
| `photos.source` + `deleted_posts.platform` — post identity scoped per platform | `737c409` | Done |
| Per-platform archive folders (`handle__x`, `r_sub__reddit`), `SCRAPE_FOLDER_SUFFIX=0` to merge | `737c409` | Done |
| **Source as a view filter** — `?source=` on `/api/photos` + `/api/creators`, registry-driven pills, `sourceFilter` view pref | `2697495` | Done |
| Creator rollup regrouped `GROUP BY creator, source`; `sources` map stays unfiltered | `2697495` | Done |
| gallery-dl sync badges — folder-keyed `SyncCheckpoints` write | `2697495` | Done |
| `checkpoints.py` load→mutate→save lock (prerequisite for lanes) | `2697495` | Done |
| **Per-source scrape lanes** — `scrape:<source>` leases, `ScrapeLane`, lane-scoped cancel / pause / status / pacing | `6284867` | Done |
| v2 `creator_scrape_queue.json` with a `lanes` block; v1 and flat `sync_status.json` migrate in place | `6284867` | Done |
| One chip per lane, each with its own Cancel and Pause | `6284867` | Done |
| Lane lifecycle fixes — lease release on refusal, global pause coverage, per-lane cap, `paused` union | `3114e83` | Done |
| `test_source_filter.py` · `test_scrape_lanes.py` · `test_sources.py` · `test_source_dispatch.py` · `tests/ui/test_source_filter.js` · `tests/ui/test_scrape_lanes.js` | — | Done |

**The source layer shipped before the phase that names it.** `737c409` landed on
2026-08-09, ahead of the classifier and both UI-hardening stages, and was never
recorded here — which is why the filter and lane work below reads as sudden. The
row exists so the sequence is legible, not to claim it as new.

**Provenance is a column, never a folder name.** The `__x` / `__reddit` suffix is
right for most rows and silently wrong for the two cases the feature exists to
serve: `SCRAPE_FOLDER_SUFFIX=0` merges platforms into one folder on purpose, and
any folder can hold manual uploads. A folder is a *location*; `photos.source` is
the *provenance*. This is the one rule that shaped the whole filter design.

**One line was the entire concurrency bug.** Every running source bound to a
process-wide cancel Event (`should_cancel=self.is_cancel_requested`), so
cancelling X killed Reddit with it. Lanes are mostly bookkeeping around replacing
that with `lane.is_cancel_requested`.

**Measured, per rule 13.** The `GROUP BY creator, source` regroup costs
**1.4 ms → 2.9 ms** at 4,400 rows in the harness's worst case, where every
creator folder is round-robined across all three sources — 3× the groups a real
archive produces. Full table in [design_source_filter.md](design_source_filter.md) §9.1.

**Instagram's pacing was not relaxed.** Lanes make the *other* sources faster.
Instagram stays pinned to one job forever, keeps `IG_ACCOUNT_PAUSE_*` and
`IG_BATCH_*`, and its batch-pause counter became per-source so a finishing Reddit
job can no longer trigger it.

**What the follow-up caught,** all one root cause — lanes are created lazily and
the spec reasoned about lanes that already exist. A global pause recorded only
Instagram, so enqueueing X seconds later found a fresh unpaused lane and started
scraping. Pausing one lane made the flat `paused` key read true for the whole
queue. The pending cap was shared, so a full Instagram queue blocked an idle
Reddit lane. And the lane lease leaked on every refusal path that was not
contention. Detail in [design_scrape_lanes.md](design_scrape_lanes.md) §12.

---

## Phase 13 — Instrument, then close the loop ✅

**Goal:** measure whether the pipeline works, stop losing what it produces, and
let the user see and judge output for the first time.

Theme A items are specified in
[design_generation_loop.md](design_generation_loop.md).

| Deliverable | ID | Status |
|-------------|----|--------|
| Quality dashboard — prompt edit rate, regenerate rate, score distribution per `prompt_version` (`GET /api/insights`) | B1 | **Done** |
| **Provenance** — record the seed actually used (`resolve_seed`, required builder arg, returned by the API, echoed in the UI) | A0 | **Done** |
| **Storage** — `generations` table in `archive.db`, full prompts, drop the 20-per-source cap, retire `resolve_archive_file` | A0 | **Done** |
| **Atomic JSON writes everywhere** — `storage/atomic.py`, applied to all ten writers | S1 | **Done** |
| **Top-level error boundary** — unhandled route errors return JSON 500 instead of dropping the connection | S2 | **Done** |
| **`logging` + rotating file handler** — 34 `print()` converted; `except: pass` sweep still open | S3 | **Done** |
| `export --derived` / import — prompts, favorites, styles, verdicts, phashes, generations | E1 | **Done** |
| Rate generations (keep / discard / ⭐) — `PUT /api/generation/rate`, `keep_rate` on `/api/insights` | A3 | **Done** |
| Outputs gallery — `GET /api/generations/list`, filter by creator/date/checkpoint/rating, full provenance | A1 | **Done** |

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

**What A0 turned out to be worth.** The seed defect was the headline, but three
more fell out of the same 60 lines once something read them: output filenames
used a *second*-resolution stamp, so two generations of one photo inside a
second silently overwrote each other on disk; `GET /api/generations` had to move
onto the table because the JSON index has no rating column, so a rated output
reopened blank; and unknown `POST`/`DELETE` routes answered **500** rather than
404, because `super().do_POST()` does not exist on `SimpleHTTPRequestHandler`.
None appear in the design.

**E1 diverged from its own spec, because the spec predates two decisions.**
`product_review.md` E1 lists "glam scores" (the whole subsystem was removed in
`1cc0f44`) and "labels" (B3, Phase 15, not built). What ships covers what exists
and is expensive: prompts, favourites, styles, **verdicts**, phashes and
generations. Verdicts are the addition that matters — Phase 12c made them the
second GPU-hours-expensive thing in the archive, and E1 was written before it.
`photos` is deliberately **not** exportable: it is an index *of* the media and
`rebuild()` re-derives it, so restoring it would resurrect rows for files that
are not on disk.

**Not met, and recorded rather than glossed:** the second half of A1's gate,
"regenerate-same-seed produces a byte-identical image", needs a running ComfyUI
and a pinned checkpoint. The wiring is tested; the image equality is not.

The B4 pass-rate gate and E5a's saturation check remain Todo in Phase 14 and
[backlog_engineering.md](backlog_engineering.md) — `keep_rate` now exists for
them to gate on, which it did not before.

---

## Phase 14 — Studio usable at archive scale ✅

**Goal:** generation becomes a batch operation, and the single hardcoded
workflow stops being the ceiling.

| Deliverable | ID | Status |
|-------------|----|--------|
| `BackgroundJob` base class, three managers migrated | S6 | **Done** `cebba18` |
| Batch generate queue — `POST /api/comfy/batch` + status/cancel, `renderJobChip('generate', …)` | A2 | **Done** `71e6037` `855e421` |
| Workflow registry — `graph.json` + `slots.json`, `pro`/`txt2img` migrated, picker | A4 | **Done** `a52b939` — import UI deferred |
| Post / carousel grouping in the gallery | C2 | **Done** `326e242` |
| Pass-rate badge per filter + a failing check when one bucket exceeds 60% | B4 · E5a | **Done** `9a4cef3` |

**924 pytest + 2 skipped, 495 browser checks across 14 suites, ruff clean.**
The 2 skips are E5a's real-archive gates, inert until 100 items are classified.
One pre-existing failure remains: `test_sort_newest`'s macOS APFS artifact
(`st_birthtime` clamps to `mtime`), which does not apply to the Windows target.

**S6 shipped scoped, not whole.** `BatchPromptManager`, `ClassifyJobManager` and
the new `ComfyBatchManager` are on `BackgroundJob`; `SyncManager` and
`CreatorScrapeQueue` deliberately are not — pause/resume, multi-day pacing and
per-lane queue state are a different shape, and widening the base to cover them
would have meant deleting scaffolding the review itself called cosmetic at the
cost of an abstraction that describes nothing.

The migration found a **third lease bug** of the same family as the two the
lease work turned up, present in every hand-rolled manager: acquire-then-check-
`running` meant a duplicate start request released the lease out from under the
job still holding it, and the next contender sailed through. Details in
[review_backend_architecture.md](review_backend_architecture.md) S6.

**A2 also forced two extractions**, and they were most of the work:
`comfy/params.py` (the ninety inline lines of prompt assembly that lived in
`/api/comfy/generate` and had no test coverage at all) and `comfy/runner.py`
(`ComfyRunner` — upload, queue, poll, download, save, index). One-shot and batch
now share one per-item code path instead of two that would have drifted.

Mode E turned out to make "has this photo been analyzed" undecidable from the
resolved prompt: its fallback fills an empty one with a generic styling string.
That is right for img2img, where the reference image carries the subject, but
reading the text would have batched every unanalyzed photo through one string.
`GenerationParams.has_prompt_source` is what A2 skips on.

**C2 was near-free to build and is not free to run.** `photos.post_id` was
already a populated, indexed column, so it needed no ingest step and no use of
`group_by_post_id()` at `storage/metadata.py:91` (which walks sidecars per
creator and would have been a filesystem scan). But grouping is a full pass by
construction — an exact post count has to visit every matching row, while the
flat query rides an index and stops at 60. Measured: **3x slower at 4 400 files,
5x at 40 000**, or 10 ms vs 3 ms at the real archive size. Off by default, and
the numbers are in
[review_backend_architecture.md](review_backend_architecture.md) S10 along with
the two formulation changes that halved it.

The thing that had to be got right was not the grouping but the **paging**:
`total` and `has_more` feed an infinite-scroll sentinel, so the response counts
posts while `photos` still carries every slide, and names the unit (`rows`) so
the client never has to infer it.

**A4 deleted the thing it replaced**, which was the test of whether it was
designed right: `PRO_NODE_*` and both hand-written injectors are gone and
`client.py` is 154 lines lighter. The gate — a registry-built `pro` graph
byte-identical to the old builder's — was written before anything was removed,
against fixtures captured from those builders, and re-verified afterwards by
regenerating both from `401dce4` and diffing. A golden fixture is only a gate
if it predates the code it guards.

The `seed` slot takes a list, which generalises the FaceDetailer special-case
rather than preserving it: two sampler nodes wanting one seed is a property of
that graph, not of the product.

**A4's import flow is deferred, not forgotten** — `POST /api/workflows/import`,
class_type→slot auto-proposal, the remap form and `/object_info` validation.
Every one of those is gated on a running ComfyUI, so shipping them now means
shipping them untested. Dropping a `slots.json` beside a `graph.json` covers
the need meanwhile. Picked up in Phase 15 or when ComfyUI is available to test
against.

**B4 is standing policy from here** — Phase 5's Sexy filter reached 92% pass
rate with nothing to notice, and AGENTS.md rule 17 now says a new score ships
its pass rate and a guard case with it.

### Not met, and not quietly dropped

Two Phase 13–14 success criteria need a running ComfyUI and a pinned
checkpoint, so neither is verified on this machine:

- **Regenerate-same-seed produces a byte-identical image** (§5 criterion 2).
  The seed round-trip is tested; the pixels are not.
- **An unattended 50-item batch** (§5 criterion 5). The wiring, the per-item
  failure isolation and the cancel path are tested against a faked ComfyUI;
  the throughput is not.

---

## Phase 15 — Taste as the durable asset 🔜

**Goal:** stop hand-encoding a preference function in English. Learn it from the
labels the product has been discarding, and mine the same embeddings three ways.

| Deliverable | ID | Status |
|-------------|----|--------|
| Rapid labeling mode + `labels` table, seeded from `photos.favorite` and `_trash/` | B3 | **Done** |
| Embeddings cached in `archive.db` (hashed n-grams over vision JSON + prompt; optional Ollama `/api/embed`) + logistic head → `P(keep)`, "For You" sort | B2 | **Done** |
| Semantic search — `?search=…&mode=semantic` cosine over the same vectors | C1 | **Done** |
| Near-dup collapse — pHash groups first, embedding kNN second; Duplicates review UI | C3 | **Done** |
| Faceted attributes (setting / outfit / pose / lighting) as columns + filter chips | C5 | **Removed** — freeform vision phrases were not a usable browse axis |
| Collections / saved views — cross-creator boards, saved filter sets | C4 | **Done** |
| Activity view over the run journal (JSONL per job kind) | E2 | **Done** |

**One embedding job serves B2, C1 and C3.** Build it once. This retires the
Phase 6 note ("CLIP? Not needed…") — that call was right for the Comfy loop and
wrong for everything else, because the cost side changed: sqlite-vec drops into
the `archive.db` that already exists, no server.

**Superseded (2026-08-09):** the glam classifier and its whole scoring stack
were removed rather than demoted. Any future media scoring starts from a clean
sheet; the eval harness, prompt versions and label sets went with it.

E2 depends on S3 landing in Phase 13.

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
├── config.py  jobs.py  insights.py  logging_setup.py
├── storage/     archive db favorites metadata thumbs
│                atomic dedupe journal paths trash
├── scraping/    session downloader filters queue checkpoints
│                organizer sync_manager video_frames results
│                creator_queue classify_job media_classifier
│   └── sources/ base instagram_source gallery_dl_source
├── prompts/     cache engine styles batch comfy_mode
├── comfy/       client params runner batch registry + workflows/<name>/
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
