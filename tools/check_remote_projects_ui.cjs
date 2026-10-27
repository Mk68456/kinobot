// Isolated mobile fixture: no live server, media or Telegram operations.
const {chromium}=require('C:/Users/Vertex/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
(async()=>{
  const root=path.join(__dirname,'..','webpanel','static'),requests=[],commands=[],errors=[];
  const project={id:'project-a',title:'The Boys <img src=x onerror="window.__xss=1">',folder:'D:/Series',state:'cards_ready',message:'Карточки созданы',ready:96,total:96,priority:0,
    variants:[{source:'Episode.S01E01.mkv',quality:'1080p',voice:'Русский',state:'done'},{source:'Episode.S01E01.mkv',quality:'720p',voice:'English',state:'running',status:'Кодирование',detail:'42%'}]};
  const agents=[{instance_id:'computer-a',name:'Домашний ПК',online:true,folders:['D:/Series'],status:{busy:true,operation_busy:false,folder:'D:/Series',capabilities:{project_control:1,remote_render:1,folder_browser:1},remote_browse_enabled:true,mode:'single',hardware:'auto',load:'balanced',qualities:['720p'],catalog_section:'movies',projects:[project],project_parallelism:2}},
    {instance_id:'computer-old',name:'Старый лаунчер',online:true,folders:[],status:{projects:[]}}];
  const browser=await chromium.launch({headless:true,executablePath:'C:/Users/Vertex/AppData/Local/ms-playwright/chromium_headless_shell-1208/chrome-headless-shell-win64/chrome-headless-shell.exe'});
  try{
    const page=await browser.newPage({viewport:{width:390,height:844}});
    page.on('pageerror',e=>errors.push(e.message));page.on('dialog',dialog=>dialog.accept());
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
      }else throw new Error('Unexpected URL '+url.pathname);
      return route.fulfill({json:data});
    });
    await page.goto('http://127.0.0.1:9876/');await page.locator('#workspace').waitFor({state:'visible'});
    await page.evaluate(()=>showTab('remote'));await page.locator('#project-select option').waitFor({state:'attached'});
    assert.equal(await page.locator('#project-add').isEnabled(),true);
    assert.equal(await page.locator('#remote-folder-path').isEnabled(),true);
    await page.locator('#project-series').fill('123');await page.locator('#project-season').fill('2');
    await page.locator('#project-add').click();
    await page.waitForFunction(()=>document.querySelector('#remote-feedback').textContent==='Выполнено');
    assert.deepEqual(requests[0].payload.series,{movie_number:123,season_number:2});
    assert.equal(requests[0].action,'project_add');assert.equal(requests[0].instance_id,'computer-a');
    for(const action of ['pause','priority','cards','send']){
      await page.locator(`[data-project-action="${action}"]`).click();
      await page.waitForFunction(()=>document.querySelector('#remote-feedback').textContent==='Выполнено');
      assert.equal(requests.at(-1).action,'project_'+action);
    }
    assert.equal(requests.at(-1).payload.confirmed,true);
    project.state='paused';await page.evaluate(()=>loadRemote());await page.locator('[data-project-action="resume"]').click();
    await page.waitForFunction(()=>document.querySelector('#remote-feedback').textContent==='Выполнено');
    assert.equal(requests.at(-1).action,'project_resume');
    await page.locator('#project-pause-all').click();await page.waitForFunction(()=>document.querySelector('#remote-feedback').textContent==='Выполнено');
    agents[0].status.busy=false;await page.evaluate(()=>loadRemote());
    await page.locator('#project-parallelism').selectOption('3');await page.locator('#project-set-parallelism').click();
    await page.waitForFunction(()=>document.querySelector('#remote-feedback').textContent==='Выполнено');
    assert.deepEqual(requests.at(-1).payload,{parallelism:3});
    await page.locator('#project-details summary').click();await page.evaluate(()=>loadRemote());
    assert.equal(await page.locator('#project-details details[open]').count(),1);
    assert.equal(await page.evaluate(()=>window.__xss),undefined);
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
    await page.locator('#remote-project-panel').screenshot({path:path.join(__dirname,'..','outputs','remote-projects-mobile.png')});
    agents[0].online=false;await page.evaluate(()=>loadRemote());
    assert.equal(await page.locator('#project-add').isDisabled(),true);
    assert.equal(await page.locator('[data-project-action="pause"]').isDisabled(),true);
    await page.locator('#remote-device').selectOption('computer-old');
    assert.match(await page.locator('#remote-project-hint').innerText(),/Обновите/);
    assert.equal(await page.locator('#project-series').inputValue(),'');
    assert.deepEqual(errors,[]);
    console.log('Mobile projects: all actions, confirmation, busy rendering, per-PC drafts, offline/old clients, escaping and layout OK.');
  }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
