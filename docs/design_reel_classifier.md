# Design: Reel / video glam classifier

| Field | Value |
|-------|--------|
| **Status** | PR1–PR3 implemented (smart frames, reel prompt, best-frame thumbs) |
| **Date** | 2026-08-08 |
| **Problem** | Photo-oriented glam classify fails or mis-scores Instagram reels; ~55% of local videos still unscored |
| **Owner modules** | `outfit_classifier.py`, `classify_job.py`, `thumbs.py`, optional download cover path |

---

## 1. Executive summary

PromptStudio already **attempts** video classify (`classify_media` → `classify_video`: sample 3 frames → photo vision prompt → **max glam_score**). That is a thin wrapper on the **photo** path. Reels are not still photos: cover cards, transitions, motion blur, text overlays, and multi-outfit cuts make single-frame photo logic unreliable and expensive (3× Ollama calls per `.mp4`).

**Recommendation:** keep vision on **still images** (Ollama multimodal cannot usefully ingest full MP4s today), but replace “3 even frames + photo prompt + max score” with a **reel-specific pipeline**:

1. **Cheap OpenCV prefilter** — sample more candidate frames by **time**, drop black/blurry/low-info frames.  
2. **Pick 1–2 best frames** (sharpness × mid-brightness, optional face/skin heuristic later).  
3. **Reel-tuned vision prompt** (freeze-frame of a reel, ignore UI text / watermarks / pure title cards).  
4. **Aggregation policy** documented for keep vs gallery sexy filter.  
5. **Optional cover image** from Instaloader video thumbnails when present.  
6. **Persist evidence** (`glam.source=video`, frame timestamps, scores) for debugging.

Do **not** send raw video bytes to Ollama. Do **not** re-use gallery thumb = first frame only as the sole classify input.

---

## 2. Current system (as implemented)

### 2.1 Entry points

| Surface | Path | Videos? |
|---------|------|---------|
| UI **Classify** (creator) | `POST /api/classify/start` → `ClassifyJobManager` | Yes (`include_videos: true`) |
| CLI archive score | `scripts/classify_local_photos.py` | Yes (unless `--no-videos`) |
| Following dry-run | `scripts/classify_following.py` | **No** — skips `post.is_video` when fetching; local list is images-only helpers |
| Prompt engine (SD caption) | `prompts/engine.py` | Separate; uses first-ish OpenCV frame patterns, not glam |

### 2.2 Core logic (`outfit_classifier.py`)

```
classify_media(path)
  if .mp4/.webm → classify_video(path, max_frames=3)
  else           → classify_image(path)

classify_video:
  extract up to 3 frames at fractions 1/6, 3/6, 5/6 of FRAME_COUNT
  for each frame JPEG temp:
      classify_image(frame)   # same CLASSIFY_PROMPT as photos
  return verdict with highest glam_score (tie-break confidence)
```

Photo prompt (`CLASSIFY_PROMPT` / `v2-skin-exposure`) is explicitly:

> “You classify **Instagram photos** for a personal KEEP filter…”

Fields: `has_woman`, `sexy_revealing_outfit`, `good_breasts`, `confidence`, `brief_reason` → glam 0–3.

### 2.3 Gallery thumbs (`thumbs.py`)

Video thumbs = **first decoded frame only**. Same first-frame bias users see in the grid; often not the best outfit moment.

### 2.4 Download side

- `download_video_thumbnails=False` on Instaloader → **no IG cover JPG** next to most reels.  
- Metadata has `is_video: true` when written from a Post.  
- Carousel posts can mix `.jpg` + `.mp4` slides under the same UTC prefix.

### 2.5 Live archive snapshot (2026-08-08)

| Metric | Count |
|--------|------:|
| Local images | ~3693 |
| Local videos (`.mp4`/`.webm`) | ~750 |
| DB video rows | 748 |
| Videos with `glam_score < 0` (unscored) | **415 (~55%)** |
| Videos scored 3 / 2 / 1 / 0 | 284 / 21 / 24 / 4 |

So classify **does** run on some reels and often yields high scores when frames are good — but over half never finished, and the method is not reel-aware.

### 2.6 Spot check: frame quality (6 sample reels)

Even spacing by `CAP_PROP_POS_FRAMES` produced **usable and blurry** frames on longer clips (e.g. 55–90% marked BLUR via Laplacian variance). Brightness was rarely pure black on these samples, but production reels often start with title cards / white flashes that the **current 3-point grid can miss or hit randomly**.

Seeking by **frame index** is also codec-unreliable for H.264 (Instagram); seeking by **milliseconds** (`CAP_PROP_POS_MSEC`) is generally more stable.

---

## 3. Why “same as photo” fails for reels

| Failure mode | Photo | Reel |
|--------------|-------|------|
| Subject always in frame | Usually full body/face in one still | Subject enters mid-clip; early frames logo/text |
| Outfit stable | One outfit | Outfit changes, cuts, zooms |
| Sharpness | High | Motion blur, whip pans |
| Text overlays | Rare | Captions, stickers, “Part 1” cards → model confuses “clothing” |
| Cost | 1 vision call | Today **3** vision calls; slow on GPU, job feels stuck |
| Aggregation | N/A | Max-of-3 can **over-keep** on one lucky frame or **under-score** if all samples are bad moments |
| Product language | “photo” | Model may refuse / hedge on freeze-frames that look like screenshots |
| Cover art | Image is the content | IG cover is often the best single still — **we don’t download it** |

Prompt fields still make sense (`has_woman`, revealing outfit, figure) — the **input selection and prompt framing** are wrong, not the glam 0–3 scale.

---

## 4. Goals & non-goals

### Goals

1. **Reliable glam_score for `.mp4`/`.webm`** comparable to photos for gallery Sexy filter and classify keep/reject.  
2. **Cost control:** prefer **1 vision call** per reel (2 only when uncertain).  
3. **Deterministic enough** for resume (`glam_score >= 0` skip) and re-force.  
4. **Debuggable:** sidecar stores which frames / policy produced the score.  
5. **Shared path:** UI classify job + `classify_local_photos.py` use the same `classify_video`.  
6. **Better video thumbs** can reuse best-frame selection (optional same PR or follow-up).

### Non-goals (this design)

- Full video understanding / action recognition / audio.  
- Cloud APIs (clip embeddings, commercial moderators).  
- Auto-delete low-glam reels (still forbidden without UI confirm).  
- Changing photo classify prompt semantics except shared versioning if needed.  
- Real-time classify during download (can enqueue later).

---

## 5. Options considered

### Option A — Status quo (3 even frames + photo prompt + max)

- **Pros:** Already coded.  
- **Cons:** Costly, blur-prone, photo wording, no quality gate.  
- **Verdict:** Reject as long-term; keep as fallback.

### Option B — Single middle frame only

- **Pros:** Cheap (1×).  
- **Cons:** Middle of a transition / cut often worst.  
- **Verdict:** Too weak alone.

### Option C — Smart multi-sample + quality rank + 1–2 vision calls (**recommended**)

1. Sample **K candidate frames** (default 8–12) at **time** positions, skip first ~5–8% and last ~5% (title/end cards).  
2. Score each with cheap metrics: brightness mean, Laplacian variance, optional skin-color fraction.  
3. Discard dark / blurry / near-duplicate frames.  
4. Take top **M=1** (or 2 if top scores within ε) → vision.  
5. Aggregate: if M=1 use that; if M=2 use **max glam** only if both `ok`, else higher confidence / prefer has_woman.  
6. Reel-specific prompt variant.  

- **Pros:** Addresses blur, cost, wording; OpenCV already a dependency.  
- **Cons:** Heuristics need tuning; still not perfect on pure dance-from-behind clips.

### Option D — Download Instagram video cover / thumbnail

Enable Instaloader `download_video_thumbnails=True` (or fetch cover URL from Post). Classify cover first; if confidence high and glam clear, skip sampling; else fall back to Option C.

- **Pros:** Cover is editor-chosen “hero” still — often ideal for glam.  
- **Cons:** Not always on disk today; re-download/backfill needed; cover can be face-crop without outfit.

### Option E — External video models (Whisper-style / VLM video)

- **Pros:** Temporal.  
- **Cons:** Heavy, not in stack, overkill for glam keep filter.  
- **Verdict:** Out of scope.

### Option F — Post-level score (collapse carousel jpg+mp4)

Average/max glam across all media sharing `post_id` / UTC group for **account** keep decisions; gallery still per-file.

- **Pros:** Matches user mental model of “this post”.  
- **Cons:** Schema/UI work; secondary to fixing per-file reel quality.

**Chosen backbone:** **C + optional D**, with F as a later polish for following-classify only.

---

## 6. Proposed architecture

```
                    ┌─────────────────────┐
  .jpg/.png ───────►│ classify_image      │──► PostVerdict source=image
                    │ (existing prompt)   │
                    └─────────────────────┘

  .mp4/.webm ──┐
               │    ┌──────────────────────────────┐
               ├───►│ 1. resolve_cover_jpg?        │── if high-quality cover
               │    └──────────────────────────────┘     classify as image-like
               │                    │ miss / weak
               ▼                    ▼
        ┌──────────────────────────────────────────┐
        │ 2. sample_candidates (time-based, K=10)  │
        │ 3. filter black/blur/dup                 │
        │ 4. rank → top M frames (M=1..2)          │
        └──────────────────────────────────────────┘
                          │
                          ▼
        ┌──────────────────────────────────────────┐
        │ 5. classify_image_with_prompt(REEL_PROMPT)│
        │ 6. aggregate → PostVerdict source=video  │
        │ 7. persist glam + frame evidence         │
        └──────────────────────────────────────────┘
```

### 6.1 Config knobs (`config.py` / env)

| Env | Default | Meaning |
|-----|---------|---------|
| `CLASSIFY_REEL_CANDIDATES` | `10` | Candidate frames to decode |
| `CLASSIFY_REEL_VISION_MAX` | `1` | Max Ollama calls per reel (2 if uncertain) |
| `CLASSIFY_REEL_SKIP_HEAD_FRAC` | `0.08` | Skip start of clip |
| `CLASSIFY_REEL_SKIP_TAIL_FRAC` | `0.06` | Skip end |
| `CLASSIFY_REEL_MIN_BRIGHT` | `22` | Drop darker frames |
| `CLASSIFY_REEL_MIN_SHARP` | `35` | Laplacian variance floor (tune on archive) |
| `CLASSIFY_REEL_UNCERTAIN_BAND` | `0.45–0.65` conf | Second frame if mid confidence |
| `CLASSIFY_MAX_EDGE` | `768` | Unchanged encode size |

### 6.2 Reel prompt (draft)

Differences from photo prompt:

- Explicit: “This is a **freeze-frame from an Instagram Reel / short video**, not a studio photo.”  
- Ignore: stickers, captions, progress bars, watermarks, pure text title cards → if only text/logo, `has_woman=false`.  
- Motion blur: if person unrecognizable, low confidence rather than false outfit.  
- Still **generous** on glam when woman + skin/figure visible (same keep product).  
- Same JSON schema so `PostVerdict` / glam mapping stay unchanged.

Version bump: e.g. `CLASSIFY_PROMPT_VERSION = "v3-reel-frames"` only for video path (photos can stay `v2-skin-exposure` until a unified bump).

### 6.3 Aggregation policy (recommended V1)

| Situation | Score |
|-----------|--------|
| 1 vision frame, `ok` | That glam_score |
| 2 frames, both ok | **Max** glam_score (prefer not missing sexy moments) |
| 2 frames, conflict (one 0 one 3) | Prefer higher if `has_woman` and conf ≥ 0.55; else average rounded with bias up when either sexy flag true |
| No usable frames after filter | `ok=false`, error=`no_usable_reel_frames`, glam stays **-1** (retryable) |
| Cover-only success | `source=video_cover` |

**Rationale for max:** gallery Sexy filter and “keep glam reels” care more about false negatives (missing good reels) than one over-scored blurry keep — with quality prefilter, max is safer than on random frames.

### 6.4 Persistence (sidecar `glam` object)

Extend, non-breaking:

```json
{
  "glam_score": 3,
  "glam": {
    "score": 3,
    "has_woman": true,
    "sexy_revealing_outfit": true,
    "good_breasts": true,
    "confidence": 0.82,
    "brief_reason": "...",
    "matches_keep": true,
    "source": "video",
    "prompt_version": "v3-reel-frames",
    "frames_considered": 10,
    "frames_sent_to_vision": 1,
    "frame_times_sec": [2.4],
    "frame_metrics": [{"t": 2.4, "bright": 120.1, "sharp": 210.3}]
  }
}
```

DB column `glam_score` unchanged (no migration). Optional later: `media_kind` column — not required for V1.

### 6.5 Thumbnail reuse (recommended same PR or PR2)

`ensure_thumbnail` for video should prefer:

1. Existing best-frame JPEG cache under `_thumbs/…` or `_classify_frames/…` if classify already ran.  
2. Else run the **same ranker** (no Ollama) and write thumb from best sharp frame — not frame 0.

This fixes gallery “black/title card” tiles without vision cost.

### 6.6 Download cover (optional PR)

In `create_instaloader` / downloader:

- Set `download_video_thumbnails=True` **or** explicitly save `post.url` cover when `is_video`.  
- Naming: keep Instaloader defaults or `{same_stem}.jpg` beside `.mp4`.  
- Classify: if companion cover exists and passes bright/sharp gates, vision it first with reel prompt.

Backfill: only new downloads get covers unless a one-off script re-fetches (rate-limit heavy — low priority).

---

## 7. API / UI impact

| Area | Change |
|------|--------|
| `POST /api/classify/start` | No contract break; optional `reel_frames`, `include_videos` already present |
| `GET /api/classify/status` | Optional counters: `videos_done`, `avg_ms_video` (nice-to-have) |
| UI Classify button | Copy: “Scores photos + reels (smart frames)” |
| Sexy filter | Unchanged (`glam_score >= 2`) |
| Progress UX | Status `current` already shows path; long reels less painful with 1× vision |

No new endpoints required for V1.

---

## 8. Failure modes & mitigations

| Risk | Mitigation |
|------|------------|
| OpenCV seek fails / 0 frames | Fall back to sequential read every Nth frame; then fail unscored |
| All frames blurry (action reel) | Lower sharp threshold once; second pass denser mid-clip; else unscored |
| Cover is face-only, body later | Cover + mid-clip second frame when cover glam=1 but sharp body candidates exist |
| 3× slower than photos still | Cap vision to 1; candidates are CPU-cheap |
| Reclassify floods GPU | Keep job serial; optional `include_videos: false` in UI |
| Version drift photo vs reel | Separate prompt constants; log `prompt_version` in sidecar |
| POS_FRAMES wrong on Windows H.264 | Prefer `POS_MSEC`; validate monotonic timestamps |

---

## 9. Implementation plan (PRs)

### PR1 — Smart reel frames + reel prompt (core)

| | |
|--|--|
| **Files** | `outfit_classifier.py`, `config.py`, docs |
| **Work** | Time-based candidates, quality filter, top-1 vision, `CLASSIFY_REEL_PROMPT`, richer `glam` sidecar, keep max aggregation for M=2 |
| **Test** | Manual: 10 unscored reels mixed blur/title; compare old vs new scores; ensure photos unchanged |
| **Risk** | Low — video path only |

### PR2 — Uncertain second frame + metrics in classify status

| | |
|--|--|
| **Files** | `outfit_classifier.py`, `classify_job.py` |
| **Work** | Second vision call when conf in uncertain band or `has_woman` false but sharp “person-like” metrics; job stats `video_count` |
| **Test** | Reels that flipped false→true with 2nd frame |

### PR3 — Video thumbs from best frame

| | |
|--|--|
| **Files** | `thumbs.py` (share ranker helper, e.g. `video_frames.py`) |
| **Work** | Extract shared `select_best_video_frames()`; thumbs + classify both use it |
| **Test** | Gallery tiles improve on known bad first-frames |

### PR4 — Optional IG cover download

| | |
|--|--|
| **Files** | `session.py` / downloader, metadata |
| **Work** | `download_video_thumbnails=True` or explicit cover save; classify prefers cover |
| **Test** | New reel download has companion image; classify uses it |

### PR5 — Following classify + post-level polish (later)

| | |
|--|--|
| **Files** | `classify_following.py`, `list_local_images` |
| **Work** | Include local videos; optional post_id rollup for account keep |
| **Test** | Account with reel-only local archive still classifiable |

---

## 10. Test plan (manual)

1. Pick 5 videos currently glam=-1, 5 with glam=3, 5 with glam=0/1.  
2. Run classify with force on those paths (script or UI).  
3. Record: frames_sent_to_vision, scores, brief_reason, wall time vs old 3-call path.  
4. Confirm photo classify regression: 10 random JPGs identical scores.  
5. Gallery Sexy filter still returns reels with score ≥ 2.  
6. Corrupt/short video → unscored, job continues.

Optional automated: unit-test pure ranker with synthetic frames (black / noise / sharp gradient) — no Ollama.

---

## 11. Success criteria

1. Unscored video backlog can be cleared with **~1 vision call per reel** median.  
2. Obvious title-card / black-start reels no longer dominate scores.  
3. Photo path **behavior unchanged**.  
4. Sidecar shows reel evidence (`source`, frame times).  
5. Classify UI remains single-flight with cancel; no new parallel IG/Ollama fights.  
6. Documented env knobs for tuning sharp/bright thresholds without code edits.

---

## 12. Open decisions (defaults)

| # | Question | Default proposal |
|---|----------|------------------|
| 1 | Always max glam across frames? | **Yes** for V1 after quality gate |
| 2 | Enable cover download by default? | **Yes for new downloads** in PR4; don’t re-scrape old |
| 3 | Re-score all existing videos on deploy? | **No** — only unscored / user force |
| 4 | Separate UI toggle “photos only”? | Keep API `include_videos`; optional later checkbox |
| 5 | Share best-frame cache dir? | `_thumbs` JPEG for display; temp files for classify (delete after) or small `_frame_cache` with TTL |

---

## 13. Summary

Reels need a **frame selection + prompt** strategy, not a different glam ontology. The codebase already branches on video extension but still thinks like a photo. Upgrade path: **quality-ranked freeze-frames → reel-aware JSON vision → same glam_score**, optionally boosted by **Instagram covers** and **better thumbs**. Implement PR1 first; it unblocks the 415 unscored videos without new infrastructure.
