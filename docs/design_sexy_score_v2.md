# Design: sexy score 0–100 — learned from comparisons, not prompted

**Status:** Draft for approval. Nothing implemented.
**Replaces:** `glam_score` 0–3 as the source of truth (0–3 stays as a derived view).
**Related:** [plan_photo_ordinal_holdout_v7.md](plan_photo_ordinal_holdout_v7.md) ·
[eval_photo_ordinal_log.md](eval_photo_ordinal_log.md) · [design_reel_classifier_v2.md](design_reel_classifier_v2.md)

---

## 1. The problem with a 4-point score

A 0–3 scale cannot rank. That is the whole issue, and every symptom traces back to it.

With four buckets over an archive of thousands, the top bucket holds hundreds of
photos in **arbitrary order** — `ORDER BY glam_score DESC` then falls through to
`taken_at`, so "best first" is really "newest of the top bucket first". There is no
answer to *"show me my best 50"*, which is the actual question being asked of the
archive.

Measured on the round-2 holdout, the coarseness is severe:

| | v6 |
|--|---:|
| distinct predicted values used | **4** (0, 2, 3, 4 → glam 0, 1, 2, 3) |
| photos in the single largest bucket | **79 of 120** (66%) |
| tier 2 precision | 0.519 — a dumping ground |
| tier 3 recall | 0.140 — nearly unused |

Two thirds of the archive lands on one number. A 0–100 score is not cosmetic
rescaling — it is the difference between a filter and a ranking.

### Why the current pipeline can't just emit 0–100

Asking the VLM for a number out of 100 would be worse, not better. Absolute
numeric judgment is the noisiest labelling protocol there is: annotators are
materially more consistent answering *"is A better than B"* than *"is this a 3 or
a 4"*, because a comparison is local while an absolute score demands a stable
internal scale held across a whole session. Your own labels demonstrate it — the
round-2 gold file is a re-export after **17 tier edits**, and the boundary that
drifted is the one the score depends on. Widening the scale to 100 multiplies that
instability rather than fixing it.

Three deeper causes, all of which a wider scale alone leaves untouched:

1. **Taste is stored as prose.** The scoring function *is* your preference, and it
   lives in `CLASSIFY_FRAME_V4_PROMPT` as English. v1→v7a is six full-archive
   rescores to answer six variants of one question.
2. **One number conflates five axes** — eligibility (man/poster/blur), image
   quality, garment exposure, composition, and taste. A 7B model does five-way
   reasoning and collapses it with a function private to you.
3. **The existing feedback loop is discarded.** `photos.favorite` exists, is
   indexed (`idx_photos_fav`), has a write API — and nothing reads it back into
   scoring. Every favourite is a free, unambiguous positive label in your own hand.

---

## 2. What "sexy score out of 100" should mean

This is the load-bearing decision, so it gets stated precisely rather than assumed.

### Recommended: percentile against a frozen reference snapshot

```
sexy_score = 100 × F(s)
    s = latent preference strength (learned, unbounded)
    F = empirical CDF of s over a frozen reference snapshot of the archive
```

**Reading:** `sexy_score = 87` means *"beats 87% of the reference archive"*.

Why this and not a raw model output:

- **Instantly actionable.** "Top 10%" is a decision; "0.63 logit" is not.
- **Sort works by construction** — it is a rank, so `ORDER BY sexy_score DESC` is
  exactly "my best first", with no ties to break.
- **No calibration hand-waving.** Percentiles need no probability semantics.
- **Stable per item.** `F` is fitted once and **frozen** (stored as ~101 knots,
  applied by interpolation), so a new photo gets a score without re-scoring
  anything, and existing scores never drift because you added a creator. Refitting
  `F` is a deliberate, versioned act.

The trade-off, stated plainly: percentile is *relative*. Your archive always
contains 90+ items even if a stretch of it is mediocre. For a personal "best of"
browser that is the desired behaviour, but it is not an absolute quality claim.

### So also store the absolute number

One extra `REAL` column costs nothing and covers the cases percentile can't:

| Column | Meaning | Use |
|--------|---------|-----|
| `sexy_score` | 0–100 percentile | display, sort, browsing |
| `keep_prob` | calibrated P(you'd keep it) | absolute thresholds, cross-archive comparison, eval (`keep_f1`) |

Both derive from the same latent `s`. No extra inference.

### Honest note on precision

The *ordering* will be meaningful; individual ±3 points will not. The UI should not
imply otherwise — show the number but band it visually (deciles or 5s), and never
build logic on a 1-point difference.

---

## 3. Where the score comes from

The load-bearing idea: **separate measurement from ranking.** Measure once and
cache forever; learn the ranking from labels and retrain for free.

```
                      ┌───────── retrains in ms, zero VLM calls ─────────┐
                      ▼                                                  │
image ─► L1 gates ─► L2 embedding (cached) ─► L3 ranker ─► s ─► 0–100 ────┘
           │             │                       ▲
           │             └─► L4 VLM attributes ──┤ facets + explanation
           ▼                                     │
     eligible? + reasons              pairwise comparisons + favourites
```

### L1 — Deterministic gates (no VLM)

Sharpness (`cv2.Laplacian` variance), resolution floor, text/edge density for
posters, perceptual hash for near-duplicates, person presence. Emits `eligible`
plus `gate_reasons`, **storing the measurements, not just the verdicts**, so
thresholds retune post-hoc with zero re-runs.

Currently these are VLM prompt rules achieving **3/11 recall** on discard. Blur is
arithmetic; person-presence is a detector's job. This layer is cheap and is the
single highest-confidence accuracy win in the design.

**Gates do not zero the score.** They set `eligible = false` and the browse filters
on it. Keeping the score pure means a misfiring gate is recoverable without a
rescore — the lesson already learned from `has_man` silently flattening would-be
top items.

### L2 — Image embedding, computed once, cached forever

SigLIP-2 or CLIP ViT-B/32 via ONNX → one float vector per image, stored as a
SQLite BLOB. **This layer is immune to taste changes.** Embed the archive once;
every future change of mind is an L3 retrain, not a rescore. That is precisely the
property the current design lacks and the reason it has paid for six passes.

Reels reuse the existing contact-sheet frame selection; embed the selected frames
and keep the max.

### L3 — The ranker, learned from comparisons

A linear scorer `s = w·x` over the embedding, fitted by **Bradley-Terry on pairs**:

```
P(i ≻ j) = σ( w · (xᵢ − xⱼ) )
```

which is ordinary logistic regression on *difference vectors* — about 40 lines of
numpy, no sklearn. Fitting on differences is what makes it generalise: `w` scores
any image, including ones never compared.

Label sources, all cheap:

- **Pairwise comparisons.** You see two photos, pick one. ~2s per pair, and the
  protocol with the best agreement properties.
- **Favourites, free.** Every `favorite = 1` row becomes pairs against sampled
  non-favourites. Hundreds of labels you have already produced.
- **Active sampling.** Serve pairs the model finds closest to a coin flip — far
  better label efficiency than random pairs.

You never type a number. The 0–100 is derived, never labelled — which is exactly
how a 100-point scale becomes trustworthy instead of noise.

### L4 — VLM demoted to attributes, off the scoring path

Keep the VLM; stop asking it for the verdict. It emits interpretable facets
(garment class, body emphasis, framing) for faceted browsing and for explanations
like *"micro bikini · tight crop · sharp · similar to 14 of your favourites"*. A
wrong facet costs a mislabelled chip, not a mis-scored photo. The v7 signal schema
is the right shape for this — it was only the wrong thing to compute a score from.

---

## 4. Why this is more accurate

1. **The scoring function is fitted to your labels instead of approximated in
   English.** Your own T4 definition — *"body is the subject; big bust preferred;
   composition quality matters — a far-away flat bikini can be 3, a tight crop of a
   perfect body is 4"* — is not reliably expressible as a prompt rule. v6's attempt
   scored tier-4 precision **0.688**. It is very learnable from a few hundred
   comparisons, because it is a smooth function of features an embedding encodes.
2. **Embeddings capture the unnameable part** — composition, framing, body-type
   preference, photoshoot-vs-snapshot. The residue that survived six prompt
   rewrites. A learned head never has to name it.
3. **Gates become deterministic**, from 27% recall to near-perfect.
4. **Ranking becomes real.** 100 levels from a continuous latent, so "best 50"
   finally has an answer.
5. **Labels get cheaper *and* less noisy simultaneously** via pairwise comparison,
   which also attacks the unmeasured label-noise ceiling.

**Honest caveat on the evidence.** CLIP's own paper reports zero-shot roughly
matching a 4–16-shot linear probe — but on object benchmarks where the class has a
text name ("a photo of a dog"). The claim here is *not* "probes beat zero-shot in
general". It is that **this target has no text handle at all** — no prompt names
"photos Archit would keep" — so zero-shot is unavailable in principle and a learned
head is the only thing that can represent it. Expect to need more than 16 examples;
budget a few hundred.

---

## 5. Feasibility — what the research found

| Question | Finding | Consequence |
|----------|---------|-------------|
| Reuse Ollama for embeddings? | **No.** `/api/embed` does not accept images; passing base64 returns the embedding of the *text*. Open requests since 2024 (ollama#5304, #7677) | Embeddings need a second runtime |
| Avoid a new dependency? | **Maybe — spike it.** OpenCV 5 rewrote the DNN engine: ONNX operator coverage ~22% → **>80%**, with explicit transformer support. `opencv-python-headless 5.0.0.93` is already installed | If `cv2.dnn` runs the ONNX: **zero new deps**. OpenCV 4.x demonstrably failed on ViT/CLIP, so this is new capability — worth 30 minutes to test |
| Fallback | `onnxruntime` — CPU-only, no torch. Torch-free CLIP wrappers exist (`lakeraai/onnx_clip`); SigLIP ONNX exports published (`Xenova/siglip-base-patch16-224`) | One mature wheel, Windows + macOS |
| Which model | SigLIP-2 is the strongest open image-text model as of mid-2026; CLIP ViT-B/32 the smaller well-understood baseline | B/32 for the spike, SigLIP-2 as the upgrade |
| Ranker deps | Bradley-Terry = logistic regression on difference vectors, ~40 lines | **No sklearn.** numpy already present |

**Verdict:** best case zero new dependencies, worst case one — compatible with this
repo's deliberately lean `requirements.txt`.

### Cost shape

The important number is not latency, it is **how often you pay it**:

| | Current | v2 |
|--|---------|-----|
| Per-image | 7B VLM call | one embedding pass (far cheaper) |
| When taste changes | **again, whole archive** | **never** — L3 refit only |
| Paid so far | ×6 (v1…v7a) | ×1 |

Measure real figures on the archive machine; the harness already records
`median_ms`, and archive size is unknown from here (this machine has no archive).

---

## 6. Migration — nothing breaks

`glam_score` becomes a **derived view** of the new score:

```
glam_score = 0 if not eligible else bucket(sexy_score)   # 0-3, thresholds tunable
```

The Sexy chip, `glam_min` / `glam_max`, `sort=glam`, the `g0`–`g3` badges,
`reject_only`, `unscored_only` and the insights dashboard all keep working
unchanged. `glam_tier` keeps recording the VLM tier for continuity.

New columns, all additive:

```
sexy_score  REAL     -- 0-100 percentile, the new primary
keep_prob   REAL     -- calibrated absolute probability
embedding   BLOB     -- ~1 KB/image at fp16; 50k images ≈ 50 MB
gate_flags  TEXT     -- eligibility reasons
phash       TEXT     -- near-duplicate clustering
score_model TEXT     -- which ranker version produced this
```

`score_model` matters: refitting the ranker rewrites `sexy_score` for the whole
archive, which is a **database update, not a re-inference** — seconds, not hours.
That is the entire point of the architecture.

---

## 7. Phased plan — each phase gated

Reuses the harness already built (`keep_f1`, per-class precision, `--split`).

| Phase | Work | Gate to proceed |
|-------|------|-----------------|
| **0. Spike** (½ day) | Can `cv2.dnn` load a CLIP/SigLIP ONNX and produce sane embeddings? Sanity: cosine similarity ranks obvious pairs right | Works → zero deps. Fails → adopt `onnxruntime` |
| **1. Gates** (1 day) | L1 as a standalone pass; store measurements. Tune against the 120 labelled photos | Discard recall ≥ 0.90 (vs **0.27**) with **zero** true-T3/T4 wrongly gated |
| **2. Embed + probe** (2 days) | Embed the 120, fit on dev, measure on test vs v6 and v7a | **CV `keep_f1` beats v7a on the held-out half.** Losing at n=120 is not conclusive — 120 is thin for a probe; go to Phase 3 before judging |
| **3. Comparison labelling** (1 day build, then your time) | Pairwise UI, import favourites as pairs, active sampling on the boundary | 300–500 effective labels; **measure self-agreement** on a 30-pair repeat |
| **4. Fit + map to 0–100** | Bradley-Terry refit, freeze the percentile transform `F`, Platt-calibrate `keep_prob` | `keep_f1 ≥ 0.90`; pairwise agreement with held-out pairs ≥ 0.85 |
| **5. Ship behind a flag** | `SEXY_SCORE_V2=1`; write `sexy_score` + derived `glam_score`; UI shows the number, banded; "more like this" via embedding kNN | Side-by-side on real browsing beats the 0–3 |
| **6. Retire prompt tuning** | VLM → L4 attributes only; delete the tier-tuning loop | — |

Phases 1 and 2 are independently valuable if the rest stalls.

**"More like this"** deserves calling out: nearest-neighbour on the cached
embedding is free once L2 exists, needs no labels, and is probably the largest
single browsing improvement in the whole design.

---

## 8. Risks

| Risk | Severity | Mitigation |
|------|---------|------------|
| **CLIP/SigLIP may separate this domain poorly.** Both trained with NSFW filtering; discriminative power here is empirical, not assumed | **High — the one that can kill it** | Phase 2 measures it on real labels before anything is built on top. If separation is weak, feed VLM attributes into the same L3 ranker — architecture survives, only L2 changes |
| A 0–100 score implies precision the model lacks | Medium | Band the display; never branch on ±1; publish the ordering metric, not per-item confidence |
| Percentile is archive-relative | Medium | Freeze `F`; version it via `score_model`; store `keep_prob` for absolute needs |
| Cold start (<100 labels, worse than the VLM) | Medium | Flag-gated; favourites give a head start; blend the VLM prior until labels accumulate |
| Overfitting 120 labels | Medium | Cross-validated metrics only, regularisation, dev/test split already enforced |
| Reels need frame choice before embedding | Medium | Contact-sheet selection exists and is tested; reuse |
| Personal data leaving the machine | **Must stay zero** | Fully local: ONNX runs offline, embeddings and labels never leave the archive dir. Model weights are the only download |
| Bigger change than a prompt edit | Medium | Phases 1–2 small and useful alone; the gate after Phase 2 is a genuine stop |

---

## 9. Explicit non-goals

- Removing the 0–3 column or breaking any current UI (see §6)
- Reel contact-sheet frame selection
- Changing the eval harness contract — v2 is judged by the same ruler
- Scraping, storage layout, or the prompt-generation feature

---

## 10. Decisions needed

1. **Confirm the scale semantics.** Recommended: `sexy_score` = percentile vs a
   frozen snapshot ("87 = beats 87% of your archive"), with `keep_prob` stored
   alongside for absolute thresholds. The alternative is making the displayed 0–100
   the calibrated probability directly — simpler, but then "50" means "coin flip
   whether you'd keep it", which reads oddly as a *sexiness* number and makes sort
   order bunch up wherever the model is uncertain. **This is the one choice that
   changes the most downstream, so it is worth ruling on before Phase 4.**
2. **Approve Phases 0 + 1?** Half a day plus a day, no new deps unless the spike
   fails, and Phase 1 alone fixes discard gates currently at 27% recall.
3. **Phase 2 is the real go/no-go** — does an off-the-shelf embedding separate
   *your* keeps from *your* rejects? Everything after is contingent on it.
4. **Two things I could not determine from this machine:**
   - **Archive size** — no archive here, so per-pass cost and storage are
     unestimated. A file count sizes every phase.
   - **How many favourites already exist** — they are free labels. Several hundred
     would cut Phase 3's burden sharply and let Phase 2 be measured on far more
     than 120 items.
5. **Sequencing against v7a.** Recommendation: **run it anyway** — ~60 VLM calls,
   it gives v2 the baseline to beat, and if the pure ablation fixes T3 recall
   that's a cheap win worth banking while v2 is built.

---

## Appendix: sources

- [Best Multimodal Embedding Models in 2026 — Mixpeek](https://mixpeek.com/curated-lists/best-multimodal-embedding-models)
- [Multimodal Embeddings: SigLIP-2, JinaCLIP-v2, Cohere Embed-v4 — Spheron](https://www.spheron.network/blog/multimodal-embedding-models-gpu-cloud-siglip2-jinaclip-cohere/)
- [Ollama: multimodal embedding models (#5304)](https://github.com/ollama/ollama/issues/5304)
- [Ollama: enable image embeddings for vision models (#7677)](https://github.com/ollama/ollama/issues/7677)
- [Ollama Embedding API — DeepWiki](https://deepwiki.com/ollama/ollama/3.3-embedding-api)
- [onnx_clip — torch-free CLIP (Lakera AI)](https://github.com/lakeraai/onnx_clip)
- [Xenova/siglip-base-patch16-224 — ONNX export](https://huggingface.co/Xenova/siglip-base-patch16-224)
- [OpenCV 5: next-gen DNN engine, ONNX coverage 22% → 80%](https://opencv.org/opencv-5/)
- [OpenCV DNN fails on ViT models (#27603)](https://github.com/opencv/opencv/issues/27603)
- [LP++: A Surprisingly Strong Linear Probe for Few-Shot CLIP (CVPR 2024)](https://openaccess.thecvf.com/content/CVPR2024/papers/Huang_LP_A_Surprisingly_Strong_Linear_Probe_for_Few-Shot_CLIP_CVPR_2024_paper.pdf)
- [Learning From Pairwise Preferences: Bradley-Terry — Towards Data Science](https://towardsdatascience.com/learning-from-pairwise-preferences-an-introduction-to-the-bradley-terry-model/)
- [Hybrid-MST: active sampling for pairwise preference aggregation](https://arxiv.org/pdf/1810.08851)
