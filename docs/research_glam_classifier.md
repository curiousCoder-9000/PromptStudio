# Glam Classifier — Analysis & Improvement Research

| Field | Value |
|-------|--------|
| **Document** | Glam / outfit classifier (PromptStudio) |
| **Author** | — |
| **Date** | 2026-08-09 |
| **Status** | Research / Draft — no code written |
| **Audience** | Engineers working on `outfit_classifier.py`, `video_frames.py`, `classify_job.py` |
| **Related** | [`design_reel_classifier.md`](design_reel_classifier.md) (PR1–PR3, implemented) |

---

## Overview

The glam classifier scores every archived media file 0–3 with one Ollama vision call and drives the gallery **Sexy** filter, glam sort, the reel detail panel, and follow/unfollow decisions. [`design_reel_classifier.md`](design_reel_classifier.md) fixed the *reel input* problem (smart frames, reel prompt, best-frame thumbs). This document looks at the layer above that: **the scoring ontology, the output plumbing, and the total absence of measurement.**

**Headline finding:** the classifier is a *recall-maximising detector* being used as a *ranker*. Per the snapshot in [`design_reel_classifier.md`](design_reel_classifier.md) §2.5, **85% of scored videos landed on the maximum score (284 g3 / 21 g2 / 24 g1 / 4 g0)**, and the Sexy filter at `>= 2` admits ~92% of them. Three deliberate design choices compound to produce that saturation (§2.1). There is no eval set, no metric and no test, so the saturation was never visible from inside the system.

**Headline recommendation:** in order — (0) build a labelled eval set from signals you already have, (1) fix the plumbing so verdicts are cheap to regenerate, (2) replace booleans + self-reported confidence with an ordinal rubric read via **logprobs**, (3) move the discriminative work to **cached SigLIP-2 embeddings + a linear head trained on your own favourites**, demoting the VLM to explainer and tie-breaker.

**Measurement caveat:** the archive and `archive.db` were empty when this was written, so nothing here was re-measured against live data. Quantitative claims are sourced from [`design_reel_classifier.md`](design_reel_classifier.md) §2.5 and are flagged where used.

---

## 1. Current state (grounded in code)

### 1.1 Pipeline

```
classify_media(path)                                     outfit_classifier.py:447
├─ .jpg/.png → classify_image                            :255   1 VLM call
└─ .mp4/.webm → classify_video                           :309
     ├─ find_video_cover_image → 1 call, short-circuit
     │    if _is_strong_reel_verdict                     :300, video_frames.py:335
     ├─ select_best_video_frames(candidates=10)          video_frames.py:200
     │    ranked by sharpness × brightness-centeredness  video_frames.py:49
     └─ 1–2 decoded frames → CLASSIFY_REEL_PROMPT
          → _prefer_verdict = max glam, tie-break conf   :274

verdict = {has_woman, sexy_revealing_outfit,
           good_breasts, confidence, brief_reason}       :112
       → compute_glam_score() → 0–3                      :134
       → persist_glam_score()                            :455
            ├─ photos.glam_score  (INTEGER)              db.py:48
            └─ <file>.meta.json   "glam" object          :477
```

### 1.2 Surfaces

| Surface | Entry point | Notes |
|---------|-------------|-------|
| UI **Classify** (per creator) | `POST /api/classify/start` → [`ClassifyJobManager`](../promptstudio/scraping/classify_job.py) | Serial, single-flight, cooperative cancel |
| CLI full-archive score | [`scripts/classify_local_photos.py`](../scripts/classify_local_photos.py) | Resumable via JSON report |
| Backfill | [`scripts/backfill_glam_scores.py`](../scripts/backfill_glam_scores.py) | Sidecar → DB |
| Following dry-run | [`scripts/classify_following.py`](../scripts/classify_following.py) | Images only |
| Gallery **Sexy** filter | `glam_min = GLAM_SEXY_MIN` (2) | [`handler.py:1429`](../promptstudio/server/handler.py) |
| Reel detail panel | `data.glam` object from sidecar | [`app.js:2259`](../app.js) |

### 1.3 Tunables

Eleven env knobs in [`config.py:172-183`](../promptstudio/config.py) — `GLAM_SEXY_MIN`, `CLASSIFY_MAX_EDGE`, and eight `CLASSIFY_REEL_*` values. Every one of them controls *frame selection or thresholds*; none controls scoring weight, because scoring has no weights (§2.2).

---

## 2. Findings

Ranked by impact on output quality.

### 2.1 The score barely discriminates — this is the actual problem

Snapshot from [`design_reel_classifier.md`](design_reel_classifier.md) §2.5: **284 / 21 / 24 / 4** across g3 / g2 / g1 / g0. A signal that fires at maximum on 85% of items cannot rank a gallery, and the Sexy filter at `>= 2` becomes a near no-op.

Three choices compound:

| # | Choice | Where | Effect |
|---|--------|-------|--------|
| 1 | *"Be GENEROUS toward glamorous feminine photos — when unsure, prefer true"*, plus FALSE framed as a narrow escape hatch | [`outfit_classifier.py:38-60`](../promptstudio/scraping/outfit_classifier.py), `:62-87` | Recall maximised at any precision cost |
| 2 | Two booleans OR-ed into 4 buckets — one true flag ⇒ 2 | `:134-144` | Half the scale is unreachable in practice |
| 3 | Videos take **max** across frames | `_prefer_verdict:274` | P(some frame says yes) rises with frame count |

Each is defensible in isolation. Stacked, they saturate the scale. Note the interaction between 1 and 3 specifically: a prompt biased toward `true` combined with max-pooling means adding frames strictly increases the expected score.

### 2.2 No ground truth, no metric, no test

No file under [`tests/`](../tests) touches `outfit_classifier`. There is no labelled set, no precision/recall number, no before/after. `v2-skin-exposure` and `v3-reel-frames` both shipped with their delta unmeasured.

Two label sources already exist and are never joined against `glam_score`:

- `photos.favorite` ([`db.py:42`](../promptstudio/storage/db.py)) — explicit positives
- `_trash/` ([`config.py:101`](../promptstudio/config.py)) — explicit negatives, retained 30 days

### 2.3 Two contradictory definitions of "keep"

```python
def matches_keep(self):                                  # :127
    return self.has_woman and (sexy or breasts) and self.confidence >= 0.5

def compute_glam_score(self):                            # :134
    ...                                                  # confidence never read
```

An item can be `glam_score = 3` — passing the gallery filter — while `matches_keep = False`. Both are written to the same sidecar object (`:484`), and the CLI reports on `matches_keep` while the UI filters on `glam_score`. The two surfaces disagree by construction.

### 2.4 The DB discards everything except the collapsed int

```python
def set_glam_score(self, rel_path, glam_score, *, has_woman=0, sexy=0):   # db.py:466
    ...
    "UPDATE photos SET glam_score = ? WHERE rel_path = ?"                 # db.py:479
```

`has_woman` and `sexy` are accepted and **silently dropped**. Confidence, the individual flags, `prompt_version` and frame evidence exist only in per-file sidecar JSON, so none of it is queryable. Consequences:

- Cannot select "everything scored by v2" for rescoring.
- Cannot sort or threshold by confidence.
- Cannot assemble a stratified eval sample without walking the filesystem and parsing thousands of JSON files.

### 2.5 Prompt versioning is tracked but inert

`prompt_version` is written on every verdict (`:124`, `:486`), but pending selection skips anything already scored:

```python
score = index.get_glam_score(rel)
if score is not None and int(score) >= 0:
    continue                                             # classify_job.py:126-129
```

Improving the prompt therefore never triggers a rescore. The archive silently accumulates mixed verdict generations, and — because of §2.4 — there is no way to find the stale ones short of a full `--force` pass over everything.

### 2.6 Output plumbing is fragile where it needn't be

[`_ollama_vision_json`](../promptstudio/scraping/outfit_classifier.py) (`:190-223`) generates free text with `num_predict: 180`, strips code fences with two regexes, then greedy-matches `\{[\s\S]*\}`. A long `brief_reason` truncates the JSON → `_error` → `ok=False` → `glam_score` stays `-1` permanently.

Also missing:

- **No retry / backoff.** A transient Ollama timeout is indistinguishable from a corrupt file; both land in the same unscored bucket that produced the 415-video backlog.
- **No error taxonomy.** `verdict.error` is a free-text string that is never persisted anywhere queryable.
- **No `keep_alive`.** A long serial job can pay model-reload cost between items.

### 2.7 Reel frame ranking is content-blind

```python
def frame_rank_score(bright, sharp):                     # video_frames.py:49
    bright_penalty = abs(bright - 120.0) / 120.0
    return max(0.0, sharp) * max(0.15, 1.0 - 0.65 * bright_penalty)
```

Nothing in the ranker knows whether a *person* is in the frame. A crisp title card outranks a slightly soft frame of the subject — which is precisely the failure [`design_reel_classifier.md`](design_reel_classifier.md) §3 set out to fix. The doc listed "optional skin-color fraction" in Option C step 2; it was never implemented.

Sampling is uniform in time ([`_sample_times_sec:66`](../promptstudio/scraping/video_frames.py)) and ignores shot boundaries, which is the one structural fact about reels that matters — a 4-cut reel with one outfit change gets 10 samples distributed by clock, not by content.

Minor, same file: the break conditions in the decoded-frame loop (`outfit_classifier.py:404-427`) express the same predicate three times, and `best` may still hold the *cover* verdict when the loop decides whether a second decoded frame is warranted — so a weak cover can suppress decoding that the budget would have allowed.

### 2.8 Cost model is inverted

Every file gets a 7B VLM call, serial, 180s timeout ([`classify_job.py:213`](../promptstudio/scraping/classify_job.py)). At the §2.5 snapshot volumes (~3693 images + ~750 videos) a full pass is 4400+ calls. Carousel slides from one post are classified independently with no perceptual dedupe, despite `find_video_cover_image` (`video_frames.py:335`) already knowing how to relate `_UTC_n` siblings.

Meanwhile the actual discriminative work — "is this the kind of image I keep?" — is a job for a 300 MB encoder at ~20 ms/image (§3.5).

### 2.9 Following-classify is the weakest link

[`list_local_images:511`](../promptstudio/scraping/outfit_classifier.py) filters to `IMAGE_EXTENSIONS`, so reel-only creators are invisible. [`decide_account:557`](../promptstudio/scraping/outfit_classifier.py) then majority-votes ≤3 posts through the §2.1 generous prompt — which, given saturation, returns `keep` almost always. The follow/unfollow recommendation is close to a constant.

---

## 3. Research: what would actually make it better

### 3.1 Constrained decoding instead of regex extraction

Ollama supports `format` as a **JSON Schema object** on `/api/generate`, using constrained decoding — every field, type and constraint satisfied, not "usually". This removes the entire fence-stripping / greedy-brace-match block (`:214-223`) and the truncation failure mode with it.

```python
payload = {
    "model": MODEL_NAME,
    "prompt": prompt,
    "images": [b64],
    "stream": False,
    "keep_alive": "30m",
    "format": GLAM_SCHEMA,          # JSON Schema object
    "options": {...},
}
```

Sources: [Ollama structured outputs](https://docs.ollama.com/capabilities/structured-outputs), [`POST /api/generate` reference](https://docs.ollama.com/api/generate).

### 3.2 Stop asking the model for `confidence` — read logprobs

Verbalized confidence from VLMs is known to be poorly calibrated. The directly relevant result is *"VLM Judges Can Rank but Cannot Score"*, which names the failure mode **ranking–scoring decoupling**: judges order items correctly while assigning inconsistent absolute values. Its measurements are uncomfortably on-point for this system — aesthetics-type tasks produce prediction intervals spanning ~40% of the scale range, and the *same* judge produces 4.5× narrower intervals on cleanly annotated data than on noisy single-annotator data. The recommended remedy needs no retraining: **use score-token log-probabilities**, and treat interval width as a reliability signal.

As of Ollama v0.12.11, `/api/generate` accepts `logprobs: true` and `top_logprobs: N` and returns per-token probabilities:

```json
{"response": "...", "logprobs": [
  {"token": "true", "logprob": -0.02,
   "top_logprobs": [{"token": "true", "logprob": -0.02},
                    {"token": "false", "logprob": -3.9}]}]}
```

Taking `P(true)` on each decision token yields a continuous, monotone signal — vastly better than a number the model invented about itself. It costs nothing extra: same call, one more request field.

Sources: [ollama/ollama#16117](https://github.com/ollama/ollama/issues/16117), [arXiv:2604.25235](https://arxiv.org/html/2604.25235v1), [arXiv:2504.14848](https://arxiv.org/html/2504.14848v1).

### 3.3 Redesign the scale: ordinal sub-scores → one continuous score

Replace two booleans with a schema-enforced rubric, each level anchored by a one-line description (unanchored Likert scales are where central-tendency bias creeps in):

```json
{
  "is_person": true,
  "is_woman": true,
  "skin_exposure": 3,        // 0 fully covered … 4 swimwear/lingerie
  "figure_visibility": 2,    // 0 shape hidden … 4 figure clearly defined
  "glam_styling": 3,         // 0 candid/utility … 4 editorial/styled
  "frame_quality": 3,        // 0 unusable … 4 sharp, well-exposed, subject centred
  "reason": "…"
}
```

`glam_100 = w · [skin, figure, styling] × quality_gate`, weights in `config.py`. Keep the 0–3 value as a **derived view** so `handler.py`, `app.js` and the DB column are untouched.

The practical payoff is not accuracy, it's **latency of iteration**: the gallery threshold becomes a slider you retune without re-running the model. Today, retuning the Sexy filter means 4400 VLM calls.

### 3.4 Model tier: `qwen2.5vl:7b` → `qwen3-vl:8b`

Qwen3-VL-8B is ~6 GB at Q4, scores 69.6 MMMU / 96.1 DocVQA, is Apache-2.0, and the 7B-class variant reportedly beats Llama 3.2 Vision 11B substantially. Gemma 3 (~3 GB at Q4) is the fallback tier if VRAM is tight.

This is a one-line `MODEL_NAME` change in [`config.py:203`](../promptstudio/config.py) — but it should only be made **after** §4 Stage 0, otherwise it is an unmeasured vibe swap of the kind that produced the current state.

Sources: [Best local VLMs 2026](https://tinyweights.dev/posts/best-local-vision-language-models-2026/), [Qwen3-VL local setup](https://localaimaster.com/blog/qwen-3-vl-local-setup).

### 3.5 The architectural win: embeddings + a head trained on your labels

**This is where the effort belongs.** "Glam I want to keep" is not an objective image property — it is personal taste. A zero-shot VLM cannot learn it, no matter how the prompt is tuned; every prompt iteration to date has been an attempt to hand-encode a preference function in English. A linear probe on a few hundred labels learns it directly.

```
image ──► SigLIP-2 image embedding        ~20–50 ms CPU, cached as BLOB in photos
      ──► logistic regression / small MLP  trained on favourites + trash + manual labels
      ──► calibrated P(keep) ∈ [0,1]
```

Why this shape fits here:

- **~100× cheaper** than a 7B VLM call. A full-archive rescore drops from hours to minutes, which makes *retraining as taste drifts* effectively free — the property the current design most lacks.
- **Small-data friendly.** Frozen-encoder + linear-probe is well documented as competitive with fine-tuning on limited labels; hundreds, not thousands.
- **Composable features.** NudeNet v3 returns 18 covered/exposed body-part detections with boxes in ~20 ms. Feed those as *features into the head*, not as a separate filter — they give the model explicit skin-exposure evidence that a global embedding blurs.

The VLM does **not** go away. It keeps two jobs it is genuinely good at:

1. The human-readable `brief_reason` shown in the reel panel ([`app.js:2283`](../app.js)) — a linear head can't explain itself.
2. Low-margin cases near the decision boundary — roughly 10–20% of items instead of 100%.

Sources: [SigLIP 2 (arXiv:2502.14786)](https://arxiv.org/pdf/2502.14786), [Embeddings are all you need (arXiv:2412.09445)](https://arxiv.org/pdf/2412.09445), [NSFW detection pipelines](https://pic-tomo.com/en/blog/nsfw-detection-machine-learning-pipeline).

### 3.6 Reels: shot-aware, person-aware frame selection

Three changes to [`video_frames.py`](../promptstudio/scraping/video_frames.py), all inside the existing OpenCV dependency:

| Today | Proposed |
|-------|----------|
| 10 uniform time samples (`_sample_times_sec:66`) | Shot-boundary detection (PySceneDetect, or frame-difference threshold) → one candidate per shot |
| Rank = `sharp × brightness` (`:49`) | Rank = `person_present × sharp × brightness`, using YuNet (ships with OpenCV) or a cheap person detector |
| Aggregate = **max** across frames (`:274`) | Aggregate = **75th percentile** across shots — one lucky frame stops dominating |

Persist per-shot scores in the existing `evidence` dict (`:323`) so a bad call is diagnosable from the sidecar.

### 3.7 Plumbing cleanups

- `prompt_version` and `glam_error` as **DB columns** → rescore-by-version and retry-transients become SQL queries instead of filesystem walks. Follows the additive `_IDENTITY_COLUMNS` migration pattern already in [`db.py:74`](../promptstudio/storage/db.py).
- Stop discarding `has_woman` / `sexy` in `set_glam_score` (`db.py:466`).
- Retry with backoff around the Ollama call, distinguishing transient from permanent errors.
- Perceptual-hash dedupe of carousel siblings — classify one representative, propagate to the `_UTC_n` group.
- Unify `matches_keep` with the score so the CLI and the UI stop disagreeing (§2.3).

---

## 4. Suggested sequencing

| Stage | Work | Gate / rationale |
|-------|------|------------------|
| **0 — Measure** | Stratified eval set (~300 items), labels seeded from `photos.favorite` + `_trash`, topped up via a keyboard-labelling contact sheet. Metric = **average precision** (ranking), not accuracy. Freeze as a fixture under `tests/`. | Everything below is unmeasurable without it. It also produces the training labels Stage 3 needs, so it is not overhead. |
| **1 — Plumbing** | §3.1 `format` schema + `keep_alive`; retry/backoff; `glam_error` + `prompt_version` DB columns; stop dropping flags in `set_glam_score`. | Cheap, no modelling risk. Unblocks the unscored backlog and turns Stage 2 rescoring into a query. |
| **2 — Scoring** | §3.3 ordinal rubric + §3.2 logprobs → `glam_100`; unify `matches_keep`; keep 0–3 as a derived view. Optionally §3.4 model swap, measured against Stage 0. | Fixes saturation; makes the gallery threshold tunable without re-inference. |
| **3 — Learned head** | §3.5 SigLIP-2 embeddings cached in DB + logistic head on Stage 0 labels. VLM demoted to explainer + boundary cases. | Biggest accuracy and cost win. Depends on Stage 0 labels. |
| **4 — Reels & edges** | §3.6 shot-aware frames + percentile aggregation; carousel dedupe; include videos in following-classify (§2.9). | Reel-specific; independent of Stages 2–3, can run in parallel. |

**Stage 0 is the gate.** Not because the other work is hard, but because the current state is what happens without it: a detector tuned three times for recall, used as a ranker, with no instrument that would have shown when it stopped separating anything.

---

## 5. Open questions

| # | Question | Default proposal |
|---|----------|------------------|
| 1 | Where do manual labels live? | New `labels` table in `archive.db` (`rel_path`, `label`, `labelled_at`) — keeps them out of git and joinable |
| 2 | Rescore the whole archive after Stage 2? | Yes — but only once Stage 1 makes it a `WHERE prompt_version != ?` query |
| 3 | Keep the 0–3 scale in the API? | Yes, as a derived view. `glam_100` is additive; no UI break |
| 4 | Add SigLIP-2 as a hard dependency? | Optional extra — degrade to VLM-only when the encoder is absent |
| 5 | Per-creator score normalisation? | Defer. Revisit after Stage 3; a learned head may absorb it |
| 6 | Retire the JSON report in `classify_local_photos.py`? | Yes once §3.7 DB columns land — the DB becomes the single resume source |

---

## 6. Summary

The reel work in [`design_reel_classifier.md`](design_reel_classifier.md) fixed *which pixels reach the model*. The remaining problems are one level up: **what the model is asked for**, **how the answer is read**, and **whether anyone checks**. The prompt is tuned for recall, the scale collapses to 4 buckets that one true flag saturates, confidence is self-reported and unused in the score, everything but a single integer is discarded before it reaches the database, and no test or metric exists to notice.

The cheap fixes (constrained decoding, logprobs, ordinal rubric) are worth doing and take days. The durable fix is recognising that this is a **preference model, not a detector** — and preference models are learned from labels, not written in English. Build the eval set first; it is simultaneously the instrument and the training data.

---

## Sources

- [Structured outputs — Ollama docs](https://docs.ollama.com/capabilities/structured-outputs)
- [`POST /api/generate` — Ollama API reference](https://docs.ollama.com/api/generate)
- [Add logprobs support — ollama/ollama#16117](https://github.com/ollama/ollama/issues/16117)
- [VLM Judges Can Rank but Cannot Score: Task-Dependent Uncertainty in Multimodal Evaluation (arXiv:2604.25235)](https://arxiv.org/html/2604.25235v1)
- [Object-Level Verbalized Confidence Calibration in VLMs via Semantic Perturbation (arXiv:2504.14848)](https://arxiv.org/html/2504.14848v1)
- [SigLIP 2: Multilingual Vision-Language Encoders (arXiv:2502.14786)](https://arxiv.org/pdf/2502.14786)
- [Embeddings are all you need! Training-Free Embedding Analysis (arXiv:2412.09445)](https://arxiv.org/pdf/2412.09445)
- [The Best Local Vision Language Models in 2026](https://tinyweights.dev/posts/best-local-vision-language-models-2026/)
- [Qwen 3 VL Local Setup](https://localaimaster.com/blog/qwen-3-vl-local-setup)
- [NSFW Image Detection ML — YOLO, NudeNet, Ensembles](https://pic-tomo.com/en/blog/nsfw-detection-machine-learning-pipeline)
