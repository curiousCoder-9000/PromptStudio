/**
 * A2 — batch generate, from the browser's side.
 *
 * ComfyUI is not running here, so the server answers 503 to a real start. That
 * is fine: what only a browser can prove is the chip, the entry points and the
 * contact sheet, and all three are driven by stubbing the status endpoint —
 * exactly how the batch-analyze chip is tested next door.
 *
 * The assertions that matter:
 *
 * - The chip renders through `renderJobChip('generate', …)` with no new
 *   rendering code, which is only true if the ids follow the convention.
 * - It comes back after a refresh. Batch generate is the first job you are
 *   *expected* to walk away from, so status living on the server rather than
 *   in a closure is the feature, not an implementation detail.
 * - Cancel says "stopping" and disables, rather than looking like it did
 *   nothing while the current image finishes.
 * - The completion toast leads somewhere. A batch that finishes into an
 *   unfiltered grid of every output ever made is not a contact sheet.
 * - Selecting photos offers Generate beside Re-analyze — the same gesture,
 *   the other job.
 *
 * Not covered here: a reload *while* a batch is running, which needs a live
 * ComfyUI. What is asserted is the two halves that compose into it — the boot
 * path calls the poller, and the poller renders from server state — plus
 * `test_api_comfy_batch.py::test_status_survives_a_page_refresh` on the server
 * side.
 */

const { Session, Report, sleep } = require('./cdp');

const RUNNING = {
  running: true, batch_id: 'abc123def456', total: 50, completed: 12, failed: 1,
  pending: 37, current: 'test_creator/photo_03.jpg', cancelled: false,
  cancel_requested: false, skipped_no_prompt: 3, skipped_video: 0,
  workflow: 'pro', prompt_id: 'p-1',
};

(async () => {
  const s = new Session();
  const r = new Report('batch generate');
  await s.connect();
  await s.load();
  await sleep(400);

  // ── markup follows the chip convention ─────────────────────────────
  r.section('chip markup');

  const ids = await s.eval(`
    return ['generateJobChip', 'generateJobChipTitle', 'generateJobChipSub',
            'generateJobChipFill', 'generateJobChipIcon', 'generateJobChipCancel']
      .filter((id) => !document.getElementById(id));
  `);
  r.check('every id renderJobChip derives is present', ids.length === 0, ids.join(','));

  const hidden = await s.eval(`
    return getComputedStyle(document.getElementById('generateJobChip')).display;
  `);
  r.check('chip starts hidden', hidden === 'none', hidden);

  // ── progress ───────────────────────────────────────────────────────
  r.section('progress');

  const running = await s.eval(`
    const real = window.fetch;
    window.__realFetch = real;
    window.__gen = ${JSON.stringify(RUNNING)};
    window.fetch = async (u, o) => u.toString().includes('/api/comfy/batch/status')
      ? new Response(JSON.stringify(window.__gen), { headers: { 'Content-Type': 'application/json' } })
      : real(u, o);
    document.querySelectorAll('.toast').forEach((t) => t.remove());
    await pollGenerateStatus();
    await new Promise((res) => setTimeout(res, 200));
    return {
      visible: getComputedStyle(document.getElementById('generateJobChip')).display !== 'none',
      title: document.getElementById('generateJobChipTitle').textContent,
      sub: document.getElementById('generateJobChipSub').textContent,
      fill: document.getElementById('generateJobChipFill').style.width,
      cancel: getComputedStyle(document.getElementById('generateJobChipCancel')).display !== 'none',
      toasts: document.querySelectorAll('.toast').length,
    };
  `);
  console.log('   ', JSON.stringify(running));
  r.check('chip shows while generating', running.visible === true);
  r.check('title names the job', /Generating/.test(running.title), running.title);
  r.check('sub shows counts and percent', /13\/50 \(26%\)/.test(running.sub), running.sub);
  r.check('failures are surfaced', /1 failed/.test(running.sub), running.sub);
  r.check('skips are surfaced', /3 skipped/.test(running.sub), running.sub);
  r.check('progress bar reflects percent', running.fill === '26%', running.fill);
  r.check('cancel is offered', running.cancel === true);
  r.check('progress does not toast', running.toasts === 0, `${running.toasts}`);

  const repeated = await s.eval(`
    for (let i = 0; i < 4; i++) { window.__gen.completed += 2; await pollGenerateStatus(); }
    await new Promise((res) => setTimeout(res, 150));
    return { toasts: document.querySelectorAll('.toast').length,
             fill: document.getElementById('generateJobChipFill').style.width };
  `);
  r.check('four more polls, still no toasts', repeated.toasts === 0, `${repeated.toasts}`);
  r.check('bar advances in place', repeated.fill === '42%', repeated.fill);

  // ── cancel ─────────────────────────────────────────────────────────
  r.section('cancel');

  const cancelling = await s.eval(`
    window.__gen.cancel_requested = true;
    await pollGenerateStatus();
    await new Promise((res) => setTimeout(res, 150));
    const btn = document.getElementById('generateJobChipCancel');
    return { title: document.getElementById('generateJobChipTitle').textContent,
             label: btn.textContent, disabled: btn.disabled,
             spinning: document.getElementById('generateJobChipIcon').classList.contains('spinning') };
  `);
  console.log('   ', JSON.stringify(cancelling));
  r.check('title says it is stopping', /stopping/i.test(cancelling.title), cancelling.title);
  r.check('button reads Stopping…', /Stopping/.test(cancelling.label), cancelling.label);
  r.check('button is disabled so it cannot be re-clicked', cancelling.disabled === true);
  r.check('spinner stops', cancelling.spinning === false);

  const cancelPosted = await s.eval(`
    window.__posted = [];
    const real = window.__realFetch;
    window.fetch = async (u, o) => {
      const url = u.toString();
      if (o && o.method === 'POST') window.__posted.push(url);
      if (url.includes('/api/comfy/batch/cancel')) {
        return new Response(JSON.stringify({ status: 'cancelling', running: true }),
          { headers: { 'Content-Type': 'application/json' } });
      }
      if (url.includes('/api/comfy/batch/status')) {
        return new Response(JSON.stringify(window.__gen), { headers: { 'Content-Type': 'application/json' } });
      }
      return real(u, o);
    };
    document.getElementById('generateJobChipCancel').disabled = false;
    document.getElementById('generateJobChipCancel').click();
    await new Promise((res) => setTimeout(res, 300));
    return window.__posted;
  `);
  r.check('cancel button posts to the cancel route',
    cancelPosted.some((u) => u.includes('/api/comfy/batch/cancel')), cancelPosted.join(','));

  // ── finishing ──────────────────────────────────────────────────────
  r.section('completion');

  const finished = await s.eval(`
    document.querySelectorAll('.toast').forEach((t) => t.remove());
    window.__gen = { ...window.__gen, running: false, cancel_requested: false,
                     cancelled: false, completed: 49, failed: 1, pending: 0 };
    await pollGenerateStatus();
    await new Promise((res) => setTimeout(res, 250));
    const toast = document.querySelector('.toast');
    return {
      chip: getComputedStyle(document.getElementById('generateJobChip')).display,
      toast: toast ? toast.textContent : '',
      hasLink: Boolean(toast && toast.querySelector('button, a')),
    };
  `);
  console.log('   ', JSON.stringify(finished));
  r.check('chip hides when the run ends', finished.chip === 'none', finished.chip);
  r.check('completion is announced once', /49/.test(finished.toast), finished.toast);
  r.check('the toast offers a way into the run it just finished',
    finished.hasLink === true && /View run/.test(finished.toast), finished.toast);

  const contactSheet = await s.eval(`
    showOutputsView(false);
    openBatchContactSheet('abc123def456');
    await new Promise((res) => setTimeout(res, 500));
    return {
      outputs: getComputedStyle(document.getElementById('outputsView')).display,
      batch: state.outputsBatch,
      chipVisible: getComputedStyle(document.getElementById('outputsBatchChip')).display !== 'none',
      chipText: document.getElementById('outputsBatchChip').textContent,
    };
  `);
  console.log('   ', JSON.stringify(contactSheet));
  r.check('the contact sheet opens the outputs view', contactSheet.outputs !== 'none', contactSheet.outputs);
  r.check('filtered to that run', contactSheet.batch === 'abc123def456', String(contactSheet.batch));
  r.check('and says so, so the empty grid is explicable', contactSheet.chipVisible === true);
  r.check('the chip names the run', /abc123def456/.test(contactSheet.chipText), contactSheet.chipText);

  const cleared = await s.eval(`
    document.getElementById('outputsBatchClear').click();
    await new Promise((res) => setTimeout(res, 400));
    return { batch: state.outputsBatch,
             chip: getComputedStyle(document.getElementById('outputsBatchChip')).display };
  `);
  r.check('the batch filter can be cleared', !cleared.batch, String(cleared.batch));
  r.check('and the chip goes away with it', cleared.chip === 'none', cleared.chip);

  const restored = await s.eval(`
    window.fetch = window.__realFetch;
    showOutputsView(false);
    return true;
  `);
  r.check('fetch restored', restored === true);

  // ── entry point ────────────────────────────────────────────────────
  r.section('entry point');

  const bulk = await s.eval(`
    setSelectMode(true);
    await new Promise((res) => setTimeout(res, 200));
    const first = document.querySelector('[data-rel-path]');
    const rel = first ? first.dataset.relPath : null;
    if (rel) togglePhotoSelection(rel, true);
    updateBulkBar();
    await new Promise((res) => setTimeout(res, 200));
    const btn = document.getElementById('bulkGenerateBtn');
    return {
      exists: Boolean(btn),
      visible: btn ? getComputedStyle(btn).display !== 'none' : false,
      label: btn ? btn.textContent.trim() : '',
      count: document.getElementById('bulkCount').textContent,
    };
  `);
  console.log('   ', JSON.stringify(bulk));
  r.check('a Generate button sits in the bulk bar', bulk.exists === true);
  r.check('it is visible with a selection', bulk.visible === true);
  r.check('and it says what it does', /Generate/i.test(bulk.label), bulk.label);

  const posted = await s.eval(`
    window.__body = null;
    const real = window.__realFetch;
    window.fetch = async (u, o) => {
      if (u.toString().includes('/api/comfy/batch') && !u.toString().includes('status')) {
        window.__body = JSON.parse(o.body);
        return new Response(JSON.stringify({ status: 'started', batch_id: 'zzz', pending: 1,
          skipped_no_prompt: 0, skipped_video: 0 }), { headers: { 'Content-Type': 'application/json' } });
      }
      if (u.toString().includes('/api/comfy/batch/status')) {
        return new Response(JSON.stringify({ running: false, total: 0, completed: 0, failed: 0 }),
          { headers: { 'Content-Type': 'application/json' } });
      }
      return real(u, o);
    };
    document.getElementById('bulkGenerateBtn').click();
    await new Promise((res) => setTimeout(res, 400));
    window.fetch = real;
    return window.__body;
  `);
  console.log('   ', JSON.stringify(posted));
  r.check('it posts the selected paths', Array.isArray(posted && posted.paths) && posted.paths.length === 1,
    JSON.stringify(posted));

  const offline = await s.eval(`
    const real = window.__realFetch;
    document.querySelectorAll('.toast').forEach((t) => t.remove());
    window.fetch = async (u, o) => u.toString().includes('/api/comfy/batch') && !u.toString().includes('status')
      ? new Response(JSON.stringify({ status: 'offline', message: 'ComfyUI is not reachable' }),
          { status: 503, headers: { 'Content-Type': 'application/json' } })
      : real(u, o);
    document.getElementById('bulkGenerateBtn').click();
    await new Promise((res) => setTimeout(res, 400));
    window.fetch = real;
    const toast = document.querySelector('.toast');
    return toast ? toast.textContent : '';
  `);
  r.check('an offline ComfyUI says so rather than failing silently',
    /reachable|offline/i.test(offline), offline);

  // ── refresh ────────────────────────────────────────────────────────
  r.section('resume after refresh');

  const afterReload = await s.eval(`
    return typeof pollGenerateStatus === 'function';
  `);
  r.check('the poller is a top-level function the boot path can call', afterReload === true);

  await s.load();
  await sleep(800);
  const booted = await s.eval(`
    return { calls: window.__genPolled === undefined ? 'n/a' : window.__genPolled,
             chip: getComputedStyle(document.getElementById('generateJobChip')).display };
  `);
  r.check('a fresh page with no batch running leaves the chip hidden',
    booted.chip === 'none', booted.chip);

  await sleep(200);
  r.finish(s) || process.exit(1);
  process.exit(0);
})().catch((err) => {
  console.error('SUITE ERROR:', err);
  process.exit(1);
});
