# Instagram Saved Images Downloader & Sync Guide

Local archive: `~\Pictures\InstagramSaved`  
Session user: configured in `promptstudio.config.SESSION_USER` (default `YOUR_INSTAGRAM_USERNAME`)

---

## Sync modes

| Mode | CLI | API |
|------|-----|-----|
| Saved posts | `py scripts/download_instagram_saved.py` | `POST /api/sync/saved` |
| Creator feed | `py scripts/download_creator_feed.py HANDLE --max-posts 50` | `POST /api/sync/creator` |
| Following bulk | `py scripts/download_following.py` | `POST /api/sync/following` |

Export following list first (includes biographies for keyword filters):

```powershell
py scripts/export_following_list.py
py scripts/download_following.py --accounts-per-day 20 --max-posts 30 --keywords ""
```

Use `--keywords ""` to disable the bio keyword filter and queue every public account that passes `min_media`.

---

## Phase A — Anti-ban pacing

Following sync is designed for **multi-day** crawls, not one overnight dump.

| Behaviour | Default |
|-----------|---------|
| Post delay | random 4–12 s (`IG_POST_DELAY_MIN` / `IG_POST_DELAY_MAX`) |
| Between accounts | random 30–120 s |
| Soft batch pause | every 10 accounts, random 5–15 min |
| Daily / run cap | 20 accounts (`--accounts-per-day` / `max_accounts`) |
| Hard abort | 3 consecutive rate-limits, or `feedback_required` / `challenge_required` / `PleaseWaitFewMinutes` |

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
`accounts_today` resets when `day_key` rolls to a new calendar day. A second run the same day only consumes the **remaining** daily budget.

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
| Deleted locally (UI or disk) | Index row cleared → re-download if still in feed |
| Incomplete carousel | Re-fetch to fill missing slides |
| Partial / aborted run | Continue past known tip; stop after `IG_CATCH_UP_STREAK` (default **3**) consecutive already-archived posts |

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

## Web UI

Open Sync modal → **Sync Saved Posts**, **Sync Feed** (`@handle`), or **Sync Following List** (accounts/day · posts · keywords). Status polls every 2.5s and shows abort reason + queue summary when present.
