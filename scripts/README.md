# Scripts — thin CLI wrappers around `promptstudio/`

Run from the repo root so Python finds the `promptstudio` package.

## Instagram scraping

```powershell
# Sync :saved collection
py scripts/download_instagram_saved.py

# One creator feed
py scripts/download_creator_feed.py roxeuoon --max-posts 50

# Refresh following list (includes biographies)
py scripts/export_following_list.py

# Bulk from following (anti-ban pacing; empty keywords = all public matches)
py scripts/download_following.py --accounts-per-day 20 --max-posts 30 --keywords ""

# Or with bio keyword filter
py scripts/download_following.py --accounts-per-day 20 --max-posts 20 --keywords model,lingerie --min-media 5

# Dry-run: classify following (index >= 71) for woman + sexy outfit + good breasts — no unfollow
# Local archive images only (safe, no Instagram):
py scripts/classify_following.py --limit 20
# Re-score after criteria changes:
py scripts/classify_following.py --force
# Fetch a few posts when local folder is empty (slow / rate-limited):
py scripts/classify_following.py --fetch --limit 10
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
