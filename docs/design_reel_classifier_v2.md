# Design: Reel classifier V2 — whole-reel understanding

| Field | Value |
|-------|--------|
| **Status** | P1–P4 implemented. **P0 (eval set) and P5 (model A/B) outstanding — the thresholds below are untuned guesses until P0 exists.** |
| **Date** | 2026-08-09 |
| **Supersedes** | `design_reel_classifier.md` (V1 — PR1–PR3 shipped) |
| **Problem** | V1 scores reels from *one sharp frame*. Reels reveal the payoff outfit at the end, in motion. The pipeline is structurally blind to it, and the prompt has collapsed the output distribution. |
| **Owner modules** | `video_frames.py`, `outfit_classifier.py`, `classify_job.py`, `config.py` |

---

## 1. TL;DR

V1 is not "a bit undertuned" — it has three independent defects that each alone would cap quality, and they compound:

1. **The reveal is never sampled.** Frame candidates stop at **89.7%** of the clip. The last ~10% — where transition reels land the hero outfit — is excluded by config.
2. **The frame ranker is uncorrelated with the target.** Frames are picked by `sharpness × brightness`. The reveal happens *during motion* (spin, whip-pan, jump-cut), so it is systematically the **blurriest** moment and loses to the static intro shot where she is still in the "before" outfit.
3. **The prompt destroyed discrimination.** It says "be GENEROUS… when unsure, prefer true" twice. Result: **85% of all scored videos are glam 3**. A classifier that emits one value 85% of the time carries almost no information — no amount of frame-picking fixes that.

The fix is to change the **unit of inference** from "one lucky frame" to "the whole reel", while keeping the ~1 vision call budget:

> Sample the full timeline (tail included) → segment by shot cut → compose a **timestamped contact sheet** of 9 frames → **one** vision call that scores every panel and reports the peak → optional full-resolution confirm on the winning panel.

Plus three supporting changes: **JSON-schema structured output** (kills the parse-failure class that likely dominates the 55% unscored backlog), a **recalibrated ordinal prompt** (restores discrimination), and a **model upgrade** to `qwen3-vl:8b`.

Nothing here is measurable without ground truth, so **P0 is a labeled eval set** — not code.

---

## 2. Evidence

### 2.1 The tail is excluded by construction

`config.py:178-179` sets `SKIP_HEAD_FRAC=0.08`, `SKIP_TAIL_FRAC=0.06`; `video_frames.py:66-84` spreads `K=10` candidates across the remaining window at bin centres. Actual sampled positions:

```
0.123 0.209 0.295 0.381 0.467 0.553 0.639 0.725 0.811 0.897
                                                    ^^^^^ last candidate
```

The final **10.3%** of every reel is never decoded. For a 15 s reel that is the last 1.5 s — the standard hold on the final outfit.

### 2.2 The ranker prefers the "before" shot

`video_frames.py:49-54`:

```python
def frame_rank_score(bright, sharp):
    bright_penalty = abs(bright - 120.0) / 120.0
    bright_factor  = max(0.15, 1.0 - 0.65 * bright_penalty)
    return max(0.0, sharp) * bright_factor
```

Rank is dominated by Laplacian variance. Measured on the real function: a static intro frame at `sharp=300` ranks **5×** a mid-transition frame at `sharp=60`, at identical brightness. `video_frames.py:242-249` then sorts purely by that rank with **no temporal-diversity constraint**, so the top picks routinely come from the same static shot.

This is the known-weak approach. The literature on keyframe selection says so directly: low-level brightness/sharpness scoring "may fail to capture semantically meaningful content such as objects, characters, or actions."

With `CLASSIFY_REEL_VISION_MAX=1` (`config.py:177`), exactly **one** such frame reaches the model.

### 2.3 The prompt collapsed the score distribution

From the V1 doc's archive snapshot (2026-08-08), of 333 scored videos:

| glam | count | share |
|------|------:|------:|
| 3 | 284 | **85.3%** |
| 2 | 21 | 6.3% |
| 1 | 24 | 7.2% |
| 0 | 4 | 1.2% |

Causes, all in `outfit_classifier.py:62-87`:

- "Be **GENEROUS** … when unsure, prefer true" — explicit bias toward the positive class.
- `good_breasts` is defined so broadly ("attractive body outline is enough", "side/back view … count as TRUE") that it is true for essentially any woman in frame.
- `compute_glam_score` (`outfit_classifier.py:134-144`) awards **3** whenever both flags are true — which the prompt has made the default.

The Sexy filter is `glam_score >= 2`, so 91.6% of reels pass. The filter is close to a no-op.

**Principle being violated:** generosity is a *policy* choice and belongs at the threshold (`GLAM_SEXY_MIN`), not baked into the measurement. Bias the prompt and you lose the ability to move the threshold at all.

### 2.4 Reliability defects that inflate the unscored backlog

`_ollama_vision_json` (`outfit_classifier.py:183-223`):

- **No `format` parameter.** Output is free text, then regex-scraped (`re.search(r"\{[\s\S]*\}")`). Ollama supports JSON-schema-constrained decoding that makes malformed JSON mechanically impossible — unused here.
- **`num_predict: 180`** can truncate mid-JSON on a verbose `brief_reason` → parse failure.
- **No retry.** Any transient error → `ok=False` → `classify_job.py:246-250` leaves `glam_score = -1`.

Three independent paths to "unscored", none retried. This is the most likely bulk explanation for **415 / 750 (55%) unscored videos**, and it is the cheapest thing on this list to fix.

### 2.5 Two logic bugs

**Carousel cover mis-attribution** — `video_frames.py:350-356`. For a carousel slide `..._UTC_3.mp4`, the cover search also globs siblings `..._UTC_1.jpg` … `_UTC_5.jpg`. Those are *different slides*, not this video's cover. `outfit_classifier.py:366-374` then classifies that unrelated photo and, if `_is_strong_reel_verdict`, **returns immediately — the .mp4 is never decoded.** The video inherits another image's score.

**Confidence-gated early stop is backwards** — `_needs_second_reel_frame` (`outfit_classifier.py:287-297`) requests another frame only when confidence is *middling* (0.45–0.65). A confident read of the modest intro (`has_woman=True, glam=1, conf=0.8`) returns `False` → loop breaks at `outfit_classifier.py:426`. **The exact failure the user reports terminates the search early.** Uncertainty should be measured on the *quantity of interest* (could a later frame score higher?), not on the model's self-reported confidence in one frame.

### 2.6 Minor

- `evidence["frames_considered"] = len(picks)` (`outfit_classifier.py:384`) records picks (≤3), not candidates (10) — misleading debug data.
- `top_n=min(3, max(2, vision_budget+1))` encodes 2 JPEGs when budget is 1; one is always discarded.
- Model is `qwen2.5vl:7b` (`config.py:203`), a generation behind what Ollama now ships.

---

## 3. Target architecture

```
 .mp4 ──► 1. sample_timeline(K=16, head=0.02, tail=0.00, +forced last frame)
          2. detect_cuts(HSV histogram delta) → shots
          3. per-shot representative: rank = sharp^0.5 × skin_fraction × bright_factor
             ─ guarantee ≥1 frame from the FINAL shot
          4. compose_contact_sheet(9 panels, timestamp burned in)
                      │
                      ▼  ONE vision call, JSON-schema constrained
          5. per-panel {has_woman, exposure_tier 0-4} + peak_panel + reel rollup
                      │
                      ▼  only if peak is borderline (tier 2/3 boundary or low conf)
          6. full-resolution confirm on the peak panel's frame
          7. glam_score = map(reel_exposure) → unchanged 0-3 DB contract
```

### 3.1 Why a contact sheet

- **One call sees the whole arc.** The model can answer "does this reel show a revealing outfit *at any point*" instead of "is this one frame revealing". That is the actual product question and it is what the current design cannot ask.
- **Cost is flat.** Nine frames, one inference. Same budget as today, ~9× the coverage.
- **Model-agnostic.** Ollama's multi-image support is model-dependent and contested — `mllama` is hard-capped at one image, `qwen3-vl` accepts several subject to memory. A montage is a single image and works everywhere. Build the frame batch as an abstraction that can emit *either* N images *or* one montage, probe capability once, and default to montage.
- **Panels are addressable.** Burning `#3 4.2s` into each panel lets the model return `peak_panel: 3`, which gives us the reveal timestamp for free — good evidence, and it feeds the thumbnail picker.

Panel geometry: 3×3 of 256×456 (reel aspect) → 768×1368 sheet. Requires `CLASSIFY_MAX_EDGE` ≥ 1368 for the sheet path and `num_ctx` 8192.

**Trade-off, stated plainly:** each panel is ~256 px wide, below full-frame detail. For "how much skin is visible" that is sufficient; for fine texture (sheer vs opaque) it is not. That is exactly what step 6 exists for — the cascade recovers precision on the small set of borderline cases, at a median cost still under 2 calls.

### 3.2 Skin fraction — make the ranker point at the target

Add a YCrCb skin-tone mask fraction per candidate frame (numpy + opencv, both already installed, no new dependency) and fold it into the rank:

```python
rank = (sharp ** 0.5) * (0.3 + skin_fraction) * bright_factor
```

The `sqrt` deliberately flattens sharpness so a moderately blurry reveal can beat a razor-sharp intro. Skin fraction is *directly* correlated with the quantity we score, which sharpness never was.

**Limitation, up front:** YCrCb skin thresholds vary with lighting and skin tone and will fire on sand, wood, and warm walls. It is used as a **ranking term with a floor, never as a gate** — a frame is never discarded for low skin fraction, it just sorts lower. Threshold ranges get tuned on the eval set, not guessed.

### 3.3 Recalibrated ordinal prompt

Replace the two coupled booleans with one **anchored 0–4 exposure ordinal**, and drop every "be generous" instruction:

| tier | anchor |
|------|--------|
| 0 | no woman — text card, logo, male-only, scenery, food, cartoon |
| 1 | fully modest — everyday coverage, no skin beyond face/hands |
| 2 | normal fashion — some skin (arms, neck, shoulders), fitted but not revealing |
| 3 | revealing — midriff / cleavage / back / thighs visible, short dress, tight fit emphasising figure |
| 4 | maximally revealing — bikini, swimwear, lingerie, bodysuit, sheer/mesh |

Calibration instruction replaces the bias instruction: *"If ambiguous, choose the lower tier and report confidence below 0.5."*

Back-compat mapping to the existing DB column — **no migration, Sexy filter untouched**:

```
tier 0 → glam 0      tier 3 → glam 2
tier 1 → glam 0      tier 4 → glam 3
tier 2 → glam 1
```

Keep `has_woman` (genuinely useful, cheap). Retire `good_breasts` as a *scoring* input — it is the flag that saturated — but keep emitting it in the sidecar so nothing downstream breaks. Version as `v4-reel-sheet`.

### 3.4 Structured output

Constrain decoding with a JSON schema via Ollama's `format` parameter, which forces the model to emit only schema-conforming tokens:

```json
{"type":"object","required":["panels","peak_panel","reel_exposure","confidence"],
 "properties":{
   "panels":{"type":"array","items":{"type":"object",
     "required":["i","has_woman","exposure_tier"],
     "properties":{"i":{"type":"integer"},"has_woman":{"type":"boolean"},
                   "exposure_tier":{"type":"integer","minimum":0,"maximum":4}}}},
   "peak_panel":{"type":"integer"},
   "reel_exposure":{"type":"integer","minimum":0,"maximum":4},
   "outfit_changes":{"type":"boolean"},
   "confidence":{"type":"number"},
   "brief_reason":{"type":"string","maxLength":120}}}
```

Keep the regex fallback for one release in case a model/version rejects `format`.

### 3.5 Aggregation

`reel_exposure = max(panel.exposure_tier where panel.has_woman)`, computed **in Python from the panel array**, not taken from the model's own rollup field — the array is the auditable part, and max-over-panels is precisely the "reveal anywhere counts" semantic. Keep the model's rollup only as a cross-check; log disagreements.

Escalate to the step-6 full-res confirm when: peak tier ∈ {2,3} (the Sexy-filter boundary), or `confidence < 0.5`, or `peak_panel` is in the final shot and tier ≥ 3 (highest-value case — confirm before trusting).

---

## 3.6 What implementation changed about the design

Two things surfaced while building that the design above got wrong. Both are the
*same* defect as §2.2 — a heuristic that is anti-correlated with the target —
hiding one layer further down.

**The sharpness gate deleted the reveal before ranking saw it.** Fixing the
ranker is not enough when `_quality_pool` drops frames below an absolute
Laplacian floor first. A motion-blurred reveal shot measured ~28 against a floor
of 35 and was erased wholesale, so the improved ranker never got a vote. The
gate now removes garbage without ever emptying a shot: a shot where nothing
clears the floor is thinned, not deleted, and `select_timeline_frames` skips the
sharpness gate entirely — a soft frame still shows what the outfit is.

**Uniform time buckets straddle cuts.** Bucketing the timeline into N equal
slices lets the bucket containing the cut be won by the long, sharp intro shot.
On a measured 3 s test clip the reveal occupied 28% of the runtime and got
**1 panel out of 9**. Panels are now allocated *per shot* in proportion to
screen time (min 1 each, final shot always represented), which gave the same
clip 6 intro panels and 3 reveal panels.

Generalisable form: **any absolute threshold applied before the signal-aware
ranking is a place where the reveal can be lost.** Worth checking first if reel
scores still look wrong after tuning.

---

## 4. Phased plan

### P0 — Eval sets and harness ⚙️ *harness built (reels + photos), labelling outstanding*

Nothing below is verifiable without this, and V1 shipped without it. The
**tooling is done and tested**; what remains is the ~2 h of human judgement,
which has to happen on the machine that holds the real archive.

```powershell
# 1. stratified sample + one contact sheet per reel   (no Ollama needed)
py scripts/eval_reel_classifier.py sample --kind reel --count 120

# 2. open the printed file:// page, label with 0-4 and `r`, hit Export
py scripts/eval_reel_classifier.py label --kind reel --import <downloaded labels.jsonl>

# 3. record the current pipeline as the baseline        (needs Ollama)
py scripts/eval_reel_classifier.py run --kind reel --name baseline

# 4. after any change: re-run and diff
py scripts/eval_reel_classifier.py run --kind reel --name v4-sheet
py scripts/eval_reel_classifier.py report --kind reel --name v4-sheet --against baseline
```

**Photos use the same harness** (`--kind photo`), with a downscaled copy of the
image instead of a contact sheet and no `reveal_at_end` question. That set
exists to answer one open decision: §6 ships `CLASSIFY_PHOTO_ORDINAL=0`, so
photos still run the generous three-boolean prompt with the same collapse
problem §2.3 describes, and there is currently no evidence either way.

```powershell
py scripts/eval_reel_classifier.py sample --kind photo --count 120
py scripts/eval_reel_classifier.py run    --kind photo --name legacy
$env:CLASSIFY_PHOTO_ORDINAL=1
py scripts/eval_reel_classifier.py run    --kind photo --name ordinal
py scripts/eval_reel_classifier.py report --kind photo --name ordinal --against legacy
```

Compare on **`glam_accuracy`**. The legacy prompt produces no `exposure_tier`,
so tier accuracy is undefined for it and the report says so rather than printing
a misleading `0.0`; the 0–3 glam score is the one axis both vocabularies share,
and `true_glam()` projects the label onto it. Flip the flag only if ordinal wins
there *and* `top_score_share` drops.

Everything lands in `<archive>/_eval/` — gitignored and excluded from the
gallery index, because the labels encode personal taste over personal media.
They are also the one artefact worth backing up: sheets and results regenerate,
hours of judgement do not.

**Stratification is by *current* glam score** (unscored / 0–1 / 2 / 3), not by
reveal-vs-stable. The true strata are what the labelling is meant to discover,
so they cannot be used to select — current score is the available proxy and
guarantees the set spans the pipeline's whole output range instead of only the
cases it already handles. `--seed` makes the sample reproducible.

**Two fields, not three.** `true_exposure` (0–4, same anchors the model is
given) and `reveal_at_end`. `peak_time_sec` is in the schema but not asked for:
no metric in §5 uses it, and every extra field slows 120 judgements down.

`report` prints each §5 target with PASS/FAIL, a `mean_signed_error` (positive =
over-scoring), and a true→predicted confusion matrix so a systematic failure is
visible rather than averaged away. With `--against` it shows the delta per
metric.

**Do step 3 before changing anything else** — a baseline recorded after a change
is not a baseline.

### P1 — Reliability ✅ *(smallest diff, likely largest immediate win)*

`outfit_classifier.py` only. Independent of the architecture work — ship first.

- Add `format=<schema>` structured output; keep regex fallback.
- `num_predict` 180 → 400; add `keep_alive: "30m"`.
- 2 retries with backoff on transient/parse failure.
- Fix the carousel cover bug: only accept `{stem}.jpg` as a cover; drop the `_UTC_{1..5}` sibling glob.
- Fix `frames_considered` to record candidates.

**Expected:** most of the 415 unscored clear on re-run. Measure the unscored rate before/after.

### P2 — Timeline coverage ✅

`video_frames.py`, `config.py`.

- `SKIP_TAIL_FRAC` 0.06 → **0.0**, `SKIP_HEAD_FRAC` 0.08 → 0.02, `CANDIDATES` 10 → 16.
- Force-include the last decodable frame and one at ~0.97·D.
- HSV-histogram cut detection over candidates → shot segmentation; guarantee ≥1 representative per shot and **always one from the final shot**.
- Add skin fraction; switch to the §3.2 rank.
- Enforce temporal diversity in `_dedupe_near` (min gap = D/12, not a fixed 0.35 s).

**Gate:** reveal recall must improve on the P0 eval set. Pure-CPU change, no extra vision cost.

### P3 — Contact sheet + one-call whole-reel scoring ✅

`video_frames.py` (`compose_contact_sheet`), `outfit_classifier.py` (`classify_reel_sheet`).

- 3×3 sheet, timestamp + index burned per panel.
- New `CLASSIFY_REEL_PROMPT_V4` with the §3.3 ordinal.
- Python-side max-over-panels aggregation; cascade to full-res confirm per §3.5.
- Sidecar evidence: `panels`, `peak_panel`, `peak_time_sec`, `outfit_changes`, `sheet_times`.
- Feed `peak_time_sec` into `thumbs.py` — the gallery tile becomes the reveal frame, free.

**Gate:** exact accuracy and reveal recall both beat P2; median calls ≤ 2.

### P4 — Prompt recalibration ✅ *(threshold re-tune still needs P0)*

Can land with P3 but **measure separately** — otherwise you cannot attribute the change.

- Strip generosity language; add tier anchors and the calibration instruction.
- Re-tune `GLAM_SEXY_MIN` against the eval set so the Sexy filter targets a sane pass rate (~35–50%, not 92%).
- **Distribution guard:** if any single glam value exceeds 60% of outputs, fail the eval run loudly.

### P5 — Model upgrade *(pending P0)*

- A/B `qwen2.5vl:7b` (current) vs **`qwen3-vl:8b`** (6.1 GB, 256K ctx) vs `qwen3-vl:4b` (3.3 GB) on the frozen eval set. Qwen3-VL is the current strongest open VLM in this size class and is materially better at reading burnt-in panel labels — which the contact sheet depends on.
- If VRAM allows, also try `qwen3-vl:30b` as a quality ceiling reference.
- Probe multi-image support once at startup; if the chosen model accepts 9 images, offer N-images mode as an alternative to the montage and A/B it — full per-frame resolution at the same call count.

### Deliberately out of scope

Re-scoring the whole archive on deploy (only unscored + user force, per V1 decision 3); post-level carousel rollup; audio; cloud APIs; auto-delete.

---

## 5. Success criteria

Measured on the frozen P0 eval set, versus the recorded V1 baseline:

| # | Metric | Target |
|---|--------|--------|
| 1 | **Reveal recall** (transition reels scored at their peak outfit) | ≥ 0.85 |
| 2 | Exact ordinal accuracy | ≥ 0.75 |
| 3 | ±1 ordinal accuracy | ≥ 0.95 |
| 4 | Max share of any single glam value | ≤ 0.60 |
| 5 | Unscored rate after retries | ≤ 0.02 |
| 6 | Median vision calls per reel | ≤ 2 |
| 7 | Photo path scores | byte-identical (regression gate) |

(1) and (4) are the two that matter — (1) is the reported bug, (4) is the one nobody noticed.

---

## 6. Config changes

| Env | Old | New | Note |
|-----|----:|----:|------|
| `CLASSIFY_REEL_CANDIDATES` | 10 | 16 | CPU-only cost |
| `CLASSIFY_REEL_SKIP_HEAD_FRAC` | 0.08 | 0.02 | |
| `CLASSIFY_REEL_SKIP_TAIL_FRAC` | 0.06 | **0.00** | the bug |
| `CLASSIFY_REEL_SHARP_REF` | — | `140` | sharpness saturation point |
| `CLASSIFY_REEL_CUT_THRESHOLD` | — | `0.45` | HSV correlation => shot cut |
| `CLASSIFY_REEL_VISION_MAX` | 1 | 2 | sheet + confirm |
| `CLASSIFY_REEL_SHEET` | — | `1` | montage vs N-images |
| `CLASSIFY_REEL_SHEET_PANELS` | — | `9` | |
| `CLASSIFY_REEL_SKIN_WEIGHT` | — | `1.0` | 0 disables skin term |
| `CLASSIFY_MAX_EDGE` | 768 | 1368 | sheet path only |
| `CLASSIFY_NUM_CTX` | 4096 | 8192 | |
| `CLASSIFY_RETRIES` | — | `2` | |
| `OLLAMA_VISION_MODEL` | `qwen2.5vl:7b` | *(unchanged)* | switch to `qwen3-vl:8b` after the P5 A/B |

---

## 7. Risks

| Risk | Mitigation |
|------|------------|
| Contact-sheet panels too small for sheer/texture calls | Step-6 full-res confirm on the peak panel; if accuracy still lags, drop to 2×2 sheets ×2 calls |
| Skin heuristic fires on sand/wood/warm walls | Ranking term with a floor, never a gate; tune on eval set; `SKIN_WEIGHT=0` kills it |
| Model ignores burnt-in panel indices | Schema requires `i` per panel; validate `len(panels)==9` and indices unique, else fall back to single-frame path |
| Recalibrated prompt flips many existing scores | Only unscored + force are re-run; old scores keep their `prompt_version`; UI can show mixed versions |
| qwen3-vl needs a newer Ollama (≥ 0.12.7) | Version check at startup; keep qwen2.5vl fallback |
| Sheet raises per-call latency | Fewer calls offsets it; measure wall-clock per reel in the eval harness, not per call |
| Eval set reflects one person's taste | It is a personal keep filter — that is the correct target. Document it; re-label if the product intent changes |
```
