/**
 * Dialog behaviour and the accessibility tree.
 *
 * Measured before the fix, at 1440x900, driving six dialogs:
 *
 * - 5 of 6 had no `role`, no `aria-modal` and no name at all. A screen reader
 *   announced the gallery behind them, not a dialog.
 * - Focus stayed on <body> on open in 5 of 6 (only #newCreatorModal focused
 *   its input), and Tab from there walked into the gallery behind the card.
 * - After Escape, `document.activeElement` was <body> in **all six** — the
 *   control you opened the dialog from was gone, so the next Tab restarted at
 *   the top of the document.
 * - Escape did nothing at all in #insightsModal and #activityModal. Neither
 *   appeared in the prioritised Escape chain, so a 792px panel had no
 *   keyboard exit.
 * - #syncModal's only visible close sat at y=995 on a 900px viewport.
 *
 * None of that is visible in the markup: the Escape chain reads complete, and
 * the roles are absent rather than wrong. It takes a driven pass to see it.
 */
const { Session, Report, sleep } = require('./cdp');

/** Accessible name, near enough for the sources this app actually uses. */
const HELPERS = `
  window.__accName = (el) => {
    if (el.getAttribute('aria-label')) return { name: el.getAttribute('aria-label').trim(), from: 'aria-label' };
    const lb = el.getAttribute('aria-labelledby');
    if (lb) {
      const t = document.getElementById(lb);
      if (t && t.textContent.trim()) return { name: t.textContent.trim(), from: 'labelledby' };
      return { name: '', from: 'labelledby-broken' };
    }
    const txt = (el.textContent || '').replace(/\\s+/g, ' ').trim();
    if (txt) return { name: txt, from: 'text' };
    if (el.getAttribute('title')) return { name: el.getAttribute('title').trim(), from: 'title' };
    return { name: '', from: 'none' };
  };
  window.__vis = (el) => {
    if (!el) return false;
    const s = getComputedStyle(el);
    if (s.display === 'none' || s.visibility === 'hidden' || Number(s.opacity) === 0) return false;
    const r = el.getBoundingClientRect();
    return r.width > 0 && r.height > 0;
  };
  return true;
`;

(async () => {
  const s = new Session();
  await s.connect();
  await s.send('Emulation.setDeviceMetricsOverride',
    { width: 1440, height: 900, deviceScaleFactor: 1, mobile: false });
  await s.load();
  await s.eval(HELPERS);

  const r = new Report('dialogs and the accessibility tree');

  /** A real Tab keypress — synthetic events do not move focus. */
  const tab = async (shift = false) => {
    for (const type of ['keyDown', 'keyUp']) {
      await s.send('Input.dispatchKeyEvent', {
        type,
        key: 'Tab',
        code: 'Tab',
        windowsVirtualKeyCode: 9,
        nativeVirtualKeyCode: 9,
        modifiers: shift ? 8 : 0,
      });
    }
    await sleep(120);
  };
  const active = () => s.eval(`
    const a = document.activeElement;
    return a ? { id: a.id, tag: a.tagName, cls: a.className,
                 inDialog: (() => {
                   if (typeof topmostOpenDialog !== 'function') return false;
                   const d = topmostOpenDialog();
                   return Boolean(d && d.contains(a));
                 })() } : null;
  `);

  // ── landmarks ───────────────────────────────────────────────────────
  r.section('landmarks');

  const landmarks = await s.eval(`
    const h1s = [...document.querySelectorAll('h1')];
    const navs = [...document.querySelectorAll('nav')];
    return {
      h1Count: h1s.length,
      h1Text: h1s.map((h) => h.textContent.replace(/\\s+/g, ' ').trim()),
      mainCount: document.querySelectorAll('main').length,
      navNames: navs.map((n) => window.__accName(n).name),
      // The outputs view used to be a second <main>, which this grid placed
      // into the sidebar's own column at row 2.
      outputsTag: document.getElementById('outputsView')?.tagName,
      outputsIsInsideMain: Boolean(
        document.getElementById('outputsView')?.parentElement?.closest('main')),
    };
  `);
  r.check('the document has exactly one h1', landmarks.h1Count === 1,
    JSON.stringify(landmarks.h1Text));
  r.check('and it names the product', landmarks.h1Text[0] === 'PromptStudio',
    JSON.stringify(landmarks.h1Text));
  r.check('exactly one <main>, not one per view', landmarks.mainCount === 1,
    `found ${landmarks.mainCount}`);
  r.check('both gallery views live inside it, and neither is its own <main>',
    landmarks.outputsIsInsideMain && landmarks.outputsTag !== 'MAIN',
    JSON.stringify({ tag: landmarks.outputsTag, inside: landmarks.outputsIsInsideMain }));
  r.check('a named <nav> exists',
    landmarks.navNames.length >= 1 && landmarks.navNames.every(Boolean),
    JSON.stringify(landmarks.navNames));

  // ── every control has a name ────────────────────────────────────────
  r.section('accessible names');

  const names = await s.eval(`
    const btns = [...document.querySelectorAll('button')].filter(window.__vis);
    const rows = btns.map((b) => {
      const n = window.__accName(b);
      return {
        id: b.id || b.className,
        name: n.name,
        from: n.from,
        iconOnly: (b.textContent || '').trim() === '',
        hasLabel: b.hasAttribute('aria-label'),
        hasTitle: b.hasAttribute('title'),
      };
    });
    return {
      total: rows.length,
      unnamed: rows.filter((x) => !x.name).map((x) => x.id),
      // An icon-only control needs aria-label as its name and title as the
      // hover hint. title alone is a name AT can read and a sighted mouse
      // user can discover, and nothing a touch user can reach.
      iconOnlyWithoutLabel: rows.filter((x) => x.iconOnly && !x.hasLabel).map((x) => x.id),
      iconOnlyWithoutTitle: rows.filter((x) => x.iconOnly && !x.hasTitle).map((x) => x.id),
    };
  `);
  r.check('no visible button is unnamed', names.unnamed.length === 0,
    JSON.stringify(names.unnamed));
  r.check('every icon-only button carries an aria-label',
    names.iconOnlyWithoutLabel.length === 0, JSON.stringify(names.iconOnlyWithoutLabel));
  r.check('and a title for the hover hint',
    names.iconOnlyWithoutTitle.length === 0, JSON.stringify(names.iconOnlyWithoutTitle));

  const cardTrash = await s.eval(`
    const b = document.querySelector('.card-trash-btn');
    if (!b) return null;
    const n = window.__accName(b);
    return { name: n.name, from: n.from };
  `);
  r.check('the per-card delete names the file it deletes',
    Boolean(cardTrash) && cardTrash.from === 'aria-label' && /\.jpg$/i.test(cardTrash.name),
    JSON.stringify(cardTrash));

  // ── toggles say whether they are on ─────────────────────────────────
  r.section('toggle state');

  const toggles = await s.eval(`
    const els = [...document.querySelectorAll('.filter-chip, #gridNormal, #gridLarge')]
      .filter(window.__vis);
    return {
      total: els.length,
      missing: els.filter((e) => !e.hasAttribute('aria-pressed')).map((e) => e.id || e.className),
      disagree: els.filter((e) => e.hasAttribute('aria-pressed')
        && (e.getAttribute('aria-pressed') === 'true') !== e.classList.contains('active'))
        .map((e) => e.id || e.className),
      agree: els.filter((e) => e.hasAttribute('aria-pressed')
        && (e.getAttribute('aria-pressed') === 'true') === e.classList.contains('active')).length,
    };
  `);
  r.check('every visible toggle declares aria-pressed',
    toggles.total > 0 && toggles.missing.length === 0, JSON.stringify(toggles));
  r.check('and every one of them agrees with the active class',
    toggles.total > 0 && toggles.agree === toggles.total, JSON.stringify(toggles));

  const tracked = await s.eval(`
    const btn = document.getElementById('favoritesFilterBtn');
    const before = btn.getAttribute('aria-pressed');
    btn.click();
    await new Promise((res) => setTimeout(res, 900));
    const after = btn.getAttribute('aria-pressed');
    btn.click();
    await new Promise((res) => setTimeout(res, 900));
    return { before, after, back: btn.getAttribute('aria-pressed') };
  `);
  r.check('aria-pressed follows a real click, both ways',
    tracked.before === 'false' && tracked.after === 'true' && tracked.back === 'false',
    JSON.stringify(tracked));

  // ── the dialogs ─────────────────────────────────────────────────────
  r.section('dialog semantics, focus and escape');

  const DIALOGS = [
    ['insightsModal', '#insightsBtn', 'Quality Insights'],
    ['activityModal', '#activityBtn', 'Activity'],
    ['duplicatesModal', '#duplicatesBtn', 'Duplicates'],
    ['syncModal', '#syncInstagramBtn', 'Download media'],
    ['uploadModal', '#uploadPhotoBtn', 'Upload Photo'],
    ['newCreatorModal', '#newCreatorBtn', 'Create Creator Folder'],
  ];

  for (const [id, trigger, expectedName] of DIALOGS) {
    // Focus the trigger the way a keyboard user would, so "focus came back"
    // is a real claim and not an artifact of clicking.
    await s.eval(`document.querySelector('${trigger}').focus(); return true;`);
    await s.eval(`document.querySelector('${trigger}').click(); return true;`);
    await sleep(800);

    const state = await s.eval(`
      const d = document.getElementById('${id}');
      const a = document.activeElement;
      const label = d.getAttribute('aria-labelledby');
      const named = label ? document.getElementById(label) : null;
      // Match the id prefix, not the label: #syncModal's scrape queue has a
      // "Cancel job" button, and counting that as a way out of the dialog is
      // how this check first passed on the dialog whose only real exit was at
      // y=995 on a 900px viewport.
      const closers = [...d.querySelectorAll('button')]
        .filter(window.__vis)
        .filter((b) => /^(close|cancel|done)/i.test(b.id));
      return {
        open: getComputedStyle(d).display !== 'none',
        role: d.getAttribute('role'),
        ariaModal: d.getAttribute('aria-modal'),
        name: named ? named.textContent.replace(/\\s+/g, ' ').trim() : d.getAttribute('aria-label'),
        focusInside: Boolean(a && d.contains(a)),
        focusedId: a ? (a.id || a.tagName) : null,
        reachableClosers: closers
          .filter((b) => b.getBoundingClientRect().bottom <= window.innerHeight).length,
        closerPositions: closers.map((b) => b.id + '@' + Math.round(b.getBoundingClientRect().bottom)),
      };
    `);
    r.check(`${id} opens`, state.open);
    r.check(`${id} is a modal dialog with a name`,
      (state.role === 'dialog' || state.role === 'alertdialog')
      && state.ariaModal === 'true' && state.name === expectedName,
      JSON.stringify({ role: state.role, ariaModal: state.ariaModal, name: state.name }));
    r.check(`${id} moves focus into itself`, state.focusInside, `on ${state.focusedId}`);
    r.check(`${id} has a dismiss control above the fold`,
      state.reachableClosers >= 1, JSON.stringify(state.closerPositions));

    // Tab must not leave. Walk past the end and check we are still inside.
    const count = await s.eval(`
      if (typeof dialogFocusables !== 'function') return -1;
      return dialogFocusables(document.getElementById('${id}')).length;
    `);
    if (count < 0) {
      r.check(`${id} has focus containment at all`, false, 'dialogFocusables() does not exist');
    }
    let escaped = null;
    for (let i = 0; i < Math.max(count, 0) + 2; i += 1) {
      await tab();
      const a = await active();
      if (!a || !a.inDialog) { escaped = a; break; }
    }
    if (count >= 0) {
      r.check(`${id} contains Tab through ${count + 2} presses`,
        escaped === null, escaped ? `escaped to ${escaped.id || escaped.tag}` : '');
    }

    await s.key('Escape');
    await sleep(400);
    const after = await s.eval(`
      const d = document.getElementById('${id}');
      const a = document.activeElement;
      return {
        closed: getComputedStyle(d).display === 'none',
        focused: a ? (a.id || a.tagName) : null,
      };
    `);
    r.check(`${id} closes on Escape`, after.closed);
    r.check(`${id} hands focus back to the control that opened it`,
      after.focused === trigger.slice(1), `focus is on ${after.focused}`);
    if (!after.closed) {
      await s.eval(`document.getElementById('${id}').style.display = 'none'; return true;`);
    }
  }

  // ── the destructive confirm ─────────────────────────────────────────
  r.section('delete confirm');

  await s.eval(`
    document.querySelector('.photo-card .card-trash-btn').click();
    return true;
  `);
  await sleep(700);
  const confirm = await s.eval(`
    const d = document.getElementById('deleteConfirmModal');
    const a = document.activeElement;
    return {
      open: getComputedStyle(d).display !== 'none',
      role: d.getAttribute('role'),
      focusedId: a ? a.id : null,
    };
  `);
  r.check('the delete confirm is an alertdialog', confirm.role === 'alertdialog',
    String(confirm.role));
  r.check('it opens focused on Cancel, not on the destructive button',
    confirm.open && confirm.focusedId === 'cancelDeleteBtn', JSON.stringify(confirm));
  await s.key('Escape');
  await sleep(400);

  // ── the gallery from the keyboard alone ─────────────────────────────
  r.section('gallery keyboard access');

  // Everything past the grid -- prompts, triage, favourite, every ComfyUI
  // export -- is reachable only through the lightbox, and the lightbox opened
  // only on a click. The card was a <div> with no tabindex and no key handler.
  const cardFocus = await s.eval(`
    const card = document.querySelector('.photo-card');
    const name = window.__accName(card);
    card.focus();
    return {
      tabIndex: card.tabIndex,
      focusable: document.activeElement === card,
      name: name.name,
      from: name.from,
      // Compared against the card's own <img alt>, not a filename pattern:
      // suites that run earlier seed x_01.jpg and mira/nadia handles too.
      filename: card.querySelector('img').getAttribute('alt'),
      // A focusable control has to show it has focus.
      ring: (() => {
        const cs = getComputedStyle(card);
        return { outlineWidth: cs.outlineWidth, outlineStyle: cs.outlineStyle };
      })(),
    };
  `);
  r.check('a photo card can take keyboard focus',
    cardFocus.tabIndex === 0 && cardFocus.focusable, JSON.stringify(cardFocus));
  r.check('and it has a name that says which photo it is',
    cardFocus.from === 'aria-label'
      && Boolean(cardFocus.filename)
      && cardFocus.name.includes(cardFocus.filename)
      && cardFocus.name.includes('@'),
    JSON.stringify(cardFocus));

  // Enter, dispatched for real. The opener is now the focused card, so the
  // focus-return claim below is about the card and not a leftover from the
  // dialog loop above.
  for (const type of ['keyDown', 'char', 'keyUp']) {
    await s.send('Input.dispatchKeyEvent', {
      type,
      key: 'Enter',
      code: 'Enter',
      text: type === 'char' ? '\r' : undefined,
      windowsVirtualKeyCode: 13,
      nativeVirtualKeyCode: 13,
    });
  }
  await sleep(1600);
  const lb = await s.eval(`
    const d = document.getElementById('lightboxModal');
    const a = document.activeElement;
    return {
      open: getComputedStyle(d).display !== 'none',
      role: d.getAttribute('role'),
      name: d.getAttribute('aria-label'),
      focusedId: a ? a.id : null,
      focusInside: Boolean(a && d.contains(a)),
    };
  `);
  r.check('Enter on a card opens the lightbox', lb.open, JSON.stringify(lb));
  r.check('the lightbox is a named modal dialog',
    lb.role === 'dialog' && lb.name === 'Media inspector', JSON.stringify(lb));
  r.check('it takes focus on open', lb.open && lb.focusInside, JSON.stringify(lb));

  await s.key('Escape');
  await sleep(600);
  const lbAfter = await s.eval(`
    const a = document.activeElement;
    return {
      closed: getComputedStyle(document.getElementById('lightboxModal')).display === 'none',
      onCard: Boolean(a && a.closest && a.closest('.photo-card')),
      focused: a ? (a.id || a.className || a.tagName) : null,
    };
  `);
  r.check('Escape closes it', lbAfter.closed);
  r.check('and focus lands back on the card that opened it',
    lbAfter.onCard, JSON.stringify(lbAfter));

  r.finish(s);
  process.exit(process.exitCode || 0);
})().catch((e) => {
  console.error('HARNESS ERROR:', e.message);
  process.exit(2);
});
