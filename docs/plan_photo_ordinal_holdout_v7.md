# Plan: Align photo ordinal classifier with your holdout labels (esp. T4)

**Status:** Analysis complete — awaiting approval before code changes.  
**Gold labels:** `c:\Users\archi\Downloads\labels-photo (1).jsonl` (120 photos, seed 20260810)  
**Baseline run:** `holdout-ordinal` = `v4-ordinal-frame-v6` vs `holdout-legacy`

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

T4 is already almost solved by chance on this set; **T0 + T3** are the real product bugs. Expanding T4 the wrong way (more “bikini=4”) would **hurt** you (more false 4s you labelled 3).

---

## 3. Recommended design: multi-signal schema + policy mapping

Do **not** only rewrite free-text anchors. Make the model report **detectable facts**, then map to tier in Python (policy you control, re-tunable without re-prompting every edge).

### 3.1 New structured fields (FRAME_V4_SCHEMA → v7)

```text
has_woman: bool
has_man: bool                    # any adult male clearly visible
is_poster_or_graphic: bool       # flyer / promo layout / heavy designed text
quality_usable: bool             # false if heavy blur / distortion / unreadable
garment_skimpy: bool             # micro bottoms, lingerie, bra-as-outer, thong, sheer micro
body_focus: "none"|"bust"|"ass"|"both"   # what the composition showcases
bust_prominent: bool             # large/clear bust emphasis (your “big boobs preferred”)
ass_prominent: bool              # clear ass-primary angle
revealing_daywear: bool          # crop+midriff, deep cleavage, mini, sheer fashion, short-shorts sexy
exposure_tier: 0-4               # model’s own guess (audit only; code may override)
confidence, brief_reason
```

### 3.2 Deterministic policy (in `_verdict_from_tier_data`)

```text
if not quality_usable or is_poster_or_graphic or has_man or not has_woman:
    tier = 0
elif garment_skimpy and (bust_prominent or ass_prominent or body_focus in {bust, ass, both}):
    tier = 4   # your peak keep
elif revealing_daywear or (garment_skimpy and not body showcase):
    tier = 3   # sexy but not peak (bikini top + denim, distant bikini, mesh catsuit)
elif clothed normal / gym:
    tier = 2
else:
    tier = 1
```

**Why this matches gold:**

| Case | Signals → tier |
|------|----------------|
| Beach microbikini big bust | skimpy + bust → **4** |
| Thong ass view | skimpy + ass → **4** |
| Bra as top + huge side bust | skimpy (bra-outer) + bust → **4** |
| Bikini top + denim shorts vanity | skimpy top but bottoms not micro / weaker full package → revealing_daywear → **3** (prompt must teach this) |
| Mesh booty unitard | sheer fashion, not undress-class micro → **3** |
| Crop + short shorts | revealing_daywear → **3** |
| Gym sports set full leggings | normal → **2** |
| Man in shot | has_man → **0** |
| Event poster | is_poster → **0** |

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
| Classic CV blur metric (Laplacian) | Good backup for quality_usable; add if VLM still soft on blur |
| Rescore production archive | Only after holdout ship rule passes |

---

## 4. Implementation steps (after approval)

1. **Import gold labels**  
   `label --kind photo --import "…\labels-photo (1).jsonl"`  
   Archive previous holdout metrics as baseline notes.

2. **Code** (`promptstudio/scraping/outfit_classifier.py`)  
   - Expand `FRAME_V4_SCHEMA` + `CLASSIFY_FRAME_V4_PROMPT` + `_TIER_ANCHORS`  
   - Policy function `_tier_from_signals(data) -> int`  
   - Wire in `_verdict_from_tier_data` (override model tier when signals disagree)  
   - Bump `CLASSIFY_FRAME_V4_VERSION`  
   - Mirror discard gates on reel sheet panel path (same signals or shared helper)  
   - Update `evalset.TIER_ANCHORS` + short labels  

3. **Tests**  
   - Unit tests for `_tier_from_signals` table (the rows in §3.2)  
   - Existing ordinal path tests updated for new schema fields (defaults)  
   - Force `ordinal=False` legacy test already fixed  

4. **Re-eval (no full dual run required first)**  
   - `run --kind photo --name holdout-v7 --ordinal`  
   - Report vs `holdout-legacy` and vs `holdout-ordinal` (v6)  
   - Success criteria on **this** gold set:

| Metric | Target |
|--------|--------|
| glam_accuracy | ≥ legacy (0.46) and ideally ≥ v6 (0.60) |
| T4 recall | ≥ 0.90 (keep 22/23) **and** T4 precision watch (don’t flood false 4s) |
| T3 exact or within±0 on glam | T3→glam2 rate much better than 6/43 |
| T0 recall | ≥ 0.70 (men + posters) |
| top_score_share | ≤ 0.60 |
| Ship rule | glam ≥ legacy **and** top_share drops vs legacy |

5. **Visual compare**  
   Open `view` HTML; spot-check remaining T3↔4 and T0 misses with you.

6. **Only then** restart server (already `CLASSIFY_PHOTO_ORDINAL=1`) so production uses v7; optional pilot rescore one creator.

---

## 5. Risks & trade-offs

| Risk | Mitigation |
|------|------------|
| Richer schema → more VLM mistakes | Booleans are easier than one 0–4 jump; code policy is source of truth |
| T4 under-fire if “body quality” too strict | Prefer recall on gold T4; loosen bust/ass thresholds if recall &lt; 90% |
| T4 over-fire if skimpy alone maps to 4 | Require body_focus/prominent; distant bikini → revealing only |
| Reel sheet prompt length | Share `_TIER_ANCHORS` + short signal list; panels may only get has_man/poster/quality/tier first |
| Eval set small (n=23 T4, n=11 T0) | Treat as regression suite; don’t overfit one photo |

---

## 6. Explicit non-goals (this pass)

- Changing glam 0–3 DB mapping or Sexy filter thresholds  
- Re-labelling the whole archive  
- Turning ordinal off  
- Multi-model ensemble  

---

## 7. Decision needed from you

**Approve this plan** to implement signal schema + policy + v7 prompt + re-eval on `labels-photo (1).jsonl`.

One clarification only if you disagree with the inferred rule:

> **Bikini / lingerie is T4 only when the body is clearly showcased (bust and/or ass) and clothing is truly minimal; bikini top with normal shorts or a distant full-body bikini can stay T3.**

That rule is what your labels show (purple vanity bikini top → 3; beach micro + big bust → 4). Confirm or correct before implementation.
