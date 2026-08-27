# Scripts — thin CLI wrappers around `promptstudio/`

Agent map: [docs/context.md](../docs/context.md).  
Run from the repo root so Python finds the `promptstudio` package. Logic belongs in the package, not here.

## Instagram scraping

```powershell
# Sync :saved collection
py scripts/download_instagram_saved.py

# One creator feed
py scripts/download_creator_feed.py roxeuoon --max-posts 50

# Refresh following list (includes biographies)
py scripts/export_following_list.py

# Bulk from following (anti-ban pacing; reels ON by default; empty keywords = all public)
py scripts/download_following.py --accounts-per-day 15 --max-posts 30 --keywords ""
# Skip videos: add --no-reels

# Or with bio keyword filter
py scripts/download_following.py --accounts-per-day 15 --max-posts 20 --keywords model,lingerie --min-media 5

# Reorder following_queue.json
py scripts/prioritize_following_queue.py
py scripts/prioritize_following_queue.py --dry-run
py scripts/prioritize_following_queue.py --requeue-keep
```

Queue + daily budget live in `~/Pictures/InstagramSaved/following_queue.json`. On abort (rate-limit streak / abuse signal) the CLI exits with code `2`.


## Archive maintenance

```powershell
py scripts/organize_and_filter.py

# Creator folders with no photos or videos (leftover sidecars count as empty)
py scripts/prune_empty_creators.py
py scripts/prune_empty_creators.py --dry-run

# Byte-identical duplicates
py scripts/deduplicate.py

# Near-duplicates (perceptual hash) — catches re-encodes, resizes, light crops
# and cross-creator reposts that byte-matching misses. Report only, never deletes.
py scripts/find_duplicates.py
py scripts/find_duplicates.py --distance 4      # near-identical only
py scripts/find_duplicates.py --json dupes.json
```

## Back up derived state

Everything the archive cannot re-download — prompts and verdicts that cost GPU
hours, favourites and generation ratings that are your own judgement, styles,
phashes, and the generation index with its seeds. Media is **not** included:
it is the one thing that can be fetched again.

```powershell
py scripts/export_derived.py                     # -> derived_state.json.gz
py scripts/export_derived.py backup.json         # uncompressed
py scripts/export_derived.py --kinds prompts,verdicts

py scripts/export_derived.py --import backup.json.gz
py scripts/export_derived.py --import backup.json.gz --dry-run
py scripts/export_derived.py --import backup.json.gz --kinds prompts
```

Import merges and is safe to re-run: every kind has a natural key, so a
half-finished restore overwrites row-for-row rather than duplicating, and
importing onto a live archive will not un-favourite anything starred since the
export. `--kinds prompts` is the "a re-run invalidated a prompt version and I
want the old one back" case.

## Web UI

```powershell
py server.py
# → http://localhost:5000
```

Sync modal: Saved / Creator feed / Following. Batch Analyze runs Ollama over uncached photos.

See [docs/roadmap.md](../docs/roadmap.md).
