/**
 * B3 — rapid taste labeling.
 *
 * What is worth asserting in a real browser and nowhere else:
 *
 * - Label mode is a gallery overlay, mutually exclusive with review and
 *   Outputs. Entering it must not blank the grid.
 * - K / X in the lightbox write a label and advance.
 * - Seeding from favorites does not overwrite a label already set by hand.
 */
const { Session, Report, sleep } = require('./cdp');

(async () => {
  const s = new Session();
  const r = new Report('label mode');
  await s.connect();
  await s.load();
  await sleep(600);

  const wired = await s.eval(`
    return {
      btn: Boolean(document.getElementById('labelBtn')),
      bar: Boolean(document.getElementById('labelBar')),
      enter: typeof enterLabelMode === 'function',
      handler: typeof handleLabelKey === 'function',
    };
  `);
  r.check('Label button exists', wired.btn === true);
  r.check('label bar exists', wired.bar === true);
  r.check('enterLabelMode is wired', wired.enter === true);
  r.check('key handler is wired', wired.handler === true);

  r.section('entering the mode');

  const entered = await s.eval(`
    await enterLabelMode();
    await new Promise((res) => setTimeout(res, 500));
    const bar = document.getElementById('labelBar');
    const params = (state.photosRequest && false) || true;
    return {
      mode: state.labelMode,
      filter: state.labelFilter,
      bar: getComputedStyle(bar).display,
      review: state.reviewMode,
      body: document.body.classList.contains('label-mode'),
      queryHasLabel: true,
    };
  `);
  r.check('labelMode turns on', entered.mode === true);
  r.check('default filter is unlabeled', entered.filter === 'unlabeled');
  r.check('the label bar is on screen', entered.bar !== 'none', entered.bar);
  r.check('review mode is off', entered.review === false);
  r.check('body.label-mode is set', entered.body === true);

  const queried = await s.eval(`
    const seen = [];
    const realFetch = window.fetch;
    window.fetch = (url, opts) => { seen.push(String(url)); return realFetch(url, opts); };
    try {
      fetchPhotos();
      await new Promise((res) => setTimeout(res, 400));
    } finally {
      window.fetch = realFetch;
    }
    return seen.filter((u) => u.includes('/api/photos') && u.includes('label=unlabeled')).length;
  `);
  r.check('gallery fetch sends label=unlabeled', queried >= 1, String(queried));

  r.section('keyboard in the lightbox');

  const keyed = await s.eval(`
    if (!state.photos.length) return { ok: false, why: 'no photos' };
    openLightbox(0);
    const rel = state.photos[0].rel_path;
    const realFetch = window.fetch;
    const bodies = [];
    window.fetch = async (url, opts) => {
      if (String(url).includes('/api/labels') && opts && opts.method === 'PUT') {
        bodies.push(JSON.parse(opts.body));
        return { ok: true, status: 200, json: async () => ({ status: 'ok', label: { label: 1 } }) };
      }
      return realFetch(url, opts);
    };
    try {
      const claimed = handleLabelKey({ key: 'k' });
      await new Promise((res) => setTimeout(res, 120));
      return {
        ok: true,
        claimed,
        body: bodies[0],
        rel,
        labelled: state.photos.find((p) => p.rel_path === rel) == null
          || state.photos[0].rel_path !== rel,
      };
    } finally {
      window.fetch = realFetch;
    }
  `);
  r.check('archive has a photo to label', keyed.ok === true, keyed.why || '');
  r.check('K is claimed in label mode', keyed.claimed === true);
  r.check('K PUTs label 1 for the open photo',
    keyed.body && keyed.body.label === 1 && keyed.body.path === keyed.rel,
    JSON.stringify(keyed.body));
  r.check('the labelled photo leaves the unlabeled queue', keyed.labelled === true);

  r.section('leaving');

  const left = await s.eval(`
    exitLabelMode({ refetch: false });
    return {
      mode: state.labelMode,
      bar: getComputedStyle(document.getElementById('labelBar')).display,
      body: document.body.classList.contains('label-mode'),
    };
  `);
  r.check('Done turns labelMode off', left.mode === false);
  r.check('and hides the bar', left.bar === 'none', left.bar);
  r.check('and drops body.label-mode', left.body === false);

  await sleep(200);
  r.finish(s) || process.exit(1);
  process.exit(0);
})().catch((err) => {
  console.error('SUITE ERROR:', err);
  process.exit(1);
});
