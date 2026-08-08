# Multi-Source Media Scraping — Research & Integration Design

| Field | Value |
|-------|--------|
| **Document** | Multi-Source Media Scraping (PromptStudio) |
| **Author** | — |
| **Date** | 2026-08-08 |
| **Status** | Research / Draft — no code written |
| **Audience** | Senior engineers familiar with PromptStudio scraping stack |

---

## Overview

PromptStudio scrapes Instagram only, via `instaloader` ([`promptstudio/scraping/downloader.py`](../promptstudio/scraping/downloader.py)). This document researches **which additional platforms are viable**, **which tool to use**, and **what has to change in this codebase** to support them without forking the existing Instagram path.

**Headline recommendation:** adopt **`gallery-dl`** as a single generic adapter (one dependency → ~200 sites, including Pinterest, X/Twitter, Reddit, TikTok, Bluesky, Tumblr, Flickr, VSCO), driven **as a subprocess** behind a new `MediaSource` abstraction. Keep `instaloader` for Instagram. Add **`yt-dlp`** later, and only for short-form video, where it is used **in-process** because it — unlike gallery-dl — documents a Python API.

The blocking work is *not* the scraper. It is three schema/identity assumptions in the storage layer that are silently Instagram-specific (§5).

---

## 1. Current state (grounded in code)

| Piece | Instagram coupling |
|-------|--------------------|
| [`scraping/downloader.py`](../promptstudio/scraping/downloader.py) `InstagramDownloader` | Duck-types the instaloader `Post` object in ~8 places: `mediaid`, `shortcode`, `owner_username`, `date_utc`, `caption`, `is_video`, `mediacount`. Downloads via `self._L.download_post(post, target=creator)` (`downloader.py:331`) |
| [`scraping/session.py`](../promptstudio/scraping/session.py) | `instaloader.Instaloader` construction + `session-<user>` file loading. Fully IG-specific |
| [`scraping/filters.py`](../promptstudio/scraping/filters.py) `score_instagram_post` | Ranking on IG signals (likes/comments/caption/feed index) |
| [`scraping/sync_manager.py`](../promptstudio/scraping/sync_manager.py) `SyncManager` | Generic mechanism, IG-specific in practice: one global job at a time, `job_type` string, cooperative cancel |
| [`scraping/creator_queue.py`](../promptstudio/scraping/creator_queue.py) | Per-creator FIFO scrape jobs. No `source` field |
| [`storage/metadata.py`](../promptstudio/storage/metadata.py) `build_metadata_from_post` | Hardcodes `"source": "instagram"` (`metadata.py:45`) and `post_url` as `instagram.com/p/<shortcode>` |
| [`storage/db.py`](../promptstudio/storage/db.py) `photos` table | **No `source` column.** Identity is `(post_id, shortcode)` — both IG concepts |
| [`storage/db.py`](../promptstudio/storage/db.py) `deleted_posts` | Tombstones keyed `(creator, shortcode, post_id)` |
| Archive layout | `SAVED_DIR/<creator>/<file>` — **exactly one level deep**, enforced by `creator, _, filename = rel.partition("/")` (`db.py:346`) |

**Good news already in place:** the sidecar schema *already* carries a `source` discriminator, and [`scraping/video_frames.py`](../promptstudio/scraping/video_frames.py) already samples and ranks frames from video (built for Reels). Video-first platforms therefore feed an existing pipeline rather than needing a new one.

---

## 2. Tool evaluation

| Tool | Coverage | Programmatic API | Verdict |
|------|----------|------------------|---------|
| **gallery-dl** | ~200 sites; images **and** videos; per-extractor rate limits, cookie handling, dedupe archive, sidecar metadata built in | **No documented Python API.** Library use is a long-standing open request ([#642](https://github.com/mikf/gallery-dl/issues/642), [#1375](https://github.com/mikf/gallery-dl/issues/1375)); `gallery_dl.job` is internal and unstable | **Adopt — as subprocess** |
| **yt-dlp** | Video-first: TikTok, YouTube Shorts, X video, IG Reels | **Documented embedding API** — `YoutubeDL` + `extract_info(url, download=False)` | **Adopt later, in-process**, for video only |
| Official platform APIs (Reddit, Flickr, Tumblr, DeviantArt, Bluesky/AT Protocol) | One platform each | Stable, sanctioned, rate limits published | Use **where sanctioned access matters**; not worth N integrations up front |
| Per-platform scrapers (`pinterest-dl`, `threads-net`, …) | One platform each, varying maintenance | Varies | **Avoid** — N dependencies, N failure modes |

> **Note on gallery-dl's home:** active development **moved to Codeberg** ([announcement](https://github.com/mikf/gallery-dl/issues/9374)); latest release ~1.32.9. The GitHub mirror is still public and PyPI publishing continues. Pin the version and track Codeberg for releases.

### Why subprocess for gallery-dl, in-process for yt-dlp

This asymmetry is deliberate, not inconsistency:

- gallery-dl exposes **no supported API**, so importing its internals couples PromptStudio to private structures that change without notice. A subprocess couples only to the **CLI contract and JSON output**, which are documented and stable. It also gives free cancellation (kill the process) — which matters because `SyncManager` already has cooperative cancel and long interruptible sleeps.
- yt-dlp **documents** `YoutubeDL`/`extract_info`, so in-process use is supported, avoids re-parsing, and allows progress hooks.

---

## 3. Platform shortlist, ranked for this archive

Ranked for a fashion/portrait/creator archive (what `IG_CAPTION_KEYWORDS` and the glam classifier target), with auth cost from gallery-dl's [supported-sites table](https://raw.githubusercontent.com/mikf/gallery-dl/master/docs/supportedsites.md):

| Rank | Platform | Auth | What you can pull | Why this rank |
|------|----------|------|-------------------|---------------|
| 1 | **Bluesky** | **None** | Posts, Media Files, Videos, Likes, Feeds, Hashtags, Search, User Profiles | **Start here.** No cookies, no ban risk, open protocol → the ideal end-to-end test of the new abstraction |
| 2 | **Pinterest** | Cookies | Pins, Created Pins, Boards/Sections, Search, related Pins | Highest signal density for outfit/fashion imagery; boards are curated, so ranking matters less |
| 3 | **X / Twitter** | Cookies | Media Timelines, Likes, Bookmarks, Search, Lists, Timelines | Creators cross-post from IG; `Bookmarks` mirrors your IG *saved* flow closely |
| 4 | **Reddit** | **OAuth** | Subreddits, Submissions, User Profiles, Home Feed | Sanctioned API, high volume, subreddit-scoped. Different mental model: topic-driven, not creator-driven |
| 5 | **TikTok** | Cookies | User Posts, Likes, Saved Posts, Reposts, Stories | Video-first → routes through existing `video_frames.py`. Highest ban sensitivity |
| 6 | **VSCO / Flickr / Behance / 500px / ArtStation** | None / OAuth | Galleries, Collections, Profiles | Editorial photography: lower volume, higher per-image quality. Cheap to add once the adapter exists |
| — | **Threads** | — | — | **Not supported by gallery-dl.** Would need bespoke work against a Meta surface as hostile as IG. Deprioritize |

**Two structural notes.** Reddit is *topic*-scoped while PromptStudio's data model is *creator*-scoped (folder per creator, `ArchiveIndex.creator` as the gallery grouping key) — a subreddit maps awkwardly onto that, so treat the subreddit as the "creator" folder and keep the real author in the sidecar. Pinterest pins are frequently **re-pins of someone else's photo**, so `creator` means "board owner", not "photographer" — worth knowing before the glam scores get interpreted as per-creator signal.

---

## 4. gallery-dl CLI contract (verified flags)

These are the flags the adapter would depend on, from [`docs/options.md`](https://github.com/mikf/gallery-dl/blob/master/docs/options.md):

| Need | Flag |
|------|------|
| Enumerate available metadata keys for an extractor | `-K, --list-keywords <URL>` |
| Dry-run: metadata to stdout, no download | `-j, --dump-json` |
| Write per-file JSON sidecars | `--write-metadata` |
| Exact output directory (no per-extractor subdirs) | `-D, --directory PATH` |
| Base directory | `-d, --destination PATH` |
| Inline config override | `-o KEY=VALUE` |
| Cookies | `-C, --cookies FILE` / `--cookies-from-browser BROWSER[:PROFILE]` |
| Cap items | `--range RANGE` |
| Skip already-downloaded | `--download-archive FILE` |
| Pacing | `--sleep SECONDS`, `--sleep-request SECONDS` |
| Post-file hook | `--exec CMD` |

Filename/directory format strings (`extractor.*.filename`, `extractor.*.directory`) accept format keys — `{date}`, `{num}`, `{extension}`, `{category}` are common; **per-extractor keys must be enumerated with `-K`, not assumed.** That enumeration is Phase 0 below, because the field mapping cannot be written correctly without it.

`--download-archive` deserves emphasis: it is gallery-dl's own dedupe ledger and it overlaps with PromptStudio's `ArchiveIndex` + tombstones. Running both unreconciled means a post you deliberately deleted in the UI gets re-downloaded (gallery-dl doesn't know about tombstones) or a missing file never gets repaired (gallery-dl thinks it's done). **Recommendation: keep `ArchiveIndex` authoritative and do not use `--download-archive`** — pre-filter URLs, or accept re-downloads and let `_post_archive_state` skip them. This mirrors the existing `state in ("complete", "deleted")` logic at `downloader.py:312`.

---

## 5. The real blockers: three Instagram assumptions in storage

These are the parts that will bite, and none of them are in the scraper.

### 5.1 `photos` has no `source` column

`photos` (`db.py:30`) identifies media by `(post_id, shortcode)`. Add `source TEXT NOT NULL DEFAULT 'instagram'` via the **existing** migration mechanism — `_IDENTITY_COLUMNS` (`db.py:64`) already does additive `ALTER TABLE photos ADD COLUMN` at `db.py:168`, so this is a two-line change plus an index. Defaulting to `'instagram'` back-fills every existing row correctly.

### 5.2 Tombstone key collides across sources — **correctness bug, not cosmetic**

`deleted_posts` is keyed `(creator, shortcode, post_id)` and `is_deleted_post()` is consulted at `downloader.py:213` to guarantee *"intentional user deletes — never re-download"*. Nothing in that key names a platform. A Pinterest pin ID or a Bluesky record key can collide with an IG `mediaid`, and the failure is silent and wrong in both directions: a newly scraped post is skipped as "deleted", or a deleted post returns. `source` must join that key **and** the `is_deleted_post()` lookup before any second source writes a tombstone.

> Note the pre-existing shadowing here: `_SCHEMA` (`db.py:52`) and `_DELETED_POSTS_SCHEMA` (`db.py:71`) declare `deleted_posts` **twice, identically**. Both need the same edit, or pick this moment to collapse them to one definition.

### 5.3 Archive layout is exactly one level deep

`creator, _, filename = rel.partition("/")` (`db.py:346`) means a `<source>/<creator>/<file>` layout would break `rel_path` parsing, `/api/creators`, thumbs, trash, and favorites simultaneously.

**Recommendation: keep the flat `<creator>/` layout.** Carry `source` in the sidecar and the new DB column, not in the path. This preserves the desirable behaviour that one creator's media from IG *and* X lands in one folder — which is what you'd actually want when browsing. For the genuine collision case (different people, same handle, different platforms), disambiguate the *folder name* (`handle__x`) rather than adding a path level.

---

## 6. Proposed architecture

### 6.1 `NormalizedPost` — the seam

One dataclass that both sources produce, replacing instaloader-`Post` duck-typing:

```python
@dataclass
class NormalizedPost:
    source: str              # "instagram" | "pinterest" | "bluesky" | ...
    creator: str             # archive folder key
    post_id: str             # stable per-source id
    shortcode: str           # per-source short ref ("" if none)
    taken_at: datetime
    caption: str
    is_video: bool
    media_count: int         # carousel/slide count; 1 if single
    post_url: str
    author: str = ""         # true author when != creator (re-pins, subreddits)
    extra: dict = field(default_factory=dict)
```

`build_metadata_from_post` (`metadata.py:33`) gains a sibling `build_metadata_from_normalized()` writing the same sidecar shape, so the gallery, classifier, and prompt engine need **zero** changes — they read sidecars, not scraper objects.

### 6.2 `MediaSource` protocol

```python
class MediaSource(Protocol):
    name: str
    def supports(self, target: str) -> bool: ...
    def iter_posts(self, target: str, *, limit: int|None) -> Iterator[NormalizedPost]: ...
    def fetch(self, post: NormalizedPost, dest_dir: str) -> list[str]: ...
```

`InstagramSource` wraps today's `InstagramDownloader`. `GalleryDlSource` shells out per §4. The **orchestration** in `downloader.py` that you actually want to keep — archive-state checks, catch-up streaks, pacing, abuse-signal abort, checkpoints — moves up into a source-agnostic runner; only per-platform I/O sits behind the protocol.

### 6.3 Concurrency

Keep **global single-flight** (`SyncManager`) initially. It is the safe default and needs no change. Worth knowing for later: single-flight is an *Instagram* requirement, not a universal one — Bluesky and Reddit could safely run in parallel with each other. Per-source concurrency is a deliberate follow-up, not part of this work.

### 6.4 API surface

Extend, don't multiply: add an optional `source` field (default `"instagram"`) to `POST /api/scrape/enqueue` (`handler.py:505`) and a `source` field on `creator_queue` jobs. No new routes, and every existing caller keeps working.

---

## 7. Legal / ToS

The README already carries a scraping-responsibility note; multi-source widens it and the platforms differ materially:

- **Bluesky** is the most permissive — AT Protocol serves public data by design.
- **Reddit, Flickr, Tumblr, DeviantArt** offer **official OAuth APIs**. Where a sanctioned path exists, prefer it over cookie scraping: it is rate-limit-documented and won't cost you an account.
- **Pinterest, X, TikTok, Instagram** prohibit automated collection in their ToS. Cookie-based access carries **real account-suspension risk** — use a throwaway account, never your primary, and keep the existing pacing discipline (`POST_DELAY_*`, `ACCOUNT_PAUSE_*`, `BATCH_PAUSE_*`, abuse-signal abort).
- Scraped media stays third-party copyrighted. Personal-archive use only; do not redistribute. Keep cookie/session files gitignored exactly as `session-*` is today.

---

## 8. Phased plan

| Phase | Work | Exit criteria |
|-------|------|---------------|
| **0. Spike** *(do first — cheap, de-risks everything)* | `pip install gallery-dl` in a scratch venv. Run `gallery-dl -K` against one Bluesky, one Pinterest, one Reddit URL. Record the real metadata keys | A verified field-mapping table per extractor. **Do not write the adapter before this** |
| **1. Storage prep** | `photos.source` column + index; `source` in `deleted_posts` key **and** `is_deleted_post()`; collapse the duplicate `deleted_posts` schema | Existing IG archive opens unchanged; tombstones round-trip with `source` |
| **2. Seam** | `NormalizedPost`, `MediaSource`, `build_metadata_from_normalized()`; `InstagramSource` wraps current downloader | IG scrape behaviour byte-identical to today |
| **3. First new source: Bluesky** | `GalleryDlSource` via subprocess; `source` on enqueue API + creator_queue | End-to-end: enqueue Bluesky handle → media + sidecars in archive → gallery renders → prompts generate |
| **4. Cookie sources** | Pinterest, then X. Cookie config in `.env` (gitignored), per-source pacing | Both scrape without tripping abuse signals |
| **5. Video** | `yt-dlp` in-process for TikTok/Shorts → existing `video_frames.py` | Frames rank and classify as Reels do today |

Phase 0 is the whole point of doing this as research first: the field mapping is the only part that can't be designed from the outside, and it's the part every later phase depends on.

---

## 9. Open questions

1. **Which platform matters most to you?** The ranking in §3 assumes fashion/portrait creators. If the real goal is volume, Reddit outranks Pinterest.
2. **Throwaway accounts available** for Pinterest/X cookies, or should cookie-based sources be skipped entirely in favour of Bluesky + the OAuth platforms?
3. **Should Reddit use gallery-dl (cookies/OAuth via gallery-dl) or PRAW directly?** gallery-dl means one code path; PRAW means a sanctioned, better-documented one.
4. **Is `creator` the right grouping key for non-creator sources** (subreddits, Pinterest boards), or does the gallery need a `source` facet in the UI?

---

## Sources

- [gallery-dl — GitHub mirror](https://github.com/mikf/gallery-dl) · [Codeberg (active development)](https://codeberg.org/mikf/gallery-dl/releases) · [PyPI](https://pypi.org/project/gallery-dl/)
- [gallery-dl supported sites](https://raw.githubusercontent.com/mikf/gallery-dl/master/docs/supportedsites.md) · [options.md](https://github.com/mikf/gallery-dl/blob/master/docs/options.md) · [configuration.rst](https://raw.githubusercontent.com/mikf/gallery-dl/master/docs/configuration.rst) · [formatting.md](https://raw.githubusercontent.com/mikf/gallery-dl/master/docs/formatting.md)
- gallery-dl library-use requests: [#642](https://github.com/mikf/gallery-dl/issues/642), [#1375](https://github.com/mikf/gallery-dl/issues/1375)
- [yt-dlp Python API overview](https://yt-dlp-yt-dlp.mintlify.app/api/overview) · [embedding yt-dlp in Python scripts](https://yt-dlp.eknerd.com/docs/embedding%20yt-dlp/using-yt-dlp-in-python-scripts/) · [extraction pipeline](https://deepwiki.com/yt-dlp/yt-dlp/2.2-information-extraction-pipeline)
