/**
 * Layout budget, focus visibility, and the icons that render nothing.
 *
 * Every check here corresponds to something that was measured in a real
 * browser and could not have been seen by reading the source:
 *
 * - The first screen was 67% chrome. Reading the CSS tells you the stats bar
 *   is a 3-column grid; only a layout pass tells you the fourth card orphans
 *   onto its own row and pushes the first photo to y=604 of 900.
 * - `:focus-visible` implemented its ring as `box-shadow`, and
 *   `.btn-secondary` sets its own `box-shadow` later in the file at equal
 *   specificity. The rule was present, matched, and did nothing. Only the
 *   computed style shows that.
 * - `fa-image-slash` and `fa-sparkles` are Font Awesome *Pro* names. The
 *   vendored Free set has no glyph, so the element renders at width 0 — an
 *   empty hole where the empty state's icon should be. The class name is
 *   spelled correctly; there is nothing to see in the markup.
 * - The lightbox close and next buttons were positioned against the modal
 *   card, so they sat on top of the inspector panel's own controls.
 *
 * The contrast check is arithmetic on two tokens, so it belongs anywhere —
 * it lives here because it is the same review and the same fix batch.
 */
const { Session, Report, sleep } = require('./cdp');

/** Relative luminance per WCAG 2.1, from any CSS rgb()/rgba() string. */
function luminance(rgb) {
  const [r, g, b] = rgb
    .match(/[\d.]+/g)
    .slice(0, 3)
    .map(Number)
    .map((v) => v / 255)
    .map((v) => (v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4)));
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

function contrast(fg, bg) {
  const a = luminance(fg);
  const b = luminance(bg);
  return (Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05);
}

function hexToRgb(hex) {
  const h = hex.trim().replace('#', '');
  const n = parseInt(h.length === 3 ? h.split('').map((c) => c + c).join('') : h, 16);
  return `rgb(${(n >> 16) & 255}, ${(n >> 8) & 255}, ${n & 255})`;
}

/** Press Tab for real. Programmatic .focus() does not set :focus-visible. */
async function tab(s) {
  for (const type of ['keyDown', 'keyUp']) {
    await s.send('Input.dispatchKeyEvent', {
      type, key: 'Tab', code: 'Tab', windowsVirtualKeyCode: 9, nativeVirtualKeyCode: 9,
    });
  }
  await sleep(120);
}

(async () => {
  const s = new Session();
  const r = new Report('layout budget + focus + icon glyphs');
  await s.connect();
  await s.send('Emulation.setDeviceMetricsOverride', {
    width: 1440, height: 900, deviceScaleFactor: 1, mobile: false,
  });
  await s.load();
  await sleep(900);

  // ── the first screen is mostly photos ──────────────────────────────
  r.section('chrome budget at 1440x900');

  // Chrome is measured from the top of the document, so start there and report
  // scrollY with the number -- when this check first failed at
  // `firstCardTop=1049` the obvious suspect was a restored scroll position from
  // the previous suite, and it was not: `<main>` had escaped `.workspace`, so
  // the grid was sitting in grid row 2 *below* the 650px sidebar. Printing
  // scrollY is what ruled the innocent explanation out.
  await s.eval('window.scrollTo(0, 0); return true;');
  await sleep(300);

  const budget = await s.eval(`
    const box = (sel) => {
      const el = document.querySelector(sel);
      if (!el) return null;
      const b = el.getBoundingClientRect();
      return { top: Math.round(b.top), height: Math.round(b.height) };
    };
    const card = document.querySelector('.photo-card');
    return {
      navbar: box('.navbar'),
      stats: box('.stats-bar'),
      header: box('.gallery-header'),
      firstCardTop: card ? Math.round(card.getBoundingClientRect().top) : null,
      scrollY: Math.round(window.scrollY),
      viewport: window.innerHeight,
    };
  `);
  r.check('the archive stats are one compact strip, not a card grid',
    budget.stats && budget.stats.height <= 80, JSON.stringify(budget.stats));
  r.check('the first photo is in the top half of the first screen',
    budget.firstCardTop !== null && budget.firstCardTop < 480,
    `firstCardTop=${budget.firstCardTop} of ${budget.viewport}, scrollY=${budget.scrollY}`);

  // ── the action bar, and what it costs ─────────────────────────────
  r.section('navbar hierarchy (U25)');

  // Rows have to be clustered, not counted by distinct `top`. Buttons on the
  // same visual row differ by a pixel (one is 42px tall because of its count
  // badge), so `new Set(tops).size` reported 4 rows where there were 2 -- a
  // number I nearly wrote into the review before checking it against the
  // container height.
  const navGeom = async () => s.eval(`
    const bar = document.querySelector('.nav-actions');
    const vis = (el) => { const r = el.getBoundingClientRect(); return r.width > 0 && r.height > 0; };
    const btns = [...bar.querySelectorAll('button')].filter(vis);
    const tops = btns.map((b) => b.getBoundingClientRect().top).sort((a, b) => a - b);
    let rows = tops.length ? 1 : 0;
    for (let i = 1; i < tops.length; i += 1) if (tops[i] - tops[i - 1] > 10) rows += 1;
    return {
      rows,
      buttons: btns.length,
      sumWidth: Math.round(btns.reduce((a, b) => a + b.getBoundingClientRect().width, 0)),
      barWidth: Math.round(bar.getBoundingClientRect().width),
      barHeight: Math.round(bar.getBoundingClientRect().height),
      navHeight: Math.round(document.querySelector('.navbar').getBoundingClientRect().height),
      primaries: btns.filter((b) => b.classList.contains('btn-primary')).map((b) => b.id),
      // Printed so the two measurements below cannot silently be the same one.
      viewportWidth: window.innerWidth,
    };
  `);

  const nav1440 = await navGeom();
  r.check('the action bar is one row at 1440px',
    nav1440.rows === 1, JSON.stringify(nav1440));
  r.check('nothing in the action bar claims to be the primary action',
    nav1440.primaries.length === 0, JSON.stringify(nav1440.primaries));

  await s.send('Emulation.setDeviceMetricsOverride',
    { width: 1280, height: 800, deviceScaleFactor: 1, mobile: false });
  await sleep(400);
  const nav1280 = await navGeom();
  r.check('and still one row at 1280px, where it used to wrap',
    nav1280.rows === 1, JSON.stringify(nav1280));
  r.check('the buttons fit the bar with room to spare',
    nav1280.sumWidth < nav1280.barWidth, JSON.stringify(nav1280));

  await s.send('Emulation.setDeviceMetricsOverride',
    { width: 1440, height: 900, deviceScaleFactor: 1, mobile: false });
  await sleep(400);

  // ── the sidebar's primary navigation ──────────────────────────────
  r.section('sidebar order (U34)');

  const sidebar = await s.eval(`
    const side = document.querySelector('.sidebar');
    const list = document.getElementById('creatorList');
    return {
      order: [...side.children]
        .filter((c) => { const r = c.getBoundingClientRect(); return r.width > 0 && r.height > 0; })
        .map((c) => c.id || c.className.split(' ')[0]),
      creatorListTop: Math.round(list.getBoundingClientRect().top),
      sidebarTop: Math.round(side.getBoundingClientRect().top),
      viewport: window.innerHeight,
    };
  `);
  const listOffset = sidebar.creatorListTop - sidebar.sidebarTop;
  // Ordering, not a pixel budget: the source-pill row appears between the
  // search box and the list once more than one source is indexed, and a tight
  // offset threshold would fail on that rather than on anything being wrong.
  const iList = sidebar.order.indexOf('creatorList');
  r.check('the creator list comes before Saved views and Boards',
    iList >= 0
      && iList < sidebar.order.indexOf('savedViewsSection')
      && iList < sidebar.order.indexOf('collectionsSection'),
    JSON.stringify(sidebar.order));
  r.check('and it starts in the top third of the sidebar',
    listOffset < 200, `${listOffset}px into a sidebar at y=${sidebar.sidebarTop}`);

  // ── the pinned row is the one with the filters on it ───────────────
  r.section('sticky element');

  // A short viewport guarantees the document overflows whatever the seeded
  // photo count is, so this reads the same with 12 rows or 400.
  await s.send('Emulation.setDeviceMetricsOverride', {
    width: 1440, height: 560, deviceScaleFactor: 1, mobile: false,
  });
  await sleep(400);
  await s.eval('window.scrollTo(0, document.documentElement.scrollHeight); return true;');
  await sleep(400);
  const scrolled = await s.eval(`
    const h = document.querySelector('.gallery-header').getBoundingClientRect();
    const n = document.querySelector('.navbar').getBoundingClientRect();
    return { headerTop: Math.round(h.top), headerBottom: Math.round(h.bottom),
             navbarBottom: Math.round(n.bottom), scrollY: Math.round(window.scrollY) };
  `);
  r.check('scrolled far enough to tell sticky from static',
    scrolled.scrollY > 100, JSON.stringify(scrolled));
  r.check('the gallery header stays on screen while the grid scrolls',
    scrolled.headerTop < 80 && scrolled.headerBottom > 0, JSON.stringify(scrolled));
  r.check('the sort/filter controls are reachable without scrolling back up',
    await s.eval(`
      const sel = document.getElementById('sortSelect');
      const b = sel.getBoundingClientRect();
      return b.top >= 0 && b.bottom <= window.innerHeight;
    `));
  await s.eval('window.scrollTo(0, 0); return true;');
  await s.send('Emulation.setDeviceMetricsOverride', {
    width: 1440, height: 900, deviceScaleFactor: 1, mobile: false,
  });
  await sleep(400);

  // ── keyboard focus is visible on the controls people actually use ──
  r.section('focus ring');

  // Asserts the *outcome* — focus is visibly indicated — not the mechanism.
  // `.filter-chip` was always fine via box-shadow; `.btn-secondary` matched
  // :focus-visible and drew nothing, because it sets its own box-shadow later
  // in the stylesheet at the same specificity. Either an outline or a changed
  // box-shadow counts; neither changing does not.
  const ringOn = async (label, matcher) => {
    await s.eval('document.activeElement.blur(); window.scrollTo(0, 0); return true;');
    let found = null;
    for (let i = 0; i < 40 && !found; i++) {
      await tab(s);
      found = await s.eval(`
        const el = document.activeElement;
        if (!el || !(${matcher})) return null;
        const focused = getComputedStyle(el);
        const snapshot = {
          el: el.id || el.className,
          focusVisible: el.matches(':focus-visible'),
          outlineStyle: focused.outlineStyle,
          outlineWidth: parseFloat(focused.outlineWidth) || 0,
          focusedShadow: focused.boxShadow,
        };
        // Same element, same classes, no focus: the baseline to differ from.
        const twin = el.cloneNode(true);
        twin.style.position = 'fixed';
        twin.style.top = '-999px';
        el.parentElement.appendChild(twin);
        snapshot.restingShadow = getComputedStyle(twin).boxShadow;
        twin.remove();
        return snapshot;
      `);
    }
    if (!found) {
      r.check(`${label}: reachable by Tab`, false, 'never focused in 40 tabs');
      return;
    }
    const outline = found.outlineStyle !== 'none' && found.outlineWidth > 0;
    const shadowChanged = found.focusedShadow !== found.restingShadow;
    r.check(`${label} is visibly focused`,
      found.focusVisible && (outline || shadowChanged), JSON.stringify(found));
  };
  await ringOn('a navbar button (.btn-secondary)',
    "el.classList.contains('btn') && el.classList.contains('btn-secondary')");
  await ringOn('a filter chip', "el.classList.contains('filter-chip')");

  // ── the empty states have a visible icon ───────────────────────────
  r.section('icon glyphs resolve in the vendored set');

  const glyphs = await s.eval(`
    const probe = (cls) => {
      const i = document.createElement('i');
      i.className = cls;
      i.style.position = 'fixed';
      i.style.top = '0';
      i.style.left = '0';
      document.body.appendChild(i);
      const content = getComputedStyle(i, '::before').content;
      const width = i.getBoundingClientRect().width;
      i.remove();
      return { cls, content, width: Math.round(width) };
    };
    const used = new Set([document.getElementById('emptyStateIcon').className]);
    // Every class updateEmptyState() can assign, harvested by running it.
    return { markup: probe([...used][0]) };
  `);
  r.check('the icon in the markup renders a glyph',
    glyphs.markup.content !== 'none' && glyphs.markup.width > 0,
    JSON.stringify(glyphs.markup));

  const emptyIcons = await s.eval(`
    const out = [];
    const el = document.getElementById('emptyStateIcon');
    const before = el.className;
    const states = [
      { archivePhotoTotal: 0, creators: [], selectedCreator: null, reviewMode: false },
      { reviewMode: true },
      { searchQuery: 'zzzznotathing', reviewMode: false },
      { selectedCreator: 'test_creator', searchQuery: '', reviewMode: false },
    ];
    const snapshot = {};
    for (const key of ['archivePhotoTotal','creators','selectedCreator','reviewMode','searchQuery','photos']) {
      snapshot[key] = state[key];
    }
    for (const patch of states) {
      Object.assign(state, patch);
      state.photos = [];
      updateEmptyState();
      const cs = getComputedStyle(el, '::before');
      out.push({ patch: Object.keys(patch).join('+'), cls: el.className,
                 content: cs.content, width: Math.round(el.getBoundingClientRect().width) });
    }
    Object.assign(state, snapshot);
    el.className = before;
    updateEmptyState();
    return out;
  `);
  emptyIcons.forEach((icon) => {
    r.check(`empty state (${icon.patch}) has a visible icon`,
      icon.content !== 'none' && icon.width > 0, JSON.stringify(icon));
  });

  // ── the lightbox controls stay out of each other's way ─────────────
  r.section('lightbox chrome does not overlap');

  await s.eval("document.querySelector('.photo-card').click(); return true;");
  await sleep(1400);
  const overlap = await s.eval(`
    const rect = (sel) => document.querySelector(sel).getBoundingClientRect();
    const intersect = (a, b) => {
      const A = rect(a), B = rect(b);
      const x = Math.max(0, Math.min(A.right, B.right) - Math.max(A.left, B.left));
      const y = Math.max(0, Math.min(A.bottom, B.bottom) - Math.max(A.top, B.top));
      return Math.round(x * y);
    };
    const inside = (inner, outer) => {
      const I = rect(inner), O = rect(outer);
      return I.left >= O.left - 1 && I.right <= O.right + 1
          && I.top >= O.top - 1 && I.bottom <= O.bottom + 1;
    };
    return {
      closeOverRefresh: intersect('#lightboxClose', '#regeneratePromptBtn'),
      closeOverFavorite: intersect('#lightboxClose', '#favoritePhotoBtn'),
      nextOverPanel: intersect('#lightboxNext', '.inspector-panel'),
      nextInsideMedia: inside('#lightboxNext', '.inspector-media'),
      prevInsideMedia: inside('#lightboxPrev', '.inspector-media'),
    };
  `);
  r.check('the close button does not cover Refresh',
    overlap.closeOverRefresh === 0, `${overlap.closeOverRefresh}px^2`);
  r.check('the close button does not cover Favorite',
    overlap.closeOverFavorite === 0, `${overlap.closeOverFavorite}px^2`);
  r.check('next/prev sit on the image, not on the inspector panel',
    overlap.nextOverPanel === 0, `${overlap.nextOverPanel}px^2`);
  r.check('next/prev are contained by the media pane at every width',
    overlap.nextInsideMedia && overlap.prevInsideMedia, JSON.stringify(overlap));

  // Same buttons, stacked layout: this is where they covered the title.
  await s.send('Emulation.setDeviceMetricsOverride', {
    width: 390, height: 844, deviceScaleFactor: 1, mobile: true,
  });
  await sleep(600);
  const narrow = await s.eval(`
    const rect = (sel) => document.querySelector(sel).getBoundingClientRect();
    const intersect = (a, b) => {
      const A = rect(a), B = rect(b);
      const x = Math.max(0, Math.min(A.right, B.right) - Math.max(A.left, B.left));
      const y = Math.max(0, Math.min(A.bottom, B.bottom) - Math.max(A.top, B.top));
      return Math.round(x * y);
    };
    return { navOverTitle: intersect('#lightboxNext', '.panel-header'),
             prevOverTitle: intersect('#lightboxPrev', '.panel-header') };
  `);
  r.check('at 390px the nav arrows do not cover the panel title',
    narrow.navOverTitle === 0 && narrow.prevOverTitle === 0, JSON.stringify(narrow));
  await s.send('Emulation.setDeviceMetricsOverride', {
    width: 1440, height: 900, deviceScaleFactor: 1, mobile: false,
  });
  await s.key('Escape');
  await sleep(500);

  // ── a verdict you can see without hovering one card at a time ──────
  r.section('verdict pill');

  const pill = await s.eval(`
    const el = document.querySelector('.photo-card .verdict-pill');
    if (!el) return null;
    const cs = getComputedStyle(el);
    return { quiet: el.classList.contains('quiet'), opacity: Number(cs.opacity),
             display: cs.display };
  `);
  r.check('classified cards carry a verdict pill in the normal gallery',
    pill !== null, JSON.stringify(pill));
  if (pill) {
    r.check('the pill is legible without hover, and still quiet',
      pill.opacity > 0.25 && pill.opacity < 1, JSON.stringify(pill));
  }

  // ── the heading says what you are looking at ───────────────────────
  r.section('gallery title and count');

  const unfiltered = await s.eval(`
    return { title: document.getElementById('galleryTitle').textContent.trim(),
             count: document.getElementById('galleryCount').textContent.trim() };
  `);
  r.check('with no filters the heading is the plain scope',
    unfiltered.title === 'All Photos', unfiltered.title);
  r.check('the count does not print "N / N"',
    !/\d+\s*\/\s*\d+/.test(unfiltered.count), unfiltered.count);

  await s.eval(`
    const sel = document.getElementById('mediaTypeSelect');
    sel.value = 'photo';
    sel.dispatchEvent(new Event('change', { bubbles: true }));
    return true;
  `);
  await sleep(1200);
  const filtered = await s.eval(
    "return document.getElementById('galleryTitle').textContent.trim();");
  r.check('a media-type filter is named in the heading',
    /Photos/.test(filtered) && filtered !== 'All Photos', filtered);

  await s.eval(`
    const sel = document.getElementById('verdictFilterSelect');
    sel.value = 'reject';
    sel.dispatchEvent(new Event('change', { bubbles: true }));
    return true;
  `);
  await sleep(1400);
  const both = await s.eval(
    "return document.getElementById('galleryTitle').textContent.trim();");
  r.check('a verdict filter is named in the heading too',
    /Reject/i.test(both), both);

  // Review mode reads a different state field, so it needs its own check:
  // this is the case that used to say 'All Photos' over a reject pile.
  await s.eval(`
    const media = document.getElementById('mediaTypeSelect');
    media.value = 'all';
    media.dispatchEvent(new Event('change', { bubbles: true }));
    const verdict = document.getElementById('verdictFilterSelect');
    verdict.value = '';
    verdict.dispatchEvent(new Event('change', { bubbles: true }));
    return true;
  `);
  await sleep(1400);
  await s.eval("enterReviewMode('', 'reject'); return true;");
  await sleep(1800);
  const reviewTitles = await s.eval(`
    return { heading: document.getElementById('galleryTitle').textContent.trim(),
             strip: document.getElementById('reviewBarTitle').textContent.trim() };
  `);
  r.check('review mode names the pile in the heading, not "All Photos"',
    /Reviewing/.test(reviewTitles.heading) && /Reject/i.test(reviewTitles.heading),
    JSON.stringify(reviewTitles));
  r.check('the heading and the review strip agree on the scope',
    reviewTitles.heading.startsWith(reviewTitles.strip), JSON.stringify(reviewTitles));

  await s.eval("exitReviewMode(); return true;");
  await sleep(1600);
  r.check('leaving review mode restores the plain scope',
    (await s.eval("return document.getElementById('galleryTitle').textContent.trim();"))
      === 'All Photos');

  // ── the inspector panel spends its space on the task ───────────────
  r.section('inspector panel budget (U18)');

  await s.eval("document.querySelector('.photo-card').click(); return true;");
  await sleep(1800);
  const panel = await s.eval(`
    const R = (sel) => {
      const el = document.querySelector(sel);
      if (!el || getComputedStyle(el).display === 'none') return null;
      const b = el.getBoundingClientRect();
      return { h: Math.round(b.height), top: Math.round(b.top), bottom: Math.round(b.bottom) };
    };
    const scroller = (sel) => {
      const el = document.querySelector(sel);
      if (!el) return null;
      return { clientH: el.clientHeight, scrollH: el.scrollHeight,
               overflowY: getComputedStyle(el).overflowY };
    };
    return {
      viewport: window.innerHeight,
      panel: scroller('.inspector-panel'),
      promptContent: scroller('#promptContent'),
      header: R('.panel-header'),
      detail: R('#mediaDetailPanel'),
      captionBlock: R('#mediaCaptionBlock'),
      // Absent and hidden are different: before this change there was no such
      // element, so "costs no space" would have passed for the wrong reason.
      captionBlockExists: Boolean(document.getElementById('mediaCaptionBlock')),
      footer: R('.inspector-footer'),
      heroGone: !document.getElementById('mediaDetailThumb'),
      facts: document.getElementById('mediaDetailGrid').textContent.replace(/\\s+/g, ' ').trim(),
    };
  `);

  r.check('the panel, not the prompt, is the scroller',
    panel.panel && panel.panel.overflowY === 'auto'
      && panel.promptContent && panel.promptContent.overflowY !== 'auto'
      && panel.promptContent.scrollH <= panel.promptContent.clientH + 1,
    JSON.stringify({ panel: panel.panel, promptContent: panel.promptContent }));
  r.check('Delete / Copy bundle are on screen without scrolling',
    panel.footer && panel.footer.bottom <= panel.viewport,
    JSON.stringify({ footer: panel.footer, viewport: panel.viewport }));
  r.check('the metadata block is a line, not half the panel',
    panel.detail && panel.detail.h <= 120, JSON.stringify(panel.detail));
  r.check('the panel no longer restates the image with a thumbnail',
    panel.heroGone === true, String(panel.heroGone));
  r.check('an absent caption costs no space',
    panel.captionBlockExists === true && panel.captionBlock === null,
    JSON.stringify({ exists: panel.captionBlockExists, box: panel.captionBlock }));
  r.check('a value the archive does not have is omitted, not printed as a dash',
    !/—/.test(panel.facts), panel.facts);
  const headerRow = await s.eval(`
    const h3 = document.querySelector('.panel-header h3').getBoundingClientRect();
    const acts = document.querySelector('.inspector-header-actions').getBoundingClientRect();
    const overlap = Math.min(h3.bottom, acts.bottom) - Math.max(h3.top, acts.top);
    return { titleH: Math.round(h3.height), actionsH: Math.round(acts.height),
             verticalOverlap: Math.round(overlap) };
  `);
  r.check('the sticky panel header is one row — title and actions share it',
    headerRow.verticalOverlap > 0, JSON.stringify(headerRow));

  // The header and footer have to survive a scroll of the panel, which is the
  // whole point of moving the scroller up a level.
  const stuck = await s.eval(`
    const p = document.querySelector('.inspector-panel');
    // Nothing here generates a prompt (Ollama is offline in the harness), so
    // the panel does not overflow on its own. The question is whether the bars
    // stay pinned when it does.
    const spacer = document.createElement('div');
    spacer.id = '__stickyProbe';
    spacer.style.height = '1400px';
    spacer.style.flex = '0 0 auto';
    p.insertBefore(spacer, document.querySelector('.inspector-footer'));
    p.scrollTop = p.scrollHeight;
    const scrolledTo = p.scrollTop;
    return new Promise((resolve) => setTimeout(() => {
      const hb = document.querySelector('.panel-header').getBoundingClientRect();
      const fb = document.querySelector('.inspector-footer').getBoundingClientRect();
      const pb = p.getBoundingClientRect();
      spacer.remove();
      resolve({ scrolled: Math.round(scrolledTo),
                headerOffset: Math.round(hb.top - pb.top),
                footerOffset: Math.round(pb.bottom - fb.bottom) });
    }, 260));
  `);
  r.check('the probe actually made the panel scroll',
    stuck.scrolled > 200, JSON.stringify(stuck));
  r.check('the panel header stays put while the panel scrolls',
    Math.abs(stuck.headerOffset) <= 2, JSON.stringify(stuck));
  r.check('the footer stays put while the panel scrolls',
    Math.abs(stuck.footerOffset) <= 2, JSON.stringify(stuck));

  // Icon-only buttons must still say what they are.
  const named = await s.eval(`
    return ['favoritePhotoBtn', 'regeneratePromptBtn'].map((id) => {
      const el = document.getElementById(id);
      return { id, text: el.textContent.trim(), label: el.getAttribute('aria-label'),
               title: el.getAttribute('title') };
    });
  `);
  named.forEach((b) => {
    r.check(`${b.id} is icon-only but has an accessible name`,
      b.text === '' && Boolean(b.label) && Boolean(b.title), JSON.stringify(b));
  });

  // Both directions. The seeded archive has no sidecars, so the absent case is
  // all the DOM above can show; these drive the two helpers that decide.
  const conditional = await s.eval(`
    const out = {};
    out.dashDropped = metaCard('Posted', '—') === '';
    out.emptyDropped = metaCard('Posted', '') === '';
    out.zeroKept = metaCard('Size', 0).includes('0');
    out.valueKept = metaCard('Posted', '14 Jun 2026').includes('14 Jun 2026');
    const block = document.getElementById('mediaCaptionBlock');
    setCaptionBlock('golden hour on the balcony');
    out.shownWhenPresent = getComputedStyle(block).display !== 'none';
    out.textWhenPresent = document.getElementById('mediaDetailCaption').textContent;
    setCaptionBlock('');
    out.hiddenWhenAbsent = getComputedStyle(block).display === 'none';
    return out;
  `);
  r.check('a dash or an empty value produces no pill at all',
    conditional.dashDropped && conditional.emptyDropped, JSON.stringify(conditional));
  r.check('a real value still gets a pill, and 0 is a real value',
    conditional.valueKept && conditional.zeroKept, JSON.stringify(conditional));
  r.check('the caption block appears when there is a caption',
    conditional.shownWhenPresent
      && conditional.textWhenPresent === 'golden hour on the balcony',
    JSON.stringify(conditional));
  r.check('and goes away again when there is not',
    conditional.hiddenWhenAbsent, JSON.stringify(conditional));

  await s.key('Escape');
  await sleep(500);

  // ── the token every instruction is set in ──────────────────────────
  r.section('muted text contrast');

  const tokens = await s.eval(`
    const cs = getComputedStyle(document.documentElement);
    return { muted: cs.getPropertyValue('--text-muted').trim(),
             bg: cs.getPropertyValue('--bg-dark').trim() };
  `);
  const ratio = contrast(hexToRgb(tokens.muted), hexToRgb(tokens.bg));
  r.check('--text-muted meets AA for small text on --bg-dark',
    ratio >= 4.5, `${tokens.muted} on ${tokens.bg} = ${ratio.toFixed(2)}:1`);

  // ── chrome that belongs to a different view (U31) ─────────────────
  r.section('view-appropriate chrome (U31)');

  await s.load();
  await sleep(600);
  const before = await s.eval(`
    const side = document.querySelector('.sidebar');
    const gal = document.querySelector('.gallery-container:not(.outputs-container)');
    return { sidebarWidth: Math.round(side.getBoundingClientRect().width),
             galleryWidth: Math.round(gal.getBoundingClientRect().width) };
  `);
  await s.eval(`document.getElementById('outputsBtn').click(); return true;`);
  await sleep(1200);
  const outputs = await s.eval(`
    const side = document.querySelector('.sidebar');
    const cs = getComputedStyle(side);
    const view = document.getElementById('outputsView');
    return {
      sidebarShown: cs.display !== 'none',
      columns: getComputedStyle(document.querySelector('.workspace')).gridTemplateColumns,
      outputsWidth: Math.round(view.getBoundingClientRect().width),
      // Its own filters, which is why the creator sidebar is redundant here.
      ownFilters: ['outputsCreator', 'outputsWorkflow', 'outputsRating', 'outputsCheckpoint']
        .filter((id) => document.getElementById(id)).length,
    };
  `);
  r.check('the Outputs view has its own creator/workflow/rating filters',
    outputs.ownFilters === 4, String(outputs.ownFilters));
  r.check('so it drops the photo gallery sidebar', !outputs.sidebarShown,
    JSON.stringify(outputs));
  r.check('and the outputs grid takes the width back',
    outputs.outputsWidth > before.galleryWidth + before.sidebarWidth - 40,
    `${before.galleryWidth} + ${before.sidebarWidth} sidebar -> ${outputs.outputsWidth}`);

  await s.eval(`document.getElementById('outputsBtn').click(); return true;`);
  await sleep(900);

  await s.eval(`
    const input = document.getElementById('searchInput');
    input.value = 'zzzznomatchzzzz';
    input.dispatchEvent(new Event('input', { bubbles: true }));
    return true;
  `);
  await sleep(1400);
  const empty = await s.eval(`
    const vis = (id) => {
      const el = document.getElementById(id);
      if (!el) return false;
      const cs = getComputedStyle(el);
      if (cs.display === 'none' || cs.visibility === 'hidden') return false;
      const r = el.getBoundingClientRect();
      return r.width > 0 && r.height > 0;
    };
    return {
      cards: document.querySelectorAll('.photo-card').length,
      // Act on results that are not there.
      resultControls: ['sortSelect', 'groupPostsBtn', 'selectModeBtn', 'gridNormal', 'gridLarge']
        .filter(vis),
      // The way out of an empty view.
      filters: ['mediaTypeSelect', 'verdictFilterSelect', 'favoritesFilterBtn', 'unanalyzedFilterBtn']
        .filter(vis),
      headerHeight: Math.round(
        document.querySelector('.gallery-container:not(.outputs-container) .gallery-header')
          .getBoundingClientRect().height),
    };
  `);
  r.check('the filter matched nothing, so this is the empty case',
    empty.cards === 0, JSON.stringify(empty));
  r.check('controls that act on results are gone',
    empty.resultControls.length === 0, JSON.stringify(empty.resultControls));
  r.check('and every filter stays, because they are the way out',
    empty.filters.length === 4, JSON.stringify(empty.filters));

  // ── the app agrees with itself about Ollama (U33) ──────────────────
  r.section('offline honesty (U33)');

  // Ollama is genuinely absent in the harness, so this is the real state and
  // not a stubbed one.
  await s.load();
  await sleep(2000);
  const offline = await s.eval(`
    const badge = document.getElementById('ollamaBadge');
    const on = document.querySelector('.hint-when-online');
    const off = document.querySelector('.hint-when-offline');
    return {
      bodyClass: document.body.classList.contains('ollama-offline'),
      status: document.getElementById('ollamaStatusLabel').textContent.trim(),
      badgeTag: badge.tagName,
      badgeLabel: badge.getAttribute('aria-label'),
      badgeTitle: badge.getAttribute('title'),
      onlineHintShown: on ? getComputedStyle(on).display !== 'none' : null,
      offlineHintShown: off ? getComputedStyle(off).display !== 'none' : null,
      offlineHintText: off ? off.textContent.replace(/\\s+/g, ' ').trim() : null,
    };
  `);
  r.check('the harness really has no Ollama', offline.status === 'Offline',
    JSON.stringify(offline.status));
  r.check('the whole page knows, not just the badge', offline.bodyClass,
    JSON.stringify(offline));
  r.check('the grid stops inviting a prompt it cannot generate',
    offline.onlineHintShown === false && offline.offlineHintShown === true,
    JSON.stringify(offline));
  r.check('and says what is actually true instead',
    /offline/i.test(offline.offlineHintText || ''), String(offline.offlineHintText));
  r.check('the status pill is a control with a next step',
    offline.badgeTag === 'BUTTON'
      && /offline/i.test(offline.badgeLabel || '')
      && /11434/.test(offline.badgeTitle || ''),
    JSON.stringify(offline));

  const rechecked = await s.eval(`
    document.getElementById('ollamaBadge').click();
    await new Promise((res) => setTimeout(res, 1500));
    const toast = document.querySelector('.toast');
    return toast ? toast.textContent.replace(/\\s+/g, ' ').trim() : null;
  `);
  r.check('pressing it re-checks and names the fix',
    Boolean(rechecked) && /ollama serve/.test(rechecked), String(rechecked));

  // ── the stacked layout, where a finger is the pointer (U35) ───────
  r.section('touch targets at 390x844 (U35)');

  await s.send('Emulation.setDeviceMetricsOverride',
    { width: 390, height: 844, deviceScaleFactor: 1, mobile: false });
  await s.load();
  await sleep(1200);
  const touch = await s.eval(`
    const vis = (el) => {
      const cs = getComputedStyle(el);
      if (cs.display === 'none' || cs.visibility === 'hidden') return false;
      const r = el.getBoundingClientRect();
      return r.width > 0 && r.height > 0;
    };
    const els = [...document.querySelectorAll(
      'button, select, input[type=text], input[type=date], .filter-chip')].filter(vis);
    const boxes = els.map((el) => {
      const r = el.getBoundingClientRect();
      return { id: el.id || el.className.toString().split(' ')[0],
               w: Math.round(r.width), h: Math.round(r.height) };
    });
    return {
      total: boxes.length,
      under40: boxes.filter((b) => b.h < 40).length,
      worst: boxes.sort((a, b) => a.h - b.h).slice(0, 14),
      // The chrome budget from §11 has to survive raising the targets.
      firstCardTop: (() => {
        const c = document.querySelector('.photo-card');
        return c ? Math.round(c.getBoundingClientRect().top) : null;
      })(),
      viewport: window.innerHeight,
    };
  `);
  r.check('no visible control is under 40px in the stacked layout',
    touch.total > 0 && touch.under40 === 0,
    `${touch.under40} of ${touch.total} under 40px; worst ${JSON.stringify(touch.worst)}`);
  // A no-regression budget, not a target. At 390px the first photo has never
  // been on the first screen -- pre-fix it was at y=1217 of 844, because the
  // navbar stacks into a wrapping row of eleven buttons. Raising the targets
  // could easily have made that worse; instead the icon-only grouping brought
  // it to 1164. Getting a photo above the fold at this width needs the navbar
  // to collapse into a menu, which is U9 and still open.
  r.check('the stacked layout did not get taller for it',
    touch.firstCardTop !== null && touch.firstCardTop <= 1200,
    `firstCardTop=${touch.firstCardTop} of ${touch.viewport} (pre-fix 1217)`);

  // ── the one native control nothing styled (U30) ────────────────────
  r.section('native inputs (U30)');

  await s.send('Emulation.setDeviceMetricsOverride',
    { width: 1440, height: 900, deviceScaleFactor: 1, mobile: false });
  await s.load();
  await sleep(900);
  const native = await s.eval(`
    // Measured with the dialog OPEN: a control inside display:none reports 0.
    document.getElementById('uploadPhotoBtn').click();
    await new Promise((res) => setTimeout(res, 600));
    const file = document.getElementById('uploadFileInput');
    const ref = document.getElementById('uploadCreatorSelect');
    const cs = getComputedStyle(file);
    const rs = getComputedStyle(ref);
    return {
      height: Math.round(file.getBoundingClientRect().height),
      bg: cs.backgroundColor,
      borderWidth: cs.borderTopWidth,
      radius: cs.borderTopLeftRadius,
      font: cs.fontFamily.split(',')[0].replace(/["']/g, ''),
      refFont: rs.fontFamily.split(',')[0].replace(/["']/g, ''),
      refRadius: rs.borderTopLeftRadius,
      // The field itself always shared the grouped .modal-input/.modal-select
      // rule -- no backticks in here, they close this template literal. What
      // no rule reached is the button the UA draws *inside* the field: the grey
      // platform chunk sitting on a glass-dark card.
      buttonBg: getComputedStyle(file, '::file-selector-button').backgroundColor,
      buttonFont: getComputedStyle(file, '::file-selector-button')
        .fontFamily.split(',')[0].replace(/["']/g, ''),
      // getComputedStyle cannot see into a UA shadow pseudo-element like
      // ::-webkit-calendar-picker-indicator -- it answers with the host
      // element's own style, so asking about the glyph here returns
      // "filter: none" whatever the stylesheet says. The rule is asserted at
      // source level in tests/test_markup_structure.py instead. What *is*
      // observable, and what makes the native widget draw dark, is this:
      dateColorScheme: (() => {
        const d = document.getElementById('outputsSince');
        return d ? getComputedStyle(d).colorScheme : null;
      })(),
    };
  `);
  r.check('the field matches the select beside it',
    native.font === native.refFont && native.radius === native.refRadius,
    JSON.stringify({ file: native.font, select: native.refFont }));
  r.check('and so does the button the browser draws inside it',
    native.buttonBg !== 'rgba(0, 0, 0, 0)'
      && native.buttonBg !== 'rgb(255, 255, 255)'
      && native.buttonFont === native.refFont,
    JSON.stringify({ bg: native.buttonBg, font: native.buttonFont }));
  r.check('the date inputs ask the platform for dark widgets',
    native.dateColorScheme === 'dark', String(native.dateColorScheme));
  await s.key('Escape');
  await sleep(300);

  r.finish(s);
  process.exit(process.exitCode || 0);
})().catch((e) => {
  console.error('HARNESS ERROR:', e.message);
  process.exit(2);
});
