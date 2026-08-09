# Photo ordinal classifier — eval progress log

Append-only diary of photo glam/ordinal evaluation. Newest entries at the top.

**Machine artifacts** (not in git): `<PROMPTSTUDIO_ARCHIVE>/_eval/`  
(`labels-*.jsonl`, `results/photo-*.json`, `compare-*.html`, `sheets/`)

**Related:** [plan_photo_ordinal_holdout_v7.md](plan_photo_ordinal_holdout_v7.md) · [design_reel_classifier_v2.md](design_reel_classifier_v2.md) · `scripts/eval_reel_classifier.py`

**Ship rule (photos):** flip/keep `CLASSIFY_PHOTO_ORDINAL=1` only if ordinal **glam_accuracy ≥ legacy** **and** **top_score_share drops** vs legacy on the same labels.

**Model used for all runs below unless noted:** `qwen2.5vl:7b` via Ollama.

---

## How to append

After every `sample` / `label --import` / `run` / prompt bump, add a section with:

1. Context (seed, label file, tier histogram, prompt version, commit)  
2. Metrics table  
3. Confusion / per-tier highlights  
4. Decision + next action  
5. Artifact paths  

Do **not** rewrite old sections; correct mistakes with a short “Errata” note under the original entry.

---

## 2026-08-09 — Harness: keep@filter metrics + dev/test split · Stage A prompt (v7a)

### Context

- **code:** `promptstudio/evalset.py`, `scripts/eval_reel_classifier.py`,
  `promptstudio/scraping/outfit_classifier.py`
- **decision taken:** **recall-first** (see plan §7). v6 held keep precision 1.000
  at recall 0.576; scrolling past a few normal-fashion photos beats never seeing
  28 wanted ones. Targets set accordingly: recall ≥ 0.85 with precision as a
  **floor** of 0.90, not a maximum.
- **tests:** 616 pass. **No eval run yet** — this machine has no archive, no
  labels and no Ollama; both must run on the Windows box.

### Harness

- `keep_precision` / `keep_recall` / `keep_f1` at `GLAM_SEXY_MIN`, now the first
  three lines of the report, `keep_f1` flagged HEADLINE. Defined for both
  vocabularies.
- Per-class precision **and** recall table. Suppressed when the baseline ran on a
  different axis.
- `split` subcommand; `--split dev|test|all` on `run` / `report`. Stratified,
  seeded, persisted on the label, idempotent; an import cannot move an item
  between halves.
- `TARGETS` += `keep_f1` ≥ 0.80, `keep_recall` ≥ 0.85, `keep_precision` ≥ 0.90.
  `top_score_share` retained but annotated weak (perfect run = 0.358 here).
- Arithmetic verified against the v6 matrix below — reproduces 0.600 / 0.925 /
  0.600 / 0.658 exactly, and is pinned by a test so this diary and the code
  cannot drift.

### Stage A — `v4-ordinal-frame-v6` → `v4-ordinal-frame-v7a`

Prompt-only. Undoes v3's downward 2/3 tiebreak, which was tuned for a *round-1*
`top_score_share` problem and cost 27 of 43 true tier-3s on round-2:

1. dropped "This is the DEFAULT for Instagram fashion photos"
2. "if unsure between 2 and 3, choose **2**" → "choose **3**"; "only escalate
   when" → "escalate whenever"
3. short shorts / hot pants / tight shortalls added to the step-(3) reveal list
4. `_TIER_ANCHORS` untouched, so the reel contact-sheet vocabulary is unchanged

**Pure ablation — no carve-outs.** A gym guard ("sports bra + full-length leggings
= 2") was added during implementation and then removed on the user's instruction
before any run. Correct call: bundling the mitigation with the flip would make the
flip's own effect unattributable, and the regression it guards against is so far
only predicted, not observed. The exact v7b line is parked in plan §3.0 and gets
added **only if** true-tier-2 recall actually drops.

**Blast radius:** `CLASSIFY_FRAME_V4_PROMPT` also serves the reel confirm cascade
and sheet-fallback frame, so reels shift slightly on those two paths. Accepted;
reels need their own run before any reel claim.

### Decision

- Harness and Stage A are committed but **unmeasured**. No claim is made about
  v7a's quality until it runs.
- **Next, on the archive machine:**
  1. `label --kind photo --import "…\labels-photo (1).jsonl"`
  2. `split --kind photo` → expect ~59 dev / 61 test
  3. `report --kind photo --name holdout-ordinal --against holdout-legacy` —
     re-scores the JSONs already on disk with the new metrics, **zero VLM calls**,
     and gives the real legacy keep numbers that §2.5 could only bound
  4. `run --kind photo --name v7a-dev --ordinal --split dev` (~60 calls)
  5. `report --kind photo --name v7a-dev --against holdout-ordinal --split dev`
  6. Append the result here. **Watch true-tier-2 recall** — it is the cost side of
     the same knob, the most likely way v7a loses more than it gains, and now the
     single number that decides whether v7b (the gym guard) gets built at all.
     v6 baseline is T2 recall 1.000 (41/41) at precision 0.519.

### Artifacts

- No new eval artifacts (nothing has been run)

---

## 2026-08-09 — Re-score of v6 at the product threshold (no new model run)

### Context

- **kind:** photo · **labels:** `labels-photo (1).jsonl` (T0=11, T1=2, T2=41, T3=43, T4=23)
- **code:** no change. Derived arithmetically from the v6 confusion matrix in the
  entry below — **no VLM calls.**
- **why:** review of [plan_photo_ordinal_holdout_v7.md](plan_photo_ordinal_holdout_v7.md)
  found the plan had no metric for the surface the app actually reads.
- **validation:** re-deriving `exact`, `within±1`, `glam_acc` and `top_share` from
  that matrix reproduces the reported 0.600 / 0.925 / 0.600 / 0.658 exactly, so
  the matrix and everything below are consistent.

### The Sexy filter is a binary decision at tier ≥ 3

`handler.py` passes `glam_min=GLAM_SEXY_MIN` (default **2**) to `query_photos`.
`glam >= 2` ⇔ `tier >= 3`. `glam_accuracy` never measures this.

| metric @ glam≥2 | v6 |
|-----------------|---:|
| keep precision | **1.000** (38/38) |
| keep recall | **0.576** (38/66) |
| keep F1 | **0.731** |

**v6 puts nothing wrong in the filter and drops 42% of the wanted photos.**

Per-tier precision (also not currently reported):

| tier | precision | recall |
|-----:|----------:|-------:|
| 0 | 3/3 = 1.000 | 3/11 = 0.273 |
| 2 | 41/79 = **0.519** | 41/41 = 1.000 |
| 3 | 6/6 = 1.000 | 6/43 = **0.140** |
| 4 | 22/32 = **0.688** | 22/23 = 0.957 |

### Error mass split by whether it crosses the filter boundary

| error | n | crosses? |
|-------|--:|----------|
| **T3→2** | **27** | **yes — 27 wanted photos never shown** |
| T4→2 | 1 | yes |
| T3→4 | 10 | no — already in the filter, only over-ranks in `sort=glam` |
| T0→2 | 8 | no — lands at G1, already below the filter |
| T1→2 | 2 | no |

**28 of 48 errors change what the product shows; 27 of those 28 are one cell.**

### Decision

- T0 gates and the T3↔4 split are **cosmetic** for the Sexy filter; they affect
  only `sort=glam` ordering. Demoted from ship gates in the plan.
- **T3 under-firing is the only product bug.** Prime suspect is not missing schema
  but two v3-era prompt lines (`"This is the DEFAULT"`, `"If unsure between 2 and
  3, choose 2"` — `outfit_classifier.py:208,226`) that were tuned to fix a
  *round-1* `top_score_share` problem.
- Legacy's keep numbers still need computing from `photo-holdout-legacy.json`, but
  are bounded: legacy predicts **G3=70** vs only **66** true keeps in the set, so
  legacy keep precision is **< 1.0 necessarily**. Legacy floods the filter; v6
  starves it. `glam_accuracy` hides both failure modes.
- Note `top_score_share` ≤ 0.60 is a weak gate here: a **perfect** classifier on
  this label distribution scores **0.358**.
- **Next:** add `keep_precision`/`keep_recall`/`keep_f1` + per-tier precision +
  `--split dev|test` to `evalset.py`, re-score the existing result JSONs, then run
  the prompt-only Stage A ablation on the dev half. Plan rev 2 §3.0/§4.0/§4.1.

### Artifacts

- Derived from `_eval/results/photo-holdout-ordinal.json` (no new files)
- Plan rev 2: `docs/plan_photo_ordinal_holdout_v7.md`

---

## 2026-08-09 — Holdout analysis vs gold labels `labels-photo (1).jsonl` (no new model run)

### Context

- **kind:** photo  
- **sample:** seed `20260810`, n=120 (disjoint from round‑1 seed `20260809`)  
- **labels:** `c:\Users\archi\Downloads\labels-photo (1).jsonl` (user re-export after ontology tightening)  
- **distribution:** T0=11, T1=2, T2=41, T3=43, T4=23  
- **code:** predictions reused from existing `holdout-legacy` / `holdout-ordinal` JSONs (prompt **v6** already scored; metrics re-scored against this gold file)  
- **what changed:** analysis + docs only; **v7 implementation deferred**

### Metrics (recomputed)

| name | vocab / version | glam_acc | exact tier | within±1 | top_share | notes |
|------|-----------------|---------:|-----------:|---------:|----------:|-------|
| holdout-legacy | v2-skin-exposure | 0.458 | — | — | 0.583 (G3=70) | boolean path |
| holdout-ordinal | **v4-ordinal-frame-v6** | **0.600** | 0.600 | 0.925 | **0.658 (G1=79)** | vs gold |

Δ glam vs legacy: **+0.142**. Ship rule: glam **pass**, top_share **fail** (pile on G1 = predicted tier 2).

### Per-tier (ordinal v6 vs gold)

| true | n | exact | under | over | pred hist |
|-----:|--:|------:|------:|-----:|-----------|
| 0 | 11 | 3 | 0 | 8 | 0→0:3, 0→2:8 |
| 1 | 2 | 0 | 0 | 2 | →2 |
| 2 | 41 | **41** | 0 | 0 | all →2 |
| 3 | 43 | **6** | 27 | 10 | →2:27, →3:6, →4:10 |
| 4 | 23 | **22** | 1 | 0 | →4:22, →2:1 |

### Ontology findings (from photo review)

Labeller scale is **product desirability**, not pure garment class:

| Tier | Meaning |
|------|---------|
| **0** | Discard: no woman; **any man**; poster/flyer; blur/distortion; food/meme |
| **2** | Normal fashion / gym (midriff in full leggings OK) |
| **3** | Sexy revealing but **not** top shelf (crop+shorts, sheer fashion, bikini top + normal bottoms, distant bikini) |
| **4** | **Peak keep:** very sexy body-first; skimpy clothes + (**big bust preferred** and/or **strong ass view**) |

Counterexamples: white bra + huge side bust = gold **4** / model **2**; purple bikini top + denim = gold **3** / model **4**; couple with man = gold **0** / model **2**; ASTROFEST promo = gold **0** / model **2**.

### Decision

- Documented in [plan_photo_ordinal_holdout_v7.md](plan_photo_ordinal_holdout_v7.md): multi-signal schema + code policy for T0 gates and T4 body showcase.  
- **v7 not implemented yet** (deferred).  
- **Next (later):** implement v7; append a new log entry with holdout-v7 metrics.

### Artifacts

- Results: `_eval/results/photo-holdout-legacy.json`, `photo-holdout-ordinal.json`  
- Compare: `_eval/compare-photo-holdout-ordinal-vs-holdout-legacy.html`  
- Plan commit: `aad614b`  
- Gold export: `Downloads/labels-photo (1).jsonl`  
- Round‑1 labels archived: `_eval/labels-photo-round1.jsonl`

---

## 2026-08-09 — Holdout sample + first label import + v6 A/B

### Context

- **sample:** `py scripts/eval_reel_classifier.py sample --kind photo --count 120 --seed 20260810 --open`  
- Round‑1 labels moved to `labels-photo-round1.jsonl`; fresh `labels-photo.jsonl`  
- **Production:** `CLASSIFY_PHOTO_ORDINAL=1` set in `.env`; server restarted  
- Label anchors updated during labelling for: men→0, blur/distortion→0, poster→0  
- Prompt versions during this work: v4 (men), v5 (blur), **v6 (poster)** — final holdout score used **v6**  
- First export: `Downloads/labels.jsonl` → T0=12, T1=2, T2=33, T3=54, T4=19  
- Later gold: `labels-photo (1).jsonl` (see entry above)

### Runs (vs first holdout export `labels.jsonl`)

| name | version | glam_acc | exact | within±1 | top_share | ship vs legacy |
|------|---------|---------:|------:|---------:|----------:|----------------|
| holdout-legacy | v2-skin-exposure | 0.425 | — | — | 0.583 | baseline |
| holdout-ordinal | v4-ordinal-frame-v6 | 0.475 | 0.475 | 0.908 | 0.658 | glam ↑; top_share fail |

Confusion (ordinal, first labels): 3→2 **35**, 0→2 **9**, 4→4 **17/19**, 2→2 **32/33**.

### Decision

- Ordinal still beats legacy on glam but **fails distribution** (defaults to tier 2).  
- Expanded T0 text in prompt not enough without structured `has_man` / poster flags.  
- User refined labels → gold file; see analysis entry.

### Artifacts

- `_eval/label-photo.html`  
- `_eval/results/photo-holdout-*.json`

---

## 2026-08-09 — Round‑1 final A/B after label edits (ordinal v3)

### Context

- **sample:** seed default `20260809`, n=120 (first photo eval set)  
- Labels edited in compare UI / export; final round‑1 archive:  
  **T0=1, T1=8, T2=33, T3=61, T4=17** (`_eval/labels-photo-round1.jsonl`)  
- Prompt: **`v4-ordinal-frame-v3`** — restore T4 + reduce dumping normal fashion into T3  
- Commits: eval harness `81e7af8` / `dfe18e4`; ordinal pipeline `9399ee0`; v3 tune `371510e`

### Runs (vs round‑1 final labels)

| name | version | glam_acc | exact | within±1 | top_share | glam hist |
|------|---------|---------:|------:|---------:|----------:|-----------|
| legacy | v2-skin-exposure | 0.458 | — | — | 0.483 (G3=58) | 0:1, 1:27, 2:34, 3:58 |
| ordinal (v1) | v4-ordinal-frame | 0.617 | 0.617 | 0.958 | **0.808** FAIL | almost no T4 (17 true T4 → all pred 3) |
| ordinal-v2 | v4-ordinal-frame-v2 | **0.700** | 0.700 | 0.958 | 0.717 FAIL | T4 16/17; still glam pile G2=86 |
| **ordinal-v3** | **v4-ordinal-frame-v3** | **0.650** | 0.650 | **0.967** | **0.458** PASS | 0:2, 1:31, 2:55, 3:32 |

### Per-tier highlights (v3)

- T4: **16/17** exact  
- T3: 40 exact, 7 under→2, 14 over→4  
- T2: 20 exact, 12 over→3  
- Ship rule vs legacy: glam 0.65 ≥ 0.46 **and** top_share 0.458 ≤ 0.483 → **PASS** on this set  

### Decision

- Treat v3 as shippable **on round‑1**; enable production ordinal later.  
- Holdout (different seed + stricter T0 + refined T4) later showed v6 does **not** generalize as well — see holdout entries above.

### Artifacts

- `_eval/results/photo-legacy.json`  
- `_eval/results/photo-ordinal.json` (v1)  
- `_eval/results/photo-ordinal-v2.json`  
- `_eval/results/photo-ordinal-v3.json`  
- `_eval/compare-photo-ordinal-v3-vs-legacy.html` (and v2/v1 compares)

---

## 2026-08-09 — Round‑1 ordinal v1 / v2 iteration notes

### What was done

1. Built photo eval path (`--kind photo`), label HTML, `run --legacy` / `--ordinal`, compare report.  
2. **v1 (`v4-ordinal-frame`):** never emitted T4; piled on T3/G2 (top_share ~0.81).  
3. **v2:** hard “bikini/lingerie = 4”; T4 recall fixed (~16/17) but normal fashion still over-fired T3 (top_share ~0.72).  
4. **v3:** decision order 4 before 3; default clothed fashion = 2; “if unsure 2 vs 3 → 2”. Trade accuracy for better score spread → ship rule pass on round‑1.

### Decision

Continue A/B until glam ≥ legacy and top_share ≤ 0.60 (and preferably lower than legacy’s pile).

---

## Template (copy for next entry)

```markdown
## YYYY-MM-DD — <title>

### Context
- **kind:** photo
- **sample:** seed …, n=…
- **labels:** path + histogram T0=… T1=… T2=… T3=… T4=…
- **prompt / code:** CLASSIFY_FRAME_V4_VERSION=…, commit …
- **what changed:** …

### Runs
| name | version | glam_acc | exact | within±1 | top_share | T0 rec | T3 exact | T4 rec | ship |
|------|---------|---------:|------:|---------:|----------:|-------:|---------:|-------:|------|

### Confusion / notes
- …

### Decision
- …

### Artifacts
- …
```

---

## Index of result files (archive)

| results file | prompt_versions (meta) | sample |
|--------------|------------------------|--------|
| `photo-legacy.json` | v2-skin-exposure | round‑1 seed 20260809 |
| `photo-ordinal.json` | v4-ordinal-frame | round‑1 |
| `photo-ordinal-v2.json` | v4-ordinal-frame-v2 | round‑1 |
| `photo-ordinal-v3.json` | v4-ordinal-frame-v3 | round‑1 |
| `photo-holdout-legacy.json` | v2-skin-exposure | holdout seed 20260810 |
| `photo-holdout-ordinal.json` | v4-ordinal-frame-v6 | holdout seed 20260810 |
