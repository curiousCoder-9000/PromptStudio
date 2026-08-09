# Feature backlog — F1–F8

| Field | Value |
|-------|--------|
| **Date** | 2026-08-09 |
| **Source** | [`review_ui_product.md`](review_ui_product.md) §2 |
| **Relationship to Themes A/B/C/E** | Deliberately **distinct**. [`product_review.md`](product_review.md) owns "close the generation loop"; these are the items that review did not surface, mostly because they ride on code that already exists. |
| **Status** | All Todo. Sequence in §9. |

Sizes: **S** ≈ days · **M** ≈ 1–2 weeks · **L** ≈ 3+ weeks.

Three of these (F2, F3, F4) exist to put a UI on a subsystem that is **already written
and tested**. That is why they rank above items with larger headline value: the expensive
half is done and currently returns nothing.

---

## F1 — Make captions searchable ⭐ (S)

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

**Touches**
- `storage/db.py` — add the caption to `prompt_search_blob`; it already reads the sidecar
  via `read_sidecar()` during `upsert_photo`, so there is no extra I/O.
- One-time reindex so existing rows pick it up — `PROMPTSTUDIO_REBUILD_INDEX=1`.
- `prompts_fts` stays in sync automatically (still off by default, see S5).

**Watch out**
- The blob is `LOWER`ed and matched with a leading wildcard. Long captions grow the scan
  cost per row; measure the search hot path before and after (AGENTS.md rule 13) — this
  is exactly the shape of change that looked free and was not for FTS5.
- Captions are third-party text. They reach the UI only through the existing escaped
  paths, but confirm nothing new interpolates them raw.

**Done when.** Searching a hashtag that appears in a caption and in no prompt returns the
post, and the search benchmark is reported alongside the change.

---

## F2 — Archive-wide classify + global review ⭐ (S/M)

**Outcome.** "Classify everything unclassified" as a top-level action, and review mode that
works with no creator selected.

**Why.** `ClassifyJobManager.start` rejects an empty creator (`scraping/classify_job.py:164`)
and the whole panel lives inside `#creatorStylePanel`, so it only appears once a creator is
picked. Batch Analyze in the navbar is archive-wide; Classify is not. The value of a
keep/reject filter is proportional to coverage, and the archive-wide run is the one you
would leave going overnight.

**Touches**
- `scraping/classify_job.py` — allow `creator=""` to mean "every creator"; `list_pending`
  already delegates to `ArchiveIndex.list_unclassified`, which takes an optional creator.
- `server/handler.py` — `POST /api/classify/start` stops requiring `creator`.
- `index.html` / `app.js` — an action beside Batch Analyze; `enterReviewMode(null)`.

**Watch out**
- The `ollama` lease is archive-wide already, so contention is handled — but a full-archive
  classify is long. It must report ETA in the chip and survive a refresh (it does; jobs
  live server-side).
- Per-creator progress in the chip stops being meaningful — switch the sub-label to
  `creator N of M`.

**Done when.** A single action classifies every unclassified item across all creators, the
chip is cancellable, and review mode opens on the whole archive.

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

## F5 — Verdict and tier as browse axes (S)

**Outcome.** Tier is usable for browsing, not only for triage.

**Why.** `sort=tier` is implemented in `storage/db.py:1685` and is **not** among the five
options in `#sortSelect` (`index.html:156-162`). `verdict=` is only sent while
`state.reviewMode` is true. So "show me every tier-4 shot across all creators" means
picking a creator and entering a delete-oriented mode.

**Touches**
- `index.html` — `Tier (harshest first)` in the sort dropdown; a verdict chip row beside
  Favorites / Unanalyzed.
- `app.js` — send `verdict=` outside review mode; add both to `PREF_FIELDS` (these are view
  prefs, unlike `reviewMode`, which must stay unrestored).
- `renderCreatorList` — a per-creator tier summary so the sidebar says which creators are
  worth opening.

**Watch out**
- Verdict pills already render in normal mode in a quiet variant. Adding a filter makes the
  card top band denser — U11 in the review; do that pass at the same time.

**Done when.** Tier sorting and verdict filtering work from the normal gallery and survive
a refresh.

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
