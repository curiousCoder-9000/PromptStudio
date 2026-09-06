# Design: per-creator keep/reject classification

| Field | Value |
|-------|--------|
| **Status** | Implemented |
| **Date** | 2026-08-09 |
| **Supersedes** | the `glam_score` subsystem removed in `1cc0f44` |
| **Problem** | The creator sidebar lost its "classify this folder, then let me delete the failures" loop when the glam scoring subsystem was deleted |

---

## 1. Why this exists, and why it is not a revert

`1cc0f44` deleted `glam_score` and everything on it. The reasons were measured
and they were right:

| | v6 on the round-2 holdout |
|--|---:|
| distinct predicted values used | **4** |
| photos in the single largest bucket | **79 of 120** (66%) |
| tier 2 precision | 0.519 |
| tier 3 recall | 0.140 |

A 0–3 scalar with two thirds of the archive on one value cannot rank. That was
the finding, and nothing here disputes it.

But the removal took two different things out at once. **Ranking** — "show me
my best 50" — genuinely needs the learned-preference work sketched in
`design_sexy_score_v2.md`. **Triage** — "which of these 400 files are men,
posters, blurs, and plain sweaters" — is a coarse eligibility question that an
ordinal VLM answers well, and it is the question the sidebar button was
actually being used for.

This restores triage only.

### The one structural change

The old pipeline stored the *answer*. `glam_score` was a policy decision baked
into a column, so every change of mind about where the line sat meant a
full-archive rescore, and six prompt revisions produced six mutually
incomparable archives.

Here, **only the measurement is stored**. `media_verdicts.tier` holds the 0–2
exposure tier; keep vs reject is derived at query time:

```sql
CASE WHEN manual IS NOT NULL THEN manual
     WHEN tier < 0            THEN 'error'
     WHEN tier <= :cut        THEN 'reject'
     ELSE 'keep' END
```

`:cut` is `CLASSIFY_REJECT_MAX_TIER`. Moving it re-thresholds the whole archive
on the next query — zero vision calls. That single property is what makes the
recall risk below survivable.

---

## 2. The ontology

`v4-ordinal-frame-v9` (photos) / `v4-reel-sheet-v9` (reels). v9 collapses the
0–4 scale: T0 absorbs old unusable + modest + covering fashion; T1 is revealing
*or* tight daywear; T2 is old T4 (swim / lingerie). Tightness counts as a keep.

| Tier | Label | Meaning |
|------|-------|---------|
| 0 | Reject | no woman · any man in frame · poster/flyer/graphic · unusable quality · covering clothes that are not tight |
| 1 | Revealing / tight | crop, plunging cleavage, short shorts, tight bodycon/mini, midriff — street/party clothes, not swim |
| 2 | Swim / lingerie | bikini, swimwear, lingerie, sheer over bare skin, near-nude |

Keep-policy is applied in code after the model returns, because the VLM
contradicts itself (``brief_reason: bikini set`` with ``exposure_tier: 1``):

- `is_graphic` → 0 (a bikini on an event flyer is still a flyer)
- `undress_class` or a bikini/lingerie/swimsuit reason → 2
- claimed T2 without undress evidence → 1
- T0 + crop/midriff/tight reason → 1

**Default cut: `tier ≤ 0` is a reject.**

### The known risk, stated plainly

The `0↔1` boundary (covering vs tight/revealing) is the new keep line and has
not been measured on a labelled holdout. v8's measured error was the old
`2↔3` cut. Check `top_tier_share` after the first v9 rescore — three buckets
are easier to saturate than five.

Three guards, none of which override the default:

1. **The cut is a config knob, not a stored answer.** Raising
   `CLASSIFY_REJECT_MAX_TIER` to 1 would treat revealing/tight as reject with
   no re-classify.
2. **The review UI splits keep.** Revealing (T1) and Swim (T2) are separate
   chips with separate select-all.
3. **Deletes are soft.** The existing `_trash` flow with Undo, and favourites are
   excluded from "select non-favourites" — the one signal that is unambiguously
   the user's own is never swept up by a machine verdict.

---

## 3. Pipeline

```
photo → one vision call on the still            → tier
reel  → 9-panel chronological contact sheet
        → one vision call
        → max-over-panels (computed here, not the model's rollup)
        → optional full-res confirm of the peak frame
                                                  → tier
```

**Why contact sheets.** Instagram creators routinely open a reel in everyday
clothes and reveal the outfit in the final seconds. Single-frame sampling
structurally cannot see that; a chronological grid spanning the whole clip can,
in one vision call.

**Why max-over-panels is recomputed.** The sheet prompt also asks for a
`reel_exposure` rollup, and it is deliberately not trusted. The panel array is
the auditable evidence; a single rollup number is not. When they disagree,
`rollup_disagreement` lands in the sidecar so prompt drift is visible.

**Why the confirm pass.** Panels are ~256px wide — enough for "how much skin",
not for sheer vs opaque. The peak frame is re-read at full resolution when the
tier lands on T1 (the keep/reject and T1/T2 cut), when confidence is under 0.5,
or when a keep came from the final shot. The confirm supersedes the sheet
*except* when it loses a subject the sheet clearly saw.

**The sheet is kept on disk.** `_classify/<creator>/<stem>.sheet.jpg`, served by
`GET /api/classify/sheet`, shown in the triage panel. This is the single most
important affordance in the feature: a wrong verdict you can see the input for
is a bug report; a wrong verdict you cannot is a reason to stop trusting the
tool. `_classify` was already in `EXCLUDED_FOLDERS`, so sheets stay out of the
gallery, the creator list and every rebuild for free.

---

## 4. Storage

Human gold labels live in `corrected_tier` on the same row and never overwrite
`tier`. Filters and derived keep/reject read `COALESCE(corrected_tier, tier)`;
insights and the distribution guard stay on the model number. See
[`design_tier_correction.md`](design_tier_correction.md).

`media_verdicts` is its own table, not columns on `photos` — the same reasoning
as `phashes`: written by a separate background pass, absent until it runs, and
it would otherwise widen the row every gallery query reads. It also keeps a
clean distance from the orphaned `glam_*` columns `1cc0f44` left on existing DBs.

`creator` is denormalised so the sidebar counters are one `GROUP BY` rather than
a join back on every render.

### Lifecycle with delete

| Event | Verdict row |
|-------|-------------|
| soft delete (`_trash`) | **kept** |
| Undo / restore | reattaches automatically |
| permanent delete | dropped |
| purge from trash | dropped (`TrashStore.purge`, the single chokepoint) |

Keeping it through a soft delete is deliberate. Trashing 40 rejects and hitting
Undo must give back 40 rejects — not 40 unclassified files needing the whole
vision pass again. The orphan is invisible meanwhile, because every verdict
query joins outward from `photos`.

---

## 5. Distribution guard

Every run records a tier histogram, `top_tier_share` and `error_rate` in
`_journal/classify.jsonl`, and `/api/insights` reports the same over the whole
archive.

This exists because the previous classifier shipped at 85% on a single value
and **nothing was reading the distribution**. `top_tier_share` above ~0.6 means
the classifier is barely discriminating, whatever the prompt claims. It is free,
it needs no labelled set, and it is the one number to check after the first real
run on a new creator.

---

## 6. UI

| Surface | Role |
|---------|------|
| Sidebar **Classify & review** | Stacked keep/reject/to-do meter, then `Classify N unclassified` · `Review N rejects` · `Re-score outdated (N)` · `Cancel`. Zero-count actions are hidden, not disabled — a permanent "Review 0" is noise, and its absence is the signal. |
| **Job chip** | Same shape as batch analyze: live `12/40 · keep 8 · reject 4`, cancellable. |
| **Review mode** | Replaces the view controls with an accented strip: `Rejects / Unusable / Modest / Keeps / Not classified` chips with counts, select-non-favourites, delete, Done. Cards get a verdict pill and rejects are desaturated, so the grid scans without reading. Sorted harshest-first. |
| **Triage lightbox** | Tier chip, model reason, contact sheet, Keep/Reject/Auto, and `K`/`R`/`X`/`←`/`→` keys. A 43-item pile is unusable by mouse. |
| **Normal gallery** | The same verdict pill, quiet — appears on hover. |

Two deliberate refusals, both from `docs/context.md:117`:

- Review mode is **not** in `PREF_FIELDS`. A refresh must never land you in a
  delete-oriented mode.
- A finished classify does **not** auto-open review. The completion toast offers
  a `Review N rejects` button instead. The old flow jumped you straight into a
  destructive mode unasked.

---

## 7. Alternatives rejected

| Option | Why not |
|--------|---------|
| Ship the 0–100 learned score from `design_sexy_score_v2.md` | Solves ranking, not triage, and needs an embedding store plus a labelled pair set before it returns anything. The `manual` column here *produces* those pairs, so this is a step toward it. |
| Route the vision call to a frontier API model | Better tiers, but adds an API key, per-image cost and a network dependency this repo has never had. The knob to revisit is one function (`_ollama_vision_json`). |
| Store the keep/reject string | Exactly the mistake being corrected. Re-thresholding would mean rewriting every row. |
| Cut at `tier ≤ 0` by default | Safer on recall, but leaves the modest pile — the bulk of what the user actually wants gone — untouched. The split chips make `≤1` recoverable. |
| Columns on `photos` instead of a table | Widens every gallery read for data that is absent until a background pass runs, and collides awkwardly with the orphaned `glam_*` columns. |
| Re-render the sidebar each poll for the live pill | Refetches creator style every 3s for the whole run. Patches the one pill instead. |
