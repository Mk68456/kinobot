// Headless fixture: never connects to the user's panel or Telegram.
const {chromium} = require('C:/Users/Vertex/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');

(async () => {
  const staticRoot = path.join(__dirname, '..', 'webpanel', 'static');
  const qualities = ['source', '720p', '480p', '360p'];
  const items = Array.from({length: 1000}, (_, i) => ({
    asset_id: i.toString(16).padStart(32, '0'), name: `Серия ${Math.floor(i / 4) + 1}`,
    quality: qualities[i % 4], voice: 'Studio', season_number: 1,
    category: '', relative_path: `${qualities[i % 4]}/${i}.mp4`,
  }));
  const assets = items.map(i => ({id: i.asset_id, filename: `${i.name}.mp4`, size: 100,
    status: 'ready', source: 'desktop', received: 100, file_type: 'video'}));
  let draft = {id: 'd'.repeat(32), status: 'draft', body: {
    title: 'Большой сезон', content_type: 'series', catalog_section: 'series',
    description: '', poster_url: '', season_number: 1, season_name: 'Сезон 1', items,
  }};
  const requests = [], errors = [];
  const browser = await chromium.launch({headless: true, executablePath:
    'C:/Users/Vertex/AppData/Local/ms-playwright/chromium_headless_shell-1208/chrome-headless-shell-win64/chrome-headless-shell.exe'});
  try {
    const page = await browser.newPage({viewport: {width: 1440, height: 1000}});
    page.on('pageerror', e => errors.push(e.message));
    await page.route('http://127.0.0.1:9876/**', async route => {
      const request = route.request(), url = new URL(request.url());
      requests.push(url.pathname + url.search);
      if (!url.pathname.startsWith('/api/')) {
        const name = url.pathname === '/' ? 'index.html' : url.pathname.replace(/^\/static\//, '');
        assert.ok(['index.html', 'app.js', 'contests.js', 'style.css', 'styles.css'].includes(name));
        const type = name.endsWith('.js') ? 'application/javascript' : name.endsWith('.css') ? 'text/css' : 'text/html';
        return route.fulfill({body: fs.readFileSync(path.join(staticRoot, name)), contentType: type});
      }
      let data;
      if (url.pathname === '/api/session') data = {admin_id: 99, csrf: 'test', max_file_bytes: 2e9};
      else if (url.pathname === '/api/catalog-sections') data = {sections: [
        {key: 'movies', title: 'Фильмы', emoji: '🎬', enabled: true, required_content_type: 'movie'},
        {key: 'series', title: 'Сериалы', emoji: '📺', enabled: true, required_content_type: 'series'},
      ]};
      else if (url.pathname === '/api/assets') data = {assets: url.searchParams.has('draft_id') ? assets : assets.slice(500)};
      else if (url.pathname === '/api/drafts' && request.method() === 'GET') data = {drafts: [draft]};
      else if (url.pathname === '/api/drafts' && request.method() === 'POST') {
        const payload = request.postDataJSON();
        assert.equal(payload.body.items.length, 1000);
        draft = {...draft, body: payload.body};
        data = draft;
      } else throw new Error('Unexpected fixture request: ' + request.url());
      return route.fulfill({json: data});
    });
    await page.goto('http://127.0.0.1:9876/');
    await page.locator('#workspace').waitFor({state: 'visible'});
    await page.locator('#draft-select').selectOption(draft.id);
    await page.waitForFunction(() => state.items.length === 1000 && state.assets.length === 1000);
    assert.equal(await page.evaluate(() => state.items.filter(i => !asset(i.asset_id)).length), 0);
    await page.evaluate(() => refresh());
    assert.equal(requests.filter(p => p.startsWith('/api/uploads/')).length, 0);
    assert.ok(requests.filter(p => p.includes('draft_id=')).length >= 2);
    await page.locator('#extra-fields > summary').click();
    await page.locator('#description').fill('Изменено после импорта 1000 файлов');
    await page.evaluate(() => saveDraft());
    assert.equal(draft.body.items.length, 1000);
    assert.equal(draft.body.description, 'Изменено после импорта 1000 файлов');
    assert.deepEqual(errors, []);
    console.log('1000-file draft opened, refreshed and saved; no per-file HTTP requests or browser errors.');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
