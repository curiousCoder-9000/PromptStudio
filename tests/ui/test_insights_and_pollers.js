/**
 * Stage-1 UI hardening: offline assets, the classify distribution panel, and
 * the poller visibility guard.
 *
 * What is worth asserting in a real browser and nowhere else:
 *
 * - No cross-origin resource is fetched. A python test can grep index.html,
 *   but only the browser proves that nothing — a stylesheet, a font, an
 *   @import three levels deep — actually reaches the network. This app is
 *   local-first and every icon disappears offline when that regresses.
 * - The icon font really resolved. A vendored CSS file that 404s its woff2
 *   still parses fine and still renders empty boxes.
 * - `top_tier_share` is on screen. It was computed, journalled and served over
 *   HTTP while `renderInsights` never read `data.classify` — the exact blind
 *   spot that let the previous classifier ship at 85% on one tier.
 * - The pollers actually stop when the tab hides, and only the ones that were
 *   running come back. Reviving a finished job's chip would be worse than the
 *   wasted polling.
 */
const { Session, Report, sleep } = require('./cdp');

(async () => {
  const s = new Session();
  const r = new Report('insights + pollers');
  await s.connect();
  await s.load();
  await sleep(600);

  // ── offline assets ─────────────────────────────────────────────────
  r.section('offline assets');

  const remote = await s.eval(`
    return performance.getEntriesByType('resource')
      .map((e) => e.name)
      .filter((u) => /^https?:\\/\\//.test(u) && !u.startsWith(location.origin));
  `);
  r.check('page loads nothing cross-origin', remote.length === 0, remote.join(' | '));

  const fontFamily = await s.eval(`
    const i = document.querySelector('i.fa-solid');
    if (!i) return 'no-icon';
    return getComputedStyle(i, null).getPropertyValue('font-family');
  `);
  r.check('icons use the vendored Font Awesome face',
    /Font Awesome/i.test(fontFamily), fontFamily);

  const glyphRendered = await s.eval(`
    // An unresolved icon font collapses the ::before pseudo-element to zero
    // width — that is the offline failure mode, and it is invisible to a
    // font-family check alone.
    const i = document.querySelector('.logo-icon');
    return i ? Math.round(i.getBoundingClientRect().width) : -1;
  `);
  r.check('icon glyph has real width', glyphRendered > 0, String(glyphRendered));

  const uiFontLoaded = await s.eval(`
    return document.fonts.check('600 16px Outfit');
  `);
  r.check('vendored UI font resolved', uiFontLoaded === true, String(uiFontLoaded));

  // ── classify insights ──────────────────────────────────────────────
  r.section('classify insights');

  await s.eval(`openInsightsModal(); return true;`);
  await sleep(900);

  const panel = await s.eval(`
    const body = document.getElementById('insightsBody');
    return {
      hasSection: /Keep \\/ reject classifier/.test(body.textContent),
      rows: body.querySelectorAll('.insights-tier-row').length,
      bars: body.querySelectorAll('.insights-tier-fill').length,
      errorRows: body.querySelectorAll('.insights-tier-row.is-error').length,
      rejectTags: body.querySelectorAll('.insights-tier-tag').length,
      metrics: [...body.querySelectorAll('.insights-metric-label')].map((e) => e.textContent.trim())
    };
  `);
  r.check('classify section renders', panel.hasSection === true, JSON.stringify(panel.hasSection));
  r.check('tier rows rendered', panel.rows > 0, String(panel.rows));
  r.check('top tier share is on screen',
    panel.metrics.includes('Top tier share'), panel.metrics.join(', '));
  r.check('reject rate is on screen',
    panel.metrics.includes('Reject rate'), panel.metrics.join(', '));

  // seed_verdicts.py writes one tier -1 row, so the failure row must appear
  // and must NOT be drawn as a distribution bar — -1 is not a measurement.
  r.check('failed rows get their own row', panel.errorRows === 1, String(panel.errorRows));
  r.check('failure row has no bar', panel.bars === panel.rows - panel.errorRows,
    `${panel.bars} bars / ${panel.rows} rows`);

  // Fixture cut is tier <= 1, so exactly T0 and T1 carry the reject tag.
  r.check('reject tag marks exactly the tiers under the cut',
    panel.rejectTags === 2, String(panel.rejectTags));

  const spread = await s.eval(`
    const body = document.getElementById('insightsBody');
    return body.querySelectorAll('.insights-warn').length;
  `);
  r.check('no saturation warning on a spread fixture', spread === 0, String(spread));

  const saturated = await s.eval(`
    document.getElementById('insightsBody').innerHTML = renderClassifyInsights({
      classified: 100, errors: 0, reject_max_tier: 1,
      distribution: { '0': 5, '1': 90, '2': 5 },
      labels: { '0': 'Unusable', '1': 'Fully modest', '2': 'Normal fashion' },
      reject_rate: 0.95, top_tier_share: 0.9, error_rate: 0
    });
    const body = document.getElementById('insightsBody');
    return {
      warn: body.querySelectorAll('.insights-warn').length,
      metricWarn: body.querySelectorAll('.insights-metric.is-warn').length
    };
  `);
  r.check('saturation above 0.6 raises a warning', saturated.warn === 1, JSON.stringify(saturated));
  r.check('the top-tier metric is flagged too', saturated.metricWarn === 1,
    JSON.stringify(saturated));

  const injected = await s.eval(`
    window.__xss = 0;
    document.getElementById('insightsBody').innerHTML = renderClassifyInsights({
      classified: 3, errors: 0, reject_max_tier: 1,
      distribution: { '0': 3 },
      labels: { '0': '"><img src=x onerror=window.__xss=1>' },
      reject_rate: 1, top_tier_share: 1, error_rate: 0
    });
    return String(window.__xss);
  `);
  r.check('tier labels cannot inject markup', injected === '0', injected);

  const emptyState = await s.eval(`
    return renderClassifyInsights({ classified: 0 }).includes('Nothing classified yet');
  `);
  r.check('empty archive gets a next action', emptyState === true, String(emptyState));

  const errorState = await s.eval(`
    return renderClassifyInsights({ error: 'db locked' }).includes('db locked');
  `);
  r.check('server-side failure is surfaced, not blank', errorState === true, String(errorState));

  await s.eval(`closeInsightsModal(); return true;`);
  await sleep(200);

  // ── poller visibility ──────────────────────────────────────────────
  r.section('poller visibility');

  const setHidden = (v) => `
    Object.defineProperty(document, 'hidden', { configurable: true, get: () => ${v} });
    Object.defineProperty(document, 'visibilityState', {
      configurable: true, get: () => '${v ? 'hidden' : 'visible'}'
    });
    document.dispatchEvent(new Event('visibilitychange'));
  `;

  const before = await s.eval(`
    ensureHealthPolling();
    return { health: state.healthPollTimer !== null, paused: state.pausedPollers.length };
  `);
  r.check('health poller runs while visible', before.health === true, JSON.stringify(before));

  await s.eval(`${setHidden(true)} return true;`);
  await sleep(200);

  const hidden = await s.eval(`
    return { health: state.healthPollTimer, paused: state.pausedPollers.slice() };
  `);
  r.check('hiding the tab clears the health interval', hidden.health === null,
    String(hidden.health));
  r.check('paused pollers are remembered',
    hidden.paused.includes('healthPollTimer'), hidden.paused.join(','));

  const noPollWhileHidden = await s.eval(`
    return [
      state.healthPollTimer, state.comfyPollTimer, state.scrapePollTimer,
      state.syncPollTimer, state.batchPollTimer, state.classifyPollTimer
    ].filter((t) => t !== null).length;
  `);
  r.check('no interval survives a hidden tab', noPollWhileHidden === 0,
    String(noPollWhileHidden));

  await s.startRecordingFetches();
  await s.eval(`${setHidden(false)} return true;`);
  await sleep(700);

  const resumed = await s.eval(`
    return { health: state.healthPollTimer !== null, paused: state.pausedPollers.length };
  `);
  r.check('showing the tab restarts the health poller', resumed.health === true,
    JSON.stringify(resumed));
  r.check('the paused list is cleared on resume', resumed.paused === 0,
    String(resumed.paused));

  const resumeCalls = (await s.fetchLog()).calls;
  r.check('resume refreshes immediately rather than waiting out the interval',
    resumeCalls.some((u) => u.includes('/api/health')), resumeCalls.join(' | '));

  // A poller that was already stopped must stay stopped — resuming it would
  // revive a chip for a job that finished while the tab was in the background.
  const idleStayedIdle = await s.eval(`
    return state.batchPollTimer === null && state.classifyPollTimer === null;
  `);
  r.check('idle pollers are not revived on resume', idleStayedIdle === true,
    String(idleStayedIdle));

  r.finish(s);
  process.exit(process.exitCode || 0);
})().catch((e) => {
  console.error('HARNESS ERROR:', e.message);
  process.exit(2);
});
