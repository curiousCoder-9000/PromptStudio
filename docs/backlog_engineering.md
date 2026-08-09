# Engineering backlog — E1–E5

| Field | Value |
|-------|--------|
| **Date** | 2026-08-09 |
| **Source** | [`review_ui_product.md`](review_ui_product.md) §3 |
| **Relationship to S1–S8** | [`review_backend_architecture.md`](review_backend_architecture.md) owns the Python side (durability, storage, leases, logging). These are the items it does not cover — frontend runtime, the UI-side monolith, and gaps in the verification net. |
| **Status** | E1 done (Stage 1). E5b done (Stage 2). E2, E3, E4, E5a Todo. |

---

## E1 — Poller visibility ✅ done

**Was.** Six independent `setInterval` pollers — health 30s, comfy 2.5s, scrape 2.5s,
sync 2.5s, batch 4s, classify 3s — with no `visibilitychange` listener. A backgrounded tab
polled forever. Nothing they watch can change *visibly* while hidden, and every job lives
server-side, so the state is still correct on return.

**Now.** `PAUSABLE_POLLERS` in `app.js`: hidden clears every live interval and records which
were running; visible resumes only those. Every poller is self-arming — the polled function
re-creates its own interval while there is work and clears it when there is not — so resume
is one immediate call each, which doubles as the refresh you want the instant a tab returns.

**What the test caught.** `ensureHealthPolling()` only re-armed the interval; it did not
fetch. Resuming would have shown a 30-second-stale Ollama badge, defeating the point. The
resume thunk now calls `fetchHealth()` explicitly. Covered by
`tests/ui/test_insights_and_pollers.js`.

**Still open (small).** None of the six backs off on error. A dead server produces a failed
request every 2.5s forever. Worth an exponential backoff on consecutive failures.

---

## E2 — `app.js` is now the bigger monolith (M, continuous)

**The numbers.** `app.js` is 5,600+ lines against `handler.py`'s 1,760. Both reviews
prescribe S7's treatment — decompose incrementally inside feature PRs, never as a standalone
project — and that is right for the handler, which is a route table waiting to happen.

**Why `app.js` is different.** The handler's problem is shape (a long if-chain); `app.js`'s
problem is **ownership**. One `state` object with ~60 keys, mutated by six independent
pollers, a dozen fetch paths, and every event handler. There is no place that says who owns
`state.scrapeStatus` or when `state.photos` may be replaced versus appended. That is not an
aesthetic complaint — it is why "does this refetch the gallery?" needs a browser test to
answer.

**Concrete first step, no rewrite.** Group `state` by owner with comments and give each
group a single mutator, starting with the two that already caused bugs:
- `photos` / `photoOffset` / `photoTotal` / `photoHasMore` / `photosLoading` / `photosRequest`
  — the paging invariant (offset commits only on response) is currently upheld by
  convention in `fetchPhotos`.
- the six `*PollTimer` keys — now partly formalised by `PAUSABLE_POLLERS` (E1). Extend that
  registry to own start/stop entirely, instead of `if (!state.xPollTimer)` at six sites.

**Do not** attempt ES modules until there is a reason beyond tidiness — the app ships as
three static files with no build step, and that is a feature.

---

## E3 — `CLASSIFY_REJECT_MAX_TIER` belongs in the UI (S)

**Not relitigating the settings-UI cut.** `product_review.md` §7 cut onboarding investment
including "a settings UI for the 89 env vars", and that stands.

**This knob differs in kind.** The whole design of the classifier
([`design_media_classifier.md`](design_media_classifier.md) §1) turns on storing only the
tier so the cut can move *after* the user sees the distribution — "changing it re-thresholds
the archive with no re-classify" is the stated headline property. The intended workflow is:
run classify → look at the split → decide the cut. The last step currently requires editing
`.env` and restarting the server.

Stage 1 shipped the distribution panel, so the user can now *see* the thing the knob
responds to and still cannot turn it. That asymmetry is the argument.

**Touches**
- `PUT /api/config/reject-cut` (or a general small allowlist of runtime-safe knobs).
- The value is read at query time from `config.CLASSIFY_REJECT_MAX_TIER`; it must become a
  mutable runtime value with the env var as the initial default, persisted through
  `storage/atomic.atomic_write_json` (AGENTS.md rule 9).
- A control in the Insights classify panel, right under `Reject rate`, showing the counts
  that would move.

**Watch out**
- Only the tier is stored, so nothing is destroyed by a wrong setting — but the review pile
  changes size instantly. Show the before/after counts before applying.

---

## E4 — Verdict confidence is shown; the unmeasured boundary is not (S)

**Context.** [`design_media_classifier.md`](design_media_classifier.md) §2 states plainly
that the `1↔2` boundary has never been measured, and the nearest boundary that was came back
at **0.576 recall**. The default cut sits exactly on the unmeasured line.

**What the UI does today.** Triage shows the tier chip, the model's reason, `conf N%` and the
prompt version (`app.js`, `#triageMeta`) — that is real and it is good. What it does not do
is distinguish a T1 verdict, which sits on the unmeasured boundary, from a T0 verdict, which
is an unambiguous quality gate (no woman / man in frame / poster / unusable quality).

**Proposal.** In triage and on the review chips, mark T1 as the lower-confidence class:
a "boundary unmeasured" hint on the Modest chip, and de-emphasised bulk-select for T1 versus
T0. The review strip already splits `Unusable` / `Modest` with independent select-all — this
adds the *reason* that split exists to the place the decision is made.

**Cheaper alternative worth doing first.** Sort the reject pile by confidence ascending, so
the model's own least-certain calls are the first thing reviewed.

---

## E5 — Two holes in an otherwise strong verification net (S)

**What is good.** 27 Python test modules, 5 browser suites over CDP with no npm deps, ruff,
CI on two Python versions. `pytest` is 556 green. That is genuinely above the bar for a
personal project.

### E5a — Nothing fails when the distribution saturates

`top_tier_share` is computed per run (journal), archive-wide (`/api/insights`), and — since
Stage 1 — rendered with a warning above 0.6. But it is **advisory**. `product_review.md` B4
makes "CI fails if any single bucket exceeds 60% of outputs" standing policy from Phase 14,
and it is still Todo.

The previous classifier reached 85% on one tier with nothing to notice. A warning banner is
strictly better than nothing and strictly worse than a failing check, because the person who
needs to see it is the person who stopped opening the panel.

**Proposal.** A test that reads the archive-wide histogram and fails above the threshold,
skipped when fewer than N items are classified. Runs locally, not in CI (CI has no archive).

### E5b — No performance regression test ✅ done

AGENTS.md rule 13 says measure before optimising and report the number — and the repo has
twice been right to reverse an "obvious" win on measurement (FTS5, incremental rebuild).
There is no test that *notices* a regression in the other direction.

This matters most for the two hot paths with known headroom:
- `query_photos` with `search=` — a leading-wildcard `LIKE` over `prompt_search`, about to
  get longer if **F1** lands.
- gallery render at archive scale — U2 in the review; ~53k DOM nodes at the bottom of an
  unfiltered scroll.

**Proposal.** A benchmark script under `scripts/` that seeds N rows and reports timings, run
by hand and pasted into the PR, rather than a CI gate on a shared runner where the numbers
are noise. That matches how the existing measurements in
[`review_backend_architecture.md`](review_backend_architecture.md) were produced.

---

## Sequence

| Order | Item | Rationale |
|-------|------|-----------|
| — | **E1** | ✅ Done in Stage 1. |
| 1 | **E4** confidence-first review | Hours. Sorting the reject pile by ascending confidence needs no new data. |
| 2 | **E5b** benchmark script | Needed *before* F1 lands, so the caption change has a baseline to be measured against. |
| 3 | **E3** reject-cut control | Small, and it completes the workflow the classifier was designed around. |
| 4 | **E5a** saturation gate | Do it with, or just after, B4 in Phase 14 rather than twice. |
| 5 | **E2** `app.js` ownership | Continuous, inside feature PRs — same terms as S7. |
