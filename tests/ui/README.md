# Browser UI suites

These drive the **real** `index.html` + `app.js` in headless Chrome over the
DevTools Protocol. They exist because the interesting properties of this UI are
behavioural and can't be asserted from Python:

- **Absence of a refetch.** Deleting a photo must issue exactly one request (the
  `DELETE`) and leave scroll position and loaded pages intact. A unit test can't
  see that; recording `window.fetch` can.
- **Escaping actually holds.** Injecting `"><img src=x onerror=…>` as a filename,
  IG handle, or `full_name` and asserting the payload never executes is only
  meaningful in a browser that would execute it.
- **Debounce and abort ordering.** Six keystrokes → one request; four rapid
  filter changes → three aborts and the newest result applied.

## Running

```bash
tests/ui/run.sh                    # both suites
tests/ui/run.sh test_escaping.js   # one suite
```

`run.sh` seeds a throwaway archive, boots a server on `:5099`, launches headless
Chrome on CDP `:9222`, runs the suites, and tears it all down. If no Chrome is
found it prints `SKIP` and exits 0 — set `CHROME_BIN` to point at a binary.

Requirements: **Node 22+** (for the built-in `WebSocket` — there are no npm
dependencies) and Chrome or Chromium.

Overridable env: `TEST_PORT`, `CDP_PORT`, `PHOTO_COUNT`, `PYTHON`, `CHROME_BIN`.

## Files

| File | Purpose |
|------|---------|
| `cdp.js` | CDP session + assertion reporter; fixes the viewport for reproducibility |
| `test_delete_flow.js` | Soft delete, undo, trash modal, bulk delete, optimistic refresh |
| `test_escaping.js` | `escapeHtml` at real render sites, search debounce, abort ordering |
| `test_jobs_and_prefs.js` | Job chips, persisted view prefs, skeletons, sync-mode payloads |
| `test_post_grouping.js` | Carousels as one tile, `←`/`→` through a post's slides, the toggle as a pref |
| `seed_*.py` | Fixtures the HTTP API cannot produce (verdicts, non-Instagram sources, carousels) — written straight into the index, and wired per suite in `run.sh` |
| `run.sh` | Fixture seeding + server/Chrome lifecycle |

## Writing a new suite

```js
const { Session, Report, sleep } = require('./cdp');

(async () => {
  const s = new Session();
  const r = new Report('my feature');
  await s.connect();
  await s.load();

  await s.startRecordingFetches();       // then read via s.fetchLog()
  const value = await s.eval('return state.photos.length;');
  r.check('gallery loaded', value > 0, `${value} photos`);

  r.finish(s);                            // also asserts no console/page errors
  process.exit(process.exitCode || 0);
})().catch((e) => { console.error('HARNESS ERROR:', e.message); process.exit(2); });
```

Two things that caused false failures while these were written, both handled by
`cdp.js` now — don't reintroduce them:

1. **Viewport leakage.** A `setDeviceMetricsOverride` from an earlier script
   persists in the browser session and changes how much the page can scroll, so
   scroll assertions clamp to 0 and "fail". `Session.connect()` pins the
   viewport every run.
2. **Assumed fixture state.** A suite that expects an item already in the trash
   breaks when run in a different order. Seed what you assert on.
