/**
 * Per-lane scrape chips — see docs/design_scrape_lanes.md §7.
 *
 * Driven entirely by stubbing `/api/scrape/status`: what is under test is the
 * chip rendering and the lane-scoped Cancel/Resume wiring, not the scrapers.
 *
 * The load-bearing assertion is `Cancel posts only its own lane` — with the
 * pre-lane single chip, one Cancel button stopped every running source.
 */

const { Session, Report, sleep } = require('./cdp');

const laneStatus = (lanes, extra = {}) =>
  JSON.stringify({
    lanes,
    pending: Object.values(lanes).flatMap((l) => l.pending || []),
    running_jobs: Object.values(lanes).filter((l) => l.running_job).map((l) => l.running_job),
    running_job: Object.values(lanes).find((l) => l.running_job)?.running_job || null,
    paused: Object.values(lanes).every((l) => l.paused),
    history: [],
    sync: { running: false, lanes: {} },
    stats: { completed_today: 2, downloaded_today: 20 },
    ...extra,
  });

const stub = (lanes, extra) => `
  const _base = window.__originalFetch || window.fetch;
  window.fetch = async (u, o) => u.toString().includes('/api/scrape/status')
    ? new Response(${JSON.stringify(laneStatus(lanes, extra))},
        { headers: { 'Content-Type': 'application/json' } })
    : _base(u, o);
  state.scrapeChipDismissed.clear();
  await pollScrapeStatus();
  await new Promise(r => setTimeout(r, 250));
`;

const RUNNING_IG = {
  source: 'instagram',
  paused: false,
  pause_reason: '',
  pending: [{ username: 'next_one', mode: 'full' }],
  running_job: { username: 'model_sera', mode: 'full', deep: true },
};
const PAUSED_X = {
  source: 'x',
  paused: true,
  pause_reason: 'cookies expired',
  pending: [{ username: 'kaya', mode: 'full' }],
  running_job: null,
};
const RUNNING_REDDIT = {
  source: 'reddit',
  paused: false,
  pause_reason: '',
  pending: [],
  running_job: { username: 'r/streetwear', mode: 'full', deep: false },
};

(async () => {
  const s = new Session();
  const r = new Report('scrape lanes');
  await s.connect();
  await s.load();

  // ── three chips ────────────────────────────────────────────────────
  r.section('one chip per active lane');
  const three = await s.eval(`
    ${stub({ instagram: RUNNING_IG, x: PAUSED_X, reddit: RUNNING_REDDIT })}
    const chips = [...document.querySelectorAll('#scrapeLaneChips .scrape-job-chip')]
      .filter(c => getComputedStyle(c).display !== 'none');
    return {
      count: chips.length,
      sources: chips.map(c => c.dataset.source),
      ids: chips.map(c => c.id),
      titles: chips.map(c => c.querySelector('[data-role="title"]').textContent),
      subs: chips.map(c => c.querySelector('[data-role="sub"]').textContent),
      pausedClasses: chips.map(c => c.classList.contains('paused')),
      resumeVisible: chips.map(c =>
        getComputedStyle(c.querySelector('[data-role="resume"]')).display !== 'none'),
      cancelVisible: chips.map(c =>
        getComputedStyle(c.querySelector('[data-role="cancel"]')).display !== 'none'),
    };
  `);
  console.log('   ', JSON.stringify(three, null, 1));

  r.check('three chips rendered', three.count === 3, String(three.count));
  r.check('one per source', three.sources.join(',') === 'instagram,reddit,x',
    three.sources.join(','));
  r.check(
    'Instagram keeps the primary element id',
    three.ids.includes('scrapeJobChip'),
    three.ids.join(',')
  );
  r.check(
    'other lanes get suffixed ids',
    three.ids.includes('scrapeJobChip_x') && three.ids.includes('scrapeJobChip_reddit'),
    three.ids.join(',')
  );

  const byIdx = (src) => three.sources.indexOf(src);
  r.check('IG title names its running creator',
    /@model_sera/.test(three.titles[byIdx('instagram')]), three.titles[byIdx('instagram')]);
  r.check('Reddit title names its own target',
    /r\/streetwear/.test(three.titles[byIdx('reddit')]), three.titles[byIdx('reddit')]);
  r.check('X title carries its pause reason',
    /cookies expired/.test(three.titles[byIdx('x')]), three.titles[byIdx('x')]);
  r.check('every title is labelled by platform',
    three.titles.every(t => /Instagram|Reddit|X/i.test(t)), three.titles.join(' | '));

  r.check('only the paused lane is styled paused',
    three.pausedClasses[byIdx('x')] === true &&
    three.pausedClasses[byIdx('instagram')] === false,
    JSON.stringify(three.pausedClasses));
  r.check('Resume offered only on the paused lane',
    three.resumeVisible[byIdx('x')] === true &&
    three.resumeVisible[byIdx('instagram')] === false,
    JSON.stringify(three.resumeVisible));
  r.check('Cancel offered only on running lanes',
    three.cancelVisible[byIdx('instagram')] === true &&
    three.cancelVisible[byIdx('reddit')] === true &&
    three.cancelVisible[byIdx('x')] === false,
    JSON.stringify(three.cancelVisible));

  r.section('chips stack rather than overlap');
  const stackOk = await s.eval(`
    const rects = [...document.getElementById('jobChipStack').querySelectorAll('.scrape-job-chip')]
      .filter(c => getComputedStyle(c).display !== 'none')
      .map(c => c.getBoundingClientRect());
    let overlap = false;
    for (let i = 0; i < rects.length; i++)
      for (let j = i + 1; j < rects.length; j++)
        if (!(rects[i].bottom <= rects[j].top || rects[j].bottom <= rects[i].top)) overlap = true;
    return { n: rects.length, overlap };
  `);
  r.check('no two chips overlap', stackOk.overlap === false, JSON.stringify(stackOk));

  // ── lane-scoped actions ────────────────────────────────────────────
  r.section('Cancel posts only its own lane');
  const cancelBody = await s.eval(`
    ${stub({ instagram: RUNNING_IG, reddit: RUNNING_REDDIT })}
    const posted = [];
    window.fetch = async (u, o) => {
      const url = u.toString();
      if (url.includes('/api/sync/cancel')) {
        posted.push(JSON.parse((o && o.body) || '{}'));
        return new Response(JSON.stringify({ status: 'cancelling' }),
          { headers: { 'Content-Type': 'application/json' } });
      }
      if (url.includes('/api/scrape/status')) {
        return new Response(${JSON.stringify(laneStatus({ instagram: RUNNING_IG, reddit: RUNNING_REDDIT }))},
          { headers: { 'Content-Type': 'application/json' } });
      }
      return _base(u, o);
    };
    document.querySelector('#scrapeJobChip_reddit [data-role="cancel"]').click();
    await new Promise(r => setTimeout(r, 400));
    return posted;
  `);
  console.log('   ', JSON.stringify(cancelBody));
  r.check('exactly one cancel posted', cancelBody.length === 1, String(cancelBody.length));
  r.check('cancel names the reddit lane', cancelBody[0] && cancelBody[0].source === 'reddit',
    JSON.stringify(cancelBody[0]));

  r.section('Resume posts only its own lane');
  const resumeBody = await s.eval(`
    ${stub({ instagram: RUNNING_IG, x: PAUSED_X })}
    const posted = [];
    window.fetch = async (u, o) => {
      const url = u.toString();
      if (url.includes('/api/scrape/resume')) {
        posted.push(JSON.parse((o && o.body) || '{}'));
        return new Response(JSON.stringify({ status: 'resumed', drain_started: true }),
          { headers: { 'Content-Type': 'application/json' } });
      }
      if (url.includes('/api/scrape/status')) {
        return new Response(${JSON.stringify(laneStatus({ instagram: RUNNING_IG, x: PAUSED_X }))},
          { headers: { 'Content-Type': 'application/json' } });
      }
      return _base(u, o);
    };
    document.querySelector('#scrapeJobChip_x [data-role="resume"]').click();
    await new Promise(r => setTimeout(r, 400));
    return posted;
  `);
  console.log('   ', JSON.stringify(resumeBody));
  r.check('resume names the x lane', resumeBody[0] && resumeBody[0].source === 'x',
    JSON.stringify(resumeBody[0]));

  r.section('dismissing one chip leaves the others');
  const dismissed = await s.eval(`
    ${stub({ instagram: RUNNING_IG, x: PAUSED_X, reddit: RUNNING_REDDIT })}
    document.querySelector('#scrapeJobChip_reddit [data-role="dismiss"]').click();
    await new Promise(r => setTimeout(r, 150));
    const vis = (id) => {
      const el = document.getElementById(id);
      return el ? getComputedStyle(el).display !== 'none' : false;
    };
    return { ig: vis('scrapeJobChip'), x: vis('scrapeJobChip_x'),
             reddit: vis('scrapeJobChip_reddit'),
             tracked: [...state.scrapeChipDismissed] };
  `);
  console.log('   ', JSON.stringify(dismissed));
  r.check('dismissed lane hidden', dismissed.reddit === false);
  r.check('other lanes still visible', dismissed.ig === true && dismissed.x === true,
    JSON.stringify(dismissed));
  r.check('dismissal tracked per lane', dismissed.tracked.join(',') === 'reddit',
    dismissed.tracked.join(','));

  r.section('dismissing a paused chip survives the next poll');
  const pauseDismiss = await s.eval(`
    ${stub({
      instagram: {
        source: 'instagram',
        paused: true,
        pause_reason: 'Paused after Instagram 429 — do not auto-retry web_profile_info',
        pending: [],
        running_job: null,
      },
    })}
    document.querySelector('#scrapeJobChip [data-role="dismiss"]').click();
    await pollScrapeStatus();
    await new Promise((r) => setTimeout(r, 200));
    const el = document.getElementById('scrapeJobChip');
    return {
      visible: el ? getComputedStyle(el).display !== 'none' : false,
      tracked: [...state.scrapeChipDismissed],
    };
  `);
  r.check('paused Instagram chip stays hidden after Hide', pauseDismiss.visible === false);
  r.check('instagram remains in the dismissed set',
    pauseDismiss.tracked.includes('instagram'), pauseDismiss.tracked.join(','));

  // ── idle lanes disappear ───────────────────────────────────────────
  r.section('a lane going idle removes its chip');
  const idled = await s.eval(`
    ${stub({ instagram: RUNNING_IG })}
    const vis = (id) => {
      const el = document.getElementById(id);
      return el ? getComputedStyle(el).display !== 'none' : false;
    };
    return { ig: vis('scrapeJobChip'), x: vis('scrapeJobChip_x'),
             reddit: vis('scrapeJobChip_reddit') };
  `);
  console.log('   ', JSON.stringify(idled));
  r.check('still-busy lane keeps its chip', idled.ig === true);
  r.check('idle lanes hidden', idled.x === false && idled.reddit === false,
    JSON.stringify(idled));

  // ── one-shot lock is Instagram-only ────────────────────────────────
  r.section('a busy Reddit lane does not lock Saved/Following');
  const oneshot = await s.eval(`
    ${stub({ reddit: RUNNING_REDDIT })}
    const saved = document.getElementById('syncSavedBtn');
    return {
      savedDisabled: saved ? saved.disabled : null,
      banner: (document.getElementById('syncOneShotBanner') || {}).textContent || '',
    };
  `);
  console.log('   ', JSON.stringify(oneshot));
  r.check('Saved stays enabled while only Reddit runs', oneshot.savedDisabled === false,
    String(oneshot.savedDisabled));

  r.section('a busy Instagram lane does lock them');
  const igLock = await s.eval(`
    ${stub({ instagram: RUNNING_IG })}
    const saved = document.getElementById('syncSavedBtn');
    return { savedDisabled: saved ? saved.disabled : null };
  `);
  r.check('Saved disabled while Instagram runs', igLock.savedDisabled === true,
    String(igLock.savedDisabled));

  // ── back-compat ────────────────────────────────────────────────────
  r.section('a pre-lane (flat) status payload still renders');
  const flat = await s.eval(`
    const real = window.__originalFetch || window.fetch;
    window.fetch = async (u, o) => u.toString().includes('/api/scrape/status')
      ? new Response(JSON.stringify({
          paused: false, pause_reason: '', pending: [{ username: 'legacy', mode: 'full' }],
          history: [], running_job: { username: 'legacy_run', mode: 'full', deep: true },
          sync: { running: true, job_type: 'creator_queue', progress: 'Downloading…' },
          stats: {},
        }), { headers: { 'Content-Type': 'application/json' } })
      : real(u, o);
    state.scrapeChipDismissed.clear();
    await pollScrapeStatus();
    await new Promise(r => setTimeout(r, 250));
    const chip = document.getElementById('scrapeJobChip');
    return {
      visible: chip ? getComputedStyle(chip).display !== 'none' : false,
      title: chip ? chip.querySelector('[data-role="title"]').textContent : '',
    };
  `);
  console.log('   ', JSON.stringify(flat));
  r.check('flat payload synthesises the Instagram lane', flat.visible === true);
  r.check('and shows its running creator', /@legacy_run/.test(flat.title), flat.title);

  r.section('instagram backend hint');
  const hint = await s.eval(`
    if (elements.scrapeSourceSelect) elements.scrapeSourceSelect.value = 'instagram';
    applyInstagramBackendFrom({
      instagram_backend: 'gallery-dl',
      instagram_cookies: { mode: 'browser', browser: 'brave', ready: true },
    });
    return document.getElementById('scrapeSourceHint')
      ? document.getElementById('scrapeSourceHint').textContent
      : '';
  `);
  r.check('gallery-dl hint names brave cookies',
    /gallery-dl/i.test(hint) && /brave/i.test(hint), hint);

  await sleep(200);
  r.finish(s) || process.exit(1);
  process.exit(0);
})().catch((err) => {
  console.error('SUITE ERROR:', err);
  process.exit(1);
});
