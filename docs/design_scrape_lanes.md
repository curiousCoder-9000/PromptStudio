# Design — Per-source scrape lanes

**Status:** accepted, not implemented · **Date:** 2026-08-10
**Sequence:** ships after [design_source_filter.md](design_source_filter.md).

Delivers the follow-up that `multi_source_scraping.md` §6 already names:

> Only one scrape job runs at a time globally, as before — single-flight is an
> Instagram requirement, but it's kept for all sources for now. Per-source
> concurrency (Reddit could safely run alongside Instagram) is a deliberate
> follow-up, not part of this work.

---

## 1. Shape

Three **lanes** — `instagram`, `x`, `reddit` — each **width 1**. Maximum three
concurrent scrape jobs; Instagram is pinned to one forever.

A lane is the unit of *concurrency, cancellation, pausing and pacing*. Those four
are global today, and each one is a distinct defect once two sources run at once.

Lanes are derived from `known_sources()`, so registering a fourth source in
`sources/__init__.py:_REGISTRY` creates its lane with no further edits.

## 2. What already supports this

Worth stating, because it bounds the work — none of this needs changing:

| Seam | Where |
|------|-------|
| Queue identity is already `(source, username)` | `creator_queue.py:35-45` |
| Jobs already store `source`; legacy files backfill to `instagram` | `creator_queue.py:112-117` |
| Folders already disambiguated per platform — lanes cannot collide on disk | `sources/base.py:43-62` |
| `photos.source` + `deleted_posts.platform` scope post identity per platform | `db.py:62,184` |
| SQLite is concurrency-ready: `check_same_thread=False`, RLock, WAL, `busy_timeout=5000` | `db.py:340-366` |
| `RunJournal` appends under a lock | `journal.py:140` |
| `creator_scrape_queue.json` writes are RLock + `atomic_write_json` | `creator_queue.py:122-124` |
| `MediaSource.run()` and `InstagramDownloader` are per-job instances, no module state | `instagram_source.py:56` |
| `LeaseRegistry` already does atomic all-or-nothing multi-resource acquire | `jobs.py:58-78` |

Ollama and Comfy leases are untouched — different resource names, no new contention.

## 3. `jobs.py` — one lease name per source

`SyncManager.start_job` acquires `INSTAGRAM` unconditionally (`sync_manager.py:199`).
A Reddit job takes the *Instagram* lease. That single line is what serialises
everything.

Add `scrape_resource(source) -> f"scrape:{source}"`. Redefine `INSTAGRAM` as
`"scrape:instagram"` so it stays a working alias — `/api/health`'s lease snapshot
and `tests/test_job_leases.py` keep passing unchanged. Derive `ALL_RESOURCES` from
`known_sources()` instead of the hardcoded tuple at `jobs.py:48`.

No new primitive. The registry was already the right abstraction; it just had the
wrong names in it.

## 4. `creator_queue.py` — lanes inside one file

One file, per-source lanes. Not one file per source: the `(source, username)`
dedupe, the `CREATOR_SCRAPE_MAX_PENDING` cap and the shared history all live in one
place already, and a single RLock plus `atomic_write_json` serialises concurrent
lane writes correctly within one process.

```jsonc
{
  "version": 2,
  "jobs":    [ { "id": …, "source": "x", "username": …, … } ],   // unchanged
  "history": [ … ],                                              // unchanged
  "lanes": {
    "instagram": { "paused": false, "pause_reason": "", "paused_at": null,
                   "finished_session": 3, "stats": { … } },
    "x":         { "paused": true,  "pause_reason": "cookies expired", … },
    "reddit":    { "paused": false, … }
  }
}
```

**Migration:** `_load()` folds a v1 file's top-level `paused` / `pause_reason` /
`paused_at` / `stats` into the instagram lane and seeds the rest as idle — the same
shape as the existing source-backfill at `creator_queue.py:112-117`. No separate
migration step, no new file.

**Methods gaining a `source`:** `peek_next`, `is_paused`, `pause`, `resume`,
`pending_count`, `should_account_pause_before`, `should_batch_pause`,
`record_stats_from_result`. `pause(source=None)` pauses every lane, preserving the
existing global-pause button.

`status_snapshot()` gains `lanes`; its flat keys become the union across lanes
(`paused` = all lanes paused, `pending_count` = total) so existing readers degrade
sensibly rather than breaking.

### 4.1 A live bug this fixes

`_jobs_finished_session` is one counter (`creator_queue.py:449-459`). Once lanes
run, a **Reddit** job finishing increments the counter that triggers **Instagram's**
5–15 minute `BATCH_PAUSE`. It becomes per-source.

## 5. `sync_manager.py` — the real work

Extract a `ScrapeLane` owning what the singleton owns today: its own `_status`
dict, its own `threading.Event` for cancel, its own `RunHandle`. `SyncManager`
holds `_lanes: Dict[str, ScrapeLane]`.

### 5.1 The critical line

```python
ctx = SourceContext(..., should_cancel=self.is_cancel_requested, ...)  # :471
```

Every running source binds to one process-wide Event. **Cancel X today and Reddit
dies with it.** It becomes `lane.is_cancel_requested`. This is the single most
important change in the spec; everything else is bookkeeping around it.

### 5.2 Signature changes

| Method | Change |
|--------|--------|
| `start_job(job_type, fn, *, source)` | acquires `scrape_resource(source)` |
| `request_cancel(source=None)` | `None` cancels every lane — keeps `/api/sync/cancel` honest |
| `is_running(source=None)` | `None` = any lane busy |
| `try_drain_creator_queue(source=None)` | `None` sweeps every idle, unpaused lane |
| `get_status()` | instagram lane's flat keys **plus** `lanes`, for back-compat |

The `finally` block's re-drain (`sync_manager.py:317-328`) drains its own lane, then
sweeps — a lane that just freed a lease should not wait for another lane's job.

### 5.3 Failure isolation

The abuse-signal pause at `sync_manager.py:491,523` pauses only the failing lane. An
expired X cookie must not stop Instagram. Global pause remains available as an
explicit user action.

### 5.4 `sync_status.json`

Becomes `{"lanes": {…}}`. `_load_status` migrates a legacy flat file into the
instagram lane. `_recover_stuck_running` runs per lane.

### 5.5 Instagram one-shots

`saved`, `following` and oneshot `creator` pass `source="instagram"`.
`_creator_queue_blocks_oneshot()` (`handler.py:84`) narrows to the instagram lane —
today a pending *Reddit* job blocks an Instagram saved-posts sync for no reason.

## 6. `checkpoints.py` — close the lost-update window

`update()` is load → mutate → write-whole-dict with no lock
(`checkpoints.py:40-57`). Safe today only because exactly one thread scrapes.

[design_source_filter.md](design_source_filter.md) §6 makes gallery-dl lanes write
checkpoints too, so a module-level `threading.Lock` around load→mutate→save must
land **before** lanes ship. Four lines, and it is the difference between correct
and silently losing writes. It is also what would otherwise pin Instagram to one
job for the wrong reason.

## 7. `handler.py` / `app.js`

- `/api/scrape/pause`, `/api/scrape/resume`, `/api/scrape/cancel` take an optional
  `source`; a `job_id` resolves its own lane. `/api/sync/cancel` = all lanes.
- `/api/scrape/status` gains `lanes`.
- Enqueue drains only the new job's lane.
- Chips: one per lane, keyed `scrape:<source>`, each with its own Cancel and Pause.
  `renderJobChip()` builds from a template instead of looking up three hand-written
  static id blocks (`index.html:796-813`). The "one chip per job kind" invariant in
  AGENTS.md becomes "one chip per job kind, where a scrape lane is a kind" —
  AGENTS.md §UI needs the matching edit.
- `setOneShotSyncEnabled` keys on the instagram lane only.

## 8. Pacing gets un-borrowed

Reddit currently inherits `IG_ACCOUNT_PAUSE_MIN/MAX` (30–120s between creators) and
`IG_BATCH_PAUSE_MIN/MAX` (5–15 min). Those are Instagram anti-ban constants;
gallery-dl already self-paces via `SCRAPE_SLEEP`, `SCRAPE_SLEEP_REQUEST` and
`SCRAPE_SLEEP_429`. Per-lane pacing config, defaulting to near-zero for gallery-dl
lanes and unchanged for Instagram.

Instagram's pacing is not relaxed anywhere in this work. Lanes make the *other*
sources faster; they must not make Instagram more detectable.

## 9. Risks

| Risk | Mitigation |
|------|------------|
| Instagram exceeding one concurrent job | lease + lane width 1; asserted in tests |
| Lost checkpoint writes | §6 lock, landed first |
| Two lanes writing `archive.db` | already safe — WAL + RLock + `busy_timeout` |
| Two lanes rewriting the queue file | already safe — RLock + atomic write, one process |
| Two lanes appending `_journal/sync.jsonl` | already safe — `RunJournal` lock |
| Clients reading the old flat status shape | flat keys retained as the instagram-lane projection |

## 10. Testing

`tests/test_scrape_lanes.py`:

- cancelling X leaves Reddit running (the `should_cancel` regression, §5.1)
- an X auth failure pauses only the X lane
- three fake sources drain concurrently; Instagram never exceeds one job
- a Reddit job finishing does not trigger Instagram's batch pause (§4.1)
- a pending Reddit job does not block an Instagram one-shot (§5.5)
- v1 `creator_scrape_queue.json` and a flat `sync_status.json` both migrate

`tests/test_job_leases.py` (extend): two different sources acquire concurrently;
the same source blocks with an attributable holder name.

`tests/test_source_dispatch.py` (extend): lane-scoped `peek_next` / `pause` /
`resume`; per-lane finished counters.

`tests/ui/`: three chips render, and Cancel on one leaves the others running.

## 11. Out of scope

- Lane width > 1 for any source.
- A `following`-style bulk mode for X or Reddit (still one target per job).
- Ranking for non-Instagram sources — `score_instagram_post` stays Instagram-only.
- Making Ollama and Comfy mutually exclusive; unrelated product decision
  (`jobs.py:28-31`).
