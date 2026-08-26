# Product review — where the value is, and what to build

| Field | Value |
|-------|--------|
| **Date** | 2026-08-09 |
| **Scope** | Whole product — value chain, feature allocation, signals, metrics |
| **Method** | Read of all 19 docs, `promptstudio/` (17k LOC), `app.js`, git history; LOC allocation by package |
| **Companion** | [`review_backend_architecture.md`](review_backend_architecture.md) — engineering review (durability, observability, storage). Deliberately not repeated here. |
| **Descoped** | Onboarding / time-to-first-value (wizard, doctor, demo mode, settings UI) — considered and **cut by owner**. See §7. |
| **Verdict** | An exceptionally well-engineered acquisition pipeline attached to an unfinished studio. Stop building ingest; close the loop. |
| **Status** | Accepted — all themes below to be implemented. |

---

## 1. The central finding

Positioning and investment disagree.

- **README says:** "AI Vision Prompt Studio."
- **The code says:** a very good multi-source scraper with a prompt feature attached.

| Package | Python LOC | Role in the value chain |
|---|---:|---|
| `scraping/` | 5,861 | acquire |
| `storage/` | 2,025 | hold |
| `server/` | 1,661 | serve |
| `prompts/` | 1,274 | **create value** |
| `comfy/` | 612 | **deliver value** |

The stated pipeline is **scrape → organize → analyze → search → generate**, and effort decays
monotonically along it. Ingest carries **3.1×** the investment of everything that produces user
value. Roadmap phases 1, 3, 3b and 3c are all ingest. Three scrape sources exist; one hardcoded
ComfyUI graph exists.

Every finding below is a consequence of that allocation.

---

## 2. Findings

### P1 — The value loop does not close 🔴

The user's job is *"turn images I admire into images I can generate."* The product walks them to
the doorstep and stops.

| Gap | Evidence |
|---|---|
| No outputs view | `GET /api/generations?path=` answers only "generations for **this one photo**". There is no cross-photo browse, no filter, no comparison. `_generations/` is a write-only directory. |
| Generation is one-at-a-time | Batch **analyze** exists — `BatchPromptManager` (`prompts/batch.py:15`), cooperative cancel, progress chip, resume-after-refresh. Batch **generate** does not. Nobody scrapes 4,400 images to render one at a time through a lightbox. |
| Outputs are terminal | `ComfyJobManager._save_outputs` (`comfy/client.py:565`) records prompt, files, timestamp — **no rating, no keep flag**. The user's verdict on the only artefact the product exists to produce is never captured. |
| One workflow, hardcoded | `modelToimage_pro.api.json` plus a legacy txt2img fallback. No LoRA, no alternate checkpoint graph, no video. `roadmap.md` files custom-workflow import under *Future (optional)* — it is not optional, it is the ceiling. |

Neither `roadmap.md` nor `review_backend_architecture.md` F1–F6 contains a single item on the
generate half. **Half the product is missing and nothing on any plan addresses it.**

### P2 — Taste is the only proprietary asset, and it is discarded 🔴

Instagram content is not defensible. Ollama is not. gallery-dl is not. The user's accumulated
judgment is — and the product treats it as exhaust.

| Signal | Already on disk | Currently read by |
|---|---|---|
| `favorites.json` — explicit positives | ✅ | one filter chip |
| `_trash/` — explicit negatives, 30-day retention | ✅ | nothing |
| `parameters.manual_edit: true` — set at `server/handler.py:358` | ✅ | **nothing** |
| Prompt `history`, ≤3 snapshots (`prompts/cache.py:104`) | ✅ | a restore button |
| Generations per source photo (`generations_index.json`) | ✅ | nothing |

`manual_edit` is a **free, already-instrumented prompt-quality metric**. Edit rate is the fraction
of prompts the user refused to accept; regenerate count is dissatisfaction; generation retries are
the same signal on the Comfy path.

`review_backend_architecture.md` F3 proposes *building* a golden-set harness for prompt quality
from scratch. A continuous, zero-cost quality signal already exists and nothing reads it. Wire that
up first — it is days of work and it tells you whether anything else is working.

### P3 — A shipped feature with zero value, and no instrument said so 🔴

85% of scored videos land on glam 3. The Sexy filter (`glam_score >= 2`) admits ~92%. A filter that
admits 92% is a filter the user has stopped trusting.

This is a **product process failure**, not a model bug. Three prompt versions shipped
(`v2-skin-exposure`, `v3-reel-frames`, now `v4-reel-sheet`) with the delta unmeasured. The system
had no way to notice that its flagship filter had become a no-op.

`design_reel_classifier_v2.md` §P4 finally adds a distribution guard (fail if any single value
exceeds 60%). **Make that a platform rule, not a one-off** — see B4.

### P4 — The organizational model is an ingest artifact 🟠

The only unit of organization is `<creator>/` — a folder created by the scraper.

| Users think in | Product offers | Data already present |
|---|---|---|
| Posts | 5 unrelated tiles per carousel | `post_id` in sidecars; `group_by_post_id()` at `storage/metadata.py:100` — **unused by the gallery** |
| Themes / moods across creators | nothing | — |
| Outfit, setting, framing | one 0–3 `glam_score` int | the vision call already sees all of it |

Search is `LOWER(prompt_search) LIKE '%q%'` over **LLM-generated text**. "red bikini" returns
nothing unless the prompt writer happened to use those words. Users will read that as broken
search, not as a limitation of substring matching.

### P5 — Derived state is irreplaceable and lives in one place 🟠

Prompts, glam scores, favorites and styles represent hundreds of GPU-hours. They exist on one disk,
in files written with bare `open(path, "w")` (see the backend review's S1). There is no export.

---

## 3. Constraints

Stated once, because they govern how much polish is worth buying.

- **ToS/legal caps distribution.** Scraping at scale plus session-cookie handling means this can be
  published as code for personal use, but cannot become a hosted product or take money. Build for a
  technical single user.
- **No auth, CORS `*`.** Correct for localhost-only. It also means "run it on a home server" is off
  the table until that changes — decide that explicitly rather than by default.
- **Single-user, single-machine, local GPU.** A *strength* — privacy, no inference cost — and the
  positioning should lean on it.

---

## 4. Feature proposals

Each item lists the outcome, the size, and the files it touches, so it can be picked up directly.
Sizes: **S** ≈ days · **M** ≈ 1–2 weeks · **L** ≈ 3+ weeks.

### Theme A — Close the loop

The highest-value work in the repo. Converts an archive into a studio.

**Specified in [`design_generation_loop.md`](design_generation_loop.md).** That document adds an
**A0** prerequisite not identified here: the default generation path never records the seed it
used (`comfy/client.py:218-220` resolves it inside the builder and discards it), so the existing
corpus is unreproducible and A1's provenance panel would ship reading `null`. A0 also moves the
generations index out of JSON, which A1 and A2 cannot be built on.

#### A1 — Outputs gallery ⭐ (M)

A real view over `_generations/`: filter by creator, date, checkpoint, rating; full provenance per
output (source image, prompt bundle, seed, denoise, steps, workflow, Mode E on/off).

- **Why:** the product currently cannot show a user what they have made.
- **Touches:** `comfy/client.py` (`GenerationsIndex` at `:258` — needs a global list/query, not just
  `list_for(rel_path)`), new `GET /api/generations/list` in `server/handler.py`, new view in
  `app.js` + `index.html`.
- **Note:** `_save_outputs` already truncates prompts to 500/300 chars. Widen or store a reference
  to the prompt-cache entry instead, so provenance is exact.

#### A2 — Batch generate queue ⭐ (M)

Select N photos → queue → progress chip → contact sheet of results.

- **Why:** matches how a 4,400-image archive is actually used.
- **Touches:** clone the `BatchPromptManager` shape (`prompts/batch.py`) for Comfy; `POST
  /api/comfy/batch` + `/status` + `/cancel`; `renderJobChip('generate', …)` in `app.js` — the chip
  stack, cancel semantics and resume-after-refresh are already built and tested.
- **Constraint:** respects the existing single-flight rule on the Comfy resource; do not run
  alongside a `ComfyJobManager` one-shot.

#### A3 — Rate outputs ⭐ (S)

Keep / discard / ⭐ on each generation, one keyboard shortcut.

- **Why:** the only signal that says whether the prompt engine works end-to-end. Feeds A1 ranking
  and B2's preference model.
- **Touches:** one field in the `generations_index.json` record (`comfy/client.py:595`), `PUT
  /api/generation/rate`, lightbox + A1 view.
- **Do this before A1** — it is small, and A1 should ship with ranking already available.

#### A4 — Custom workflow import (M)

Upload a `workflow_api.json`, map prompt / negative / seed / image slots in the UI, save as a named
workflow.

- **Why:** removes the hard ceiling. Unlocks LoRAs, alternate checkpoints, video graphs.
- **Touches:** `comfy/workflows/` becomes a user directory; `build_pro_workflow`
  (`comfy/client.py:200`) generalises to a slot-map; new picker in the lightbox.
- **Promote out of `roadmap.md` "Future (optional)".**

### Theme B — Make taste a first-class asset

#### B1 — Quality dashboard from existing signals ⭐ (S)

Compute and display: prompt **edit rate** (`manual_edit`), **regenerate rate** (history depth),
**score distribution per `prompt_version`**, and generations-per-photo.

- **Why:** turns three unmeasurable things measurable with **no new instrumentation**. Would have
  caught P3 on day one.
- **Touches:** read-only aggregate over `prompts_cache.json` + `photos.glam_score` +
  `generations_index.json`; `GET /api/insights`; a small panel in the UI.
- **Build this first.** Everything else is easier to justify once it exists.

#### B2 — Learned preference score → "For You" sort (L)

SigLIP-2 embedding cached per image + a logistic head trained on favorites / trash / B3 labels →
calibrated `P(keep)`.

- **Why:** "glam I want to keep" is personal taste, not an objective image property. A zero-shot VLM
  cannot learn it; a linear probe on a few hundred labels can. ~100× cheaper than a VLM call, so
  full-archive rescoring drops from hours to minutes — which makes *retraining as taste drifts*
  effectively free. See `research_glam_classifier.md` §3.5 for the technical case.
- **Touches:** embedding BLOB column in `photos` (follow the additive migration pattern at
  `storage/db.py:75`), sqlite-vec in the existing `archive.db`, new sort mode in `query_photos`.
- **The VLM stays** for two jobs it is good at: the human-readable `brief_reason`, and low-margin
  cases near the boundary (~10–20% of items instead of 100%).
- **Depends on B3 labels.** Shares infrastructure with C1 and C3 — build the embedding job once.

#### B3 — Rapid labeling mode (M)

Keyboard-driven contact sheet; ~20 minutes for 300 items. Labels land in a `labels` table in
`archive.db` (`rel_path`, `label`, `labelled_at`) — joinable, and out of git.

- **Why:** both research docs name this as the blocking gate (`design_reel_classifier_v2.md` P0,
  `research_glam_classifier.md` Stage 0). Ship it as a **feature** — a 20-minute session that
  permanently improves every ranking in the app — rather than a test fixture, and it will actually
  get used.
- **Seed from signals that already exist:** `photos.favorite` as positives, `_trash/` as negatives.
- **Touches:** new `labels` table, `GET/POST /api/labels`, a dedicated labeling view.

#### B4 — Distribution guard as a platform rule (S) ✅ shipped (Phase 14)

Every score or filter surfaces its pass rate in the UI, and CI fails if any single bucket exceeds
60% of outputs.

- **Why:** prevents P3 from recurring on the next classifier version.
- **Touches:** assertion in the eval harness; a pass-rate badge next to each filter chip.

**As built.** Both halves landed together with [E5a](backlog_engineering.md#e5a--nothing-fails-when-the-distribution-saturates);
three deviations from the sketch above, each deliberate:

- **The badge is archive-wide, not view-scoped.** `ArchiveIndex.verdict_facet_counts()` answers
  every chip in one grouped query, off the same `_verdict_predicate` that `/api/photos?verdict=`
  filters with, and rides on `/api/stats` (already fetched at init and at the end of a classify
  run) rather than a round trip per chip. Scoping it to the selected creator would have made a
  number that moves as you click — uncomparable against a fixed 60% line. Both the review chips
  and the browse dropdown carry it, because review mode is opt-in and a guard you have to go
  looking for is the insights panel again. Cost, measured per AGENTS.md rule 13: **12.6 ms**
  at 20k photos / 16k verdicts, taking `/api/stats` from ~15 ms to ~28 ms — six
  `SUM(CASE …)` over one join scan, where a COUNT per chip would pay that scan six times.
- **"CI fails" is really "the local run fails."** CI has no archive, so the gate lives in
  `tests/test_distribution_guard.py`, reads the developer's real archive read-only, and skips
  below a minimum N. This is the same call E5b made about benchmarks: a check that can only be
  meaningful where the data is.
- **It is genuinely a platform rule now.** The same `insights.saturation_report()` gates
  generation ratings against `keep_rate`'s own denominator (rated outputs only). The next score
  or filter wires in by handing it a bucket→count mapping and a minimum N.

The one thing this does *not* do is make the tier distribution itself better — it makes a flat one
impossible to ship unnoticed, which is what P3 was actually about.

### Theme C — Navigate at scale

#### C1 — Semantic search (M)

Text→image retrieval over B2's embeddings, via sqlite-vec in the existing `archive.db`.

- **Why:** search finds what is *in* the photo, not what the generated prompt happened to say.
  Today's `LIKE '%q%'` over `prompt_search` cannot use an index and cannot find un-described content.
- **Touches:** `storage/db.py` `query_photos`, `/api/photos?search=` gains a `mode=semantic`.
- **Rides on B2.** No new dependency beyond what B2 already adds.

#### C2 — Post grouping in the gallery (S)

Collapse carousels into one tile with a slide count; expand in the lightbox.

- **Why:** pure UI win over data that already exists — `post_id` in every sidecar,
  `group_by_post_id()` at `storage/metadata.py:100`, currently unused by the gallery.
- **Touches:** `query_photos` grouping option, grid renderer in `app.js`.

#### C3 — Near-dup collapse (M)

pHash column first (cheap, catches exact and near-exact reposts), embedding kNN second (catches
re-crops and cross-creator duplicates).

- **Why:** reposts, re-crops and carousel siblings clutter the grid. Doubles as a **cost win** —
  classify and analyze one representative per group instead of five.
- **Touches:** `phash` column in `photos`, a grouping pass, dedupe UI in the gallery.
- **Supersedes** `scripts/deduplicate.py`, which is exact-match only.

#### C4 — Collections / saved views (M)

Cross-creator boards and saved filter sets.

- **Why:** the first organizational unit that is not a scraper artifact.
- **Touches:** `collections` + `collection_items` tables, `/api/collections`, sidebar section.

#### C5 — Faceted attributes (M) — **removed**

Setting (studio / beach / street), outfit type, shot framing, pose — as columns and filter chips.

- **Why (as proposed):** rides **free** on a vision call already being made. Better browsing *and*
  better Comfy conditioning. Replaces the single collapsed `glam_score` int as the browsing axis.
- **What shipped:** four dropdowns over freeform first-phrases from `structured_vision`. Only
  setting was canonicalized; outfit / pose / lighting were unique-ish strings. Comfy never read
  them. Removed 2026-08-26 as unused chrome — vision fields themselves stay on the prompt bundle.

### Theme E — Don't lose the irreplaceable

#### E1 — Export / import derived state ⭐ (S)

`promptstudio export --derived` → one portable file containing prompts, glam scores, favorites,
styles, labels, generation index. Plus an import.

- **Why:** hundreds of GPU-hours, one disk, files written with bare `open(path, "w")`. Also useful
  when a rescore invalidates a prompt version and the old one is wanted back.
- **Touches:** new thin script in `scripts/`, logic in `promptstudio/storage/`.
- **Do this early.** It gets more valuable and no cheaper as the archive grows.

#### E2 — Activity view over the run journal (M)

Product surface on top of the backend review's F2 (append-only JSONL per job kind): what did last
night's sync actually do, which account did it stop at, what was the backoff pattern.

- **Why:** "why did the following sync stop at account 12?" is currently unanswerable.
- **Touches:** journal writer alongside logging (backend review S3), `GET /api/activity`, a view in
  `app.js`.
- **Depends on** S3 (logging) landing first.

---

## 5. Metrics

Not one user-outcome metric appears in the 19 existing docs — every roadmap gate is a system
property. Starter set; **all four are computable from data already on disk or from Theme A work.**

| Metric | Definition | Source | Reads on |
|---|---|---|---|
| **Prompt acceptance rate** | 1 − (prompts with `manual_edit` or a regenerate in history) | prompt cache | prompt-engine quality |
| **Filter pass rate** | share of the archive admitted by each active filter | `archive.db` | saturation, the day it happens |
| **Generate → keep rate** | rated-keep ÷ generated | needs A3 | whether the loop produces wanted output |
| **Cost per kept output** | vision calls + Comfy jobs ÷ kept generations | B1 + A3 | whether quality work is paying off |

B1 delivers the first two immediately. A3 unlocks the second two.

---

## 6. Sequence

Tracked in [`roadmap.md`](roadmap.md) as **Phase 13** (Stage 1), **Phase 14** (Stage 2) and
**Phase 15** (Stage 3), with the interleaved S-items placed there too. B4 is introduced in Phase 14
and is standing policy from that point on.

**Stage 1 — Instrument, then close the loop**

1. **B1** quality dashboard — days, and it tells you whether anything else is working.
2. Backend review **S1 + S2 + S3** (atomic writes, error boundary, logging). Cheap, protects
   irreplaceable data, makes everything after it debuggable. Ordering agreed with that document.
3. **E1** export — before the archive gets any larger.
4. **A3** rate outputs → **A1** outputs gallery.

**Stage 2 — Make the studio usable**

5. **A2** batch generate queue.
6. **A4** custom workflow import.
7. **C2** post grouping — near-free over existing data.

**Stage 3 — The durable moat**

8. **B3** labeling mode → **B2** preference model → **C1** semantic search → **C3** near-dup
   collapse. One embedding job, four features: build the infrastructure once and mine it repeatedly.
9. **C4** collections. (**C5** faceted attributes shipped then removed — unused chrome.)
10. **E2** activity view, once logging exists.

**Continuous**

- **B4** distribution guards on every new scoring or filtering feature. Standing policy since
  Phase 14: hand the new distribution to `insights.saturation_report()` and add a case to
  `tests/test_distribution_guard.py`. Both are three lines; the rule already exists.
- Backend review **S7** (router refactor) incrementally, inside feature PRs — never as a standalone
  project. `app.js` at 5,074 lines needs the same treatment on the same terms.

---

## 7. Explicitly not building

Recorded so the decisions are not relitigated.

| Not building | Why |
|---|---|
| **Onboarding investment** — first-run wizard, doctor, demo mode, settings UI for the 89 env vars | Considered and **cut by owner**. Time-to-first-value is genuinely hours and the empty state is a bare `display:flex`, but the ToS constraint (§3) caps this at a personal tool for a technical user, so the return does not justify the build. Revisit only if the distribution intent changes. |
| **A fourth scrape source** | Three is enough. Every hour here widens the P1 gap. |
| **More classifier prompt tuning before the eval set exists** | Iterating an unmeasured prompt is exactly what produced 85% saturation. `design_reel_classifier_v2.md` says the same; hold the line. |
| **`handler.py` / `app.js` refactors as standalone projects** | Real debt, user-invisible. Incremental, inside feature PRs. |
| **Auth / multi-user / hosting** | The ToS constraint makes it a dead end. |
| **A general-purpose vector DB** | sqlite-vec in the existing `archive.db` — no new process, no new deploy story. |

---

## 8. Summary

The scraper is production-grade: idempotent post identity, tombstones, anti-ban pacing, cooperative
cancel, a persistent multi-day queue, 289 tests and CI. That work is done and it is good.

The half that justifies the scraper's existence has one hardcoded workflow, no output gallery, no
batch mode, and no feedback signal. Meanwhile the product's only defensible asset — the user's own
taste, already recorded in favorites, trash, prompt edits and regenerates — is written to disk and
never read.

Close the loop, then let taste become the thing that makes it worth using.
