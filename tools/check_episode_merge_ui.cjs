// Isolated browser fixture. No requests reach the actual panel, Telegram or filesystem.
const {chromium}=require('C:/Users/Vertex/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
(async()=>{
  const root=path.join(__dirname,'..','webpanel','static'),output=path.join(__dirname,'..','outputs');
  const errors=[],requests=[],payloads=[],malicious='<img src=x onerror="window.__xss=1">';
  const initial=()=>({movie_number:1000,content_type:'series',title:'Сериал '+malicious,folders:[
    {id:11,name:'Сезон 1',files:[{id:311,name:'Серия 3: '+malicious,qualities:Array.from({length:20},(_,index)=>`720p · Озвучка ${index+1}`)},
      {id:312,name:'Серия 3: Mole Man',qualities:['480p · English']},{id:313,name:'Серия 4',qualities:['720p · English']}]},
    {id:12,name:'Сезон 2',files:[{id:321,name:'Серия 1',qualities:['720p · Dub']},{id:322,name:'Серия 2',qualities:['720p · Dub']}]},
  ]});
  let catalog=initial(),sequence=0,conflict=false,rejectPreview=false,rejectApply=false,gate=null;
  const conflictRecord=()=>({quality:'720p',voice:'Dub '+malicious,episodes:[{id:311,name:'Серия 3: '+malicious,variant_id:7},{id:312,name:'Серия 3: Mole Man',variant_id:8}],reason:'Разные файлы с одинаковым качеством и озвучкой.'});
  const browser=await chromium.launch({headless:true,executablePath:process.env.KINOTIME_CHROMIUM||'C:/Users/Vertex/AppData/Local/ms-playwright/chromium_headless_shell-1208/chrome-headless-shell-win64/chrome-headless-shell.exe'});
  try{
    const page=await browser.newPage({viewport:{width:390,height:844}});
    page.on('pageerror',error=>errors.push(error.message));
    await page.route('http://127.0.0.1:9881/**',async route=>{
      const request=route.request(),url=new URL(request.url());requests.push({path:url.pathname,method:request.method()});let data,status=200;
      if(!url.pathname.startsWith('/api/')){
        const name=url.pathname==='/'?'index.html':url.pathname.replace(/^\/static\//,'');
        assert.ok(['index.html','app.js','contests.js','style.css'].includes(name));
        return route.fulfill({body:fs.readFileSync(path.join(root,name)),contentType:name.endsWith('.js')?'application/javascript':name.endsWith('.css')?'text/css':'text/html'});
      }
      if(url.pathname==='/api/session')data={admin_id:99,csrf:'fixture',max_file_bytes:2e9};
      else if(url.pathname==='/api/catalog-sections')data={sections:[{key:'series',title:'Сериалы',enabled:true,required_content_type:'series'}]};
      else if(url.pathname==='/api/assets')data={assets:[]};
      else if(url.pathname==='/api/drafts')data={drafts:[]};
      else if(url.pathname==='/api/catalog/1000/folders'){
        if(request.method()==='POST'){
          const body=request.postDataJSON();payloads.push({operation:'rename',...body});
          catalog.folders=catalog.folders.map(folder=>{const renamed=body.folders.find(item=>item.id===folder.id);return {...folder,name:renamed.name,files:folder.files.map(file=>({...file,name:renamed.files.find(item=>item.id===file.id).name}))};});
        }
        data=catalog;
      }else if(url.pathname==='/api/catalog/1000/episodes/merge'){
        const body=request.postDataJSON();payloads.push({operation:'merge',...body});
        assert.equal(request.headers()['x-csrf-token'],'fixture');
        if(body.action==='preview'){
          if(gate)await gate.promise;
          if(rejectPreview){status=400;data={error:'Выбранная серия уже изменена.'};}
          else data={token:'preview-'+(++sequence),movie_number:1000,...body,episodes:body.episode_ids.map(id=>catalog.folders.flatMap(folder=>folder.files).find(file=>file.id===id)),counts:{episodes_before:body.episode_ids.length,episodes_after:1,variants_before:21,variants_after:20,duplicates:1,watched_users:3},conflicts:conflict?[conflictRecord()]:[]};
        }else{
          assert.ok(body.expected_token.startsWith('preview-'));
          if(rejectApply){status=409;data={error:'Каталог изменился. Проверьте объединение заново.',conflicts:[conflictRecord()]};}
          else{
            const folder=catalog.folders.find(item=>item.id===body.folder_id),target=folder.files.find(file=>file.id===body.target_id);
            target.name=body.name;target.qualities=['source · Dub','720p · Dub','480p · English'];folder.files=folder.files.filter(file=>!body.episode_ids.includes(file.id)||file.id===body.target_id);
            data={merged:true,...body,catalog};
          }
        }
      }else if(url.pathname==='/api/catalog/2000/folders')data={movie_number:2000,content_type:'movie',title:'Фильм',folders:[{id:22,name:'1080p',files:[{id:41,name:'Dub'},{id:42,name:'English'}]}]};
      else throw new Error('Unexpected fixture request: '+request.url());
      return route.fulfill({status,json:data});
    });
    await page.goto('http://127.0.0.1:9881/');await page.locator('#workspace').waitFor({state:'visible'});await page.evaluate(()=>editCatalogFolders(1000));
    const dialog=page.locator('dialog.catalog-explorer-dialog'),pick=id=>dialog.locator(`[data-merge-episode="${id}"]`);
    const preview=dialog.locator('[data-merge-preview]'),apply=dialog.locator('[data-merge-apply]'),mergeName=dialog.locator('[data-merge-name]');
    assert.equal(await dialog.locator('img').count(),0);assert.equal(await page.evaluate(()=>window.__xss),undefined);
    assert.equal(await dialog.locator('.catalog-explorer-variants summary').innerText(),'Вариантов: 20');
    await pick(311).check();assert.equal(await dialog.locator('[data-merge-options]').isVisible(),false);await pick(312).check();
    assert.equal(await dialog.locator('[data-merge-target] option').count(),2);assert.equal(await apply.isDisabled(),true);
    await dialog.locator('[data-folder-name]').fill('Сезон 1 · исправлен');
    assert.match(await dialog.locator('[data-merge-hint]').innerText(),/Сохраните изменения/);assert.equal(await preview.isDisabled(),true);assert.equal(payloads.length,0,'No automatic writes or preview during dirty rename');
    await dialog.locator('[data-open-folder="12"]').click();assert.equal(await dialog.locator('[data-merge-episode]:checked').count(),0);await pick(321).check();assert.equal(await dialog.locator('[data-merge-options]').isVisible(),false);
    await dialog.locator('[data-open-folder="11"]').click();assert.equal(await dialog.locator('[data-folder-name]').inputValue(),'Сезон 1 · исправлен');await pick(311).check();await pick(312).check();
    await dialog.locator('[data-merge-target]').selectOption('312');await mergeName.fill('Серия 3 · имя до сохранения');
    await dialog.locator('[type="submit"]').click();await page.waitForFunction(()=>document.querySelector('[data-catalog-feedback]').textContent.includes('сохранены'));
    assert.equal(await dialog.isVisible(),true);assert.equal(await dialog.locator('[data-merge-episode]:checked').count(),2);assert.equal(payloads[0].operation,'rename');assert.equal(await dialog.locator('[data-merge-target]').inputValue(),'312');assert.equal(await mergeName.inputValue(),'Серия 3 · имя до сохранения');
    await dialog.locator('[data-merge-target]').selectOption('312');await mergeName.fill('Серия 3 · новое имя & '+malicious);await preview.click();await page.waitForFunction(()=>document.querySelector('[data-merge-result]').textContent.includes('Конфликтов нет'));
    assert.deepEqual(payloads.at(-1),{operation:'merge',action:'preview',folder_id:11,target_id:312,episode_ids:[311,312],name:'Серия 3 · новое имя & '+malicious});
    assert.equal(payloads.filter(body=>body.action==='apply').length,0,'Preview is read-only');assert.equal(await apply.isDisabled(),false);assert.match(await dialog.locator('[data-merge-result]').innerText(),/ID 312/);assert.match(await dialog.locator('[data-merge-result]').innerText(),/Варианты: 21 → 20/);
    assert.equal(await dialog.locator('[data-merge-result] img').count(),0);
    await mergeName.fill('Серия 3 · окончательное имя');assert.equal(await apply.isDisabled(),true);await preview.click();await page.waitForFunction(()=>!document.querySelector('[data-merge-apply]').disabled);
    await dialog.locator('[data-merge-target]').selectOption('311');assert.equal(await apply.isDisabled(),true);assert.equal(await mergeName.inputValue(),'Серия 3 · окончательное имя');await dialog.locator('[data-merge-target]').selectOption('312');
    conflict=true;await preview.click();await page.waitForFunction(()=>document.querySelector('[data-merge-result]').textContent.includes('Разные файлы'));
    assert.equal(await apply.isDisabled(),true);assert.match(await dialog.locator('[data-merge-result]').innerText(),/720p/);assert.match(await dialog.locator('[data-merge-result]').innerText(),/ID 311/);assert.equal(await dialog.locator('[data-merge-result] img').count(),0);
    conflict=false;rejectPreview=true;await preview.click();await page.waitForFunction(()=>document.querySelector('[data-merge-result]').textContent.includes('уже изменена'));assert.equal(await apply.isDisabled(),true);rejectPreview=false;
    let resolveGate;gate={promise:new Promise(resolve=>{resolveGate=resolve;})};const count=payloads.length;await preview.click();await page.waitForFunction(()=>document.querySelector('[data-merge-preview]').textContent.includes('Проверяем'));
    assert.equal(await pick(311).isDisabled(),true);assert.equal(await dialog.locator('[data-open-folder="12"]').isDisabled(),true);assert.equal(await dialog.locator('[data-close]').isDisabled(),true);await page.evaluate(()=>document.querySelector('[data-merge-preview]').click());assert.equal(payloads.length,count+1);
    // A script-induced late input must invalidate even a pending preview response.
    await page.evaluate(()=>{const input=document.querySelector('[data-merge-name]');input.value='Серия 3 · позднее изменение';input.dispatchEvent(new Event('input',{bubbles:true}));});resolveGate();gate=null;
    await page.waitForFunction(()=>!document.querySelector('[data-merge-preview]').disabled);assert.equal(await apply.isDisabled(),true);assert.equal(await mergeName.inputValue(),'Серия 3 · позднее изменение');
    await preview.click();await page.waitForFunction(()=>!document.querySelector('[data-merge-apply]').disabled);rejectApply=true;await apply.click();await page.waitForFunction(()=>document.querySelector('[data-merge-result]').textContent.includes('Каталог изменился'));assert.equal(await apply.isDisabled(),true);assert.match(await dialog.locator('[data-merge-result]').innerText(),/Разные файлы/);rejectApply=false;
    await preview.click();await page.waitForFunction(()=>!document.querySelector('[data-merge-apply]').disabled);
    await dialog.locator('[data-merge-result]').scrollIntoViewIfNeeded();assert.equal(await dialog.evaluate(element=>element.scrollWidth<=element.clientWidth),true,'Mobile dialog fits');assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
    fs.mkdirSync(output,{recursive:true});await page.screenshot({path:path.join(output,'episode-merge-mobile.png')});
    const token=sequence;await apply.click();await page.waitForFunction(()=>document.querySelector('[data-catalog-feedback]').textContent.includes('Серии объединены'));
    assert.deepEqual(payloads.at(-1),{operation:'merge',action:'apply',folder_id:11,target_id:312,episode_ids:[311,312],name:'Серия 3 · позднее изменение',expected_token:'preview-'+token});
    assert.equal(await pick(311).count(),0);assert.equal(await pick(312).count(),1);assert.equal(await dialog.locator('[data-file-name="312"]').inputValue(),'Серия 3 · позднее изменение');assert.equal(await dialog.locator('[data-open-folder="11"]').evaluate(element=>element.classList.contains('active')),true);assert.equal(await dialog.locator('[data-merge-episode]:checked').count(),0);
    await dialog.locator('[data-open-folder="12"]').click();assert.equal(await dialog.locator('[data-file-name]').count(),2);
    await dialog.locator('[data-close]').click();await page.evaluate(()=>editCatalogFolders(2000));assert.equal(await page.locator('[data-episode-merge]').count(),0);assert.equal(await page.locator('[data-merge-episode]').count(),0);await page.locator('dialog [data-close]').click();
    await page.evaluate(()=>editCatalogFolders(1000));await page.setViewportSize({width:1440,height:1000});assert.equal(await page.locator('dialog.catalog-explorer-dialog').evaluate(element=>element.scrollWidth<=element.clientWidth),true);await page.screenshot({path:path.join(output,'episode-merge-desktop.png'),fullPage:true});
    assert.deepEqual(errors,[]);assert.equal(await page.evaluate(()=>window.__xss),undefined);
    const report={ok:true,mobile_width:390,desktop_width:1440,checks:['same-folder selection and target restriction','save-first preserves dirty names and selected episodes','read-only preview and explicit apply token','selection, target and name invalidate preview','busy request prevents duplicate actions','late preview response discarded','quality/voice conflicts and stale API errors block apply','safe HTML rendering','apply refresh retains current folder and main ID','movie cards do not expose episode merge','compact variant list','mobile and desktop fit'],preview_count:payloads.filter(body=>body.action==='preview').length,apply_count:payloads.filter(body=>body.action==='apply').length};
    fs.writeFileSync(path.join(output,'episode-merge-ui.json'),JSON.stringify(report,null,2));console.log('Episode merge UI: preview/apply, save-first, same-folder selection, races, conflicts, escaped text and mobile/desktop layout OK.');
  }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
