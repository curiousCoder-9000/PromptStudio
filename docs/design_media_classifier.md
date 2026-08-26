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

Here, **only the measurement is stored**. `media_verdicts.tier` holds the 0–4
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

`v4-ordinal-frame-v8` (photos) / `v4-reel-sheet-v8` (reels). v8 redefines the
`2↔3` cut — v7a treated any listed reveal (crop, cleavage, bodycon) as T3 and
tied upward, which put covering cocktail dresses and award/OOTD shots in the
keep-hot bucket. T0/T1/T4 garment rules are unchanged.

| Tier | Label | Meaning |
|------|-------|---------|
| 0 | Unusable | no woman as main subject · any man in frame · poster/flyer/graphic · unusable quality (blur, pixelation, distortion) |
| 1 | Fully modest | opaque everyday clothes, skin only face/hands |
| 2 | Normal fashion | cute/street/event wear, including tight covering dresses; not a sexual keep |
| 3 | Revealing daywear | sexy daywear a horny viewer would keep: curvy/voluptuous figure AND body as the subject AND a real reveal (or painted-on bodycon on a voluptuous figure) |
| 4 | Swim / lingerie | bikini, swimwear, lingerie, sheer over bare skin, near-nude |

Keep-policy is applied in code after the model returns, because the VLM
contradicts itself (``brief_reason: bikini set`` with ``exposure_tier: 2``):

- `is_graphic` → 0 (a bikini on an event flyer is still a flyer)
- `undress_class` or a bikini/lingerie/swimsuit reason → 4
- T2 + curvy + `body_focus` + (`bare_midriff` or a crop/midriff reason) → 3
- T3 + `figure` not in `{curvy, voluptuous}` or `body_focus=false` → 2

Missing fields fail open. T4 is never capped by the figure gate.

**Default cut: `tier ≤ 1` is a reject.**

### The known risk, stated plainly

The `1↔2` boundary has never been measured. The only holdout numbers are for
`2↔3`, and there recall was **0.576** — meaning a boundary this classifier is
asked to judge got four in ten wrong in the direction of dropping keepers.
Cutting at `≤1` will therefore reject some things worth keeping.

v8 flips the `2↔3` error budget the other way on purpose: covering bodycon,
award/product shots, and non-curvy figures were over-admitted as T3. The
prompt now ties down when unsure, and `figure` / `body_focus` cap a T3 to 2
in code. Expect T3 precision to rise and T3 recall to fall; check
`top_tier_share` after the first rescore.

Three guards, none of which override the default:

1. **The cut is a config knob, not a stored answer.** `CLASSIFY_REJECT_MAX_TIER=0`
   narrows rejects to the unambiguous quality gate with no re-classify.
2. **The review UI splits the pile.** `Unusable (T0)` and `Modest (T1)` are
   separate chips with separate select-all, so a cautious pass can delete only
   the tier nobody argues about.
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
tier lands on a decision boundary (1, 2, 3), when confidence is under 0.5, or
when a high tier came from the final shot. The confirm supersedes the sheet
*except* when it loses a subject the sheet clearly saw.

**The sheet is kept on disk.** `_classify/<creator>/<stem>.sheet.jpg`, served by
`GET /api/classify/sheet`, shown in the triage panel. This is the single most
important affordance in the feature: a wrong verdict you can see the input for
is a bug report; a wrong verdict you cannot is a reason to stop trusting the
tool. `_classify` was already in `EXCLUDED_FOLDERS`, so sheets stay out of the
gallery, the creator list and every rebuild for free.

---

## 4. Storage

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
