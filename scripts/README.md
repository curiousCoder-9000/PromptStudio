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

# Dry-run: classify following for woman + sexy outfit — no unfollow
py scripts/classify_following.py --limit 20
py scripts/classify_following.py --force
py scripts/classify_following.py --fetch --limit 10

# Put classify:keep accounts first in following_queue.json
py scripts/prioritize_following_queue.py
py scripts/prioritize_following_queue.py --dry-run
py scripts/prioritize_following_queue.py --requeue-keep

# Score local images+videos for gallery Sexy filter (writes glam_score)
py scripts/classify_local_photos.py --limit 40
py scripts/backfill_glam_scores.py   # from existing report, no Ollama
```

Queue + daily budget live in `~/Pictures/InstagramSaved/following_queue.json`. On abort (rate-limit streak / abuse signal) the CLI exits with code `2`.

Classifier report (resumable): `following_classify_report.json` + `docs/following_classify_report.md`.

## Archive maintenance

```powershell
py scripts/organize_and_filter.py
py scripts/deduplicate.py
```

## Web UI

```powershell
py server.py
# → http://localhost:5000
```

Sync modal: Saved / Creator feed / Following. Batch Analyze runs Ollama over uncached photos.

See [docs/roadmap.md](../docs/roadmap.md).
