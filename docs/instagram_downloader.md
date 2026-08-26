# Instagram Saved Images Downloader & Sync Guide

Agent map: [context.md](context.md).

Local archive: `~/Pictures/InstagramSaved` (`PROMPTSTUDIO_ARCHIVE`)  
Session user: `promptstudio.config.SESSION_USER` from `INSTAGRAM_SESSION_USER` in `.env` (required for scrape)

## Fetch backend (`IG_BACKEND`)

Instagram stays one source / one lane / bare-handle folders. The tool that
talks to Instagram is switchable:

| `IG_BACKEND` | Auth | First request | Cancel |
|--------------|------|---------------|--------|
| `instaloader` (default) | `session-<user>` file | `Profile.from_username()` → `web_profile_info` | cooperative; Instaloader’s 429 sleep is **not** interruptible |
| `gallery-dl` | `IG_COOKIES_FILE` or `SCRAPE_COOKIES_FROM_BROWSER` | gallery-dl `user-strategy=search,web` — **never** `web_profile_info` | kills the subprocess |

gallery-dl + a real logged-in browser session is the **most reliable option in
practice** right now (Instaloader issue #2726). It is not undetectable.
Instagram can still challenge, rate-limit, or lock the account.

```ini
IG_BACKEND=gallery-dl
SCRAPE_COOKIES_FROM_BROWSER=brave
# or: IG_COOKIES_FILE=C:\path\to\ig-cookies.txt
```

Restart the server after changing `IG_BACKEND`. Close Brave/Chrome while
scraping if cookie-DB read fails; Chromium locks `Cookies` SQLite. If `brave`
fails: `chrome`, then a profile path, then export Netscape cookies.txt.
Do not run the logged-in browser and a scrape at the same time — they share
Instagram’s rate budget.

Saved posts on the gallery-dl backend use
`https://www.instagram.com/{INSTAGRAM_SESSION_USER}/saved/` (the session user
is still required for the URL, even though the Instaloader session file is
not). Following bulk keeps `following_list.json` and Instagram lane pacing;
each account is a gallery-dl profile scrape (chronological, **no caption rank**).

---

## Sync modes

| Mode | CLI | API |
|------|-----|-----|
| Saved posts | `py scripts/download_instagram_saved.py` | `POST /api/sync/saved` |
| Creator feed | `py scripts/download_creator_feed.py HANDLE --max-posts 50` | `POST /api/sync/creator` |
| Following bulk | `py scripts/download_following.py` | `POST /api/sync/following` |

**Videos / reels:** default **ON** (`IG_INCLUDE_VIDEOS=1`, config `INCLUDE_VIDEOS_DEFAULT`).  
CLI: omit flag (default on), or `--no-reels` / `--include-reels`.  
API/UI: `include_videos` body field + Sync modal checkbox.

### Acquisition playbook

```powershell
# 1) Refresh following + bios
py scripts/export_following_list.py

# 2) Order the daily queue
py scripts/prioritize_following_queue.py
# dry-run:  py scripts/prioritize_following_queue.py --dry-run

# 3) Bulk pull (reels on by default; caption-rank inside each feed)
py scripts/download_following.py --accounts-per-day 15 --max-posts 30

# High-value single creator
py scripts/download_creator_feed.py HANDLE --max-posts 50

# User-saved posts (always trust saved; no keyword filter)
py scripts/download_instagram_saved.py
```

**Feed ranking (PR3):** when `IG_POST_RANK=1` (default), each creator feed scans up to `max_posts * IG_POST_SCAN_FACTOR` (default 3×) posts, scores captions/reels/carousels, then downloads the top `max_posts`.

**Media scoring:** removed. `glam_score` and the whole vision-classify
subsystem (prompts, `_GLAM_COLUMNS`, the Sexy / Rejects / Unscored chips,
`sort=glam`, `/api/classify/*`) were deleted; only the video **frame ranker**
survives, because thumbnails and near-duplicate detection use it
(`CLASSIFY_REEL_*` in `.env.example`).

Export following list first (includes biographies for keyword filters):

```powershell
py scripts/export_following_list.py
py scripts/download_following.py --accounts-per-day 20 --max-posts 30 --keywords ""
```

Use `--keywords ""` to disable the bio keyword filter and queue every public account that passes `min_media`.

---

## Phase A — Anti-ban pacing

Following sync is designed for **multi-day** crawls, not one overnight dump. Instaloader also applies its own per-request sleep (`sleep=True`).

| Behaviour | Default |
|-----------|---------|
| Post delay | random 4–12 s after each successful download (`IG_POST_DELAY_MIN` / `IG_POST_DELAY_MAX`) |
| Between accounts / queue jobs | random 30–120 s |
| Soft batch pause | every 10 accounts, random 5–15 min |
| Daily / run cap | 20 accounts (`--accounts-per-day` / `max_accounts`) |
| Hard abort | 3 consecutive rate-limits, or `feedback_required` / `challenge_required` / `PleaseWaitFewMinutes` |

**UI “Sync new posts”** enqueues `mode=full, deep=true` (walk entire feed, download every missing post, no early catch-up stop).

**Server safety (2026-08-08):** `mode=latest` without `catch_up_only=true` is **upgraded to full+deep** in `CreatorScrapeQueue.enqueue` so partial archives cannot stop after 50 newest posts (`stop_reason=ceiling`). Modal creator sync and `/api/sync/creator` also route to the scrape queue as full+deep when the queue is enabled.

**Rules of thumb**

- Keep the Instagram app/browser closed while syncing (shared session budget).
- Reuse the Instaloader session file — do not password-login every run.
- On abort, stop for the day; resume tomorrow (queue + checkpoints persist).

---

## Following queue

Progress across days is stored in `~/Pictures/InstagramSaved/following_queue.json`:

```json
{
  "day_key": "2026-08-08",
  "accounts_today": 3,
  "accounts": {
    "some_creator": {
      "status": "done",
      "downloaded": 12,
      "last_error": "",
      "updated_at": "2026-08-08T00:00:00+00:00"
    }
  }
}
```

Statuses: `pending` | `done` | `skipped` | `error`.  
Optional fields: `priority` (int, higher first), `reason` (e.g. `bio:keyword`).  
`next_pending` sorts by **priority desc**, then username.  
`accounts_today` resets when `day_key` rolls to a new calendar day. A second run the same day only consumes the **remaining** daily budget.

Seed priorities from bio keywords:

```powershell
py scripts/prioritize_following_queue.py
# --requeue-keep  → reset done keep accounts to pending for another pull
```

---

## Filters (Phase 3)

- Public accounts only by default
- Minimum `media_count` (default 5)
- Bio/name/username keyword match (defaults: model, influencer, fitness, onlyfans, lingerie, bikini, glamour, actress)
- Pass empty `--keywords ""` to disable keyword filter

---

## Resume & idempotent downloads

Posts are identified by Instagram `shortcode` / `post_id` in `*.meta.json` and the SQLite archive index (`post_id`, `shortcode` columns).

| Situation | Behaviour |
|-----------|-----------|
| Post already on disk | Skip (`Skip (archived)`) — never re-download |
| Deleted on Instagram after download | Keep local files forever |
| Deleted locally **via app UI** | Tombstone in `deleted_posts` → **never re-download** on any sync mode |
| Deleted only on disk (Explorer) | No tombstone — may re-download if still in feed (delete via app to lock out) |
| Incomplete carousel | Re-fetch to fill missing slides (unless post is tombstoned) |
| Partial / aborted run | Continue past known tip; stop after `IG_CATCH_UP_STREAK` (default **3**) consecutive **archived or deleted** posts |

### Sync latest (existing creators)

```http
POST /api/scrape/enqueue
{ "username": "handle", "mode": "latest", "max_posts": 50 }
```

- Streams feed newest-first (no ranking).
- Downloads only missing/incomplete posts.
- Stops after catch-up streak of already-local **or tombstoned** posts.
- UI: select creator → **Sync new posts** in the sidebar.

CLI: `py scripts/download_creator_feed.py HANDLE --latest`

`sync_state.json` still stores last shortcode / counts for telemetry, but is **not** used as the sole stop signal (that caused missed older posts after partial runs).

```json
{
  "some_creator": {
    "last_shortcode": "ABC123",
    "last_post_id": "123456",
    "downloaded_count": 42,
    "updated_at": "2026-08-08T00:00:00+00:00"
  }
}
```

---

## Rate limits & abort

On Instagram connection / 429 errors the downloader uses **exponential backoff** starting at `IG_RATE_LIMIT_BACKOFF` (default 60s), capped at `IG_RATE_LIMIT_BACKOFF_MAX` (default 300s).

If the consecutive rate-limit streak hits `IG_ABORT_RATE_LIMIT_STREAK` (default 3), or an abuse phrase is detected, the job sets `aborted: true` and stops. Counters appear in `GET /api/sync/status` and the Sync modal.

---

## Metadata sidecars

Each downloaded image may have a `*.meta.json` sibling with `post_id`, `shortcode`, `caption`, `carousel_index`, `post_url`. Carousel frames share a `post_id` and can be grouped via `promptstudio.storage.metadata.group_by_post_id(creator)`.

---

## Creator scrape queue (serial full archive)

Enqueue handles for **one-at-a-time** full (or bounded) scrapes without running parallel Instagram jobs.

| Piece | Detail |
|-------|--------|
| API | `POST /api/scrape/enqueue`, `GET /api/scrape/status`, pause/resume/cancel |
| Persist | `~/Pictures/InstagramSaved/creator_scrape_queue.json` |
| Mutex | `SyncManager` — same single-flight as saved/following/creator |
| Full mode | Streams feed; `deep=true` (default) disables catch-up so top-N folders still backfill |
| Ceiling | `IG_FULL_SCRAPE_MAX_POSTS` (default 5000 downloaded media units; `0` = unlimited) |
| Fairness | One-shot `/api/sync/*` returns **409** while queue has pending (unless paused) |
| Abort | Hard rate-limit/abuse **pauses** the queue; resume when safe |
| Flag | `IG_CREATOR_SCRAPE_QUEUE=0` disables scrape API |

CLI full scrape:

```powershell
py scripts/download_creator_feed.py HANDLE --full
py scripts/download_creator_feed.py HANDLE --full --no-deep   # catch-up resume walk
```

Design: [archive/design_creator_scrape_queue.md](archive/design_creator_scrape_queue.md).

## Web UI

Open Sync modal → **Scrape creator** (enqueue full archive queue), **Sync Saved Posts**, **Sync Feed** (`@handle` one-shot), or **Sync Following List**. Status polls every 2.5s. One-shot buttons disable while the scrape queue has pending jobs.
