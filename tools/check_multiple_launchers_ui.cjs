// Isolated browser fixture; no request reaches the live panel or Telegram.
const {chromium} = require('C:/Users/Vertex/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
(async () => {
  const root=path.join(__dirname,'..','webpanel','static'),errors=[],commands=[];
  const agents=[{instance_id:'computer-alpha',name:'Ноутбук',online:true,last_seen:'2026-10-06 12:00:00',
    folders:['D:/Laptop'],status:{message:'Подготовка 720p · 40%',folder:'D:/Laptop',busy:true,qualities:['720p']}},
    {instance_id:'computer-bravo',name:'Стационарный ПК',online:true,last_seen:'2026-10-06 12:00:00',
    folders:['E:/Desktop'],status:{message:'Ожидает',folder:'E:/Desktop',busy:false,qualities:['360p']}}];
  const browser=await chromium.launch({headless:true,executablePath:'C:/Users/Vertex/AppData/Local/ms-playwright/chromium_headless_shell-1208/chrome-headless-shell-win64/chrome-headless-shell.exe'});
  try {
    const page=await browser.newPage({viewport:{width:1440,height:1000}});
    page.on('pageerror',e=>errors.push(e.message));
    await page.route('http://127.0.0.1:9876/**',async route=>{
      const request=route.request(),url=new URL(request.url());let data;
      if(!url.pathname.startsWith('/api/')){
        const name=url.pathname==='/'?'index.html':url.pathname.replace(/^\/static\//,'');
        assert.ok(['index.html','app.js','contests.js','style.css','styles.css'].includes(name));
        return route.fulfill({body:fs.readFileSync(path.join(root,name)),contentType:name.endsWith('.js')?'application/javascript':name.endsWith('.css')?'text/css':'text/html'});
      }
      if(url.pathname==='/api/session')data={admin_id:99,csrf:'fixture',max_file_bytes:2e9};
      else if(url.pathname==='/api/catalog-sections')data={sections:[{key:'movies',title:'Фильмы',enabled:true,required_content_type:'movie'}]};
      else if(url.pathname==='/api/assets')data={assets:[]};
      else if(url.pathname==='/api/drafts')data={drafts:[]};
      else if(url.pathname==='/api/launcher-control')data={agents,commands:[{id:1,instance_id:'computer-alpha',action:'prepare',status:'done',result:'Карточки ноутбука'}]};
      else if(url.pathname==='/api/launcher-control/commands'){
        commands.push(request.postDataJSON());data={id:2,status:'pending'};
      }else throw new Error('Unexpected fixture request: '+request.url());
      return route.fulfill({json:data});
    });
    await page.goto('http://127.0.0.1:9876/');
    await page.locator('#workspace').waitFor({state:'visible'});
    await page.evaluate(()=>showTab('remote'));
    await page.locator('#remote-device option').first().waitFor({state:'attached'});
    assert.equal(await page.locator('#remote-device option').count(),2);
    assert.match(await page.locator('#remote-status').innerText(),/720p · 40%/);
    await page.locator('#remote-device').selectOption('computer-bravo');
    await page.waitForFunction(()=>document.querySelector('#remote-folder').value==='E:/Desktop');
    assert.doesNotMatch(await page.locator('#remote-history').innerText(),/Карточки ноутбука/);
    await page.locator('#remote-prepare').click();
    await page.waitForFunction(()=>document.querySelector('#remote-feedback').textContent.includes('Выбранный компьютер'));
    assert.deepEqual(commands[0],{action:'prepare',payload:{qualities:['360p']},instance_id:'computer-bravo'});
    await page.locator('#remote-switch').click();
    await page.waitForTimeout(100);
    assert.deepEqual(commands[1],{action:'switch_folder',payload:{folder:'E:/Desktop'},instance_id:'computer-bravo'});
    agents[1].online=false;
    await page.locator('#remote-refresh').click();
    await page.waitForFunction(()=>document.querySelector('#remote-send').disabled);
    assert.equal(await page.locator('#remote-prepare').isDisabled(),true);
    assert.deepEqual(errors,[]);
    fs.mkdirSync(path.join(__dirname,'..','outputs'),{recursive:true});
    await page.screenshot({path:path.join(__dirname,'..','outputs','multiple-launchers-panel.png'),fullPage:true});
    console.log('Two computers: own folders, qualities, progress, command history, targeted requests and offline controls OK.');
  }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
