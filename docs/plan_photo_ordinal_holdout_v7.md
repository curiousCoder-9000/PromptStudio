# Plan: Align photo ordinal classifier with your holdout labels

**Status:** Analysis complete — **implementation deferred**.  
**Gold labels:** `c:\Users\archi\Downloads\labels-photo (1).jsonl` (120 photos, seed 20260810)  
**Baseline run:** `holdout-ordinal` = `v4-ordinal-frame-v6` vs `holdout-legacy`  
**Eval diary (completed runs):** [eval_photo_ordinal_log.md](eval_photo_ordinal_log.md)

> **Rev 2 (2026-08-09) — reviewed against the code and the v6 confusion matrix.**
> The ontology work in §1 stands. Three things changed:
>
> 1. **Priority inverted.** Re-read against the actual product surface
>    (`glam_score >= GLAM_SEXY_MIN`), **T3 under-firing is the only real bug** —
>    see the new §2.5. T0 gates and the T3↔4 split are invisible to the Sexy
>    filter. Rev 1 spent 7 of 8 new schema fields on those two.
> 2. **The rev-1 policy in §3.2 was degenerate** — it collapsed to
>    `garment_skimpy → 4`, which *is* the v6 bug it set out to fix. Rewritten as
>    an additive score in §3.2.
> 3. **A cheap ablation now runs first** (§3.0). v6's "default 2 / if unsure
>    choose 2" is a round-1 tuning artifact and the likely direct cause of
>    T3→2. One prompt edit may recover most of the loss without any schema
>    change; the 11-field rewrite is only justified if it doesn't.
>
> Also new: a dev/test split (§4.0), because round-1 already proved this
> codebase can pass on the set it was tuned on and fail on a fresh one.

---

## 1. What your labels actually mean (from photos, not from the prompt)

After comparing every true tier against model predictions **and** reading representative sheets, your scale is **product desirability**, not pure garment taxonomy.

| Tier | Your product meaning | Gold examples from this set | Count |
|------|----------------------|----------------------------|------:|
| **0** | **Discard** — no keep value | Food/cooking; **man in frame** (couple at table, TV panel with men); **event promo poster** (ASTROFEST layout + heavy text) | 11 |
| **1** | Fully modest | (only 2 — thin) | 2 |
| **2** | Normal / stylish / gym — keep-ish but not sexy filter | Pink sports bra + full leggings (midriff OK); casual fashion portraits | 41 |
| **3** | Sexy / revealing — **not** top shelf | Crop + short shorts; sheer mesh crop; tight short overalls with ass emphasis; **bikini top + denim shorts** (vanity); distant/less-showcase bikini | 43 |
| **4** | **Highest keep** — very sexy, body-first | Microbikini beach with big bust; **thong/ass-primary** bikini; panties + cleavage crop; sheer stage micro-bra + mini; white bra as top + massive side-bust | 23 |

### Your T4 definition (locked from you + confirmed on gold)

T4 is **not** “any bikini.” It is the **best bucket**:

1. **Very sexy / body is the subject** of the photo  
2. **Big bust preferred** when front/side chest is the focus  
3. **Short / minimal clothing** (micro bottoms, lingerie, bra-as-top, thong, sheer micro)  
4. **If chest is not the focus → strong ass view** still qualifies (back/thong pool shots you labelled 4)  
5. **Composition quality** matters: a far-away flat bikini on a daybed can be **3**; a tight crop of perfect body in skimpy clothes is **4**

Counterexamples that prove the current prompt is wrong:

| Photo | You | Model v6 | Why model failed |
|-------|----:|---------:|------------------|
| White bra + denim, huge side bust (tractor) | **4** | **2** | Not in “bikini/lingerie list”; treated as normal top |
| Beach micro red bikini, big bust | **4** | 4 | Garment rule luckily matches |
| Thong ass microbikini (nikkisandiego) | **4** | 4 | Ass + micro bottoms |
| Tank + pink panties, huge cleavage | **4** | 4 | Lingerie-class bottoms |
| Purple bikini top + denim shorts (makeup) | **3** | **4** | Model: “bikini=always 4”; you: sexy but not peak package |
| Mesh booty catsuit | **3** | **4** | Model: sheer→4; you: revealing fashion, not top tier |
| Crop jersey + white shorts (court) | **3** | **2** | Model default fashion; you: short + midriff sexy |
| Couple with man at table | **0** | **2** | Men rule not enforced in practice |
| ASTROFEST promo graphic | **0** | **2** | Poster rule not enforced |
| Food pan | **0** | **0** | Easy no-woman case works |

**17 label edits** vs previous export: you tightened T3→2 and promoted some 3→4 — ontology is stable enough to train against.

---

## 2. Why the code fails (root causes)

### 2.1 Ontology mismatch (main)

Current `_TIER_ANCHORS` / v6 prompt:

- T4 = **garment class** (bikini / lingerie / sheer over bare skin) — hard rule “ALWAYS 4”  
- T3 = list of reveals (midriff, cleavage, mini) but soft  
- Default “if unsure → 2” (v3 hangover) → **pred hist: tier2=79, tier3=6, tier4=32, tier0=3**

Your labels:

- T4 = **body showcase + skimpy + sex appeal**  
- T3 = sexy but not peak (includes some bikini tops, sheer fashion, short shorts)  
- T2 = gym / normal fashion even with midriff  

So the model **over-fires 4 on any bikini/sheer** and **under-fires 3** (collapses into 2).

### 2.2 Discard gates are prompt-only and weak

Men / blur / poster were added as text, but:

- Structured output only requires `has_woman`, `exposure_tier`, `confidence`  
- No `has_man` / `is_poster` / `quality_ok` fields → no **code-side force to 0**  
- Result: **8/11 true-T0 → predicted 2** (men + posters especially)

### 2.3 Glam mapping is fine; T3 is the filter border

```
T0,1 → glam 0   |  T2 → glam 1   |  T3 → glam 2 (Sexy)   |  T4 → glam 3 (top)
```

Broken T3 (27× 3→2, 10× 3→4) is why glam accuracy is only **60%** on new labels (still beats legacy **46%**, but fails top-bin share because 79× glam1).

### 2.4 Ship rule on this holdout (v6)

| | Legacy | Ordinal v6 |
|--|-------:|-----------:|
| glam_acc | 45.8% | **60.0%** |
| exact tier | — | 60.0% |
| T4 recall | — | **22/23 (96%)** |
| T3 exact | — | **6/43 (14%)** |
| T0 recall | — | **3/11 (27%)** |
| top_score_share | 58% G3 | **66% G1** FAIL |

T4 recall is already high on this set — but at **precision 22/32 = 0.69**, so the
10 false 4s are real. Expanding T4 the wrong way (more “bikini=4”) would **hurt**
(more false 4s you labelled 3).

### 2.5 What the product actually consumes (the metric that was missing)

`glam_accuracy` is 4-way exact match on the 0–3 axis. Nothing in the app asks
that question. Two surfaces read `glam_score`:

| Surface | Code | Question it asks |
|---------|------|------------------|
| Sexy filter | `handler.py` → `query_photos(glam_min=GLAM_SEXY_MIN)`, default **2** | binary: is `glam >= 2`? ⇔ **is tier ≥ 3?** |
| Glam sort | `db.py` `sort == "glam"` → `ORDER BY glam_score DESC` | ordering, so **G3 vs G2 matters here** |

Re-scoring the v6 confusion matrix at the filter boundary (tier ≥ 3):

| | v6 |
|--|---:|
| keep precision | **1.000** (38/38) |
| keep recall | **0.576** (38/66) |
| keep F1 | 0.731 |

**v6 puts nothing wrong into the Sexy filter and silently drops 42% of what you
wanted.** Splitting the 48 tier errors by whether they cross that boundary:

| Error | n | Crosses the filter? | Cost |
|-------|--:|---------------------|------|
| **T3→2** | **27** | **yes** | **27 wanted photos never reach the Sexy filter** |
| T4→2 | 1 | yes | 1 wanted photo lost |
| T3→4 | 10 | no | in the filter already; only over-ranks in glam sort |
| T0→2 | 8 | no | lands at G1, *below* the filter — already excluded |
| T1→2 | 2 | no | G0 vs G1, both below the filter — invisible |

**28 of 48 errors change what you see; 20 do not. 27 of those 28 are one cell.**

Consequences for the rev-1 design:

- **T0 gates are cosmetic here.** A poster or a couple shot predicted tier 2 →
  glam 1 → already excluded from the Sexy filter. Fixing T0 recall 3/11 → 11/11
  changes *nothing* the product shows; it only tidies glam-sort ordering and the
  `has_woman` / `sexy` columns. Worth doing — not worth a ship gate, and not
  worth 3 of 8 schema fields.
- **T3↔4 is a ranking bug, not a filter bug.** It matters only under
  `sort=glam` (and `glam_min=3`, reachable via query param). Real, second-order.
- **T2/T3 is the whole ballgame** and rev 1 gave it a single boolean
  (`revealing_daywear`) while giving the two cosmetic problems seven fields.

Legacy's numbers at this boundary need recomputing from
`photo-holdout-legacy.json`, but it can be bounded now: legacy predicts **G3=70**
alone, against only **66** true keeps in the whole set, so legacy keep precision
is **< 1.0 necessarily** — it floods the filter, where v6 starves it. That is the
honest legacy-vs-v6 trade, and `glam_accuracy` hides it completely.

**Action:** add `keep_precision` / `keep_recall` / `keep_f1` at `GLAM_SEXY_MIN`
to `evalset.Metrics` and to the report, and make **keep_f1** the headline. This
is ~15 lines in `compute_metrics` and is a prerequisite for judging v7 at all.

---

## 3. Recommended design: multi-signal schema + policy mapping

Do **not** only rewrite free-text anchors. Make the model report **detectable facts**, then map to tier in Python (policy you control, re-tunable without re-prompting every edge).

That thesis is right. Rev 1 then failed to apply it where the error mass is (§2.5)
and applied it incorrectly where it did (§3.2). Corrected below.

### 3.0 Stage A first: the one-line ablation (do this before any schema work)

The v6 prompt does not merely fail to find T3 — **it is explicitly instructed not
to.** From `outfit_classifier.py:208` and `:226`:

```text
(4) Else if she is clothed in normal or stylish fashion … → 2.
    This is the DEFAULT for Instagram fashion photos.
  - Only escalate 2→3 when a listed reveal in step (3) is clearly visible.
    If unsure between 2 and 3, choose 2.
```

Both lines were added in **v3** to fix a *round-1* problem: v2 over-fired T3 and
blew `top_score_share` to 0.72. They did their job on round-1 (top_share 0.458,
ship-rule pass) and are now the prime suspect for **all 27 T3→2 errors** on
holdout — a tiebreak tuned on one set, generalising badly to another, exactly the
failure mode §4.0 exists to stop.

The step-(3) reveal list itself already covers the gold T3 set almost perfectly:
bare midriff, deep cleavage, bare back, mini at upper thigh, torso cut-outs. The
gold T3 examples are crop + short shorts, sheer mesh crop, tight shortalls,
bikini top + denim. Those *are* listed. The model is being told to resolve them
downward.

**Stage A = `v4-ordinal-frame-v7a`, prompt-only, no schema change:**

1. Delete "This is the DEFAULT for Instagram fashion photos."
2. Replace "If unsure between 2 and 3, choose 2" with "If unsure between 2 and 3,
   choose **3**" — the boundary is now asymmetric in the right direction, because
   §2.5 shows the filter's cost is entirely recall, and T3→4 confusion is free.
3. Add short-shorts / tight ass-hugging shortalls to the step-(3) list explicitly
   (present in gold T3, absent from the reveal list).
4. Keep everything else byte-identical.

Cost: one edit, one `run --kind photo`, ~120 VLM calls. If Stage A recovers keep
recall to ≥0.85 while holding precision ≥0.90, **most of §3.1–3.2 is unnecessary**
and should not be built. Ship Stage A, then treat T0 and T3↔4 as separate,
smaller, non-blocking work.

Only if Stage A stalls does the schema rewrite earn its complexity. Rev 1 jumped
straight to an 11-field rewrite on a 7B model without testing the two-line
hypothesis first; that is a lot of risk bought for nothing.

### 3.1 Stage B structured fields (FRAME_V4_SCHEMA → v7) — only if Stage A stalls

Field budget follows error mass (§2.5), which reverses rev 1: **six fields for the
T2/T3 cut** (27 visible errors), **three** for the T3/T4 cut (10 invisible), and
the T0 gates moved off the VLM where possible.

```text
has_woman: bool
# ── T0 gates (cosmetic; cheap to ask, must not be ship-gating) ──
has_man: bool                    # any adult male clearly visible
is_poster_or_graphic: bool       # flyer / promo layout / heavy designed text
# quality_usable: DROPPED from the schema — see §3.2a, do it in OpenCV instead
# ── T2/T3 cut: the six reveals, asked as independent observations ──
midriff_bare: bool               # stomach skin between top and bottoms
cleavage_deep: bool              # plunging, not a modest V-neck
hem_upper_thigh: bool            # mini skirt/dress/short shorts at upper thigh
back_bare: bool                  # fully bare back
torso_cutouts: bool              # large cut-outs, sideboob through clothes
bodycon_tight: bool              # tight fit clearly emphasising bust/hips/ass
# ── T3/T4 cut: peak-package signals ──
garment_class: "covered"|"daywear"|"revealing_daywear"|"swim"|"underclass"
                                 # underclass = lingerie, bra-as-outer, thong, micro, sheer-over-bare
subject_scale: "distant"|"full_body"|"waist_up"|"tight_crop"
body_emphasis: "none"|"bust"|"ass"|"both"
exposure_tier: 0-4               # model’s own guess (audit only; code may override)
confidence, brief_reason
```

Changes from rev 1 and why:

- **`revealing_daywear` (1 bool) → six reveal booleans.** This is the whole point
  of the "report facts, decide in Python" thesis, and rev 1 applied it everywhere
  *except* the cut that matters. "Is this revealing?" is a judgment call the model
  already gets wrong; "is stomach skin visible?" is an observation.
- **`garment_skimpy` (bool) → `garment_class` (enum).** A boolean cannot separate
  gold-3 `bikini top + denim shorts` from gold-4 `bra-as-top + side bust`; both
  are "skimpy". The 5-way enum can, and it subsumes rev 1's dead
  `revealing_daywear`-vs-skimpy overlap.
- **`bust_prominent` + `ass_prominent` + `body_focus` → `body_emphasis` alone.**
  Rev 1 had three fields encoding one thing, then OR'd them (§3.2), which is what
  made the policy degenerate.
- **`subject_scale` is new and load-bearing.** It is the only signal that
  expresses your own stated rule — *"a far-away flat bikini on a daybed can be 3;
  a tight crop of perfect body in skimpy clothes is 4"* (§1). Rev 1 asserted that
  rule in prose and gave the policy no field to decide it with. Scale is also far
  more reliable from a 7B VLM than "is the bust large", which is subjective,
  unevenly represented in training data, and the field most likely to misfire.

### 3.2 Deterministic policy (in `_verdict_from_tier_data`)

Rev 1's policy was:

```text
elif garment_skimpy and (bust_prominent or ass_prominent or body_focus in {bust, ass, both}):
    tier = 4
```

**This is a no-op gate.** For any bikini/lingerie photo a VLM reports
`body_focus` as `bust` or `both` — a bikini photo *is* a body photo — so the
conjunction reduces to `garment_skimpy → 4`, i.e. v6's "bikini = ALWAYS 4" rule
re-expressed as Python. The next branch (`garment_skimpy and not body showcase`)
is then **unreachable**, so rev 1's own worked example — *bikini top + denim
shorts → 3* — cannot be produced by rev 1's own code. The doc papered over this
with "(prompt must teach this)", which is the soft-rule mechanism that failed in
v6. Replaced with an additive score, so no single field can dominate:

```text
# 1. discard gates (cosmetic — log the reason, do not let one bool zero a T4)
if not has_woman or has_man or is_poster_or_graphic or not quality_usable:
    tier = 0; evidence["discard_reason"] = <first gate hit>

# 2. T2/T3: any one reveal is enough. Asymmetric on purpose (§2.5: cost is recall)
reveals = midriff_bare + cleavage_deep + hem_upper_thigh + back_bare \
        + torso_cutouts + bodycon_tight          # count of True

# 3. T3/T4: peak needs TWO independent signals, one of which is framing
peak = (garment_class == "underclass")           \
     + (garment_class in {"swim", "underclass"} and body_emphasis != "none") \
     + (subject_scale in {"waist_up", "tight_crop"})

if garment_class in {"swim", "underclass"} or reveals >= 1:
    tier = 4 if peak >= 2 else 3
elif garment_class == "daywear":
    tier = 2
else:
    tier = 1
```

**Why this matches gold** (and, unlike rev 1, is actually derivable from the code):

| Case | Signals | peak | → tier |
|------|---------|-----:|-------:|
| Beach microbikini, big bust | underclass, emphasis=bust, tight_crop | 3 | **4** |
| Thong ass view | underclass, emphasis=ass, waist_up | 3 | **4** |
| Bra as top + huge side bust (tractor) | underclass, emphasis=bust, waist_up | 3 | **4** |
| Tank + panties, huge cleavage | underclass, emphasis=bust, tight_crop | 3 | **4** |
| **Bikini top + denim shorts (vanity)** | **swim**, emphasis=bust, waist_up | **2** | **4** ⚠ |
| **Distant bikini on daybed** | swim, emphasis=none, **distant** | **0** | **3** ✓ |
| Mesh booty unitard | revealing_daywear, bodycon_tight, full_body | 0 | **3** ✓ |
| Crop + short shorts (court) | daywear + midriff + hem_upper_thigh | 0 | **3** ✓ |
| Gym sports set, full leggings | daywear, midriff_bare… | — | **3** ⚠ |
| Man in shot | has_man | — | **0** ✓ |
| Event poster | is_poster | — | **0** ✓ |

Two rows are still wrong, and they are flagged rather than hidden — this is the
honest state of the design:

- **Bikini top + denim shorts → 4, gold says 3.** Needs `garment_class` split
  into top/bottom (`bottom_class == "normal"` would cancel a peak point), or it
  stays a known 1-item miss. Cheap to add; decide at implementation.
- **Gym sports bra + full leggings → 3, gold says 2** (41 true-T2 currently
  perfect — this is the regression risk Stage A also carries). `midriff_bare`
  fires on a sports bra. Mitigation: `midriff_bare` alone does **not** reach 3
  when `garment_class == "daywear"` *and* `hem_upper_thigh` is false *and*
  `bodycon_tight` is false — i.e. require **2 reveals when the only reveal is
  midriff**. Your §1 note "*Pink sports bra + full leggings (midriff OK)*" is
  exactly this case and it is 41 items of currently-correct behaviour, so it must
  be regression-tested, not hand-waved.

That second row is the real tension in the whole plan: **T3 recall and T2
precision are the same knob.** v6 chose T2 precision (41/41, T3 6/43). Stage A
and Stage B both move that knob the other way. Both must therefore report T2
precision alongside keep recall, or a "win" will just be a relabelled loss.

### 3.2a Do quality in OpenCV, not the VLM

`quality_usable` is dropped from the schema. Blur is a **deterministic 5-line
measurement** and asking a 7B VLM for it burns a field, is unverifiable, and
cannot be tuned without a re-run:

```python
gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
if cv2.Laplacian(gray, cv2.CV_64F).var() < BLUR_MIN:   # tune on the 120-set
    tier = 0
```

Rev 1 filed this under "§3.5 optional later / defer". It is cheaper *and* more
reliable than the VLM path it was deferred behind, so it moves into the first
patch. Threshold is tunable post-hoc against stored variances with **zero** VLM
re-runs — the opposite of a prompt field.

### 3.3 Prompt rewrite (still required)

Teach the VLM the **signal definitions** with your language:

- T4 prose: “highest keep — very sexy body showcase; big bust preferred when chest is shown; short/minimal clothing; if no bust focus, a clear nice ass view in skimpy bottoms also counts”  
- Explicit **T4 vs T3**: skimpy alone is not enough if the body is small in frame / casual / partial outfit with normal bottoms  
- Explicit **T3 vs T2**: short shorts + crop / deep cleavage / tight ass-hugging shortalls = 3; full-length gym / normal fashion = 2 even with midriff  
- Discard gates first (men, poster, quality) — **and** code enforces them from booleans  

Version string: `v4-ordinal-frame-v7` (or `v5-signals` if we rename).

### 3.4 Label UI anchors (evalset `TIER_ANCHORS`)

Rewrite to match the gold ontology so future labelling stays consistent:

```text
0 discard — no woman; any man; poster/flyer; blur/distortion; food/meme
1 modest — everyday covered
2 normal fashion / gym — stylish or sporty, not sexy-filter
3 sexy revealing — crop, short shorts, cleavage, tight shortalls, partial bikini, sheer fashion — not peak body package
4 top keep — very sexy body-first; skimpy clothes + (big bust and/or strong ass view)
```

### 3.5 Optional later (not in first patch)

| Idea | Why defer |
|------|-----------|
| Separate “body quality” score 0–2 | Extra label burden; signals above may be enough |
| Two-pass classify (detect → score) | Latency; try single structured pass first |
| Rescore production archive | Only after holdout ship rule passes |
| Merge T0/T1 into one label | `TIER_TO_GLAM` maps both to glam 0, so T1 is product-invisible and gold has only **n=2**. Exact-tier accuracy on T1 is noise. Not urgent, but stop treating T1 as a real class in reports |

**Removed from this table:** the Laplacian blur metric — promoted into the first
patch (§3.2a). It was deferred behind a VLM field that is strictly worse.

### 3.6 Do **not** expand the reel sheet schema (correcting a rev-1 step)

Rev 1's step 2 said "mirror discard gates on reel sheet panel path (same signals
or shared helper)". Per-panel, that would break the reel path outright:

- `CLASSIFY_REEL_SHEET_PANELS = 9`, `CLASSIFY_NUM_PREDICT = 400`
- current per-panel payload `{i, has_woman, exposure_tier}` ≈ 15 tokens → ~135
  tokens for 9 panels, plus the aggregate block — comfortably inside 400
- adding 9 signal fields per panel ≈ 50 tokens/panel → **~450 tokens for the
  panel array alone**, before the aggregate block

That truncates the JSON mid-array, which the code already has scar tissue for:
`_SHEET_UNREADABLE`, the `brief_reason` `maxLength` caps, and the
`panels_incomplete` evidence flag all exist because of exactly this failure.

**Rule: signal fields are photo-path only.** If the reel path needs discard gates,
add `has_man` / `is_poster` at the **aggregate** level only (2 fields, ~10 tokens),
share `_TIER_ANCHORS` text as today, and raise `CLASSIFY_NUM_PREDICT` deliberately
with a measured token count rather than by guess. Reels are also out of scope per
§6 — this belongs in a separate change with its own reel eval run.

---

## 4. Implementation steps (after approval)

### 4.0 Split the set first — this is not optional

Rev 1 proposed tuning the policy against the 120 holdout photos and then declaring
success on **those same 120** ("success criteria on *this* gold set"). That is the
mistake round-1 already made and already paid for, documented in this repo's own
diary:

> v3 → *"Ship rule vs legacy: glam 0.65 ≥ 0.46 and top_share 0.458 ≤ 0.483 →
> **PASS** on this set"* … then *"Holdout (different seed + stricter T0 + refined
> T4) later showed v6 does **not** generalize as well."*

Round-1 (seed 20260809) is burned as a tuning set. If round-2 (seed 20260810) is
also burned, **there is no clean set left** and every subsequent number is a
training-set number.

**Do this before any code:**

1. Deterministically split the 120 gold labels **60 dev / 60 test**, stratified by
   true tier (T3 and T4 are the classes under test; T1 n=2 goes 1/1 or all to dev).
   Fixed seed, written to disk, committed to the eval dir — not re-drawn per run.
2. **All** prompt/policy iteration reads dev only. Stage A, Stage B, threshold
   tuning, the `BLUR_MIN` sweep — dev.
3. Test is scored **once**, on the candidate you intend to ship. If you look at
   test and then tune, it is dev now and you need a new test set.
4. Needs a small harness change: `run` / `report` take `--split dev|test|all`.
   ~20 lines in `evalset.py` alongside the §2.5 metrics.

At n=60 per half, a keep-recall difference below roughly ±0.12 is not
distinguishable from noise (binomial, ~33 true keeps per half). Do not ship on a
2-point move; if dev and test disagree by more than that, draw a third sample
before believing either.

### 4.1 Steps

1. **Import gold labels**  
   `label --kind photo --import "…\labels-photo (1).jsonl"`  
   Archive previous holdout metrics as baseline notes.

2. **Harness first** (`promptstudio/evalset.py`) — before touching the classifier  
   - `keep_precision` / `keep_recall` / `keep_f1` at `GLAM_SEXY_MIN` in `Metrics`
     + `compute_metrics` + `_print_report` (§2.5)  
   - per-tier precision as well as recall in the report (v6's T4 precision 0.69
     and T2 precision 0.52 are both invisible today)  
   - `--split dev|test|all` (§4.0)  
   - Re-score the **existing** `photo-holdout-*.json` with it. No VLM calls: this
     gives real legacy-vs-v6 keep numbers and a correct starting line for free.

3. **Stage A** (§3.0) — prompt-only, `v4-ordinal-frame-v7a`, dev split only.  
   Stop here if it clears the gate below.

4. **Stage B** (§3.1–3.2a) only if Stage A stalls  
   - Expand `FRAME_V4_SCHEMA` + `CLASSIFY_FRAME_V4_PROMPT` + `_TIER_ANCHORS`  
   - Policy function `_tier_from_signals(data) -> int`  
   - Wire in `_verdict_from_tier_data`. **Record the model's own
     `exposure_tier` and the `discard_reason` in `verdict.evidence`** whenever the
     policy overrides it — otherwise a policy misfire is undebuggable without a
     full re-run, and `evidence` already flows to the sidecar via
     `_GLAM_EVIDENCE_KEYS`, so this is nearly free  
   - Laplacian `BLUR_MIN` gate (§3.2a); store the variance in `evidence` so the
     threshold can be re-tuned with zero VLM calls  
   - Bump `CLASSIFY_FRAME_V4_VERSION`  
   - **Reel path: aggregate-level gates only, or nothing** (§3.6)  
   - Update `evalset.TIER_ANCHORS` + short labels  

5. **Tests**  
   - Table-driven unit tests for `_tier_from_signals` — every row in §3.2,
     including the two rows marked ⚠  
   - **Regression test the 41 true-T2s.** They are 41/41 correct today and both
     stages push the T2/T3 knob toward T3; this is the most likely place to lose
     more than you gain  
   - Existing ordinal-path tests updated for new schema fields (defaults)  
   - A test that `has_man` alone cannot silently zero a would-be T4 without
     writing `discard_reason`

6. **Re-eval**  
   - `run --kind photo --name v7a-dev --ordinal --split dev` (then `v7b-dev`)  
   - `report … --against holdout-ordinal` for the v6 baseline  
   - Gate on **dev**, confirm once on **test**:

| Metric | v6 baseline | Target | Why |
|--------|------------:|--------|-----|
| **keep_f1** @ glam≥2 | **0.731** | **≥ 0.80** — headline | The only metric the Sexy filter expresses |
| keep_recall | 0.576 | **≥ 0.85** | 27 lost photos is the actual bug |
| keep_precision | 1.000 | **≥ 0.90** | Some give-back is the *point*; a fall below 0.90 means junk in the filter |
| T2 precision | 0.519 (41/79) | not worse than **0.85** on true-T2 recall | Guards the 41/41; see §3.2 ⚠ |
| T4 recall | 0.957 (22/23) | ≥ 0.90 | Hold what works |
| T4 precision | **0.688** (22/32) | ≥ 0.75 | Rev 1 said "watch" with no number; this is the number |
| glam_accuracy | 0.600 | ≥ 0.60 | Demoted to a secondary sanity check, not a gate |
| T0 recall | 0.273 (3/11) | report only — **not a gate** | Invisible to the filter (§2.5); do not block a ship on it |
| top_score_share | 0.658 | ≤ 0.60 | Keep as a distribution smell test. Note a *perfect* classifier scores **0.358** here, so this is a weak signal, not a quality measure |

   **Revised ship rule:** `keep_f1` improves over v6 **and** keep_precision ≥ 0.90
   **and** true-T2 recall ≥ 0.85 — confirmed on the held-out half.
   The old rule (*glam ≥ legacy and top_share drops*) stays in the report for
   continuity but should no longer decide this: it is satisfiable by a classifier
   that is worse at the only question the product asks.

7. **Visual compare**  
   Open `view` HTML; spot-check remaining T3↔4 and T0 misses with you.

8. **Only then** restart server (already `CLASSIFY_PHOTO_ORDINAL=1`) so production uses v7; optional pilot rescore one creator.

### 4.2 Label noise is an unmeasured ceiling

The gold file is a **re-export after 17 label edits**, and the T2/T3 boundary moved
during those edits — the same boundary the whole plan turns on. Nobody has measured
how repeatable that boundary is, so "exact accuracy 0.60" has no known ceiling to
be judged against; if self-agreement on T2↔T3 is ~0.80, then v6 at 0.60 is much
closer to done than it looks, and chasing 0.85 is chasing noise.

**Cheap check (~15 min):** re-label 30 photos sampled from the T2/T3/T4 region,
blind to the previous labels, and report agreement. Do this **before** Stage B. If
self-agreement on the T2/T3 cut is below ~0.85, the honest move is to sharpen the
label definition (§3.4) first — no prompt or policy can beat the label noise floor.

---

## 5. Risks & trade-offs

| Risk | Mitigation |
|------|------------|
| **Losing the 41/41 true-T2s** — the top risk, and both stages push that knob | Regression-test it; gate on true-T2 recall ≥ 0.85 (§4.1). This is the cost side of T3 recall and they cannot be optimised independently |
| Richer schema → more VLM mistakes on a **7B** model | Observations (`midriff_bare`) not judgments (`revealing_daywear`); enum over stacked booleans; quality moved to OpenCV. Rev 1's "booleans are easier than one 0–4 jump" is untested — Stage A (§3.0) is the control arm that tells us whether the schema was needed at all |
| Tuning and judging on the same 120 photos | dev/test split (§4.0). Round-1 already demonstrated this exact failure |
| Label noise on the T2/T3 cut sets an unknown ceiling | Blind re-label of 30 (§4.2) before Stage B |
| A discard boolean silently zeroing a true T4 | `discard_reason` in `evidence`; gates are report-only, never ship-gating (§2.5) |
| T4 over-fire if skimpy alone maps to 4 | This is the **rev-1 policy bug**, not a hypothetical — fixed by requiring `peak >= 2` incl. a framing signal (§3.2) |
| Reel sheet JSON truncation | Do not add per-panel fields at all: 9 panels × ~50 tok > `CLASSIFY_NUM_PREDICT=400` (§3.6) |
| Eval set small (n=23 T4, n=11 T0; n=30 T3 per split half) | Treat as regression suite; ±0.12 keep-recall is noise at n=60 (§4.0); don't overfit one photo |

---

## 6. Explicit non-goals (this pass)

- Changing glam 0–3 DB mapping or Sexy filter thresholds  
- Re-labelling the whole archive  
- Turning ordinal off  
- Multi-model ensemble  

---

## 7. Decision needed from you

Rev 2 asks for a **smaller** first commitment than rev 1 did. Ordered by cost:

1. **Harness (§4.1 step 2) — approve unconditionally.** `keep_f1` + per-tier
   precision + `--split`. No VLM calls, re-scores the runs already on disk, and
   without it neither stage can be judged. This should happen whatever else you
   decide.
2. **Split the 120 (§4.0) — approve unconditionally.** Free, and it is the only
   thing standing between you and a third round of "passed on the set it was
   tuned on".
3. **Stage A (§3.0) — one prompt edit, ~120 VLM calls on the dev half.** Deletes
   the two v3 tiebreak lines that plausibly cause all 27 T3→2 errors.
4. **Stage B (§3.1–3.2a) — the schema rewrite. Do not approve yet.** Decide after
   Stage A's numbers and the label-noise check. It may be unnecessary; if it is
   necessary it will be better targeted for having seen Stage A fail.

The inferred T4 rule is unchanged and still needs your confirmation, because
`subject_scale` in §3.1 exists to encode it:

> **Bikini / lingerie is T4 only when the body is clearly showcased (bust and/or
> ass) and clothing is truly minimal; bikini top with normal shorts or a distant
> full-body bikini can stay T3.**

That rule is what your labels show (purple vanity bikini top → 3; beach micro + big bust → 4). Confirm or correct before implementation.

**One new question rev 1 did not surface.** §2.5 shows v6 has *perfect* Sexy-filter
precision and loses 42% of your keeps, while legacy over-fills it. Both stages
trade precision for recall. Which do you actually want?

- **(a) Recall-first** — better to scroll past a few normal-fashion photos than
  never see 27 you wanted. Targets in §4.1 assume this.
- **(b) Precision-first** — the Sexy filter must stay clean; accept that it shows
  roughly 58% of your keeps.

The §4.1 gates are written for **(a)**. If you want **(b)**, v6 is already close to
optimal and most of this plan should be dropped in favour of the cosmetic T0/T3↔4
tidy-up only.
