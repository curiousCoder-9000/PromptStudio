# Multi-Source Scraping — X and Reddit

How PromptStudio scrapes beyond Instagram, and the things that will bite you.

Design rationale and the platform survey live in
[archive/research_multi_source_scraping.md](archive/research_multi_source_scraping.md).

---

## 1. What's supported

| Source | Name | Auth | Fetches | Archive folder |
|--------|------|------|---------|----------------|
| Instagram | `instagram` (default) | Instaloader session **or** gallery-dl cookies (`IG_BACKEND`) | profile feed | `handle` |
| X / Twitter | `x` (alias `twitter`) | **cookies required** | `/media` timeline | `handle__x` |
| Reddit | `reddit` | none (OAuth optional) | subreddit or user submissions | `r_sub__reddit`, `u_user__reddit` |

Instagram defaults to `instaloader`. Set `IG_BACKEND=gallery-dl` to use the same
gallery-dl subprocess path as X/Reddit (browser cookies or `IG_COOKIES_FILE`).
That is the practical way around Instaloader’s `web_profile_info` 429s — not a
second source, so folders and `photos.source` stay `instagram`. Caption ranking
(`IG_POST_RANK`) is Instaloader-only. X and Reddit are gallery-dl only.

## 2. Setup

```powershell
pip install -r requirements.txt   # now includes gallery-dl
```

Reddit works immediately. X needs cookies — export a `cookies.txt` (Netscape
format) from a logged-in browser and point `.env` at it:

```ini
X_COOKIES_FILE=C:\path\to\cookies-x.txt
# or, instead of a file:
SCRAPE_COOKIES_FROM_BROWSER=firefox
```

Keep cookie files **outside** the repo. `.gitignore` covers `*cookies*.txt` as a
backstop, but the archive directory is the right home for them.

> **Use a throwaway X account.** Automated collection violates X's ToS and
> accounts do get suspended. Never commit cookie files — they are session
> credentials, exactly like `session-*`.

## 3. Usage

**UI:** Sync modal → pick the source in the dropdown next to the handle field.
The placeholder and hint change per source.

**API:**

```bash
curl -X POST localhost:5000/api/scrape/enqueue \
  -H 'Content-Type: application/json' \
  -d '{"username": "r/streetwear", "source": "reddit", "mode": "full"}'
```

Accepted target spellings:

| Source | Input |
|--------|-------|
| `x` | `nina_k`, `@nina_k`, `https://x.com/nina_k` |
| `reddit` | `r/fashion`, `fashion`, `u/bob`, `user/bob`, full reddit URL |

## 4. Things worth knowing

### Folders are suffixed, not nested

The archive is **exactly one level deep** — `storage/db.py` derives the creator
from the first path segment (`creator, _, filename = rel.partition("/")`). A
`<source>/<creator>/` layout would break `/api/creators`, thumbs, trash and
favorites simultaneously. So non-Instagram sources are disambiguated by *folder
suffix* instead: `nina__x`, `r_fashion__reddit`.

Instagram keeps bare handles, so the existing archive is untouched.

Set `SCRAPE_FOLDER_SUFFIX=0` to merge (e.g. X media into the Instagram creator
folder). Off by default because two different people can share a handle across
platforms, and merging them would pollute per-creator glam stats and creator-style
rebuilds.

### Reddit is topic-scoped, the archive is creator-scoped

A subreddit becomes the folder — so `creator` means "subreddit", not "person".
The real submitter is preserved in each sidecar's `author` field. Two
consequences:

- Per-creator glam scores for a subreddit folder describe *the subreddit*, not one person.
- Reddit posts are often reposts, so the same image can arrive under several subreddits.

### Identity is scoped by platform

Post ids are only unique *within* a platform. `photos.source` and
`deleted_posts.platform` exist so a Reddit submission id can't collide with an
Instagram mediaid. Without that scoping the failure is silent both ways: a fresh
post skipped as "already deleted", or a deliberately deleted post reappearing.

Note the two column names: `photos.source` and `deleted_posts.platform` mean the
same thing, but `deleted_posts` already had a `source` column meaning *who
performed the delete* (`"ui"`), so the platform discriminator needed its own name.

Migration is automatic and additive — existing rows back-fill to `instagram`.

### No `--download-archive`

`ArchiveIndex` plus the `deleted_posts` tombstones are already the authority on
what exists and what the user deleted on purpose. A second independent ledger
would either re-download tombstoned posts (gallery-dl can't see tombstones) or
refuse to repair a partially-downloaded post (gallery-dl thinks it's done).

### Why subprocess and not `import gallery_dl`

gallery-dl exposes **no supported Python API** — library use is a long-standing
open request, and `gallery_dl.job` is internal. The CLI contract and
`--write-metadata` JSON are documented and stable. A subprocess also makes
cancellation trivial: terminate the child.

(For contrast: if short-form video is added later, `yt-dlp` *does* document
embedding, so it should be used in-process. The asymmetry is deliberate.)

### Exit status is a bit mask

`job.py` does `self.status |= exc.code`, so several can be set at once — test
with `&`, never `==`. From `gallery_dl/exception.py`:

| Bit | Meaning | How PromptStudio reacts |
|-----|---------|-------------------------|
| 0 | success — **including a clean `--abort N` catch-up stop** (`StopExtraction` has code 0) | `end_of_feed` / `catch_up` / `nothing_new` |
| 1 | generic | error |
| 4 | `ExtractionError` / `HttpError` / `NotFoundError` | refined by output text to `not_found` / `private` / error |
| 8 | `ChallengeError` (captcha) | **abort + pause queue** |
| 16 | authentication / authorization | **abort + pause queue** |
| 32 | `InputError` — bad filter/format/`-o` | error, queue *not* paused (it's our bug) |
| 64 | `NoExtractorError` — unsupported URL (set as `retval \|= 64` in `__init__.py`, not an exception `code`) | error, queue *not* paused (our target resolution is wrong) |
| 128 | interrupted | error |

Auth and challenge failures pause the whole queue because every following job
would fail identically, and hammering a cookie-authed endpoint is how accounts
die.

### Download counts come from the filesystem

The run diffs the destination folder before and after rather than parsing
gallery-dl's human-readable stdout, which is not a documented contract.

## 5. Metadata mapping

gallery-dl writes a raw `<file>.json` sidecar; PromptStudio converts it to its own
`<file>.meta.json` (identical in shape to the Instagram sidecar, so the gallery,
prompt engine and glam classifier need no per-source logic) and then deletes the
raw one. Unmapped fields survive under `source_extra`.

Key names verified against gallery-dl 1.32.9 extractor source:

| Field | X (`twitter.py`) | Reddit (`reddit.py`) |
|-------|------------------|----------------------|
| `post_id` | `tweet_id` | `id` |
| `taken_at` | `date` | `date` (from `created_utc`) |
| `caption` | `content` | `title` |
| `author` | `author.name` | `author` |
| `media_count` | `count` (= `len(files)`) | 1 |
| `carousel_index` | `num - 1` (`num` is 1-based) | `num - 1` |
| `post_url` | built from author + `tweet_id` | `permalink` (**relative** — domain prepended) |

A missing date arrives as JSON `null` (gallery-dl's `util.NONE`); the file's mtime
is substituted so it doesn't sort to the top of the gallery forever.

To re-verify keys after a gallery-dl upgrade:

```powershell
gallery-dl -K https://www.reddit.com/r/streetwear/
gallery-dl -K https://x.com/someone/media
```

## 6. Pacing

Defaults are deliberately gentler than gallery-dl's own
(`SCRAPE_SLEEP`, `SCRAPE_SLEEP_REQUEST`, `SCRAPE_SLEEP_429`, `SCRAPE_RETRIES`).

**Scrape capacity is per platform.** One lane per source, one job per lane, so
Instagram / X / Reddit run concurrently while Instagram stays pinned to a single
job. Cancel, pause, status and pacing are all lane-scoped — full design in
[design_scrape_lanes.md](design_scrape_lanes.md).

Per-lane anti-ban waits: Instagram keeps `IG_ACCOUNT_PAUSE_MIN/MAX` (30-120s
between creators) and `IG_BATCH_*` (5-15 min every 10 jobs). gallery-dl lanes
default to ~2-6s and no batch pause, since `SCRAPE_SLEEP*` already paces them.
Override per source when a lane starts getting throttled:

```ini
SCRAPE_ACCOUNT_PAUSE_MIN_X=20
SCRAPE_ACCOUNT_PAUSE_MAX_X=60
SCRAPE_BATCH_EVERY_REDDIT=25
```

## 7. Adding another source

1. Implement `MediaSource` (`scraping/sources/base.py`): `parse_target()` + `run()`.
   For a gallery-dl-supported site, subclass `GalleryDlSource` and supply
   `parse_target`, `_cookies_file`, `_extractor_options`, `_map_raw`.
2. Register it in `scraping/sources/__init__.py` `_REGISTRY` (+ `_ALIASES`).
3. Add its option to the `#scrapeSourceSelect` dropdown and `SCRAPE_SOURCE_META`
   in `app.js`.

Nothing in storage, the queue, or `SyncManager` should need touching.

## 8. Known gaps

- **Ranking is Instagram-only.** `score_instagram_post` uses IG signals, so X and
  Reddit runs are chronological. `mode=bounded` caps by scan order, not quality.
- **`--range` counts files considered, including skipped ones**, so a bounded
  ceiling is a scan bound rather than an exact download count.
- **No `following`-style bulk** for X/Reddit — one target per job.
- **Threads is not supported by gallery-dl** and would need bespoke work.
