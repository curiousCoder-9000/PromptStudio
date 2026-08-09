# Backend architecture review

| Field | Value |
|-------|--------|
| **Date** | 2026-08-09 |
| **Scope** | `promptstudio/` (server, storage, scraping, prompts, comfy) — 17k LOC Python |
| **Method** | Read of every package + runtime probes against a live server on a throwaway archive |
| **Scope note** | Security/auth **descoped at owner's request** — not assessed, not "assessed and clean" |
| **Verdict** | Well-engineered for its age and size. The gaps are durability and observability, not correctness. |

---

## 1. What is already good

Worth stating plainly, because the rest of this document is criticism and the
baseline is high:

- **The layering is real.** `config` → `storage` → `scraping`/`prompts`/`comfy` → `server`
  with no back-edges. Scripts are genuinely thin wrappers. Most projects this age
  have a `utils.py` doing everything; this one doesn't.
- **Idempotent ingest.** Post identity (`post_id`/`shortcode`) in SQLite rather than
  filename matching, plus tombstones so a local delete isn't undone by the next sync.
  That is the correct model and most people get it wrong.
- **`resolve_path` containment is correct**, including the boundary check that a
  naive `startswith` misses — and the roadmap documents finding it via a test.
- **Byte-range support** on media, with a clear comment on *why* (Chrome won't
  scrub without 206).
- **Cooperative cancellation** that is checked between items, with a documented
  reason for not interrupting in-flight Ollama calls.
- **289 tests + CI on two Python versions**, and the tests target places where
  silent failure is expensive rather than chasing coverage.

The instinct to write down *why* — in `roadmap.md`, in comments — is the most
valuable thing in the repo. Keep doing it.

---

## 2. Architecture as built

```
Browser (app.js, 5k LOC) ──► ThreadingHTTPServer :5000
                                  │  GalleryRequestHandler (1620 LOC, 36 route branches)
                                  │  └─ falls through to SimpleHTTPRequestHandler(cwd = repo root)
                                  ▼
      ┌──────────────┬────────────┴────────┬──────────────┐
   storage        prompts              scraping         comfy
   ArchiveIndex   PromptCache          SyncManager      ComfyJobManager
   (1 sqlite      (JSON file,          CreatorQueue
    conn +        whole-file           ClassifyJob
    RLock)        rewrite)             BatchPrompt
      │                │                    │              │
   archive.db     prompts_cache.json    Instagram      ComfyUI :8188
   + 9 JSON state files                  Ollama :11434
```

Five singleton job managers, each owning a thread and a status dict. Shared
external resources (Ollama, the Instagram session, ComfyUI) are contended for
via **ad-hoc pairwise checks** rather than a lease.

---

## 3. Findings, ranked

### S1 — Every derived state file can be truncated by a crash 🟠 ✅ done

Ten writers use bare `open(path, "w")`. `creator_queue.py:122` is the **only**
one that does it correctly — mkstemp → `fsync` → `os.replace`, with a docstring
saying "Atomic replace".

The pattern is already in the codebase; it just wasn't applied to the other nine,
including `prompts/cache.py`, which holds the most expensive derived data in the
system — LLM-generated prompts for ~4400 images, hours of GPU time. A crash or
power cut during that write truncates the JSON, and `_ensure_loaded` swallows the
parse error and returns `{}`. The failure mode is *silent total loss*.

**Fix:** extract `atomic_write_json(path, data)` from `creator_queue._save` and
use it in all ten places. Half a day, removes a whole class of data loss.

### S2 — An unhandled exception drops the connection instead of returning 500 🟠 ✅ done

`do_GET`/`do_POST`/`do_PUT`/`do_DELETE` have no top-level try/except. An
unhandled error propagates to `socketserver.handle_error`, which logs a traceback
and closes the socket. The browser sees a network-level failure, so the frontend
cannot distinguish "server has a bug" from "server is down" — and `app.js`
generally treats both as offline.

**Fix:** one decorator or a `_dispatch()` wrapper that catches, logs with the
route and params, and returns a JSON 500. ~20 lines, and it makes every future
bug visible instead of invisible.

### S3 — There is no logging 🟠 ✅ done

34 `print()` calls, zero uses of `logging`. 56 broad `except Exception:` handlers,
21 of which immediately `pass`.

For a tool whose defining workload is a **six-hour rate-limited scrape**, this is
the thing that will cost the most time over the next year. When a following sync
stops early there is no record of which account, which HTTP status, or which
backoff branch fired.

**Fix:** `logging` with a rotating file handler; a `promptstudio.log` next to the
archive. Then walk the 21 `except: pass` sites and either log or narrow them.
They are not all wrong — several are genuinely optional paths — but they should
say so.

### S4 — The prompt cache is the wrong data structure 🟡 ✅ done

`prompts_cache.json` is loaded whole into memory and **rewritten in full on every
single prompt save**. At ~4400 entries that is an O(n) serialize per O(1) logical
write, and it is a second source of truth for data the `photos` table already
partially mirrors (`has_prompt`, `prompt_stale`, `prompt_search`) — with a
`_lookup` that falls back from `rel_path` to bare `filename`, so two creators with
the same filename can collide.

**Fix:** a `prompts` table in the existing `archive.db`, keyed by `rel_path`, with
the JSON kept as an import path for one release. Fixes durability (S1), write
cost, and the dual source of truth in one move.

### S5 — Search cannot use an index 🟡 ⚠️ built, left off — the measurement said no

**The recommendation above was wrong, and the benchmark is why it is not
enabled.** FTS5 is built and the index is maintained, but `query_photos` still
uses the LIKE scan by default (`PROMPTSTUDIO_FTS_SEARCH=0`).

Measured on synthetic archives with a realistic vocabulary (3000 distinct
words), same machine, same query shape:

| rows | query | matches | LIKE | FTS5 | |
|-----:|-------|--------:|-----:|-----:|---|
| 4 400 | rare word | 51 | 6.6 ms | **4.5 ms** | FTS wins 1.5x |
| 4 400 | common word | 2 946 | **3.1 ms** | 9.4 ms | LIKE wins 3x |
| 40 000 | rare word | 511 | 44 ms | **30 ms** | FTS wins 1.5x |
| 40 000 | common word | 26 902 | **35 ms** | 99 ms | LIKE wins 3x |

The integration is `rel_path IN (SELECT … MATCH …)`, which materialises every
match before probing; LIKE short-circuits per row and stops at the page limit.
So FTS only wins when the query is *selective*, and loses badly when it is
not — and "not selective" is exactly what a search box sees while someone is
still typing.

At 4400 rows LIKE costs 3–7 ms, which is imperceptible. Shipping a 3x
regression on the common path to win 1.5x on the rare one is a bad trade, so
the flag defaults off. Revisit if the archive grows an order of magnitude, or
rewrite the integration to push the page limit into the FTS scan.

**Kept anyway:** the index is maintained on every prompt write (one extra row),
so enabling it later is a flag flip rather than a backfill.

<details><summary>original recommendation</summary>

`query_photos` builds `LOWER(prompt_search) LIKE '%q%'`. A leading wildcard means
no index is usable: every search is a full table scan plus a `LOWER()` per row.
Fine at 4.4k rows, quadratically annoying as the archive grows, and the same
query runs twice (once for `COUNT(*)`, once for the page).

**Fix:** SQLite **FTS5** virtual table over `prompt_search`, kept in sync by
trigger. Turns substring scanning into a real inverted index and gives you
phrase/prefix queries for free.
</details>

### S6 — Five job managers, one missing abstraction 🟡 ✅ leases done, base class deferred

`SyncManager`, `BatchPromptManager`, `ClassifyJobManager`, `ComfyJobManager` and
`CreatorScrapeQueue` each independently reimplement: double-checked-locking
singleton, `_job_lock`, `_cancel` Event, `get_status()`, `is_running()`,
`cancel()`, thread spawn, and status-dict lifecycle.

Worse than the duplication is the **contention model**. Mutual exclusion is
encoded as pairwise checks scattered across modules —
`classify_job._vision_busy_elsewhere()` reaches into `BatchPromptManager`;
`handler._creator_queue_blocks_oneshot()` reaches into `CreatorScrapeQueue`;
`handler` re-checks `is_running()` at 8 call sites. Every new job type is O(n)
new checks, and the checks are TOCTOU races — `is_running()` is polled, then the
job starts, with no lock held across both.

The actual model is simple: there are three **exclusive external resources**
(`ollama`, `instagram`, `comfy`) and jobs need leases.

**Fix:** a `BackgroundJob` base class plus a tiny `ResourceRegistry` with
`try_acquire(resource, owner) -> bool` under one lock. Each job declares what it
needs. The 8 scattered checks collapse into one, and the race closes.

**Shipped:** `promptstudio/jobs.py` — a `LeaseRegistry` over `ollama`,
`instagram`, `comfy`. Acquisition of all of a job's resources happens under one
lock, so it is atomic and the race is closed (a 16-thread contention test
asserts exactly one winner). `classify_job._vision_busy_elsewhere()` is gone;
classify and batch-prompt now contend for the same `ollama` lease and the
refusal names the holder. `GET /api/health` reports `leases`.

Two things found while wiring it:

* The batch runner had **no try/finally**. An exception escaping the loop left
  `running=True` forever — the job could never be restarted without a server
  bounce — and would now also have stranded the lease. Both fixed.
* Comfy is declared but deliberately *not* made exclusive with Ollama. They do
  share a GPU, but today they can run together; making that exclusive is a
  product decision, not a refactor.

**Deferred:** the `BackgroundJob` base class. The duplicated
singleton/status/cancel scaffolding is real but cosmetic, and collapsing it
touches all five managers at once — poor value against the conflict risk while
another session is editing the same files. The lease was the correctness half.

### S7 — `handler.py` is a 1620-line if-chain 🟡

36 route branches; `do_POST` alone is 827 lines. Business logic (queue policy,
sync argument validation, Comfy parameter assembly) lives inline in the router,
so it is only reachable through HTTP and only testable through HTTP.

**Fix:** a route table `{(method, path): handler_fn}` and move each branch body
into the package it belongs to. Mechanical, low-risk, and it makes the logic
unit-testable. Do it incrementally — one route group per PR — not as a big bang.

### S8 — SQLite is running on defaults 🟢 ✅ done

No `journal_mode=WAL`, no `busy_timeout`, no `synchronous` tuning. One connection
shared across all request threads behind a global `RLock`, so every API call
serializes on it — including during `rebuild()`'s bulk insert.

The filesystem scan in `rebuild()` is correctly done *outside* the lock, which
shows the concern was understood. But `DELETE FROM photos` + full re-insert means
a rebuild is a stop-the-world event for the API.

**Fix:** `PRAGMA journal_mode=WAL; PRAGMA busy_timeout=5000;` at connect, and
make `rebuild()` an mtime-diffed upsert rather than delete-all.

**Shipped:** WAL, `busy_timeout=5000`, `synchronous=NORMAL`.

**Incremental rebuild: measured, then dropped.** Profiling `rebuild()` found the
cost was never the SQL — it was that four different fields of each sidecar
(`taken_at`, identity, glam, source) each loaded and parsed `*.meta.json`
independently. 4 opens per photo; 18 000 across a 4500-file archive, on every
build *and* on every per-photo `upsert_photo`.

| 4500 files | sidecar reads | rebuild |
|---|--:|--:|
| before | 18 000 (4.0/file) | 2.15 s |
| after `read_sidecar()` | 4 500 (1.0/file) | **0.70 s** |

At 0.7 s the mtime-diff is not worth its correctness risk: a skip keyed on media
mtime misses a sidecar rewritten by an out-of-process classify run, and
`rebuild()` exists precisely to repair the index from disk. `tests/
test_index_sidecar_reads.py` asserts the read counts, since the behaviour is
invisible in the output.

---

## 4. Feature proposals

Ranked by value ÷ effort. The first two are the ones I would actually build.

### F1 — Embedding index ⭐ ⚠️ pHash half shipped; embeddings not started

`roadmap.md` defers CLIP with "not needed for the generate → Comfy → compare
loop." That was a reasonable call, but the cost side has changed: **sqlite-vec**
is now the actively developed vector extension for SQLite (its predecessor
`sqlite-vss` is retired), stores vectors as BLOBs in ordinary tables, and needs
no server — it drops straight into the `archive.db` that already exists.
**SigLIP 2** is currently the strongest open image-text model for retrieval, with
better-calibrated similarity than CLIP thanks to its sigmoid loss.

One embedding per image unlocks four features that are currently separate wishes:

| Capability | Today | With embeddings |
|---|---|---|
| Search | `LIKE '%red bikini%'` over *generated prompt text* — finds nothing if the prompt didn't use those words | true text→image retrieval over pixels |
| "More like this" | absent | kNN, one query |
| Dedupe | `deduplicate.py`, exact matches only | near-dup collapse across creators and re-crops |
| Glam pre-scoring | a VLM call per item | kNN against labelled examples; only send the uncertain middle to the VLM |

That last row is the sleeper. It makes the reel classifier cheaper *and* gives
you the labelled-set infrastructure that `design_reel_classifier_v2.md` says is
blocking. Pair with a **pHash** column for exact/near-exact re-posts, which is
cheaper than embeddings and catches the most common case.

Effort: a day for the embedding job, a day for sqlite-vec plumbing and the UI.

**Shipped (the cheap half):** `storage/dedupe.py` — 64-bit DCT perceptual hash,
no new dependencies, stored in its own `phashes` table. `scripts/
find_duplicates.py` reports groups and suggests a keeper; it never deletes,
because grouping is a heuristic and deletion belongs in the UI where it lands in
the trash.

Measured separation on synthetic photos — the threshold is not on a knife edge:

| transformation | distance |
|---|--:|
| re-encode (JPEG q40) | 0 bits |
| resize to half | 0 bits |
| +45 brightness | 2 bits |
| ~6% crop | 4 bits |
| *unrelated photo* | *30–32 bits* |

Default threshold 8 sits in a 24-bit gap. Grouping the whole archive is 0.02 s
at 4.4k files and 0.71 s at 50k; `find_similar()` is 0.4 ms, fast enough to back
a "more like this" endpoint later.

**Still open:** embeddings (SigLIP 2 + sqlite-vec) for text→image search and
kNN glam pre-scoring. pHash finds the *same* picture; it cannot find a
*similar* one, and it does nothing for search. That needs the ML dependency
decision.

### F2 — Run journal (JSONL) for every background job ⭐ ✅ done

One append-only `runs.jsonl` per job kind: start, per-item outcome, backoff
events, final status. Cheap to write, trivially greppable, and it turns three
things from guesswork into queries:

- Why did last night's following sync stop at account 12?
- Is the classifier's score distribution drifting? (You just added
  `top_score_share` per run — persist it and the drift is a one-liner.)
- What is the actual rate-limit interarrival, so pacing can be tuned on data
  rather than superstition?

This is the cheapest high-leverage thing on the list and it composes with S4.

### F3 — Generalise the golden-set harness beyond the reel classifier

The reel work needs a labelled eval set. The **prompt engine has exactly the same
problem** — `ENGINE_ID`/`pipeline_version` can change and nothing detects quality
regression. One harness, two consumers: a set of pinned images with expected
outputs, a scoring script, a CI job that fails on regression.

### F4 — Faceted attributes instead of one scalar

`glam_score` compresses everything into 0–3. The vision model could return a
small structured taxonomy — setting (studio/beach/street), outfit type, shot
framing (full body/portrait), pose — stored as columns and exposed as filter
chips. Better browsing, and better Comfy conditioning, at no extra inference
cost since it rides on a call you already make.

### F5 — Archive export / backup for derived state

Prompts, glam scores, favorites and styles represent many GPU-hours and exist in
exactly one place on one disk. A `promptstudio export --derived` producing a
single portable file (and an import) is a couple of hours and removes an
uncomfortable single point of failure. Independently useful when re-scoring
invalidates a prompt version and you want the old one back.

### F6 — Content-addressed thumbnails

`_thumbs/` mirrors the archive tree, so renames orphan thumbs and the reel
classifier's peak-frame thumbs get invalidated by mtime rather than by content.
Keying on a content hash makes the cache self-healing. Low priority.

---

## 5. Suggested order

1. ~~**S1 + S2 + S3** — atomic writes, error boundary, logging.~~ **Done.**
2. ~~**F2** — run journal.~~ **Done** — `storage/journal.py`, wired into classify,
   batch-prompt and sync; served by `GET /api/journal?kind=<kind>`.
   ~~**S8** — WAL + busy_timeout.~~ **Done.**
3. ~~**S4** — prompt cache into SQLite.~~ **Done** — 7x faster writes, and the
   filename-collision bug is gone. ~~**S5** — FTS5.~~ **Built, default off**;
   the benchmark said the LIKE scan is faster for common queries at this size.
4. ~~**S6** — job/lease abstraction.~~ **Done** (leases; base class deferred).
5. **F1** — embeddings. The big feature; build it on the clean foundation.
6. **S7** — router refactor, incrementally, whenever a route is touched anyway.

~~**S8**~~ done. **S7** (router) is deliberately still open: it is a
maintainability change with no correctness or performance payoff, and the
recommendation above was to do it incrementally as routes are touched rather
than as a big-bang restructure of a file another session is editing.
**F1** needs a dependency decision. **F3–F6** are opportunistic.

`app.js` at 5074 lines has the same monolith problem as `handler.py` and is out
of scope here, but it will need the same treatment.

---

## Sources

- [sqlite-vec / state of vector search in SQLite](https://marcobambini.substack.com/p/the-state-of-vector-search-in-sqlite)
- [sqlite-vss retired in favour of sqlite-vec](https://github.com/asg017/sqlite-vss)
- [Best self-hosted embedding models 2026](https://mixpeek.com/curated-lists/best-self-hosted-embedding-models)
- [SigLIP-2 vs CLIP for retrieval](https://www.spheron.network/blog/multimodal-embedding-models-gpu-cloud-siglip2-jinaclip-cohere/)
