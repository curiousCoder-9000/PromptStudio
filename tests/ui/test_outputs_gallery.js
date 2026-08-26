/**
 * A1 — the outputs gallery.
 *
 * What is worth asserting in a real browser and nowhere else:
 *
 * - The view actually swaps. It is a sibling <main>, not a modal, so the bug
 *   to catch is both galleries visible at once or neither.
 * - Cards render from the real endpoint, including the rating badge and the
 *   "no seed" flag. Python proves the JSON; only the browser proves the grid.
 * - Regenerate-same-seed is *disabled* on a legacy row. The whole argument for
 *   doing A0 before A1 was that this button must not exist in a form that
 *   cannot do what it says.
 * - Filters and sort re-query rather than filtering the loaded page — the
 *   total comes from the server, so client-side filtering would disagree with
 *   the count next to the title.
 * - Escape closes the detail. A modal with no keyboard exit is the same trap
 *   review mode was.
 */
const { Session, Report, sleep } = require('./cdp');

(async () => {
  const s = new Session();
  const r = new Report('outputs gallery');
  await s.connect();
  await s.load();
  await sleep(600);

  const listed = await s.eval(`
    const res = await fetch('/api/generations/list');
    const data = await res.json();
    return { status: res.status, total: data.total, keys: Object.keys(data).sort() };
  `);
  r.check('list endpoint answers', listed.status === 200, String(listed.status));
  r.check('response carries the photos paging shape',
    ['facets', 'generations', 'has_more', 'limit', 'offset', 'total']
      .every((k) => listed.keys.includes(k)), listed.keys.join(','));

  // ── view switching ─────────────────────────────────────────────────
  r.section('view switching');

  const initial = await s.eval(`
    return {
      outputs: getComputedStyle(document.getElementById('outputsView')).display,
      photos: getComputedStyle(document.querySelector('.gallery-container:not(.outputs-container)')).display,
    };
  `);
  r.check('outputs view starts hidden', initial.outputs === 'none', initial.outputs);
  r.check('photo gallery starts visible', initial.photos !== 'none', initial.photos);

  const switched = await s.eval(`
    document.getElementById('outputsBtn').click();
    await new Promise((res) => setTimeout(res, 500));
    return {
      outputs: getComputedStyle(document.getElementById('outputsView')).display,
      photos: getComputedStyle(document.querySelector('.gallery-container:not(.outputs-container)')).display,
      active: document.getElementById('outputsBtn').classList.contains('active'),
    };
  `);
  r.check('clicking Outputs shows the outputs view', switched.outputs !== 'none', switched.outputs);
  r.check('and hides the photo gallery', switched.photos === 'none', switched.photos);
  r.check('the nav button reads as active', switched.active === true);

  const switchedBack = await s.eval(`
    document.getElementById('outputsBtn').click();
    await new Promise((res) => setTimeout(res, 200));
    return getComputedStyle(document.querySelector('.gallery-container:not(.outputs-container)')).display;
  `);
  r.check('clicking again returns to photos', switchedBack !== 'none', switchedBack);

  // ── rendering ──────────────────────────────────────────────────────
  r.section('rendering');

  const rendered = await s.eval(`
    // Render from a known fixture rather than whatever the archive holds, so
    // the badge and flag assertions below are deterministic.
    state.outputs = [
      { gen_id: 'g-modern', creator: 'nina', workflow: 'pro', rating: 2,
        seed: 4242, seed_recorded: true, url: '/media/x.png', thumb_url: '/media/thumb/x.png',
        source_rel: 'nina/photo.jpg', positive_prompt: 'a portrait', negative_prompt: 'blurry',
        steps: 32, cfg: 6, denoise: 0.7, mode_e: true, prompt_version: 'v2', created_at: '2026-08-10' },
      { gen_id: 'g-legacy', creator: 'nina', workflow: 'pro', rating: 0,
        seed: -1, seed_recorded: false, url: '/media/y.png', thumb_url: '/media/thumb/y.png',
        source_rel: 'nina/photo.jpg', positive_prompt: 'older', negative_prompt: '',
        steps: 30, cfg: 7, denoise: null, mode_e: false, prompt_version: null, created_at: '2026-08-01' },
    ];
    state.outputsTotal = 2;
    state.outputsHasMore = false;
    renderOutputs({ append: false });
    return {
      cards: document.querySelectorAll('#outputsGrid .output-card').length,
      rateBtns: document.querySelectorAll('#outputsGrid .gen-rate-btn').length,
      starred: document.querySelectorAll('#outputsGrid .gen-rate-btn.star.active').length,
      sources: document.querySelectorAll('#outputsGrid .output-source-badge').length,
      flags: document.querySelectorAll('#outputsGrid .output-flag').length,
      count: document.getElementById('outputsCount').textContent,
    };
  `);
  r.check('one card per generation', rendered.cards === 2, String(rendered.cards));
  r.check('each card has the four-point rating control', rendered.rateBtns === 8,
    String(rendered.rateBtns));
  r.check('the starred card shows the star as active', rendered.starred === 1,
    String(rendered.starred));
  r.check('cards with a source_rel get a source thumb badge', rendered.sources === 2,
    String(rendered.sources));
  r.check('only the seedless card gets the no-seed flag', rendered.flags === 1,
    String(rendered.flags));
  r.check('count reflects the server total', /2 items/.test(rendered.count), rendered.count);

  const escaped = await s.eval(`
    state.outputs = [{ gen_id: 'g-x', creator: '<img src=x onerror=alert(1)>',
      workflow: 'pro', rating: 0, seed: 1, seed_recorded: true, url: '/media/x.png',
      thumb_url: '/media/thumb/x.png', source_rel: 'a/b.jpg', positive_prompt: '',
      negative_prompt: '', steps: 1, cfg: 1, denoise: null, mode_e: false,
      prompt_version: null, created_at: '2026-08-10' }];
    state.outputsTotal = 1; state.outputsHasMore = false;
    renderOutputs({ append: false });
    return document.querySelectorAll('#outputsGrid .photo-creator img').length;
  `);
  r.check('a hostile creator handle is escaped, not injected', escaped === 0, String(escaped));

  // ── detail ─────────────────────────────────────────────────────────
  r.section('detail + provenance');

  const detail = await s.eval(`
    state.outputs = [
      { gen_id: 'g-modern', creator: 'nina', workflow: 'pro', rating: 2,
        seed: 4242, seed_recorded: true, url: '/media/x.png', thumb_url: '/media/thumb/x.png',
        source_rel: 'nina/photo.jpg', positive_prompt: 'a portrait', negative_prompt: 'blurry',
        steps: 32, cfg: 6, denoise: 0.7, mode_e: true, prompt_version: 'v2', created_at: '2026-08-10' },
      { gen_id: 'g-legacy', creator: 'nina', workflow: 'pro', rating: 0,
        seed: -1, seed_recorded: false, url: '/media/y.png', thumb_url: '/media/thumb/y.png',
        source_rel: 'nina/photo.jpg', positive_prompt: 'older', negative_prompt: '',
        steps: 30, cfg: 7, denoise: null, mode_e: false, prompt_version: null, created_at: '2026-08-01' },
    ];
    openOutputDetail('g-modern');
    const meta = document.getElementById('outputDetailMeta').textContent;
    return {
      open: document.getElementById('outputDetailModal').style.display,
      meta,
      positive: document.getElementById('outputDetailPositive').textContent,
      sameSeedDisabled: document.getElementById('outputRegenSameSeed').disabled,
    };
  `);
  r.check('detail opens', detail.open === 'flex', detail.open);
  r.check('provenance shows the real seed', /4242/.test(detail.meta));
  r.check('provenance shows the checkpoint row', /Checkpoint/.test(detail.meta));
  r.check('full prompt is shown', detail.positive === 'a portrait', detail.positive);
  r.check('regenerate-same-seed is enabled for a recorded seed',
    detail.sameSeedDisabled === false);

  const legacy = await s.eval(`
    openOutputDetail('g-legacy');
    const btn = document.getElementById('outputRegenSameSeed');
    return {
      disabled: btn.disabled,
      title: btn.title,
      meta: document.getElementById('outputDetailMeta').textContent,
    };
  `);
  r.check('regenerate-same-seed is DISABLED on a legacy row', legacy.disabled === true);
  r.check('and the disabled reason is stated', /cannot be reproduced/i.test(legacy.title),
    legacy.title);
  r.check('provenance says the seed was not recorded',
    /not recorded/.test(legacy.meta));

  const closed = await s.eval(`
    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
    await new Promise((res) => setTimeout(res, 120));
    return document.getElementById('outputDetailModal').style.display;
  `);
  r.check('Escape closes the detail', closed === 'none', closed);

  // ── filters re-query the server ────────────────────────────────────
  r.section('filters');

  const requery = await s.eval(`
    const seen = [];
    const realFetch = window.fetch;
    window.fetch = (url, opts) => { seen.push(String(url)); return realFetch(url, opts); };
    try {
      document.getElementById('outputsRating').value = '2';
      document.getElementById('outputsRating').dispatchEvent(new Event('change'));
      await new Promise((res) => setTimeout(res, 400));
    } finally {
      window.fetch = realFetch;
    }
    return seen.filter((u) => u.includes('/api/generations/list'));
  `);
  r.check('changing a filter re-queries the server', requery.length >= 1,
    requery.join(' | '));
  r.check('and sends the rating as a query parameter',
    requery.some((u) => /rating=2/.test(u)), requery.join(' | '));

  const ratedOnly = await s.eval(`
    const seen = [];
    const realFetch = window.fetch;
    window.fetch = (url, opts) => { seen.push(String(url)); return realFetch(url, opts); };
    try {
      document.getElementById('outputsRating').value = 'rated';
      document.getElementById('outputsRating').dispatchEvent(new Event('change'));
      await new Promise((res) => setTimeout(res, 400));
    } finally {
      window.fetch = realFetch;
    }
    return seen.filter((u) => u.includes('/api/generations/list')).join(' | ');
  `);
  r.check('"Rated (any)" maps to rated_only, not a rating value',
    /rated_only=1/.test(ratedOnly) && !/rating=/.test(ratedOnly), ratedOnly);

  // ── review mode is mutually exclusive with this view ───────────────
  //
  // Four defects found by an audit probe after the first cut of A1, all from
  // the two modes not knowing about each other. Kept as regressions because
  // each one is invisible rather than loud.
  r.section('review mode interaction');

  const enteringOutputs = await s.eval(`
    exitReviewMode();
    if (state.outputsView) document.getElementById('outputsBtn').click();
    await new Promise((res) => setTimeout(res, 300));
    enterReviewMode(null, 'reject');
    await new Promise((res) => setTimeout(res, 300));
    document.getElementById('outputsBtn').click();
    await new Promise((res) => setTimeout(res, 700));
    const controls = document.getElementById('outputsView').querySelector('.view-controls');
    return {
      reviewMode: state.reviewMode,
      controls: getComputedStyle(controls).display,
      sortHasBox: elements.outputsSort.offsetParent !== null,
    };
  `);
  r.check('opening Outputs leaves review mode', enteringOutputs.reviewMode === false);
  r.check('so the outputs filter bar is not blanked by body.review-mode',
    enteringOutputs.controls !== 'none', enteringOutputs.controls);
  r.check('and the sort dropdown has a layout box', enteringOutputs.sortHasBox === true);

  const enteringReview = await s.eval(`
    if (!state.outputsView) document.getElementById('outputsBtn').click();
    await new Promise((res) => setTimeout(res, 400));
    // The classify toast firing while the user browses Outputs.
    enterReviewMode(null, 'reject');
    await new Promise((res) => setTimeout(res, 500));
    return {
      outputsView: state.outputsView,
      photoGallery: getComputedStyle(
        document.querySelector('.gallery-container:not(.outputs-container)')).display,
      reviewBarOnScreen: document.getElementById('reviewBar').offsetParent !== null,
    };
  `);
  r.check('entering review mode leaves the outputs view',
    enteringReview.outputsView === false);
  r.check('so the photo gallery is back on screen',
    enteringReview.photoGallery !== 'none', enteringReview.photoGallery);
  r.check('and review mode actually has a surface',
    enteringReview.reviewBarOnScreen === true);

  // ── freshness and paging ───────────────────────────────────────────
  r.section('freshness + paging');

  const refetched = await s.eval(`
    exitReviewMode();
    if (!state.outputsView) document.getElementById('outputsBtn').click();
    await new Promise((res) => setTimeout(res, 600));
    const seen = [];
    const realFetch = window.fetch;
    window.fetch = (url, opts) => { seen.push(String(url)); return realFetch(url, opts); };
    try {
      document.getElementById('outputsBtn').click();   // leave
      await new Promise((res) => setTimeout(res, 250));
      document.getElementById('outputsBtn').click();   // come back
      await new Promise((res) => setTimeout(res, 800));
    } finally {
      window.fetch = realFetch;
    }
    return seen.filter((u) => u.includes('/api/generations/list')).length;
  `);
  r.check('reopening Outputs refetches the grid, not just the badge',
    refetched >= 1, `${refetched} list calls`);

  const offsetAfterDelete = await s.eval(`
    state.outputs = [
      { gen_id: 'a', creator: 'n', workflow: 'pro', rating: 0, seed: 1, seed_recorded: true,
        url: '/media/a.png', thumb_url: '/media/thumb/a.png', source_rel: 'n/p.jpg',
        positive_prompt: '', negative_prompt: '', steps: 1, cfg: 1, denoise: null,
        mode_e: false, prompt_version: null, created_at: '2026-08-10' },
      { gen_id: 'b', creator: 'n', workflow: 'pro', rating: 0, seed: 2, seed_recorded: true,
        url: '/media/b.png', thumb_url: '/media/thumb/b.png', source_rel: 'n/p.jpg',
        positive_prompt: '', negative_prompt: '', steps: 1, cfg: 1, denoise: null,
        mode_e: false, prompt_version: null, created_at: '2026-08-09' },
    ];
    state.outputsOffset = 2;
    state.outputsTotal = 2;
    renderOutputs({ append: false });
    state.outputDetail = state.outputs[0];
    // 'a' is not a real row, so DELETE answers 404 and the catch runs — assert
    // on the success path by driving the same mutations with a stubbed fetch.
    const realFetch = window.fetch;
    window.fetch = async () => ({ ok: true, status: 200, json: async () => ({}) });
    const realConfirm = window.confirm;
    window.confirm = () => true;
    try {
      await deleteOutput();
      await new Promise((res) => setTimeout(res, 200));
    } finally {
      window.fetch = realFetch;
      window.confirm = realConfirm;
    }
    return { offset: state.outputsOffset, loaded: state.outputs.length };
  `);
  r.check('deleting an output keeps offset equal to the rows still loaded',
    offsetAfterDelete.offset === offsetAfterDelete.loaded,
    `offset=${offsetAfterDelete.offset} loaded=${offsetAfterDelete.loaded}`);

  // ── rating on the grid ─────────────────────────────────────────────
  r.section('card + detail rating');

  const cardRate = await s.eval(`
    state.outputs = [
      { gen_id: 'g-rate', creator: 'nina', workflow: 'pro', rating: 0,
        seed: 1, seed_recorded: true, url: '/media/x.png', thumb_url: '/media/thumb/x.png',
        source_rel: 'nina/photo.jpg', has_source: true, positive_prompt: 'p',
        negative_prompt: '', steps: 32, cfg: 6, denoise: 0.7, mode_e: true,
        prompt_version: 'v2', created_at: '2026-08-10' },
    ];
    state.outputsTotal = 1; state.outputsHasMore = false;
    renderOutputs({ append: false });
    const realFetch = window.fetch;
    const bodies = [];
    window.fetch = async (url, opts) => {
      if (String(url).includes('/api/generation/rate')) {
        bodies.push(JSON.parse(opts.body));
        return { ok: true, status: 200, json: async () => ({}) };
      }
      return realFetch(url, opts);
    };
    try {
      document.querySelector('#outputsGrid .gen-rate-btn.star').click();
      await new Promise((res) => setTimeout(res, 80));
    } finally {
      window.fetch = realFetch;
    }
    const active = document.querySelector('#outputsGrid .gen-rate-btn.active');
    return {
      body: bodies[0],
      active: active ? active.dataset.rating : null,
      detailOpen: document.getElementById('outputDetailModal').style.display,
    };
  `);
  r.check('clicking star on a card PUTs rating 2',
    cardRate.body && cardRate.body.rating === 2 && cardRate.body.gen_id === 'g-rate',
    JSON.stringify(cardRate.body));
  r.check('and paints the star as active', cardRate.active === '2', String(cardRate.active));
  r.check('without opening the detail modal',
    cardRate.detailOpen === 'none' || cardRate.detailOpen === '',
    String(cardRate.detailOpen));

  const gridKey = await s.eval(`
    state.outputsView = true;
    state.outputsFocusedId = 'g-rate';
    state.compareMode = false;
    state.currentGenId = null;
    const realFetch = window.fetch;
    window.fetch = async (url, opts) => {
      if (String(url).includes('/api/generation/rate')) {
        return { ok: true, status: 200, json: async () => ({}) };
      }
      return realFetch(url, opts);
    };
    const realError = console.error;
    console.error = () => {};
    let claimed;
    try {
      claimed = handleGenerationRatingKey({ key: '1' });
      await new Promise((res) => setTimeout(res, 80));
    } finally {
      console.error = realError;
      window.fetch = realFetch;
    }
    const active = document.querySelector('#outputsGrid .gen-rate-btn.active');
    return { claimed, active: active ? active.dataset.rating : null };
  `);
  r.check('1 on the outputs grid keeps the focused card',
    gridKey.claimed === true && gridKey.active === '1',
    JSON.stringify(gridKey));

  const detailRate = await s.eval(`
    const realFetch = window.fetch;
    window.fetch = async (url, opts) => {
      if (String(url).includes('/api/generation/rate')) {
        return { ok: true, status: 200, json: async () => ({}) };
      }
      return realFetch(url, opts);
    };
    try {
      openOutputDetail('g-rate');
      const active = document.querySelector('#outputDetailRating .gen-rate-btn.active');
      const claimed = handleGenerationRatingKey({ key: 'x' });
      await new Promise((res) => setTimeout(res, 80));
      const after = document.querySelector('#outputDetailRating .gen-rate-btn.active');
      closeOutputDetail();
      return {
        reflected: active ? active.dataset.rating : null,
        claimed,
        after: after ? after.dataset.rating : null,
      };
    } finally {
      window.fetch = realFetch;
    }
  `);
  r.check('detail rating control reflects the stored value',
    detailRate.reflected === '1', String(detailRate.reflected));
  r.check('x in the detail rates discard',
    detailRate.claimed === true && detailRate.after === '-1',
    JSON.stringify(detailRate));

  // ── copy parameters into lightbox controls ─────────────────────────
  r.section('copy parameters');

  const copied = await s.eval(`
    const photos = await fetch('/api/photos?limit=1').then((r) => r.json());
    const photo = (photos.photos || [])[0];
    if (!photo) return { ok: false, why: 'no photos' };
    state.photos = [photo];
    state.outputDetail = {
      gen_id: 'g-copy', creator: photo.creator, workflow: 'pro', rating: 0,
      seed: 4242, seed_recorded: true, url: '/media/x.png',
      source_rel: photo.rel_path, has_source: true,
      positive_prompt: 'copied positive', negative_prompt: 'copied negative',
      steps: 41, cfg: 5.5, denoise: 0.62, mode_e: false, checkpoint: 'ckpt',
    };
    await copyOutputParams();
    await new Promise((res) => setTimeout(res, 200));
    return {
      ok: true,
      lightbox: document.getElementById('lightboxModal').style.display,
      positive: document.getElementById('positivePromptText').textContent,
      negative: document.getElementById('negativePromptText').textContent,
      steps: document.getElementById('comfyStepsInput').value,
      cfg: document.getElementById('comfyCfgInput').value,
      denoise: document.getElementById('comfyDenoiseInput').value,
      seed: document.getElementById('comfySeedInput').value,
      locked: document.getElementById('comfySeedLock').checked,
      modeE: document.getElementById('comfyModeECheck').checked,
      outputsView: state.outputsView,
    };
  `);
  r.check('copy-params has a photo to open', copied.ok === true, copied.why || '');
  r.check('opens the lightbox on the source photo', copied.lightbox === 'flex',
    String(copied.lightbox));
  r.check('fills the positive prompt from the generation',
    copied.positive === 'copied positive', copied.positive);
  r.check('fills the negative prompt from the generation',
    copied.negative === 'copied negative', copied.negative);
  r.check('fills steps/cfg/denoise',
    copied.steps === '41' && copied.cfg === '5.5' && copied.denoise === '0.62',
    JSON.stringify({ steps: copied.steps, cfg: copied.cfg, denoise: copied.denoise }));
  r.check('locks the recorded seed into the generate controls',
    copied.locked === true && copied.seed === '4242',
    JSON.stringify({ locked: copied.locked, seed: copied.seed }));
  r.check('restores Mode E from the generation', copied.modeE === false,
    String(copied.modeE));
  r.check('leaves the outputs view so the lightbox is on the photo gallery',
    copied.outputsView === false, String(copied.outputsView));

  await s.eval(`
    if (typeof closeLightbox === 'function') closeLightbox();
    return true;
  `);

  // ── date range + has-source re-query ───────────────────────────────
  r.section('date + has-source filters');

  const extraFilters = await s.eval(`
    const seen = [];
    const realFetch = window.fetch;
    window.fetch = (url, opts) => { seen.push(String(url)); return realFetch(url, opts); };
    try {
      document.getElementById('outputsHasSource').value = '1';
      document.getElementById('outputsHasSource').dispatchEvent(new Event('change'));
      document.getElementById('outputsSince').value = '2026-08-01';
      document.getElementById('outputsSince').dispatchEvent(new Event('change'));
      document.getElementById('outputsUntil').value = '2026-08-31';
      document.getElementById('outputsUntil').dispatchEvent(new Event('change'));
      await new Promise((res) => setTimeout(res, 500));
    } finally {
      window.fetch = realFetch;
    }
    const list = seen.filter((u) => u.includes('/api/generations/list'));
    return {
      hasSource: list.some((u) => /has_source=1/.test(u)),
      since: list.some((u) => /since=2026-08-01/.test(u)),
      until: list.some((u) => /until=2026-08-31/.test(u)),
      urls: list,
    };
  `);
  r.check('has-source filter is sent to the list endpoint', extraFilters.hasSource === true,
    extraFilters.urls.join(' | '));
  r.check('since date is sent to the list endpoint', extraFilters.since === true,
    extraFilters.urls.join(' | '));
  r.check('until date is sent to the list endpoint', extraFilters.until === true,
    extraFilters.urls.join(' | '));

  await sleep(200);
  r.finish(s) || process.exit(1);
  process.exit(0);
})().catch((err) => {
  console.error('SUITE ERROR:', err);
  process.exit(1);
});
