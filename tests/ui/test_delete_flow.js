/**
 * Soft delete + optimistic gallery refresh, driven through the real UI.
 *
 * The point of these assertions is the *absence* of a refetch: deleting used to
 * call initApp(), which reset paging to page 1 and lost scroll position. So we
 * record every fetch() and assert only the DELETE went out.
 *
 * Expects a server seeded with 12 photos in one creator folder (see run.sh).
 */

const { Session, Report, sleep } = require('./cdp');

(async () => {
  const s = new Session();
  const r = new Report('delete flow');
  await s.connect();
  await s.load();

  r.section('initial state');
  await s.startRecordingFetches();
  const before = await s.eval(`
    return {
      cards: document.querySelectorAll('.photo-card').length,
      photos: state.photos.length,
      photoTotal: state.photoTotal,
      trashEnabled: state.trashEnabled,
      trashBtnVisible: getComputedStyle(document.getElementById('trashBtn')).display !== 'none',
      statTotal: document.getElementById('statTotalPhotos').textContent,
      creatorBadge: document.querySelector('.creator-item:nth-child(2) .creator-badge')?.textContent,
      maxScroll: document.body.scrollHeight - window.innerHeight,
    };
  `);
  r.check('gallery rendered', before.cards === 12, `${before.cards} cards`);
  r.check('trash enabled via /api/stats', before.trashEnabled === true);
  r.check('Trash button visible', before.trashBtnVisible === true);
  r.check('page actually scrollable (scroll assertions need this)',
    before.maxScroll > 400, `${before.maxScroll}px`);

  r.section('single delete is optimistic');
  await s.eval('window.scrollTo(0, 400); return true;');
  await sleep(300);
  await s.resetFetchLog();
  const target = await s.eval('return state.photos[1].rel_path;');
  await s.eval(`
    window.__scrollBefore = window.scrollY;
    promptDeletePhoto(state.photos.find(p => p.rel_path === ${JSON.stringify(target)}));
    document.getElementById('confirmDeleteBtn').click();
    return true;
  `);
  await sleep(1200);

  const after = await s.eval(`
    return {
      cards: document.querySelectorAll('.photo-card').length,
      photos: state.photos.length,
      photoTotal: state.photoTotal,
      statTotal: document.getElementById('statTotalPhotos').textContent,
      creatorBadge: document.querySelector('.creator-item:nth-child(2) .creator-badge')?.textContent,
      calls: window.__calls,
      scrollBefore: window.__scrollBefore,
      scrollNow: window.scrollY,
      toast: document.querySelector('.toast')?.textContent || '',
      hasUndo: !!document.querySelector('.toast-action-btn'),
      stillInState: state.photos.some(p => p.rel_path === ${JSON.stringify(target)}),
      cardInDom: !!document.querySelector('.photo-card[data-rel-path="' + CSS.escape(${JSON.stringify(target)}) + '"]'),
    };
  `);
  r.check('card removed from DOM', after.cards === before.cards - 1, `${before.cards} -> ${after.cards}`);
  r.check('removed from state', after.stillInState === false);
  r.check('that specific card is gone', after.cardInDom === false);
  r.check('photoTotal decremented', after.photoTotal === before.photoTotal - 1);
  r.check('stat counter updated locally', after.statTotal === String(Number(before.statTotal) - 1));
  r.check('creator badge updated locally',
    after.creatorBadge === String(Number(before.creatorBadge) - 1));
  r.check('no /api/photos refetch', !after.calls.some((c) => c.includes('/api/photos')),
    after.calls.join(', '));
  r.check('no /api/stats refetch', !after.calls.some((c) => c.includes('/api/stats')));
  r.check('exactly one request (the DELETE)',
    after.calls.length === 1 && after.calls[0].includes('/api/photo?'), after.calls.join(', '));
  r.check('scroll position preserved', after.scrollNow === after.scrollBefore,
    `${after.scrollBefore} -> ${after.scrollNow}`);
  r.check('toast reports Trash', /Moved to Trash/.test(after.toast), after.toast);
  r.check('Undo offered', after.hasUndo === true);

  r.section('undo restores');
  await s.eval(`document.querySelector('.toast-action-btn').click(); return true;`);
  await sleep(1800);
  const undone = await s.eval(`
    return { cards: document.querySelectorAll('.photo-card').length,
             back: state.photos.some(p => p.rel_path === ${JSON.stringify(target)}),
             toast: Array.from(document.querySelectorAll('.toast')).map(t => t.textContent).join(' | ') };
  `);
  r.check('photo is back in the gallery', undone.back === true);
  r.check('card count restored', undone.cards === before.cards, `${undone.cards}`);
  r.check('restore toast shown', /Restored/.test(undone.toast), undone.toast);

  r.section('trash modal');
  const seeded = await s.eval(`
    const rel = state.photos[0].rel_path;
    const res = await fetch('/api/photo?path=' + encodeURIComponent(rel), { method: 'DELETE' });
    const data = await res.json();
    removePhotosFromView([rel]);
    await fetchStats();
    window.__seededRel = rel;
    return { rel, trashId: data.trash_id };
  `);
  await sleep(500);
  await s.eval(`document.getElementById('trashBtn').click(); return true;`);
  await sleep(1200);
  const modal = await s.eval(`
    return {
      display: getComputedStyle(document.getElementById('trashModal')).display,
      rows: document.querySelectorAll('.trash-row').length,
      summary: document.getElementById('trashSummary').textContent,
      firstTitle: document.querySelector('.trash-row-title')?.textContent || '',
      badge: document.getElementById('trashCountBadge').textContent,
      hasRestore: !!document.querySelector('.trash-row-actions .btn-secondary'),
      hasPurge: !!document.querySelector('.trash-row-actions .btn-danger'),
    };
  `);
  r.check('modal opened', modal.display === 'flex');
  r.check('trashed entry listed', modal.rows === 1, `${modal.rows} rows`);
  r.check('row shows the rel_path', modal.firstTitle === seeded.rel, modal.firstTitle);
  r.check('nav badge shows the count', modal.badge === '1', modal.badge);
  r.check('restore + purge buttons present', modal.hasRestore && modal.hasPurge);

  r.section('restore from modal');
  await s.eval(`document.querySelector('.trash-row-actions .btn-secondary').click(); return true;`);
  await sleep(2000);
  const restored = await s.eval(`
    return { rows: document.querySelectorAll('.trash-row').length,
             summary: document.getElementById('trashSummary').textContent,
             badgeVisible: getComputedStyle(document.getElementById('trashCountBadge')).display !== 'none',
             cards: document.querySelectorAll('.photo-card').length };
  `);
  r.check('trash now empty', restored.rows === 0, `${restored.rows} rows`);
  r.check('summary says empty', /empty/i.test(restored.summary), restored.summary);
  r.check('badge hidden at zero', restored.badgeVisible === false);
  r.check('all 12 back in the gallery', restored.cards === 12, `${restored.cards} cards`);

  r.section('escape closes the modal');
  await s.eval(`document.getElementById('trashBtn').click(); return true;`);
  await sleep(800);
  await s.key('Escape');
  const closed = await s.eval(`return getComputedStyle(document.getElementById('trashModal')).display;`);
  r.check('Escape closed it', closed === 'none', closed);

  r.section('bulk delete with undo-all');
  await s.resetFetchLog();
  await s.eval(`
    setSelectMode(true);
    state.photos.slice(0, 2).forEach(p => state.selectedPaths.add(p.rel_path));
    updateBulkBar();
    promptBulkDelete();
    return true;
  `);
  const copy = await s.eval(`return {
    title: document.getElementById('deleteConfirmTitle').textContent,
    body: document.getElementById('deleteConfirmBody').textContent,
    btn: document.getElementById('confirmDeleteBtn').textContent.trim(),
  };`);
  r.check('confirm copy says Trash, not permanent',
    /Trash/.test(copy.title) && !/cannot be undone/.test(copy.body), copy.title);
  r.check('confirm button says Move to Trash', /Move to Trash/.test(copy.btn), copy.btn);

  await s.eval(`document.getElementById('confirmDeleteBtn').click(); return true;`);
  await sleep(2400);
  const bulk = await s.eval(`
    return { cards: document.querySelectorAll('.photo-card').length,
             calls: window.__calls,
             toast: Array.from(document.querySelectorAll('.toast')).map(t => t.textContent).join(' | '),
             undoLabel: document.querySelector('.toast-action-btn')?.textContent || '' };
  `);
  r.check('both cards removed', bulk.cards === 10, `${bulk.cards} cards`);
  r.check('bulk did not refetch the gallery',
    !bulk.calls.some((c) => c.includes('/api/photos')), bulk.calls.join(', '));
  r.check('undo-all offered', /Undo all \(2\)/.test(bulk.undoLabel), bulk.undoLabel);

  await s.eval(`document.querySelector('.toast-action-btn').click(); return true;`);
  await sleep(2400);
  const undoneAll = await s.eval(`return { cards: document.querySelectorAll('.photo-card').length };`);
  r.check('both photos restored', undoneAll.cards === 12, `${undoneAll.cards} cards`);

  r.finish(s);
  process.exit(process.exitCode || 0);
})().catch((e) => {
  console.error('HARNESS ERROR:', e.message);
  process.exit(2);
});
