/**
 * escapeHtml at real render sites, plus search debounce and abort ordering.
 *
 * The payload is injected as the kind of data that genuinely reaches these
 * fields — Instagram handles, filenames, IG full_name, Ollama vision tags —
 * and the assertion is that it renders as visible text, never as markup.
 */

const { Session, Report, sleep } = require('./cdp');

const XSS = `"><img src=x onerror="window.__XSS_FIRED=true">`;
const Q = JSON.stringify(XSS);

(async () => {
  const s = new Session();
  const r = new Report('escaping / debounce / abort');
  await s.connect();
  await s.load();

  r.section('escapeHtml unit behaviour');
  const escaped = await s.eval(`return escapeHtml('<img src=x onerror=alert(1)>&"\\'');`);
  r.check('escapes < > & " \'',
    escaped === '&lt;img src=x onerror=alert(1)&gt;&amp;&quot;&#39;', escaped);
  r.check('null becomes empty string', (await s.eval('return escapeHtml(null);')) === '');
  r.check('undefined becomes empty string', (await s.eval('return escapeHtml(undefined);')) === '');
  r.check('numbers pass through', (await s.eval('return escapeHtml(42);')) === '42');

  r.section('gallery card: hostile filename + creator');
  const card = await s.eval(`
    window.__XSS_FIRED = false;
    state.photos = [{
      rel_path: 'evil/' + ${Q}, filename: ${Q}, creator: ${Q},
      url: '/media/x.jpg', thumb_url: '/media/thumb/x.jpg',
      favorite: false, has_prompt: false, prompt_stale: false
    }];
    state.photoTotal = 1; state.photoHasMore = false;
    renderGallery({});
    await new Promise(r => setTimeout(r, 400));
    const el = document.querySelector('.photo-card');
    return {
      fired: window.__XSS_FIRED === true,
      injected: document.querySelectorAll('img[onerror]').length,
      altLiteral: el.querySelector('img').getAttribute('alt') === ${Q},
      creatorText: el.querySelector('.photo-card-creator').textContent,
      imgCount: el.querySelectorAll('img').length,
    };
  `);
  r.check('no script executed', card.fired === false);
  r.check('no onerror element injected', card.injected === 0);
  r.check('alt holds the literal payload', card.altLiteral === true);
  r.check('creator rendered as text', card.creatorText === '@' + XSS, card.creatorText.slice(0, 30));
  r.check('no smuggled extra img', card.imgCount === 1, String(card.imgCount));

  r.section('creator sidebar: hostile handle');
  const side = await s.eval(`
    window.__XSS_FIRED = false;
    state.creators = [{ name: ${Q}, photo_count: 3, last_synced_at: ${Q} }];
    renderCreatorList();
    await new Promise(r => setTimeout(r, 400));
    return { fired: window.__XSS_FIRED === true,
             injected: document.querySelectorAll('img[onerror]').length,
             nameText: document.querySelectorAll('.creator-item')[1].querySelector('.creator-name').textContent.trim() };
  `);
  r.check('no script executed', side.fired === false);
  r.check('nothing injected', side.injected === 0);
  r.check('handle is literal text', side.nameText.startsWith('@' + XSS), side.nameText.slice(0, 30));

  r.section('prompt tags + history: hostile vision output');
  const tags = await s.eval(`
    window.__XSS_FIRED = false;
    applyPromptData({
      positive_prompt: 'p', negative_prompt: 'n',
      visual_tags: [${Q}, 'safe_tag'],
      parameters: { sampler: 's', steps: 1, cfg_scale: 1, aspect_ratio: '1:1' },
      history: [{ positive_prompt: ${Q}, saved_at: new Date().toISOString() }]
    });
    await new Promise(r => setTimeout(r, 400));
    return { fired: window.__XSS_FIRED === true,
             injected: document.querySelectorAll('img[onerror]').length,
             tagText: document.querySelector('.tag-pill')?.textContent || '',
             histText: document.querySelector('.history-preview')?.textContent || '',
             dataTag: document.querySelector('.tag-pill')?.getAttribute('data-tag') || '' };
  `);
  r.check('no script executed', tags.fired === false);
  r.check('nothing injected', tags.injected === 0);
  r.check('tag renders as text', tags.tagText.includes('<img'), tags.tagText.slice(0, 30));
  r.check('data-tag round-trips the literal', tags.dataTag === XSS);
  r.check('history preview renders as text', tags.histText.includes('<img'), tags.histText.slice(0, 30));

  r.section('following picker: hostile IG full_name');
  const following = await s.eval(`
    window.__XSS_FIRED = false;
    const realFetch = window.__originalFetch || window.fetch;
    const patched = async (u, o) => {
      if (String(u).startsWith('/api/following')) {
        return new Response(JSON.stringify({ accounts: [{ username: 'ok_user',
          full_name: ${Q}, is_private: false, media_count: 5, followers_count: 100 }], total: 1 }),
          { headers: { 'Content-Type': 'application/json' } });
      }
      return realFetch(u, o);
    };
    const saved = window.fetch;
    window.fetch = patched;
    await loadFollowingPicker('');
    await new Promise(r => setTimeout(r, 400));
    window.fetch = saved;
    return { fired: window.__XSS_FIRED === true,
             injected: document.querySelectorAll('img[onerror]').length,
             nameText: document.querySelector('.following-name')?.textContent || '' };
  `);
  r.check('no script executed', following.fired === false);
  r.check('nothing injected', following.injected === 0);
  r.check('full_name renders as text', following.nameText === XSS, following.nameText.slice(0, 30));

  r.section('search debounce');
  await s.load();
  await s.startRecordingFetches();
  const debounced = await s.eval(`
    const input = document.getElementById('searchInput');
    for (const ch of 'bikini') {
      input.value += ch;
      input.dispatchEvent(new Event('input', { bubbles: true }));
      await new Promise(r => setTimeout(r, 25));
    }
    await new Promise(r => setTimeout(r, 900));
    const photoCalls = window.__calls.filter(u => u.includes('/api/photos'));
    return { count: photoCalls.length, last: photoCalls.pop() || '',
             inputValue: input.value, stateQuery: state.searchQuery };
  `);
  r.check('6 keystrokes produce 1 request', debounced.count === 1, `${debounced.count} calls`);
  r.check('request carries the final query', /search=bikini/.test(debounced.last), debounced.last);
  r.check('state matches the input box', debounced.stateQuery === debounced.inputValue);

  r.section('Enter bypasses the debounce');
  await s.resetFetchLog();
  const entered = await s.eval(`
    const input = document.getElementById('searchInput');
    input.value = 'sunset';
    input.dispatchEvent(new Event('input', { bubbles: true }));
    input.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));
    await new Promise(r => setTimeout(r, 120));   // under the 250ms debounce
    return window.__calls.filter(u => u.includes('/api/photos')).length;
  `);
  r.check('fires before the debounce elapses', entered >= 1, `${entered} calls`);

  r.section('AbortController: newest request wins');
  await sleep(700);
  await s.resetFetchLog();
  const race = await s.eval(`
    document.getElementById('searchInput').value = '';
    state.searchQuery = '';
    state.mediaType = 'photo'; fetchPhotos();
    state.mediaType = 'video'; fetchPhotos();
    state.mediaType = 'all';   fetchPhotos();
    state.sortMode = 'newest';  fetchPhotos();
    await new Promise(r => setTimeout(r, 1600));
    return { aborts: window.__aborts, loading: state.photosLoading,
             noDangling: state.photosRequest === null,
             photos: state.photos.length,
             cards: document.querySelectorAll('.photo-card').length,
             busy: document.getElementById('galleryGrid').getAttribute('aria-busy') };
  `);
  r.check('the 3 superseded requests aborted', race.aborts === 3, `${race.aborts} aborts`);
  r.check('loading flag cleared', race.loading === false);
  r.check('no dangling controller', race.noDangling === true);
  r.check('final result applied, not dropped', race.photos > 0, `${race.photos} photos`);
  r.check('DOM matches state', race.cards === race.photos, `${race.cards} vs ${race.photos}`);
  r.check('aria-busy reset', race.busy === 'false', race.busy);

  r.section('filter changes are no longer swallowed mid-flight');
  const noDrop = await s.eval(`
    state.mediaType = 'all'; state.sortMode = 'name';
    await fetchPhotos();
    state.mediaType = 'video'; fetchPhotos();
    state.mediaType = 'photo'; fetchPhotos();
    await new Promise(r => setTimeout(r, 1400));
    const truth = await (await (window.__originalFetch || fetch))('/api/photos?media_type=photo&sort=name&offset=0&limit=60');
    const body = await truth.json();
    return { stateCount: state.photos.length, serverCount: (body.photos || []).length };
  `);
  r.check('last filter actually applied',
    noDrop.stateCount === noDrop.serverCount,
    `state ${noDrop.stateCount} vs server ${noDrop.serverCount}`);

  r.finish(s);
  process.exit(process.exitCode || 0);
})().catch((e) => {
  console.error('HARNESS ERROR:', e.message);
  process.exit(2);
});
