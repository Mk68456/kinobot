// Isolated 280-file fixture: no live panel, database or Telegram operations.
const {chromium} = require('C:/Users/Vertex/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');

(async () => {
  const root = path.join(__dirname, '..', 'webpanel', 'static');
  const assets = Array.from({length: 280}, (_, i) => ({id: (i + 1).toString(16).padStart(32, '0'),
    filename: `episode-${i}.mp4`, size: 123, received: 123, source: 'desktop', status: 'ready'}));
  const draft = {id: 'd'.repeat(32), status: 'draft', body: {title: 'Задорные друзья',
    content_type: 'series', catalog_section: 'series', season_name: '', season_number: null,
    description: '', poster_url: '', items: assets.map((a, i) => ({asset_id: a.id, name: `Серия ${i + 1}`,
      category: '', quality: '360p', voice: 'Любительская (RUS)', season_number: i % 3 + 1,
      relative_path: `360p/season-${i % 3 + 1}/episode-${i + 1}/voice.mp4`}))}};
  draft.body.items[270].name = draft.body.items[16].name;
  draft.body.items[270].season_number = 2;
  draft.body.items[271] = {...draft.body.items[271], name: draft.body.items[16].name, season_number: 2,
    voice: 'Оригинальный дубляж (ENG)'};
  let publishCalls = 0;
  const errors = [];
  const browser = await chromium.launch({headless: true, executablePath:
    'C:/Users/Vertex/AppData/Local/ms-playwright/chromium_headless_shell-1208/chrome-headless-shell-win64/chrome-headless-shell.exe'});
  try {
    const page = await browser.newPage({viewport: {width: 1440, height: 1000}});
    page.on('pageerror', error => errors.push(error.message));
    page.on('dialog', dialog => dialog.accept());
    await page.route('http://127.0.0.1:9877/**', async route => {
      const request = route.request(), url = new URL(request.url());
      if (!url.pathname.startsWith('/api/')) {
        const name = url.pathname === '/' ? 'index.html' : url.pathname.replace(/^\/static\//, '');
        assert.ok(['index.html', 'app.js', 'contests.js', 'style.css'].includes(name));
        return route.fulfill({body: fs.readFileSync(path.join(root, name)), contentType:
          name.endsWith('.js') ? 'application/javascript' : name.endsWith('.css') ? 'text/css' : 'text/html'});
      }
      let data, status = 200;
      if (url.pathname === '/api/session') data = {admin_id: 99, csrf: 'fixture', max_file_bytes: 2e9};
      else if (url.pathname === '/api/catalog-sections') data = {sections: [
        {key: 'movies', title: 'Фильмы', enabled: true, required_content_type: 'movie'},
        {key: 'series', title: 'Сериалы', enabled: true, required_content_type: 'series'}]};
      else if (url.pathname === '/api/assets') data = {assets};
      else if (url.pathname === '/api/drafts' && request.method() === 'GET') data = {drafts: [draft]};
      else if (url.pathname === '/api/drafts' && request.method() === 'POST') {
        draft.body = request.postDataJSON().body;
        data = draft;
      } else if (url.pathname === `/api/drafts/${draft.id}/publish`) {
        publishCalls += 1;
        status = 400;
        data = {error: 'Сезон 2 · Серия 17 · 360p · Оригинальный дубляж (ENG): строка 272 совпадает с опубликованным вариантом.',
          conflicts: [{season: 'Сезон 2', name: 'Серия 17', quality: '360p', voice: 'Оригинальный дубляж (ENG)',
            rows: [272], asset_ids: [assets[271].id], existing: true}]};
      } else throw new Error('Unexpected request: ' + request.url());
      return route.fulfill({status, json: data});
    });
    await page.goto('http://127.0.0.1:9877/');
    await page.locator('#workspace').waitFor({state: 'visible'});
    await page.locator('#draft-select').selectOption(draft.id);
    await page.locator('.queue-row').first().waitFor();
    assert.equal(await page.locator('.queue-row').count(), 280);
    assert.equal(await page.locator('.variant-conflict').count(), 2);
    assert.equal(await page.locator('#publish').isDisabled(), true);
    assert.match(await page.locator('#publish-hint').textContent(), /17, 271/);
    assert.equal(await page.locator(`[data-id="${assets[271].id}"]`).evaluate(row => row.classList.contains('variant-conflict')), false);
    await page.locator('#show-series-conflict').click();
    assert.equal(await page.evaluate(() => document.activeElement.dataset.name), assets[16].id);
    await page.waitForFunction(id => {
      const row = document.querySelector(`[data-id="${id}"]`), bounds = row.getBoundingClientRect();
      return bounds.top >= 0 && bounds.top < innerHeight / 2;
    }, assets[16].id);
    await page.screenshot({path: path.join(__dirname, '..', 'outputs', 'series-conflicts-panel.png')});

    await page.locator(`[data-id="${assets[270].id}"] [data-voice]`).fill('Другая озвучка');
    assert.equal(await page.locator('.variant-conflict').count(), 0);
    assert.equal(await page.locator('#publish').isDisabled(), false);
    assert.equal(await page.locator('#show-series-conflict').isHidden(), true);
    await page.locator('#publish').click();
    await page.waitForFunction(() => state.seriesServerConflicts.length === 1 && !state.pendingAction);
    assert.equal(publishCalls, 1);
    assert.equal(await page.locator('.variant-conflict').count(), 1);
    assert.equal(await page.evaluate(() => document.activeElement.dataset.name), assets[271].id);
    assert.match(await page.locator('#draft-feedback').textContent(), /272/);
    assert.equal(await page.locator('#publish').isDisabled(), true);
    await page.locator(`[data-id="${assets[271].id}"] [data-voice]`).fill('Другая английская озвучка');
    assert.equal(await page.locator('.variant-conflict').count(), 0);
    assert.equal(await page.locator('#publish').isDisabled(), false);
    assert.equal(await page.locator('#season-number').inputValue(), '');
    assert.equal(await page.locator('#season-name').inputValue(), '');
    assert.deepEqual(errors, []);
    fs.writeFileSync(path.join(__dirname, '..', 'outputs', 'series-conflicts-ui.json'), JSON.stringify({ok: true,
      rows: 280, duplicateRows: [17, 271], differentVoicesAllowed: true, existingVariantHighlighted: true,
      editResolvesConflict: true, threeIndividualSeasonsAllowed: true, liveActions: false}, null, 2));
    console.log('UI OK: 280 rows, duplicate pairs, distinct voices/seasons, jump, existing variant, edit recovery.');
  } finally { await browser.close(); }
})().catch(error => {console.error(error); process.exitCode = 1;});
