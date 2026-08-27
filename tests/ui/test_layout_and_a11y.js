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
      viewport: window.innerHeight,
    };
  `);
  r.check('the archive stats are one compact strip, not a card grid',
    budget.stats && budget.stats.height <= 80, JSON.stringify(budget.stats));
  r.check('the first photo is in the top half of the first screen',
    budget.firstCardTop !== null && budget.firstCardTop < 480,
    `firstCardTop=${budget.firstCardTop} of ${budget.viewport}`);

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

  r.finish(s);
  process.exit(process.exitCode || 0);
})().catch((e) => {
  console.error('HARNESS ERROR:', e.message);
  process.exit(2);
});
