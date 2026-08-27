# Gallery performance review

| Field | Value |
|-------|--------|
| **Date** | 2026-08-27 |
| **Scope** | First-page and browse latency of the photo gallery: `GET /api/photos`, `GET /media/thumb/…`, `renderGallery`, SQLite `query_photos`, thumbnail generation, HTTP serving |
| **Method** | Read of `app.js` / `handler.py` / `storage/db.py` / `storage/thumbs.py`, then **measured** against the live archive (`~/Pictures/InstagramSaved/archive.db`). SQL via a read-only connection; `ArchiveIndex.query_photos` / `list_creators` / `stats` through the real Python path; Pillow timing of one missing JPEG; filesystem census of `_thumbs/` |
| **Companions** | [`review_backend_architecture.md`](review_backend_architecture.md) S5/S8/S9/S10 — query measurements at 4.4k / 40k *synthetic* rows · [`review_ui_product.md`](review_ui_product.md) U2 — DOM cost, `content-visibility` shipped Stage 2 · [`architecture.md`](architecture.md) gallery request flow |
| **Rule 13** | Two previous "obvious" wins (FTS5 search, incremental rebuild) were measured and declined. This review does not reopen them. The new numbers are why the Stage 2 gallery work is no longer enough. |
| **Verdict** | The gallery is slow because the archive outgrew the query shape, and because thumbnails are generated on the HTTP GET path. SQL was tuned for 4.4k files; this archive is **61k**. `content-visibility` skips paint, not work. |

---

## 0. The central finding

A first page of tiles is four stacked costs, and **newest** — the sort you actually use after a scrape, and a restored view pref — is the worst case of all of them.

| Step | What happens today | Measured on this archive |
|------|--------------------|--------------------------|
| 1. Count | Every `/api/photos` runs `COUNT(*)` with **two LEFT JOINs** (`media_verdicts` + `labels`), even when the request filters on neither | Cold **1,036 ms**. Bare `COUNT(*)` is **5 ms**. EXPLAIN: scan all 61k rows, then a PK probe per row on each joined table |
| 2. Page | `ORDER BY IFNULL(p.added_at, p.mtime)` plus `SELECT p.*` | `IFNULL` **cannot use** `idx_photos_added` → full scan + temp B-tree. Bare `ORDER BY added_at DESC`: **0.1 ms**. `IFNULL` sort: **58 ms**. Slim columns + index: **0.2 ms**. `SELECT p.*` + joins + `IFNULL`: **268 ms** |
| 3. Python wrapper | `query_photos` does count + wide select + `_row_to_photo` | **Name 219 ms · Newest 1,674 ms · Posted 707 ms · Tier 1,078 ms · Search “dress” 580 ms** (median of 5) |
| 4. Thumbs | Browser then hits `/media/thumb/…` 60 times. Thumbs are created **inside that GET**, never at ingest | `_thumbs/` holds **12,148** files against **61,344** catalog rows (**20%** coverage). Newest 500 files: **45 present, 455 missing**. One 1.6 MB JPEG: **449 ms** to decode/resize/encode. Cache hit: **0.02 ms**. All 8 newest videos sampled had no thumb |

`added_at` is populated on **every** row (0 null-or-zero of 61,344). The `IFNULL` is defending a case that no longer exists, and it is the difference between an index probe and a 61k-row sort.

Creator switch, sort change, search, infinite scroll, and post-scrape “newest” all share this pipeline: a heavy count, an unindexed order, then a stampede of on-demand JPEG work through Python’s HTTP server.

---

## 1. Archive under measurement

Live `PROMPTSTUDIO_ARCHIVE` (`~/Pictures/InstagramSaved`), 2026-08-27. Server was bound on `:5000` during the census.

| Fact | Value |
|------|------:|
| `archive.db` | 62,144,512 bytes (~59 MB) |
| `photos` | 61,344 |
| `media_verdicts` | 23,738–23,740 (moved during the session) |
| `prompts` | 248 |
| `labels` | 210 |
| `embeddings` | 0 |
| `phashes` | 7,400 |
| videos (filename `LIKE %.mp4/.webm/.mov`) | 8,102 |
| distinct creators | 150 |
| avg `prompt_search` length | 6.8 (most rows empty) |
| avg `caption_search` length | 77.9 |
| sum `caption_search` | 4,777,591 bytes |
| `added_at` null or 0 | **0** / 61,344 |
| `mtime` null or 0 | **0** / 61,344 |
| `_thumbs/` files | 12,148 (**~20%** of catalog) |
| first page (`limit=60`, `sort=name`) thumbs | 40 present, 20 missing |
| newest 500 thumbs | 45 present, 455 missing |
| newest 40 JPEGs | 3 thumbed, 37 missing |
| newest 8 MP4s | 0 thumbed |
| sample original size (20 files on name-sort page) | avg 399 KB, max 3,485 KB |
| missing-thumb JPEG timed | 1,590 KB → 16.8 KB JPEG |

`photos` indexes present: `sqlite_autoindex_photos_1` (PK `rel_path`), `idx_photos_creator`, `idx_photos_taken`, `idx_photos_fav`, `idx_photos_prompt`, `idx_photos_post_id`, `idx_photos_shortcode`, `idx_photos_glam`, `idx_photos_source`, `idx_photos_glam_version`, `idx_photos_added`, `idx_photos_group_key`, `idx_photos_p_keep`.

There is **no** index on `mtime`, and `idx_photos_added` is a single-column index — `ORDER BY added_at DESC, filename` still builds a temp B-tree for the filename tie-break (cheap once the leading column is used; fatal when the leading expression is `IFNULL`).

`SELECT p.*` on this table returns leftover `glam_*` columns, `facet_*`, `prompt_search`, `caption_search`, `p_keep`. `_row_to_photo` (`db.py`) keeps a subset. The extra bytes are still read.

---

## 2. Request timeline (as built)

```
Browser  initApp()
  ├─ fetchPhotos()                GET /api/photos?offset=0&limit=60&sort=…
  │     ArchiveIndex.query_photos
  │       1. COUNT(*)  FROM photos p
  │          LEFT JOIN media_verdicts v  LEFT JOIN labels lb     ← always
  │       2. SELECT p.*, v.*, lb.* … ORDER BY <expr> LIMIT 60 OFFSET n
  │       3. _row_to_photo × 60  (builds url, thumb_url, verdict object)
  │     handler: annotate prompts (no-op if flags present), strip full_path
  │     JSON ~52 KB / 60 rows  (sample row 873 bytes, mostly verdict)
  │
  ├─ renderGallery()              innerHTML + 2–3 listeners per card, append-only
  │     <img src="/media/thumb/…"> × 60, loading=lazy
  │
  └─ 60 × GET /media/thumb/…      ThreadingHTTPServer, HTTP/1.0 (no keep-alive)
        ensure_thumbnail() on the request thread
          hit:  mtime compare, return JPEG
          miss: Pillow decode original → 400px JPEG q82 optimize=True
                video miss: cover still, else rank frames (decode timeline)
```

Relevant code:

| Piece | Where |
|-------|--------|
| Page size | `app.js` `state.photoLimit = 60` |
| Query string | `app.js` `galleryQueryParams` / `fetchPhotos` |
| Render | `app.js` `renderGallery` — `innerHTML` per card, never removes one except on filter reset |
| Sentinel | `IntersectionObserver` rootMargin `600px` (`observeLoadMoreSentinel`) |
| `content-visibility: auto` | `style.css` `.photo-card` — skips *paint* of off-screen cards, not node creation |
| SQL | `promptstudio/storage/db.py` `query_photos`, `_photo_select` |
| Count + page | same function, one `COUNT(*)` then `LIMIT/OFFSET`, both under `self._lock` |
| Thumbs | `promptstudio/storage/thumbs.py` `ensure_thumbnail`; **only caller** is `GET /media/thumb/` in `handler.py` |
| Thumb size | `PROMPTSTUDIO_THUMB_SIZE` default **400** (`config.py`) |
| HTTP server | `ThreadingHTTPServer` + `GalleryRequestHandler`; **`protocol_version` is unset** → Python default `HTTP/1.0` |
| SQLite | one connection, `check_same_thread=False`, process-wide `RLock`; WAL + `busy_timeout=5000` |

Default `sortMode` in code is `'name'`. `sortMode` is in `PREF_FIELDS`, so a user who last picked **Newest download** restores it on every reload. That is the hot path.

---

## 3. SQL measurements

### 3.1 `query_photos` (real Python path)

`ArchiveIndex` against the live DB. Median / min / max of 5, milliseconds.

| Call | median | min | max |
|------|------:|----:|----:|
| `sort=name` limit 60 | 218.5 | 196.0 | 316.8 |
| `sort=newest` limit 60 | **1,673.9** | 1,339.3 | 1,968.6 |
| `sort=posted` limit 60 | 706.8 | 556.3 | 852.4 |
| `sort=tier` limit 60 | 1,077.5 | 970.0 | 1,110.8 |
| `sort=name` grouped | 428.4 | 322.7 | 829.8 |
| `sort=newest` grouped | 1,279.8 | 1,109.2 | 1,472.5 |
| `sort=newest` offset 1,200 | 1,627.0 | 390.1 | 1,931.0 |
| `search=dress` newest 60 | 580.2 | 472.3 | 711.1 |
| `list_creators()` | 121.9 | 99.8 | 1,601.1 |
| `stats()` | 164.0 | 109.7 | 281.4 |

JSON for a 60-row name page: **52,821 bytes**. Sample keys: `added_at, creator, favorite, filename, full_path, has_prompt, post_id, prompt_stale, rel_path, shortcode, taken_at, thumb_url, url, verdict`. The verdict object carries `reason`, `prompt_version`, `classified_at`, `sheet_path`, `confidence`, `media_kind`, `verdict_source`, `manual` — the card displays a badge, not those fields. Lightbox already refetches `/api/media/detail`.

### 3.2 Raw SQL (read-only connection, warm cache)

Second pass, after the DB pages were in cache. Plans are what matter; the warm times show the floor once disk is not the story.

| Statement | EXPLAIN | Warm median |
|-----------|---------|------------:|
| `COUNT(*)` + verdicts + labels joins | `SCAN p USING COVERING INDEX sqlite_autoindex_photos_1` then `SEARCH v` / `SEARCH lb` LEFT-JOIN per row | 35.6 ms (cold was **1,036 ms**) |
| `COUNT(*)` + verdicts only | same scan, one join | 27.6 ms |
| `COUNT(*)` FROM photos | `SCAN photos USING COVERING INDEX idx_photos_p_keep` | **0.0–5 ms** |
| `ORDER BY IFNULL(added_at, mtime) DESC, filename LIMIT 60` | `SCAN p` + `USE TEMP B-TREE FOR ORDER BY` | 58.1 ms |
| `ORDER BY added_at DESC, filename LIMIT 60` | `SCAN p USING INDEX idx_photos_added` + temp B-tree for last term | **0.1 ms** |
| covering `(added_at, filename)` same order | index | 0.1 ms |
| `ORDER BY CASE WHEN mtime>0 THEN mtime ELSE added_at END` (posted) | `SCAN p` + temp B-tree | 33.2 ms |
| `ORDER BY mtime DESC, filename` | `SCAN p` + temp B-tree (**no mtime index**) | 31.3 ms |
| `ORDER BY creator, filename LIMIT 60` | `SCAN p USING INDEX idx_photos_creator` | 1.4 ms |
| `SELECT p.*, v.tier` + both joins + `IFNULL` newest LIMIT 60 | scan + temp B-tree | **267.5 ms** |
| slim columns + verdict join + `ORDER BY added_at` LIMIT 60 | index | **0.2 ms** |
| `IFNULL` newest `OFFSET 1200` | scan + temp B-tree | 85.4 ms |
| bare `added_at` `OFFSET 1200` | index | **0.5 ms** |
| `LOWER(filename) LIKE '%.mp4' OR …` (stats video count) | `SCAN photos` | 61.1 ms |
| grouped name page `GROUP BY` group-key | `SCAN p USING INDEX idx_photos_group_key` | (plan only; Python wrapper 428 ms because of the count) |

EXPLAIN for the count the gallery actually runs:

```
SCAN p USING COVERING INDEX sqlite_autoindex_photos_1
SEARCH v USING COVERING INDEX sqlite_autoindex_media_verdicts_1 (rel_path=?) LEFT-JOIN
SEARCH lb USING COVERING INDEX sqlite_autoindex_labels_1 (rel_path=?) LEFT-JOIN
```

EXPLAIN for newest as written:

```
SCAN p
USE TEMP B-TREE FOR ORDER BY
```

EXPLAIN for newest if `IFNULL` is dropped (`added_at` is never null):

```
SCAN p USING INDEX idx_photos_added
USE TEMP B-TREE FOR LAST TERM OF ORDER BY
```

That last temp B-tree is the filename tie-break. A composite `(added_at DESC, filename)` removes it.

### 3.3 Why `query_photos` is ~1.7 s on newest

The wrapper pays both statements, plus Python, plus the writer `RLock`:

1. COUNT with two joins — ~1 s cold / ~36 ms warm.
2. `SELECT p.*` with the same joins and the `IFNULL` order — ~268 ms warm, worse cold (wide rows: `glam_*`, `caption_search`, `prompt_search`).
3. `_row_to_photo` builds dicts and `urllib.parse.quote` URLs.

Name-sort is 219 ms because `ORDER BY p.creator, p.filename` can ride `idx_photos_creator`. Newest cannot ride `idx_photos_added` until the `IFNULL` goes.

### 3.4 Comparison with the 2026-08-09 synthetic numbers

From [`review_backend_architecture.md`](review_backend_architecture.md) S10, median of 7, synthetic inserts:

| gallery page limit 60 | 4,400 files | 40,000 files |
|---|--:|--:|
| `sort=name`, flat | 1.2 ms | 11.2 ms |
| `sort=newest`, flat | 3.1 ms | 28.0 ms |
| `sort=newest`, grouped | 8.3 ms | 147.7 ms |

Live 61k newest is **~60×** the synthetic 40k newest. The synthetic DB was a narrow temp file with no leftover `glam_*` columns, no 4.7 MB of `caption_search`, no 23k verdict join, and no COUNT-with-joins. S10 was right about grouping being slower than flat. It did not measure this archive, and it did not measure thumbs.

S9 `list_creators` at 40k synthetic: 160 ms full. Live 61k: 122 ms median, **1,601 ms** max (lock / cold). The sidebar is not free, but it is not the first-page story.

S5 FTS5: still off. LIKE search on this archive is 580 ms for `"dress"` under newest — that cost is the unindexed `ORDER BY IFNULL` plus four `LOWER() LIKE '%q%'` columns, not the lack of FTS. Do not flip `PROMPTSTUDIO_FTS_SEARCH` to fix it.

---

## 4. Thumbnail measurements

`ensure_thumbnail` is called from **one** site: `GalleryRequestHandler` `GET /media/thumb/`. Downloaders, `upsert_photo`, and upload do not create thumbs. After a scrape, “newest” is an empty-thumb view.

| Path | Time |
|------|------:|
| cache hit (`ensure_thumbnail` on an existing JPEG) | **0.02 ms** |
| miss, 1,590 KB JPEG, Pillow decode + `thumbnail((400,400))` | 362.7 ms |
| same + `save(JPEG, quality=82, optimize=True)` | **448.6 ms**, output 16.8 KB |

`optimize=True` is an extra Huffman pass for bytes that do not matter on loopback.

Videos (8,102 in the catalog): on miss, `ensure_thumbnail` tries a companion cover still, then `write_best_video_frame_jpeg` (ranks frames across the timeline), then first-frame OpenCV. Ranking on a gallery GET is unbounded CPU. The 8 newest MP4s sampled had no thumb.

Browser behaviour on top of that:

- 60 `<img loading="lazy">` still request the first viewport immediately.
- Chrome ~6 connections per origin.
- `protocol_version` unset → HTTP/1.0 → **no keep-alive** → 60 TCP handshakes.
- Missing thumbs encode on those six threads under the GIL (Pillow releases it in C, so encode can overlap; the Python handler still cannot reuse connections).
- Fallback when encode fails is to serve the **original** as the tile (`serve_path = thumb or full_path`) — a 3.5 MB decode in a 220 px CSS box.

`THUMB_MAX_SIZE` default 400 vs CSS `minmax(220px, 1fr)` / large `320px`. The extra pixels are paid at encode time for every miss.

A full `_thumbs/` walk: **12,148** files. Four-fifths of the catalog still encodes on first view of that file.

---

## 5. Frontend cost (still real, not first)

U2 in [`review_ui_product.md`](review_ui_product.md) shipped `content-visibility: auto` + `contain-intrinsic-size` and replaced the scroll handler that read `document.body.offsetHeight` every frame. That was the right 4.4k patch. What remains:

- **Append-only DOM.** `renderGallery({ append })` never unmounts cards except on a fresh query (`innerHTML = ''`). A long session is 10–20 pages = 600–1,200 cards, each with overlay markup and 2–3 listeners. `loading="lazy"` defers the fetch, not the node.
- **`selectMode` rebuilds the pile.** `setSelectMode` / `clearSelection` call `renderGallery()` on everything loaded. The CSS already has `.gallery-grid.select-mode`.
- **Grouped delete redraws everything.** `removePhotosFromView` calls `renderGallery()` when `groupPosts` is on.
- **Creator sidebar.** 150 rows rebuilt with `innerHTML` on every click, including the click that then `fetchPhotos()`. Cover URLs are computed in `list_creators` and **not used** in `renderCreatorList`.
- **Lightbox** sets `lightboxImg.src = photo.url` (original, 0.4–3.5 MB) with no thumb-first paint and no ±1 preload. Then `loadMediaDetailPanel` hits `/api/media/detail` (sidecar + `get_verdict`) and stills also `GET /api/generations`.
- **Filter change** shows 12 skeletons (`showGallerySkeletons` replaces the grid). Honest under a 1.7 s newest fetch; once SQL is fast, replacing a populated grid with skeletons is a flash.
- **`app.js` is still the monolith** (E2). Windowing does not require ES modules. Selection is already a `Set` of `rel_path`, which is what a recycled card window needs.

`content-visibility` is asserted in `tests/ui/test_browse_and_paging.js`. Keep it. It is not a substitute for windowing at this archive size, and it does not help first paint of newest.

---

## 6. Contention the query numbers do not show

One SQLite connection behind `RLock`. WAL is on, but WAL only helps if there **are** concurrent readers. Gallery reads queue behind:

- classify `media_verdicts` writes
- scrape `upsert_photo`
- `/api/stats` (video LIKE scan + `verdict_facet_counts` full join + `unclassified_total`)
- `list_creators` (rollup + verdict counts)

`busy_timeout=5000` means a write can stall the grid for five seconds. `list_creators` max of 1,601 ms and `query_offset_1200` min of 390 ms vs median 1,627 ms are the lock showing up in the same probe.

`/api/stats` is on `initApp` in parallel with `fetchPhotos` (good — health/Ollama no longer blocks the first page). It still shares the writer lock with that page.

---

## 7. Ranked improvements

Do these in order. Backend first: windowing a 1.7 s newest page still feels like 1.7 s.

### P0.1 — Make `/api/photos` a covering, indexed page

Four mechanical changes in `query_photos`. No new dependency.

**Stop joining for the count.** If the filter does not mention verdict or label, `SELECT COUNT(*) FROM photos WHERE …` is enough. Join `labels` only when `label=` is set (210 rows, label-mode only). Join `media_verdicts` on the COUNT only when `verdict=` or `sort=tier`. The *page* SELECT still needs the verdict join for badges.

**Stop `SELECT p.*`.** Project what the grid uses: `rel_path, creator, filename, favorite, has_prompt, prompt_stale, taken_at, added_at, post_id, shortcode, source, p_keep`, plus the badge fields (`tier, manual`, maybe `reason`). Drop `prompt_search`, `caption_search`, leftover `glam_*`, and unused verdict columns (`prompt_version, classified_at, sheet_path, confidence, error, media_kind, verdict_source`) from the grid payload. Lightbox already has `/api/media/detail`.

**Stop wrapping indexed columns in `IFNULL` / `CASE`.**

```sql
-- today, cannot use idx_photos_added
ORDER BY IFNULL(p.added_at, p.mtime) DESC, p.filename ASC

-- added_at is never null on this archive (measured)
ORDER BY p.added_at DESC, p.filename ASC
```

Coalesce at **write** time in `upsert_photo`, not in the ORDER BY. Add composite `(added_at DESC, filename)` so the filename tie-break does not build a temp B-tree. Same idea for posted: index `mtime` and `ORDER BY p.mtime DESC` — `mtime` is also never null here. Keep a write-time fallback if a future row is missing either column.

**Detect “has more” with `LIMIT n+1`, not a second COUNT.** Exact `total` can stay for the “60 / 61344” label, but it should not be on the critical path. Cache it per filter fingerprint; invalidate on upsert/delete. Grouped queries still need an exact group count if the sentinel pages by posts — S10 was right that an estimate skips content — but ungrouped `has_more` does not need `COUNT(*)`.

Also cheap in the same pass:

- `media_kind` column (`photo` / `video`) written at upsert. `stats()` and `media_type=` currently `LOWER(filename) LIKE '%.mp4' …` — a **61 ms** full scan on every `/api/stats`.
- Drop unused `cover_url` / `cover_thumb_url` from `list_creators` unless the open creator panel needs them.

Expected: newest page from **~1.7 s → tens of milliseconds**, in line with the 0.2 ms slim+indexed statement already measured.

Tests: extend `scripts/benchmark_queries.py` with a 60k-shaped case (or a live-archive opt-in that is **not** a CI gate — same policy as E5b). Extend `tests/test_sort_newest.py` to assert `EXPLAIN QUERY PLAN` contains `idx_photos_added` for `sort=newest`. A new test that `COUNT` SQL has no `LEFT JOIN` when `verdict` and `label` are unset.

### P0.2 — HTTP/1.1 keep-alive

`GalleryRequestHandler` never sets `protocol_version`. Python’s `BaseHTTPRequestHandler` default is `HTTP/1.0`. `_serve_local_file` and `_send_json` already send `Content-Length`.

```python
protocol_version = "HTTP/1.1"
```

Sixty thumbs become six reused connections instead of sixty handshakes. Minutes of work. Test with a browser suite or a curl `-H 'Connection: keep-alive'` loop against `/media/thumb/…` that the connection is reused (or assert the response status line is `HTTP/1.1`).

Optionally raise the 64 KB copy loop in `_serve_local_file`. Skip the `os.path.getmtime` pair on the thumb hit path once ingest generation is trusted (“exists ⇒ valid”).

Do **not** put nginx in front of a loopback studio for this.

### P0.3 — Thumbnails must not be created on GET

After ingest, the newest 500 files are **91% unthumbed**. The browser then asks for 60 of them at once.

1. **Generate at ingest.** Downloader / `upsert_photo` / upload enqueue a thumb. A short worker (or an existing job thread) writes `_thumbs/…jpg`. The GET path only serves a file that already exists.
2. **GET never encodes.** If the thumb is missing, return a tiny placeholder (or 202) and kick the worker. Never fall back to the original as a tile.
3. **Match tile size.** 256 px, quality ~70, no `optimize`. Cards are 220 / 320 CSS px. Add `decoding="async"` on the `<img>` (alongside existing `loading="lazy"`).

Video: at download time, write the companion cover still `ensure_thumbnail` already looks for. **Never** run `write_best_video_frame_jpeg` on a gallery GET.

Migration: a `scripts/` CLI, nice-idle, backfill for the existing 61k. After that, ingest keeps it warm. Census to beat: 12,148 → ~61,344, with newest-500 coverage near 100% immediately after scrape.

### P1 — SQLite reader pool

One writer connection, N `mode=ro` readers for `query_photos` / `list_creators` / `stats`. Gallery stays live during overnight classify. This is the fix for hitching-while-a-job-runs, which indexes will not touch.

Keep the writer `RLock` for upserts, verdicts, rebuild. Do not share a write connection across threads without it.

Cache `/api/stats` facet counts until the next classify finish. Video counts become instant with `media_kind`.

### P1 — Frontend windowing, after P0

Keep `state.photos` as the model (lightbox, `selectedPaths`, keyboard nav already index it). Recycle ~80–120 card elements.

- Clone a `<template>` instead of HTML strings per card.
- Select-mode: toggle a class on the grid, do not rebuild.
- Creator clicks: patch `.active`, do not rebuild 150 rows.
- Lightbox: paint `thumb_url` immediately, then swap to `/media/…`. Preload ±1.
- Filter change: do not skeleton-replace a populated grid until the request is slower than ~100 ms (newest currently is; it will not be after P0.1).

Do not split `app.js` into ES modules for speed (E2). Ownership comments and a single paging mutator matter more than a bundler.

Ctrl-F will not search off-screen cards under a window. Acceptable: the search box already hits the server.

---

## 8. What not to do

| Tempting | Why not |
|----------|---------|
| Enable FTS5 (`PROMPTSTUDIO_FTS_SEARCH=1`) | Already measured at 4.4k/40k: **3× slower** on common terms because `IN (subquery)` materialises every match. Revisit only if LIKE *itself* shows up in a profile **after** the sort/count fixes. The 580 ms `"dress"` search on this archive is the unindexed newest ORDER BY, not FTS vs LIKE |
| Incremental `rebuild()` keyed on mtime | S8: sidecar mtime skip was measured and dropped. Cost was four sidecar opens per file, fixed by `read_sidecar()`. A skip misses classify-written sidecars, which is why `rebuild()` exists |
| Approximate grouped `total` | Infinite scroll will skip posts. Use `LIMIT n+1` for `has_more`; keep exact totals off the first-byte path |
| Virtualize before fixing thumbs/SQL | Windowing a 1.7 s newest page still feels like 1.7 s |
| A new web framework / nginx | Loopback, no auth, stdlib on purpose. HTTP/1.1 + ingest thumbs is the missing layer, not a reverse proxy |
| `SELECT p.*` “because the row is the API” | The API is `_row_to_photo`. Wide leftover `glam_*` columns are a measured 267 ms vs 0.2 ms |

---

## 9. Suggested implementation order

1. **SQL (half a day, immediately visible).** Drop unused joins on COUNT, slim SELECT, `ORDER BY added_at` / `mtime` with composite indexes, `LIMIT n+1`. Benchmark + EXPLAIN tests. This is the first PR.
2. **HTTP/1.1 (minutes).** `protocol_version`. Can land in the same PR as (1).
3. **Thumbs at ingest + GET never encodes (a day).** Worker, backfill CLI, smaller JPEG. This is the post-scrape smoothness fix.
4. **Reader pool (half a day).** Gallery during classify/scrape.
5. **Grid windowing + select-mode without full render (a day).** Only after 1–3, or recycled cards still wait on 449 ms encodes.

Done when a first page of newest is **~10–30 ms JSON, 60 cache-hit thumbs, paint**, and a scrape of N files leaves N new thumbs before the user switches to newest. Windowing then keeps page 20 as smooth as page 1.

---

## 10. How to re-measure

Not a CI gate — timings on a shared runner are noise (same policy as `scripts/benchmark_queries.py`). Run on the machine that has the archive.

```powershell
# Catalog size + coverage
py -c "import os,sqlite3; p=os.path.expanduser('~/Pictures/InstagramSaved/archive.db'); c=sqlite3.connect('file:'+p+'?mode=ro', uri=True); print('photos', c.execute('SELECT COUNT(*) FROM photos').fetchone()[0])"

# EXPLAIN must show idx_photos_added for newest after P0.1
# (paste the live ORDER BY into EXPLAIN QUERY PLAN)

# Wrapper timings — extend scripts/benchmark_queries.py rather than a one-off
py scripts/benchmark_queries.py --rows 4400 --rows 40000

# Thumb census
# count files under <archive>/_thumbs vs COUNT(*) FROM photos
```

Pass criteria for the SQL PR, on this archive or a seed of similar width:

| Probe | Today | After |
|-------|------:|------:|
| `EXPLAIN` newest LIMIT 60 | `SCAN p` + temp B-tree | `USING INDEX idx_photos_added` (or the new composite) |
| `query_photos(sort='newest', limit=60)` | ~1,700 ms | tens of ms, same order as name-sort |
| COUNT SQL when no verdict/label | two LEFT JOINs | no joins |
| `GET /media/thumb` on a hit | HTTP/1.0, mtime pair | HTTP/1.1, no encode |
| newest-500 thumb coverage after scrape | ~9% | ~100% (after P0.3) |

---

## 11. Code index (so the next session does not rescan)

| Concern | File |
|---------|------|
| Gallery fetch / paging / render | `app.js` — `fetchPhotos`, `galleryQueryParams`, `renderGallery`, `rebuildGalleryTiles`, `observeLoadMoreSentinel` |
| Card CSS / `content-visibility` | `style.css` `.photo-card` |
| Route + JSON + thumb GET | `promptstudio/server/handler.py` — `/api/photos`, `/media/thumb/`, `_serve_local_file`, `_expand_post_groups`, `_send_json` |
| Query + COUNT + ORDER BY | `promptstudio/storage/db.py` — `query_photos`, `_photo_select`, `_row_to_photo` |
| Thumb encode | `promptstudio/storage/thumbs.py` — `ensure_thumbnail` (GET-only caller) |
| Video frame rank (must not run on GET) | `promptstudio/scraping/video_frames.py` `write_best_video_frame_jpeg` |
| Page cap / thumb size | `promptstudio/config.py` `MAX_PHOTOS_API_PAGE`, `THUMB_MAX_SIZE` |
| Query benchmark harness | `scripts/benchmark_queries.py` |
| Browser paging / content-visibility | `tests/ui/test_browse_and_paging.js` |
| Newest sort tests | `tests/test_sort_newest.py` |
