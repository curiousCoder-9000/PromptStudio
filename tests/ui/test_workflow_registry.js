/**
 * A4 — the workflow picker.
 *
 * The Python suite proves the registry builds the right graph. What only a
 * browser proves:
 *
 * - Both pickers are actually populated from `/api/workflows`. Before A4 the
 *   graph was inferred from which of three buttons you pressed, so a third
 *   workflow was unreachable from the UI no matter what the server supported.
 * - The picked name is what gets *sent* — to the one-shot route and to the
 *   batch route. A picker that renders correctly and posts `pro` anyway is the
 *   exact failure this feature exists to prevent.
 * - Labels come from a JSON file the user writes, so they are third-party text
 *   (hard rule 7). Options are built with textContent; this pins that.
 * - A workflow list that fails to load must not leave a picker with no options
 *   and a Generate button that posts nothing.
 */
const { Session, Report, sleep } = require('./cdp');

const readPicker = (id) => `
  const el = document.getElementById('${id}');
  if (!el) return { present: false };
  return {
    present: true,
    value: el.value,
    options: [...el.options].map((o) => ({ value: o.value, label: o.textContent })),
    className: el.className,
  };
`;

(async () => {
  const s = new Session();
  const r = new Report('workflow registry');
  await s.connect();
  await s.load();
  await sleep(600);

  // ── the endpoint ───────────────────────────────────────────────────
  r.section('/api/workflows is the registry, not a hardcoded pair');

  const listed = await s.eval(`
    const res = await fetch('/api/workflows');
    const data = await res.json();
    return { status: res.status, def: data.default,
             workflows: data.workflows, keys: Object.keys(data).sort() };
  `);
  r.check('endpoint answers', listed.status === 200, String(listed.status));
  r.check('shape is { workflows, default }',
    listed.keys.join(',') === 'default,workflows', listed.keys.join(','));
  const names = (listed.workflows || []).map((w) => w.name);
  r.check('pro is a registry entry', names.includes('pro'), names.join(','));
  r.check('txt2img is a registry entry', names.includes('txt2img'), names.join(','));
  r.check('default is one of them', names.includes(listed.def), String(listed.def));
  r.check('every entry carries name/label/kind',
    (listed.workflows || []).every((w) => w.name && w.label && w.kind),
    JSON.stringify(listed.workflows));
  r.check('no node ids leak to the client',
    !JSON.stringify(listed.workflows || []).includes('"node"'),
    JSON.stringify(listed.workflows));

  // ── the lightbox picker ────────────────────────────────────────────
  r.section('lightbox generate panel has a picker');

  const lightbox = await s.eval(readPicker('comfyWorkflowSelect'));
  r.check('picker exists', lightbox.present === true);
  r.check('it uses the house select style',
    (lightbox.className || '').includes('sort-select'), lightbox.className);
  r.check('populated from the registry',
    (lightbox.options || []).map((o) => o.value).sort().join(',') === 'pro,txt2img',
    JSON.stringify(lightbox.options));
  r.check('option text is the registry label, not the id',
    (lightbox.options || []).some((o) => o.value === 'pro' && o.label !== 'pro'),
    JSON.stringify(lightbox.options));
  r.check('preselects the server-declared default',
    lightbox.value === listed.def, `${lightbox.value} vs ${listed.def}`);

  // ── the batch picker ───────────────────────────────────────────────
  r.section('the batch flow has the same picker');

  const bulk = await s.eval(readPicker('bulkWorkflowSelect'));
  r.check('picker exists in the bulk bar', bulk.present === true);
  r.check('populated from the same registry',
    (bulk.options || []).map((o) => o.value).sort().join(',') === 'pro,txt2img',
    JSON.stringify(bulk.options));
  r.check('preselects the default too', bulk.value === listed.def, String(bulk.value));

  // ── the picked name is what gets sent ──────────────────────────────
  r.section('the picked workflow reaches the server');

  const oneShot = await s.eval(`
    const sent = [];
    const real = window.fetch;
    window.fetch = (url, opts) => {
      if (String(url).startsWith('/api/comfy/generate')) {
        sent.push(JSON.parse((opts || {}).body || '{}'));
        return Promise.resolve(new Response(
          JSON.stringify({ status: 'started', seed: 1, workflow: 'x' }),
          { status: 200, headers: { 'Content-Type': 'application/json' } }));
      }
      return real(url, opts);
    };
    try {
      state.comfyOnline = true;
      state.lightboxIndex = 0;
      state.currentPromptData = { positive_prompt: 'p', negative_prompt: 'n', parameters: {} };
      document.getElementById('comfyWorkflowSelect').value = 'txt2img';
      document.getElementById('comfyWorkflowSelect').dispatchEvent(new Event('change'));
      await sendToComfy('pro');
      await new Promise((res) => setTimeout(res, 200));
    } finally {
      window.fetch = real;
    }
    return sent;
  `);
  r.check('one generate request was posted', oneShot.length === 1, JSON.stringify(oneShot));
  r.check('body carries the picked workflow, not the button that was pressed',
    oneShot[0] && oneShot[0].workflow === 'txt2img', JSON.stringify(oneShot[0]));

  const batch = await s.eval(`
    const sent = [];
    const real = window.fetch;
    window.fetch = (url, opts) => {
      if (String(url).startsWith('/api/comfy/batch')) {
        sent.push(JSON.parse((opts || {}).body || '{}'));
        return Promise.resolve(new Response(
          JSON.stringify({ status: 'nothing_to_do' }),
          { status: 200, headers: { 'Content-Type': 'application/json' } }));
      }
      return real(url, opts);
    };
    try {
      document.getElementById('bulkWorkflowSelect').value = 'txt2img';
      state.selectedPaths = new Set(['test_creator/photo_01.jpg']);
      startBulkGenerate();
      await new Promise((res) => setTimeout(res, 200));
    } finally {
      window.fetch = real;
      state.selectedPaths = new Set();
    }
    return sent;
  `);
  r.check('one batch request was posted', batch.length === 1, JSON.stringify(batch));
  r.check('batch body carries the picked workflow',
    batch[0] && batch[0].workflow === 'txt2img', JSON.stringify(batch[0]));

  // ── hard rule 7 ────────────────────────────────────────────────────
  r.section('labels are user-written JSON, so they are escaped');

  const escaped = await s.eval(`
    const hostile = '<img src=x onerror="window.__pwned=1">';
    state.workflows = [{ name: 'evil', label: hostile, kind: 'txt2img' }];
    state.workflowDefault = 'evil';
    renderWorkflowPickers();
    const el = document.getElementById('comfyWorkflowSelect');
    return {
      text: el.options[0].textContent,
      html: el.innerHTML,
      pwned: Boolean(window.__pwned),
      imgs: document.querySelectorAll('#comfyWorkflowSelect img').length,
    };
  `);
  r.check('the label is shown literally', escaped.text.includes('<img'), escaped.text);
  r.check('no element was created from it', escaped.imgs === 0, String(escaped.imgs));
  r.check('the markup is escaped in the DOM',
    !escaped.html.includes('<img'), escaped.html.slice(0, 120));
  r.check('nothing executed', escaped.pwned === false);

  // ── degraded registry ──────────────────────────────────────────────
  r.section('a failed workflow fetch still leaves a usable picker');

  const degraded = await s.eval(`
    state.workflows = [];
    state.workflowDefault = '';
    renderWorkflowPickers();
    const el = document.getElementById('comfyWorkflowSelect');
    return { count: el.options.length, value: el.value,
             bulk: document.getElementById('bulkWorkflowSelect').options.length };
  `);
  r.check('the lightbox picker still offers something',
    degraded.count > 0, String(degraded.count));
  r.check('and it is a real workflow name', degraded.value === 'pro', degraded.value);
  r.check('so does the bulk picker', degraded.bulk > 0, String(degraded.bulk));

  // Put the real registry back for any suite that runs after this one.
  await s.eval('return fetchWorkflows();');

  r.finish(s) || process.exit(1);
  process.exit(0);
})().catch((err) => {
  console.error('SUITE ERROR:', err);
  process.exit(1);
});
