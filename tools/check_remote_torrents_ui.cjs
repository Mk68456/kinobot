// All requests are intercepted; no real tracker, qBittorrent or Telegram is used.
const {chromium}=require('C:/Users/Vertex/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
(async()=>{
  const root=path.join(__dirname,'..','webpanel','static'),requests=[],commands=[],errors=[];
  const torrents={busy:false,search:{state:'idle',results:[]},jobs:[],folder:'D:/Downloads'};
  const agents=[{instance_id:'computer-a',name:'Домашний ПК',online:true,folders:['D:/Series'],status:{busy:true,operation_busy:false,folder:'D:/Series',capabilities:{project_control:1,remote_render:1,torrent_control:1},mode:'single',hardware:'auto',load:'balanced',qualities:['720p'],catalog_section:'movies',projects:[],torrents}},
    {instance_id:'computer-b',name:'Другой ПК',online:true,folders:[],status:{capabilities:{remote_render:1,torrent_control:1},torrents:{busy:false,search:{state:'idle',results:[]},jobs:[]}}}];
  const browser=await chromium.launch({headless:true,executablePath:'C:/Users/Vertex/AppData/Local/ms-playwright/chromium_headless_shell-1208/chrome-headless-shell-win64/chrome-headless-shell.exe'});
  try{
    const page=await browser.newPage({viewport:{width:390,height:844}});
    page.on('pageerror',e=>errors.push(e.message));
    await page.route('http://127.0.0.1:9876/**',async route=>{
      const req=route.request(),url=new URL(req.url());let data;
      if(!url.pathname.startsWith('/api/')){
        const name=url.pathname==='/'?'index.html':url.pathname.replace(/^\/static\//,'');
        assert.ok(['index.html','app.js','contests.js','style.css','styles.css'].includes(name));
        return route.fulfill({body:fs.readFileSync(path.join(root,name)),contentType:name.endsWith('.js')?'application/javascript':name.endsWith('.css')?'text/css':'text/html'});
      }
      if(url.pathname==='/api/session')data={admin_id:99,csrf:'fixture',max_file_bytes:2e9};
      else if(url.pathname==='/api/catalog-sections')data={sections:[{key:'movies',title:'Фильмы',enabled:true}]};
      else if(url.pathname==='/api/assets')data={assets:[]};
      else if(url.pathname==='/api/drafts')data={drafts:[]};
      else if(url.pathname==='/api/launcher-control')data={agents,commands};
      else if(url.pathname==='/api/launcher-control/commands'){
        const body=req.postDataJSON();requests.push(body);
        data={id:requests.length,...body,status:'done',result:'Выполнено'};commands.unshift(data);
        if(body.action==='torrent_search')torrents.search={state:'searching',message:'Ищу…',results:[]};
        if(body.action==='torrent_add')torrents.jobs=[{id:'job-a',title:'Example S01',status:'choose_files',selected:[0],files:[{index:0,name:'S01E01.mkv',size:2**30},{index:1,name:'S01E02.mkv',size:2**30}]}];
        if(body.action==='torrent_start'){torrents.busy=true;torrents.jobs[0].status='downloading';torrents.jobs[0].progress=42;}
        if(body.action==='torrent_pause'){torrents.busy=false;torrents.jobs[0].status='paused';}
      }else throw new Error('Unexpected '+url.pathname);
      return route.fulfill({json:data});
    });
    await page.goto('http://127.0.0.1:9876/');await page.locator('#workspace').waitFor({state:'visible'});
    await page.evaluate(()=>showTab('remote'));await page.waitForFunction(()=>!document.querySelector('#torrent-search').disabled);
    await page.locator('#torrent-query').fill('Example');await page.locator('#torrent-seasons').fill('1');
    await page.locator('#torrent-search').click();await page.waitForFunction(()=>document.querySelector('#torrent-search-status').textContent==='Ищу…');
    assert.deepEqual(requests[0],{action:'torrent_search',payload:{query:'Example',seasons:'1'},instance_id:'computer-a'});
    assert.equal(await page.locator('#torrent-search').isDisabled(),true);
    assert.equal(await page.locator('#torrent-search-stop').isEnabled(),true);
    torrents.search={state:'done',message:'Найдено: 1',results:[{id:'result-a',title:'Example <img src=x onerror="window.__xss=1">',tracker:'Rutor',quality:'1080p',size:'2 ГиБ',seeds:15,issues:[]}]};
    await page.evaluate(()=>loadRemote());await page.locator('#torrent-add-result').click();
    await page.waitForFunction(()=>document.querySelector('#torrent-jobs').value==='job-a');
    assert.deepEqual(requests.at(-1).payload,{result_id:'result-a',seasons:'1'});
    await page.locator('#torrent-file-box summary').click();
    await page.locator('[data-torrent-file="1"]').check();await page.evaluate(()=>loadRemote());
    assert.equal(await page.locator('[data-torrent-file="1"]').isChecked(),true);
    await page.locator('#torrent-start').click();await page.waitForFunction(()=>document.querySelector('#torrent-job-info').textContent.includes('42%'));
    assert.deepEqual(requests.at(-1).payload,{job_id:'job-a',file_ids:[0,1],qualities:['720p']});
    assert.equal(await page.locator('#torrent-add-result').isDisabled(),true);
    await page.locator('#torrent-pause').click();await page.waitForFunction(()=>document.querySelector('#torrent-start').disabled===false);
    assert.equal(requests.at(-1).action,'torrent_pause');
    await page.locator('#torrent-files-refresh').click();await page.waitForFunction(()=>document.querySelector('#remote-feedback').textContent==='Выполнено');
    assert.equal(requests.at(-1).action,'torrent_files');
    assert.equal(await page.evaluate(()=>window.__xss),undefined);
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
    torrents.search.results[0].title='Example / Пример · сезон 1';await page.evaluate(()=>loadRemote());
    await page.locator('#remote-torrents').screenshot({path:path.join(__dirname,'..','outputs','remote-torrents-mobile.png')});
    await page.locator('#remote-device').selectOption('computer-b');
    assert.equal(await page.locator('#torrent-query').inputValue(),'');
    assert.equal(await page.locator('#torrent-jobs option').count(),0);
    await page.locator('#torrent-magnet').locator('..').locator('..').locator('summary').click();
    const magnet='magnet:?xt=urn:btih:'+'a'.repeat(40);
    await page.locator('#torrent-magnet').fill(magnet);await page.locator('#torrent-add-magnet').click();
    await page.waitForFunction(()=>document.querySelector('#remote-feedback').textContent==='Выполнено');
    assert.deepEqual(requests.at(-1),{action:'torrent_add',payload:{magnet,seasons:'0'},instance_id:'computer-b'});
    agents[1].online=false;await page.evaluate(()=>loadRemote());
    assert.equal(await page.locator('#torrent-search').isDisabled(),true);
    agents[1].online=true;agents[1].status.capabilities={};await page.evaluate(()=>loadRemote());
    assert.match(await page.locator('#torrent-hint').innerText(),/Обновите/);
    assert.deepEqual(errors,[]);
    console.log('Mobile torrents: search, results, magnet, files, start/pause/resume, progress, per-computer isolation, escaping and offline controls OK.');
  }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
