/**
 * A3 — rating a generation from the lightbox compare pane.
 *
 * What is worth asserting in a real browser and nowhere else:
 *
 * - The control reflects what the store already knows. `/api/generations` was
 *   reading `generations_index.json`, which has no rating column, so a rated
 *   output reopened with an empty control — indistinguishable from "the rating
 *   was lost". Python covers the endpoint; only the browser proves the pane
 *   renders it.
 * - `1` / `2` / `0` / `x` reach the right handler. The lightbox already binds
 *   arrows, Esc, g/c/s/f and the K/R/X triage set; a new four-key binding is
 *   exactly the kind of thing that silently loses to an earlier branch.
 * - The keys are inert when the pane is closed. Otherwise typing `x` anywhere
 *   in the lightbox would rate an invisible generation.
 */
const { Session, Report, sleep } = require('./cdp');

(async () => {
  const s = new Session();
  const r = new Report('generation rating');
  await s.connect();
  await s.load();
  await sleep(600);

  // Seed a generation for the first photo through the real API, then open it.
  const seeded = await s.eval(`
    const res = await fetch('/api/photos?limit=1');
    const data = await res.json();
    const photo = (data.photos || [])[0];
    if (!photo) return { ok: false, why: 'no photos' };
    // The table is the source of truth; there is no HTTP route that creates a
    // generation without ComfyUI, so drive the gallery to the photo and fake
    // the pane state the way a completed job would.
    return { ok: true, rel: photo.rel_path };
  `);
  r.check('archive has a photo to work with', seeded.ok === true, seeded.why || '');

  const wired = await s.eval(`
    return {
      control: Boolean(document.getElementById('genRating')),
      buttons: document.querySelectorAll('#genRating .gen-rate-btn').length,
      handler: typeof handleGenerationRatingKey === 'function',
      setter: typeof setCurrentGeneration === 'function',
    };
  `);
  r.check('rating control exists in the DOM', wired.control === true);
  r.check('all four points on the scale are present', wired.buttons === 4,
    String(wired.buttons));
  r.check('key handler is wired', wired.handler === true);

  // ── reflects an existing rating ────────────────────────────────────
  r.section('reflects stored state');

  const reflected = await s.eval(`
    setCurrentGeneration({ gen_id: 'fake-gen', rating: 2, files: [{ gen_id: 'fake-gen', rating: 2 }] });
    const active = document.querySelector('#genRating .gen-rate-btn.active');
    return {
      rating: active ? active.dataset.rating : null,
      visible: document.getElementById('genRating').style.display,
      loud: document.getElementById('genRating').classList.contains('has-rating'),
    };
  `);
  r.check('a stored rating renders as the active button', reflected.rating === '2',
    String(reflected.rating));
  r.check('control is shown when a generation is present', reflected.visible === 'flex',
    reflected.visible);
  r.check('a rated control stays fully opaque', reflected.loud === true);

  const cleared = await s.eval(`
    setCurrentGeneration(null);
    const el = document.getElementById('genRating');
    const anyEnabled = [...el.querySelectorAll('.gen-rate-btn')].some((b) => !b.disabled);
    return { visible: el.style.display, anyEnabled };
  `);
  r.check('control hides with no generation', cleared.visible === 'none', cleared.visible);
  r.check('buttons are disabled with no generation', cleared.anyEnabled === false);

  // ── keyboard ───────────────────────────────────────────────────────
  r.section('keyboard');

  const inertWhenClosed = await s.eval(`
    state.compareMode = false;
    setCurrentGeneration({ gen_id: 'fake-gen', rating: 0, files: [{ gen_id: 'fake-gen' }] });
    return handleGenerationRatingKey({ key: 'x' });
  `);
  r.check('keys are inert while the compare pane is closed',
    inertWhenClosed === false, String(inertWhenClosed));

  const claimed = await s.eval(`
    state.compareMode = true;
    setCurrentGeneration({ gen_id: 'fake-gen', rating: 0, files: [{ gen_id: 'fake-gen' }] });
    // These fire real PUTs at a gen_id the server does not have. Silence the
    // expected logging and let them settle here, or their late rejections land
    // in the middle of the next check.
    const realError = console.error;
    console.error = () => {};
    const out = {};
    try {
      for (const k of ['1', '2', '0', 'x']) {
        out[k] = handleGenerationRatingKey({ key: k });
      }
      out.unrelated = handleGenerationRatingKey({ key: 'q' });
      await new Promise((res) => setTimeout(res, 400));
    } finally {
      console.error = realError;
    }
    return out;
  `);
  r.check('1 keeps', claimed['1'] === true);
  r.check('2 stars', claimed['2'] === true);
  r.check('0 clears', claimed['0'] === true);
  r.check('x discards', claimed.x === true);
  r.check('an unrelated key is left alone', claimed.unrelated === false);

  // ── optimistic write rolls back on failure ─────────────────────────
  r.section('failed write');

  const rolledBack = await s.eval(`
    state.compareMode = true;
    setCurrentGeneration({ gen_id: 'nope-not-real', rating: 1, files: [{ gen_id: 'nope-not-real' }] });
    // Unknown gen_id — the server answers 404, so the optimistic update must
    // not stick or the UI shows a rating the store rejected. The handler logs
    // to console.error by design, so silence it for this one call rather than
    // weakening the suite's no-console-errors invariant.
    const realError = console.error;
    console.error = () => {};
    let probe;
    try {
      probe = await fetch('/api/generation/rate', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ gen_id: 'nope-not-real', rating: 2 }),
      }).then((res) => res.status);
      await rateCurrentGeneration(2);
      await new Promise((res) => setTimeout(res, 200));
    } finally {
      console.error = realError;
    }
    const active = document.querySelector('#genRating .gen-rate-btn.active');
    return { probe, active: active ? active.dataset.rating : null };
  `);
  r.check('the server really rejects an unknown gen_id', rolledBack.probe === 404,
    String(rolledBack.probe));
  r.check('a rejected rating rolls back to the previous value',
    rolledBack.active === '1', String(rolledBack.active));

  await sleep(200);
  r.finish(s) || process.exit(1);
  process.exit(0);
})().catch((err) => {
  console.error('SUITE ERROR:', err);
  process.exit(1);
});
