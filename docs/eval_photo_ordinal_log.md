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
