# Design — Source as a first-class view filter

**Status:** ✅ shipped in `2697495` (follow-ups `08bbc15`) · **Date:** 2026-08-10
**Sequence:** shipped before [design_scrape_lanes.md](design_scrape_lanes.md), as planned.
The two touch disjoint files; this one first so the UI could already separate platforms
before three lanes started filling the archive at once.

As-built notes and the §3.4 measurement are in [§9](#9-as-built). §1–§8 are the
accepted spec, kept as written so the delta is visible.

---

## 1. Problem

`photos.source` has existed since multi-source scraping shipped and is indexed
(`db.py:911`), but nothing reads it. Consequences today:

- `/api/creators` cannot say which platform a creator's media came from
  (`db.py:1611-1616` selects `creator, COUNT(*), MIN(filename)` and nothing else).
- `/api/photos` has no `source` parameter, so a mixed folder is unsplittable.
- X and Reddit creators show **no sync badge at all** — `sync_state.json` is written
  only by `downloader.py:319`, never by `GalleryDlSource`.

## 2. The one rule that shapes everything

**Source comes from `photos.source`. Never from parsing the folder name.**

It is tempting to read the `__x` / `__reddit` suffix off the folder
(`sources/base.py:43-62`) and call it done. That is wrong in two live cases:

- `SCRAPE_FOLDER_SUFFIX=0` merges platforms into one bare folder on purpose
  (documented in `multi_source_scraping.md` §4).
- Any folder can also hold manual uploads via `POST /api/photo/upload`.

A folder is a **location**; a source is a **provenance**. A creator is therefore
legitimately multi-source, and the data model already says so correctly. Deriving
provenance from location would produce an answer that is right for most rows and
silently wrong for the ones the feature exists to handle.

## 3. Storage — `promptstudio/storage/db.py`

### 3.1 `list_creators(*, source=None, with_verdicts=True)`

Replace the creator rollup with the same aggregate grouped one level finer:

```sql
SELECT creator, source, COUNT(*) AS n, MIN(filename) AS cover
FROM photos GROUP BY creator, source
```

Folded in Python this yields both things from one scan:

| Field | Meaning |
|-------|---------|
| `photo_count` | scoped to the active filter (or the total when unfiltered) |
| `sources` | `{"instagram": 120, "x": 37}` — full map, **not** narrowed by the filter |
| `cover_url` / `cover_thumb_url` | from the filtered source, else the largest source |

`sources` stays unfiltered on purpose: the sidebar needs to mark a folder as
multi-source *while* a filter is active, which a filtered map cannot express.

`ORDER BY photo_count DESC` moves into Python. Creator counts are in the hundreds;
this is not where time goes.

When `source` is set, creators with zero matching rows drop out of the list
entirely. That is the intent — a pill that lists creators yielding no results is
worse than no pill.

### 3.2 `creator_verdict_counts(*, source=None, ...)`

Add `AND p.source = ?` to the `WHERE`. Without this the sidebar reject pill counts
Instagram rejects while the user is looking at X — a number that is confidently
wrong, which is worse than a missing one.

### 3.3 `query_photos(*, source=None, ...)`

One more predicate, `p.source = ?`, table-qualified like every other predicate in
that method (both joined tables carry `source`-adjacent columns; see the comment at
`db.py:1681-1683`).

### 3.4 Measurement (hard rule 13)

Regrouping `GROUP BY creator` → `GROUP BY creator, source` must be benchmarked
against the current query and **the number reported**, not assumed free. Two
"obvious" wins in this codebase measured as losses. `scripts/benchmark_queries.py`
already exists for this.

## 4. API — `promptstudio/server/handler.py`

| Route | Change |
|-------|--------|
| `GET /api/creators` | accepts `?source=` |
| `GET /api/photos` | accepts `?source=` |
| `GET /api/sources` | unchanged — already returns the registry (`handler.py:1623`) |

Validation: an unrecognised value is **400**, not a silent unfiltered result.
Quietly returning every photo when the caller asked for X is the failure mode that
looks like success. Empty string and `all` both mean "no filter".

Validate against `known_sources()` so the list cannot drift from the registry.

## 5. Frontend — `app.js`

- `state.sourceFilter` (default `''`). Joins `PREF_FIELDS` and `saveViewPrefs()` —
  it is a view pref, so it persists across reloads. Selected creator and selection
  remain deliberately non-restored, per the existing invariant.
- Pill row above the creator list: `All · Instagram · X · Reddit`, built from
  `/api/sources` rather than hardcoded, so a new source appears without a UI edit.
- Both `fetchCreators()` and `fetchPhotos()` send `source`.
- Toggling a pill goes through the existing `state.photosRequest` AbortController;
  treat `err.name === 'AbortError'` as success-by-supersession and only clear
  loading state when `state.photosRequest === controller`.
- Multi-source creators get a small marker derived from `sources`. Handles, source
  labels and counts all go through `escapeHtml()` / `Number()` — the source name
  reaches the DOM from the registry, but the creator name is third-party text.

## 6. Closing the sync-badge gap

`GalleryDlSource.run()` gains a single `SyncCheckpoints().update(...)` after
`_ingest`, keyed on **`target.folder`, not the raw handle**.

Why folder: `sync_state.json` is keyed by handle today, and `db.list_creators`
looks it up by folder name (`db.py:1621`). For Instagram folder == handle, so
folder-keying is backward compatible; for X it is what stops `nina` on Instagram
and `nina` on X from overwriting each other's checkpoint.

One call per run, not per file — `update()` rewrites the whole dict, so per-file
would be O(n²) writes for no added information.

## 7. Testing

`tests/test_source_filter.py`:

- creators scoped by source
- a merged folder (`SCRAPE_FOLDER_SUFFIX=0`) reports both counts and appears under
  both pills
- `sources` map stays complete while a filter is active
- photos filtered by source
- verdict counts scoped by source
- unknown source → 400
- `source=all` and `source=` behave identically to no parameter

`tests/test_sources.py` (extend): gallery-dl run writes a folder-keyed checkpoint;
an Instagram and an X target sharing a handle keep separate checkpoint entries.

`tests/ui/`: pill toggle filters the sidebar, and the choice survives a reload.

## 8. Out of scope

- Per-source classify or prompt behaviour. Verdicts stay source-agnostic.
- Any change to folder naming or `SCRAPE_FOLDER_SUFFIX` semantics.
- Backfilling `source` for pre-existing rows — the column is
  `NOT NULL DEFAULT 'instagram'` (`db.py:62`), so legacy media is already correct.

---

## 9. As built

Shipped as specified — `list_creators(source=)`, `creator_verdict_counts(source=)`,
`query_photos(source=)`, `?source=` on `/api/creators` and `/api/photos` with a **400**
on an unrecognised value, `state.sourceFilter` in `PREF_FIELDS`, registry-driven pills,
and the folder-keyed `SyncCheckpoints().update(...)` in `GalleryDlSource._record_checkpoint`
(`gallery_dl_source.py:307`). Three things differ from the spec above.

### 9.1 The §3.4 measurement (hard rule 13)

The regroup was benchmarked with `scripts/benchmark_queries.py`, whose synthetic archive
round-robins every creator across all three sources — the **worst case**, 3× the groups of
the `creator`-only rollup it replaces. 4,400 rows, 40 creators, median of 7:

| Rollup | Median ms |
|--------|----------:|
| legacy `GROUP BY creator` (raw SQL) | 1.4 |
| new `GROUP BY creator, source` (raw SQL) | **2.9** |
| `list_creators()`, rollup only | 2.9 |
| `list_creators()`, full (rollup + verdicts) | 6.4 |
| `list_creators(source='x')`, full | 4.0 |

**+1.5 ms** on a sidebar query that runs once per creator-list refresh, against a real
archive where most folders are single-source and the grouping collapses. Accepted. The
Python-side fold and `ORDER BY photo_count` cost nothing measurable on top of the raw SQL
(2.9 ms for both), which is the part §3.1 asserted without evidence.

### 9.2 The filter broke a button the spec called out of scope

§8 ruled per-source classify out of scope, and that still holds — but **Classify All**
read its count from `state.creators`, which `/api/creators?source=` narrows, while the job
it starts is archive-wide regardless. Filter to a platform whose backlog happens to be
clear and the button **disabled itself** — "every creator is already classified" — while
another platform's pile sat untouched.

Fixed with `ArchiveIndex.unclassified_total()` (`db.py:1737`) on `/api/stats`, which is
never scoped to anything, replacing the sidebar sum (`app.js:702`). The lesson generalises:
a count derived from a filtered list must not label an unfiltered action.

### 9.3 A persisted filter can name a source that no longer exists

Not in the spec, and it would have been a permanent empty sidebar. `sourceFilter` persists
through `PREF_FIELDS`, and validation is against the live `known_sources()` — so
unregistering a source strands every client that had it selected, on a 400, on every load.

`fetchCreators()` treats a 400 as "drop the pref and retry once" (`app.js:934-941`) rather
than surfacing an error. The strict 400 from §4 is right for an API caller and wrong as a
terminal state for a stored preference; both behaviours coexist.

### 9.4 Tests

`tests/test_source_filter.py` covers every case in §7 including the merged-folder
(`SCRAPE_FOLDER_SUFFIX=0`) one; `tests/test_sources.py` gained the folder-keyed checkpoint
cases; `tests/ui/test_source_filter.js` covers pill toggling and reload persistence;
`tests/test_stats.py` gained 4 for `unclassified_total`.
