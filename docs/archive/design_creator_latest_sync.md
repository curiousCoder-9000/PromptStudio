# Creator “Sync latest” + never re-download deleted posts

| Field | Value |
|-------|--------|
| **Status** | Implemented |
| **Date** | 2026-08-08 |
| **Depends on** | Serial scrape queue / `SyncManager` single-flight (already shipped) |

---

## Problem

### What the product needs

For an **existing** creator folder:

1. A **Sync** action that pulls **only new / not-yet-local** posts (latest tip of the feed).
2. **Never re-download** media that is already on disk (already true for complete posts).
3. **Never re-download** posts the user **already deleted** (local intentional delete).

### What the code does today (gap)

| Situation | Today | Desired |
|-----------|--------|---------|
| Post complete on disk | Skip via `_post_archive_state` → `complete` | Same |
| Incomplete carousel | Re-fetch missing slides | Same (fill gaps) |
| User deletes photo via UI | File removed; index row deleted; **no memory of shortcode** | **Tombstone** shortcode/post_id → forever skip |
| File deleted outside UI | Stale index dropped in `carousel_paths` → treated as missing → **re-download** | Prefer tombstone when last slide of identity vanishes via API; disk-only deletes: optional “Forget / never re-fetch” later |
| Catch-up stop | `CATCH_UP_STREAK` consecutive **complete** only | Streak must count **complete + tombstoned** as “known” |

Evidence:

- [`docs/instagram_downloader.md`](../instagram_downloader.md): *“Deleted locally → Index row cleared → re-download if still in feed”* — opposite of product intent.
- [`ArchiveIndex.delete_photo`](../../promptstudio/storage/db.py) only `DELETE FROM photos` — no identity retention.
- [`_post_archive_state`](../../promptstudio/scraping/downloader.py) only inspects on-disk/index completeness.

---

## Goals & non-goals

### Goals

1. **Tombstones** for Instagram identities (`shortcode` and/or `post_id`) when the user deletes media through PromptStudio.
2. Downloader treats tombstones as **do-not-download** (and as “known” for catch-up).
3. **Per-creator “Sync latest”** UX: one click on an existing creator → serial IG job walks newest posts, downloads only missing, stops after catch-up streak.
4. Same global IG mutex as everything else (`SyncManager` / scrape queue fairness).

### Non-goals (V1)

- Undelete / restore from Instagram (tombstone is permanent until explicit “allow re-download” admin action).
- Detect every external Explorer delete without API (no FS watcher).
- Full-history re-scan by default (that remains **full + deep** enqueue).
- Auto-delete on classify (still forbidden).

---

## Proposed design

### 1. Tombstone store (SQLite)

New table in `archive.db` (same DB as photos — identity already lives there):

```sql
CREATE TABLE IF NOT EXISTS deleted_posts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  creator TEXT NOT NULL,
  shortcode TEXT,
  post_id TEXT,
  rel_path TEXT,          -- last known path (debug / history)
  deleted_at TEXT NOT NULL,
  source TEXT DEFAULT 'ui'  -- ui | api | import
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_deleted_shortcode
  ON deleted_posts(creator, shortcode) WHERE shortcode IS NOT NULL AND shortcode != '';
CREATE UNIQUE INDEX IF NOT EXISTS idx_deleted_post_id
  ON deleted_posts(creator, post_id) WHERE post_id IS NOT NULL AND post_id != '';
CREATE INDEX IF NOT EXISTS idx_deleted_creator ON deleted_posts(creator);
```

**API helpers** on `ArchiveIndex`:

| Method | Behavior |
|--------|----------|
| `record_deleted_post(creator, *, shortcode, post_id, rel_path, source)` | Upsert tombstone; ignore empty identities |
| `is_deleted_post(creator, *, shortcode, post_id) -> bool` | Match shortcode **or** post_id for that creator |
| `list_deleted_posts(creator) -> list` | Optional UI/debug |
| `clear_deleted_post(...)` | Explicit “allow re-download” (not in UI V1) |

**Keying:** Prefer `shortcode` (stable in Instaloader). Always store both when available. Lookup: `(creator AND shortcode)` OR `(creator AND post_id)`.

**Creator normalize:** lowercase folder name / handle used by archive (`roxeuoon`), same as `photos.creator`.

### 2. When to write a tombstone

#### A. UI / API delete (required)

`DELETE /api/photo` → `ArchiveStore.delete_photo`:

1. Resolve full path.
2. Load identity: index row `post_id`/`shortcode`, else `*.meta.json` sidecar.
3. **If either identity present** → `record_deleted_post(creator, ...)`.
4. Then existing cleanup: file, meta, thumb, favorites, prompt cache, index row.

Carousel case: deleting **one slide** still tombstones the **whole post** (same shortcode). Remaining slides on disk are left as-is until user deletes them; new downloads for that shortcode are blocked. Optional V1.1: if other slides remain, only tombstone when **last** media for that identity is removed. **Recommendation V1: tombstone on any slide delete** so user intent “I don’t want this post” is respected; remaining orphan slides can be deleted manually.

#### B. Index stale cleanup (optional V1)

Today `carousel_paths` deletes stale index rows when file missing — that **enables** re-download. Change:

- If file missing for a path that still has identity → **record tombstone then drop index** (treat as intentional loss), **or** leave as-is for external deletes.

**Recommendation V1:** Only tombstone from explicit `delete_photo` path. Do **not** auto-tombstone on stale index alone (avoids locking out re-download after accidental disk wipe). Document: “delete via app to prevent re-fetch.”

### 3. Downloader: respect tombstones

In `InstagramDownloader._post_archive_state` (or a thin wrapper used by all feed loops):

```text
if is_deleted_post(creator, shortcode, post_id):
    return "deleted"   # new terminal state for skip
# else existing complete / incomplete / missing
```

Call sites:

| Mode | On `deleted` |
|------|----------------|
| bounded collect | skip (do not add to candidates) |
| full stream | skip; **do not** reset catch-up when `deep=false`; when `deep=true` skip without counting toward ceiling |
| catch-up / latest | skip; count toward `CATCH_UP_STREAK` like `complete` |

`_download_post`: early return if tombstoned (defense in depth).

**Incomplete vs deleted:** if tombstoned, never fill carousel gaps.

### 4. “Sync latest” algorithm (existing creators)

New explicit mode (or policy flags) — recommend **named mode** for clarity:

```python
mode="latest"  # alias: catch-up sync for existing folders
```

Semantics:

| Knob | Value |
|------|--------|
| Ranking | **Off** (chronological newest-first is enough for “what’s new”) |
| Scan | Stream `get_posts()` newest → older |
| Download | Only `missing` or `incomplete` (not tombstoned) |
| Stop | After `CATCH_UP_STREAK` consecutive posts that are `complete` **or** `deleted` |
| Ceiling | Soft cap `max_posts` (default 50) on **new downloads**, same as bounded |
| Videos | Respect `include_videos` |
| Folder | Must already exist (or ensure_creator_folder idempotent) |

```text
consecutive_known = 0
for post in profile.get_posts():
  if cancel/abort: break
  if video and not include_videos: continue
  state = archive_state(post)  # complete | incomplete | missing | deleted
  if state in (complete, deleted):
    consecutive_known += 1
    if consecutive_known >= CATCH_UP_STREAK: stop_reason=catch_up; break
    continue
  consecutive_known = 0
  download if missing/incomplete
  if downloaded >= max_posts: stop_reason=ceiling; break
```

**Why not reuse `mode=bounded`?** Bounded still does glam rank + scan window (`POST_SCAN_FACTOR`), which is wrong for “just get newest missing.”  
**Why not `mode=full, deep=false`?** Full with catch-up is close, but full still walks until streak/ceiling without glam; either reuse full+`deep=false` **or** add `latest` as a clear alias of “full stream + catch-up on + download ceiling = max_posts”.  

**Recommendation:** Implement `mode="latest"` as a thin alias of streaming loop with `use_catch_up=True` and default ceiling = `DEFAULT_MAX_POSTS_PER_CREATOR`, so UI/API stay readable. Internally share code with `_sync_creator_feed_full(..., deep=False)` + default max_posts.

### 5. API surface

#### Option A (preferred): enqueue into existing scrape queue

```http
POST /api/scrape/enqueue
{
  "username": "roxeuoon",
  "mode": "latest",
  "max_posts": 50,
  "include_videos": true
}
```

- Same serial drain, pause-on-abort, fairness 409.
- `deep` ignored for `latest` (always catch-up on).

#### Option B: one-shot

```http
POST /api/sync/creator
{ "username": "roxeuoon", "mode": "latest", "max_posts": 50 }
```

Still subject to queue fairness when pending scrape jobs exist.

**V1:** Support **both** (enqueue for multi-creator, one-shot for single). UI “Sync” on creator card uses **enqueue `mode=latest`** so it queues behind full scrapes safely.

### 6. UI

| Place | Control |
|-------|---------|
| Creator sidebar / selected creator toolbar | **Sync new** button (icon `fa-arrows-rotate`) |
| Disabled when | No creator selected; or IG job running **and** same creator already running (optional); still allow enqueue if only other jobs pending |
| Click | `POST /api/scrape/enqueue` `{ username, mode: "latest", max_posts: 50, include_videos }` |
| Toast | “Queued latest sync for @x” / “Started…” / “Already pending” |
| Sync modal | Optional second action “Sync latest only” next to full archive (bounded checkbox already partial) |

Also: when deleting a photo, toast may say “Won’t re-download this post on future syncs” (once) — optional.

### 7. Edge cases

| Case | Behavior |
|------|----------|
| Delete photo with **no** shortcode/post_id (legacy file) | Cannot tombstone; may reappear if IG feed still has it — log warning; backfill identity scripts help |
| Full deep scrape after delete | Tombstone still wins — **never** re-download deleted (all modes) |
| User wants post back | Future: “Clear tombstone” in settings; V1 not exposed |
| Private / not found | Same as scrape queue error paths |
| Incomplete after partial crash | Still `incomplete` → fill slides (not tombstoned) |
| Same shortcode different creator | Impossible on IG; key includes creator folder for safety |
| Bulk delete selection | Each path records tombstone before unlink |

### 8. Mutual exclusion

Unchanged: all paths use `SyncManager`. Latest sync is just another job type payload (`mode=latest`) under `creator` or `creator_queue`.

### 9. Observability

- Log: `Skip (deleted): @user SHORTCODE`
- `SyncResult.stop_reason`: reuse `catch_up` | `ceiling` | `end_of_feed` | …
- Optional counter: `skipped_deleted` on `SyncResult` for UI (“12 new, 3 skipped deleted”)

### 10. Migration

- No backfill of historical deletes (unknown).
- Schema migrate on `ArchiveIndex` connect (same pattern as glam_score columns).

---

## Alternatives considered

| Alternative | Why not V1 |
|-------------|------------|
| Only catch-up without tombstones | Fails product requirement on deleted posts |
| Tombstone JSON file per creator | SQLite already has identity indexes; transactional with delete |
| Use `sync_state.json` last_shortcode as sole stop | Already rejected historically (missed older gaps) |
| Soft-delete files to trash folder | Heavier UX; tombstone is enough for re-fetch prevention |

---

## Implementation PR plan

### PR1 — Tombstones on delete

- `db.py`: table + `record_deleted_post` / `is_deleted_post`
- `archive.delete_photo` / handler delete: record before remove
- Unit-style manual test: delete photo, assert row in `deleted_posts`

### PR2 — Downloader honors tombstones + `mode=latest`

- `_post_archive_state` → `deleted`
- Full/bounded/saved loops skip deleted
- Catch-up streak includes deleted
- `mode=latest` (or `full`+`deep=false` wired as latest defaults)
- CLI: `download_creator_feed.py HANDLE --latest`

### PR3 — API + UI “Sync new”

- Accept `mode=latest` on scrape enqueue + sync/creator
- Creator toolbar button
- Docs: reverse the “re-download after delete” line in `instagram_downloader.md`

### PR4 (optional) — `skipped_deleted` metrics + clear-tombstone admin

---

## Key decisions

1. **Tombstones in SQLite** keyed by creator + shortcode/post_id.
2. **Tombstone only on explicit app delete** (not silent disk loss).
3. **All download modes** respect tombstones (full/deep still never resurrects deleted).
4. **Sync latest** = stream newest-first, catch-up on, no glam rank, serial via existing queue.
5. **Carousel:** any slide delete tombstones whole post (V1).

---

## Open questions (defaults if you want ship-as-is)

| # | Question | Default if unstated |
|---|----------|---------------------|
| 1 | Tombstone on **any** carousel slide delete vs only last slide? | **Any** slide |
| 2 | Auto-tombstone when index finds missing file? | **No** (explicit delete only) |
| 3 | Sync button: enqueue queue vs one-shot? | **Enqueue `mode=latest`** |
| 4 | Default `max_posts` for latest? | **50** |

---

## References

- [`promptstudio/scraping/downloader.py`](../../promptstudio/scraping/downloader.py) — `_post_archive_state`, feed modes
- [`promptstudio/storage/db.py`](../../promptstudio/storage/db.py) — photos identity, `delete_photo`
- [`docs/design_creator_scrape_queue.md`](design_creator_scrape_queue.md) — serial queue
- [`docs/instagram_downloader.md`](../instagram_downloader.md) — resume table (to be updated)
