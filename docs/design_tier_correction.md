# Design: human tier correction (gold labels)

| Field | Value |
|-------|--------|
| **Status** | Implemented |
| **Date** | 2026-08-29 |
| **Depends on** | [`design_media_classifier.md`](design_media_classifier.md) |
| **Problem** | Keep/Reject cannot move a photo between T2 and T3, so the Fashion / Revealing filters stay wrong, and there is no gold label for later training |

---

## 1. Why this exists

The classifier stores a 0–4 **exposure measurement**. Keep vs reject is derived
at query time against `CLASSIFY_REJECT_MAX_TIER` (default 1). That design is
right and stays.

What the review UI actually lets you do is pin **policy**: `manual = keep|reject`.
T2 and T3 are both keep at the default cut, so Keep on a T2 is a no-op for every
filter that matters. The measured error is the 2↔3 boundary
([`design_media_classifier.md`](design_media_classifier.md) §2).

Training later needs both numbers: *what the model said* and *what the human
said*. Overwriting `tier` would destroy the first. Reusing `labels` (B3 taste,
±1) would mix two ontologies.

This is a **measurement correction**, not another keep/reject pin.

## 2. Storage

```sql
corrected_tier INTEGER,   -- 0–4, NULL = no human label
corrected_at   TEXT       -- UTC ISO, NULL iff corrected_tier is NULL
```

`set_verdict` (the classify job) never writes these columns. Reclassify
overwrites `tier` and leaves gold alone, same as `manual`.

Effective tier, used by filters, sort, sidebar counts, and derived keep/reject:

```sql
COALESCE(v.corrected_tier, v.tier)
```

Insights / B4 / `tier_histogram()` stay on raw `tier`. Gold labels must not
make a saturated prompt look healthy.

`?verdict=disagreement` is `corrected_tier IS NOT NULL AND corrected_tier != tier`.
Its pass-rate denominator is **corrections**, never unclassified.

Setting a gold label clears `manual`. Clearing gold leaves `manual` alone.
Unclassified files are refused. Failed classify rows (`tier = -1`) may take a
label.

## 3. API

`POST /api/classify/verdict` accepts `verdict` and/or `tier`. See [`api.md`](api.md).

The grid payload adds `corrected_tier`. `verdict.tier` remains the model
measurement. Clients display `COALESCE(corrected_tier, tier)` and still must
not re-derive keep/reject.

## 4. UI

A five-way stepper in `#triageTierRow` (lightbox), shown whenever the open
photo is classified — including outside review mode. Keep/Reject stay
review-only. Keyboard `0`–`4`. The card pill shows the effective face plus
`fa-pen`. No five buttons on the 200px tile.

Browse dropdown: `disagreement` ("Corrected (disagree)").

## 5. Training, later

```sql
SELECT rel_path, tier AS model_tier, corrected_tier AS gold,
       prompt_version, classified_at, corrected_at
  FROM media_verdicts
 WHERE corrected_tier IS NOT NULL;
```

Not built here: export, confusion matrix, prompt-eval. Gold surviving
`set_verdict` is what makes a later rescore of this set a comparison rather
than a reconstruction.
