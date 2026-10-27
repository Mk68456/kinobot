// Browser fixture for long labels; no live panel, uploads or Telegram access.
const {chromium} = require('C:/Users/Vertex/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');

(async () => {
  const staticRoot = path.join(__dirname, '..', 'webpanel', 'static');
  const prefix = 'Полное название с подробностями '.repeat(6);
  const longName = prefix + 'финал <тест> "название"';
  const category = 'Категория ' + prefix;
  const assets = [{id: 'a'.repeat(32), filename: 'video.mp4', size: 123, status: 'ready',
    source: 'desktop', received: 123, file_type: 'video'}];
  const drafts = ['movie', 'series'].map((kind, index) => ({id: String(index).repeat(32), status: 'draft', body: {
    title: kind === 'movie' ? 'Фильм' : 'Сериал', content_type: kind,
    catalog_section: kind === 'movie' ? 'movies' : 'series', season_number: 1,
    season_name: 'Сезон ' + prefix, description: '', poster_url: '', items: [{asset_id: assets[0].id,
      name: longName, category: kind === 'movie' ? category : '', relative_path: '720p/video.mp4',
      quality: kind === 'series' ? '720p' : '', voice: '', season_number: 1}],
  }}));
  const folders = [{id: 11, name: category, files: [{id: 12, name: longName}]}];
  let folderSave;
  const errors = [];
  const browser = await chromium.launch({headless: true, executablePath:
    'C:/Users/Vertex/AppData/Local/ms-playwright/chromium_headless_shell-1208/chrome-headless-shell-win64/chrome-headless-shell.exe'});
  try {
    const page = await browser.newPage({viewport: {width: 1440, height: 1000}});
    page.on('pageerror', error => errors.push(error.message));
    await page.route('http://127.0.0.1:9876/**', async route => {
      const request = route.request(), url = new URL(request.url());
      if (!url.pathname.startsWith('/api/')) {
        const name = url.pathname === '/' ? 'index.html' : url.pathname.replace(/^\/static\//, '');
        assert.ok(['index.html', 'app.js', 'contests.js', 'style.css', 'styles.css'].includes(name));
        return route.fulfill({body: fs.readFileSync(path.join(staticRoot, name)),
          contentType: name.endsWith('.js') ? 'application/javascript' : name.endsWith('.css') ? 'text/css' : 'text/html'});
      }
      let data;
      if (url.pathname === '/api/session') data = {admin_id: 99, csrf: 'test', max_file_bytes: 2e9};
      else if (url.pathname === '/api/catalog-sections') data = {sections: [
        {key: 'movies', title: 'Фильмы', enabled: true, required_content_type: 'movie'},
        {key: 'series', title: 'Сериалы', enabled: true, required_content_type: 'series'},
      ]};
      else if (url.pathname === '/api/assets') data = {assets};
      else if (url.pathname === '/api/drafts' && request.method() === 'GET') data = {drafts};
      else if (url.pathname === '/api/drafts' && request.method() === 'POST') {
        const payload = request.postDataJSON();
        const current = drafts.find(d => d.id === payload.id);
        assert.ok(current);
        current.body = payload.body;
        data = current;
      } else if (url.pathname === '/api/catalog/7/folders') {
        if (request.method() === 'POST') folderSave = request.postDataJSON().folders;
        data = {title: 'Фильм', movie_number: 7, content_type: 'movie', folders, merged_folders: 0};
      } else throw new Error('Unexpected fixture request: ' + request.url());
      return route.fulfill({json: data});
    });
    await page.goto('http://127.0.0.1:9876/');
    await page.locator('#workspace').waitFor({state: 'visible'});
    await page.locator('#draft-select').selectOption(drafts[0].id);
    const nameInput = page.locator('[data-name]'), categoryInput = page.locator('[data-category]');
    await nameInput.waitFor();
    assert.equal(await nameInput.inputValue(), longName);
    assert.equal(await nameInput.evaluate(input => input.maxLength), -1);
    assert.equal(await categoryInput.evaluate(input => input.maxLength), -1);
    await nameInput.fill(longName + ' после редактирования');
    await categoryInput.fill(category + ' другое окончание');
    await page.evaluate(() => saveDraft());
    assert.equal(drafts[0].body.items[0].name, longName + ' после редактирования');
    assert.equal(drafts[0].body.items[0].category, category + ' другое окончание');
    assert.equal(await page.evaluate(name => suggestName(name + '.mp4', 0), longName), longName);

    await page.locator('#draft-select').selectOption(drafts[1].id);
    await page.waitForFunction(id => state.id === id && $('content-type').value === 'series', drafts[1].id);
    const season = page.locator('#season-name');
    assert.equal(await season.evaluate(input => input.maxLength), -1);
    await season.fill('Сезон 1 — ' + prefix + 'финал');
    await page.evaluate(() => saveDraft());
    assert.equal(drafts[1].body.season_name, 'Сезон 1 — ' + prefix + 'финал');
    assert.equal(await page.evaluate(name => suggestName(name + '.mp4', 0), longName), 'Серия 1 — ' + longName);

    await page.evaluate(() => editCatalogFolders(7));
    const dialog = page.locator('.catalog-explorer-dialog');
    const folderInput = dialog.locator('[data-folder-name]'), fileInput = dialog.locator('[data-file-name]');
    assert.equal(await folderInput.evaluate(input => input.maxLength), -1);
    assert.equal(await fileInput.evaluate(input => input.maxLength), -1);
    await folderInput.fill('Папка ' + prefix + 'финал');
    await fileInput.fill(longName + ' сохранено');
    await page.screenshot({path: path.join(__dirname, '..', 'outputs', 'long-names-panel.png')});
    await dialog.locator('[type=submit]').click();
    await dialog.waitFor({state: 'detached'});
    assert.equal(folderSave[0].name, 'Папка ' + prefix + 'финал');
    assert.equal(folderSave[0].files[0].name, longName + ' сохранено');
    assert.deepEqual(errors, []);
    console.log('Long movie/series names, categories, season labels, suggestions and catalog rename saved without truncation.');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
