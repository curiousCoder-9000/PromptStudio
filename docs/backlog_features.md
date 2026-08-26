# Feature backlog — F1–F8

| Field | Value |
|-------|--------|
| **Date** | 2026-08-09 |
| **Source** | [`review_ui_product.md`](review_ui_product.md) §2 |
| **Relationship to Themes A/B/C/E** | Deliberately **distinct**. [`product_review.md`](product_review.md) owns "close the generation loop"; these are the items that review did not surface, mostly because they ride on code that already exists. |
| **Status** | **F1, F2, F5 shipped** (Stage 2 — see [review_ui_product.md](review_ui_product.md) §6). **F3, F4, F6, F8 shipped** (Phase 15). F7 Todo. Sequence in §9. |

Sizes: **S** ≈ days · **M** ≈ 1–2 weeks · **L** ≈ 3+ weeks.

Three of these (F2, F3, F4) exist to put a UI on a subsystem that is **already written
and tested**. That is why they rank above items with larger headline value: the expensive
half is done and currently returns nothing.

---

## F1 — Make captions searchable ⭐ (S) — ✅ shipped

**Outcome.** `?search=` matches the creator's own words — hashtags, location, brand names,
product mentions — not just LLM-generated prompt text.

**Why.** `prompt_search_blob` (`storage/db.py:278`) concatenates exactly four fields:

```python
positive_prompt · negative_prompt · raw_vision_description · visual_tags
```

All four are written by the model. The caption is downloaded on every sync and saved to
every sidecar (`storage/metadata.py:41`), and it is indexed **nowhere**. So the only
human-written text in the archive is unsearchable.

`product_review.md` P4 diagnoses search as *"`LIKE '%q%'` over LLM-generated text — 'red
bikini' returns nothing unless the prompt writer happened to use those words"* and routes
the fix to C1 semantic search (Phase 15, needs an embedding store). Part of that gap is
much cheaper than that: the text was there the whole time.

**As built** (differs from the proposal above — recorded rather than quietly changed):
- A **separate `caption_search` column**, not more text in `prompt_search`. The caption is
  fixed for the life of the file; the prompt blob is rewritten on every regenerate. Merging
  them would mean re-deriving the caption on every prompt save for no gain, and it keeps the
  FTS mirror (which tracks the `prompts` table) coherent.
- Written by `rebuild()` and `upsert_photo` from the sidecar read that already happens, and
  passed explicitly by both downloaders — so the ingest hot path gains **zero** file opens
  and S8's "4 reads → 1" win is untouched.
- Precedence in `upsert_photo`: explicit `caption=` → the already-loaded sidecar → the
  existing row. That last case is load-bearing: a favourite toggle passes none of them and
  must not blank the index.
- `_migrate_caption_search()` backfills existing archives once, so no forced reindex.
- `author` is indexed too — on Reddit/X the real author is not the folder name.

**Measured** (AGENTS.md rule 13): +1.1 ms worst-case search at 4,400 rows, +5% at 40,000.
Full table in [review_ui_product.md](review_ui_product.md) §6.

**Tests.** `tests/test_caption_search.py` (17), including the favourite-toggle and
prompt-write cases that would silently blank the column.

---

## F2 — Archive-wide classify + global review ⭐ (S/M) — ✅ shipped

**Outcome.** "Classify everything unclassified" as a top-level action, and review mode that
works with no creator selected.

**Why.** `ClassifyJobManager.start` rejects an empty creator (`scraping/classify_job.py:164`)
and the whole panel lives inside `#creatorStylePanel`, so it only appears once a creator is
picked. Batch Analyze in the navbar is archive-wide; Classify is not. The value of a
keep/reject filter is proportional to coverage, and the archive-wide run is the one you
would leave going overnight.

**As built**
- `creator=""` means the whole archive through `list_unclassified` → `list_pending` →
  `start()` → `POST /api/classify/start`. A non-creator folder (`_trash`, `_thumbs`) is still
  `bad_creator`: the scope is either one real creator or everything.
- **`""` and `None` are different things** and the UI depends on it — `""` is an archive-wide
  run, `None` is no job. Conflating them renders "Classifying @" with nothing after the `@`.
- Navbar **Classify All (N)** beside Batch Analyze, with a live count, a disabled reason in
  the tooltip, and a confirm — it holds the vision model for the duration, which blocks
  batch analyze.
- The chip reads `Classifying all creators · 412/3100 · @current_creator`; pending is ordered
  by creator so that label advances monotonically.
- Review mode with no creator now sums counters across creators (`scopedVerdictCounts`)
  instead of showing zeroes on every chip.

**Tests.** `tests/test_classify_all_creators.py` (12), plus one existing test rewritten:
`test_missing_creator_is_rejected` asserted the old contract, so it became
`test_empty_creator_now_means_the_whole_archive` rather than being deleted.

---

## F3 — Duplicate review UI ⭐ (S/M)

**Outcome.** A "Duplicates" view listing near-duplicate groups, with all but the best copy
preselected, deleting through the existing soft-delete + Undo path.

**Why.** Perceptual-hash near-duplicate detection is **finished**: `storage/dedupe.py`, a
`phashes` table maintained on upsert, and `scripts/find_duplicates.py`. Grep `handler.py`
and `app.js` for `dedupe`, `duplicate` or `phash` and you get **zero hits**. A whole
subsystem is reachable only from a CLI.

Doubles as a cost win — classify and analyze one representative per group instead of five.
`product_review.md` C3 proposes this; the point here is that the hard half already exists,
which changes it from M-with-research to M-with-a-renderer.

**Touches**
- `server/handler.py` — `GET /api/duplicates` over `dedupe.group_near_duplicates`.
- `app.js` — reuse review mode nearly wholesale: grid, selection, bulk delete, Undo.
- Group ranking: prefer favourites, then largest resolution, then earliest `added_at`.

**Watch out**
- Never preselect a favourite, same rule as the classify sweep — the one unambiguous user
  signal is never swept up by a machine verdict (AGENTS.md rule 1 in spirit).
- Carousel siblings are *not* duplicates. Exclude same-`post_id` groups or C2 will fight it.

**Done when.** Duplicate groups render, a sweep deletes only non-favourites, and Undo
restores the whole group.

---

## F4 — Activity view over the run journal (S)

**Outcome.** A read-only view answering "what did last night's sync/classify actually do?"

**Why.** This is `product_review.md` E2, and it is **cheaper than that document assumes**,
because the backend already shipped: `storage/journal.py` writes append-only JSONL per job
kind and `GET /api/journal` serves it (`handler.py:1663`). `app.js` never calls it. The
remaining work is a renderer.

Today the question is answerable only by tailing `<archive>/_journal/<kind>.jsonl`.

**Touches**
- `app.js` / `index.html` — a modal or panel: kind picker, run rows (outcome, item count,
  duration, stop reason), expandable per-run summary.
- For classify runs, show `tier_hist` and `top_tier_share` per run — the per-run twin of
  the archive-wide panel shipped in Stage 1.

**Watch out**
- Runs are unbounded JSONL. `read_runs(kind, limit=)` already paginates; do not load all.

**Done when.** The last N runs of each kind are inspectable in the UI, including why a run
stopped.

---

## F5 — Verdict and tier as browse axes (S) — ✅ shipped

**Outcome.** Tier is usable for browsing, not only for triage.

**Why.** `sort=tier` is implemented in `storage/db.py:1685` and is **not** among the five
options in `#sortSelect` (`index.html:156-162`). `verdict=` is only sent while
`state.reviewMode` is true. So "show me every tier-4 shot across all creators" means
picking a creator and entering a delete-oriented mode.

**As built**
- `Tier (harshest first)` in `#sortSelect`, and a verdict `<select>` (not a chip row — seven
  options would have been seven chips) beside the filter chips, with an `.is-active` state so
  a silently-filtering select does not look identical to an idle one.
- `state.browseVerdict` is in `PREF_FIELDS`; `state.reviewMode` deliberately still is not.
- Pills go loud when filtering by verdict — the verdict is what is being looked at.
- The per-creator summary landed as the reject pill's **tooltip** (unusable / modest / keep /
  to-do / outdated), built from counters already on `/api/creators`. A visible breakdown per
  row would need per-tier counts the endpoint does not return; that is a separate change.
- **Found on the way:** `body.review-mode` was toggled with no CSS rule attached, so the
  review strip stacked *under* the normal controls instead of replacing them as designed.
  With a verdict filter now in both places, two of them disagreeing on screen would be worse
  than either — `.view-controls` is hidden in review mode.

**Tests.** `tests/ui/test_browse_and_paging.js` (17).

---

## F6 — Trash as a review surface (S)

**Outcome.** Trash renders as a grid of thumbnails with restore-per-item, not a text list.

**Why.** `_trash/` holds up to 30 days of the user's explicit negatives — the signal
`product_review.md` P2 names as discarded — and `loadTrashList` (`app.js:1001`) renders it
as rows of text. With B3 rapid labeling planned for Phase 15, this is a pre-labelled
negative set already on disk and already retained.

**Touches**
- `app.js` — thumbnail grid in the trash modal; `_trash/<entry_id>/` has the media.
- Optionally surface *why* it was deleted (manual vs. classify sweep) — needs one field on
  the trash manifest.

**Done when.** You can see what you deleted, and restore selectively.

---

## F7 — Creator lifecycle (M)

**Outcome.** Rename, merge and remove a creator.

**Why.** There is `POST /api/creator/create` and nothing else. Handles change; the same
person gets scraped twice under two spellings; a creator you stop caring about stays in the
sidebar forever.

Merge is the interesting one: `photos.creator` and `media_verdicts.creator` are both
denormalised (the latter deliberately, so sidebar counters are one `GROUP BY`), and
`favorites.json`, `creator_styles.json`, `sync_state.json` and `_classify/<creator>/` are all
keyed by handle. It is a real data operation, not a folder move.

**Touches**
- New `promptstudio/storage/creators.py` for the transactional rename/merge.
- `server/handler.py` — `POST /api/creator/{rename,merge}`, `DELETE /api/creator`.
- Every keyed-by-handle file listed above.

**Watch out**
- Must be atomic-ish and idempotent: a half-finished merge that moved files but not DB rows
  is worse than no merge. Write the DB side in one transaction, move files after, and make
  a re-run of the same merge a no-op.
- Removing a creator must route through `_trash`, not `os.unlink` (AGENTS.md rule 1).

**Done when.** A merge leaves one folder, one set of verdicts, one style, and no orphans —
proved by a test that merges two seeded creators and asserts every keyed store.

---

## F8 — Saved views (S)

**Outcome.** Name and re-run a filter set: creator + verdict + media type + sort + search.

**Why.** The cheap 80% of `product_review.md` C4 (collections). C4 needs
`collections` + `collection_items` and a membership model; this needs one table of
serialised filter state, and it is the thing you actually re-run daily.

**Touches**
- `saved_views` table in `archive.db`; `GET/POST/DELETE /api/views`.
- A sidebar section above Creators. Reuses `PREF_FIELDS` serialisation.

**Done when.** A named view restores every filter it captured in one click.

---

## 9. Sequence

| Order | Item | Rationale |
|-------|------|-----------|
| 1 | **F1** captions | Days, and it makes search feel repaired rather than needing replacement. Independent of everything. |
| 2 | **F5** tier as a browse axis | Pure surfacing of shipped backend work; pairs with the U11 card-density pass. |
| 3 | **F2** archive-wide classify | Makes the Stage-1 distribution panel meaningful over the whole archive rather than one creator. |
| 4 | **F3** duplicates | Biggest built-but-invisible subsystem; also cuts classify/analyze cost for everything after. |
| 5 | **F4** activity view | Renderer over a shipped API. |
| 6 | **F6** trash grid · **F8** saved views | Small, independent. |
| 7 | **F7** creator lifecycle | Largest and the only one with real data-migration risk. |

F1–F5 are Stage 2 in [`review_ui_product.md`](review_ui_product.md) §4. None of these
competes with Phase 13 — they are the cheap surfacing work that makes Phase 13 easier to
evaluate.
