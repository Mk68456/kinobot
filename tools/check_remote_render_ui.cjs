// Local route fixtures only: no panel, Telegram, or computer filesystem is accessed.
const {chromium}=require('C:/Users/Vertex/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
(async()=>{
  const root=path.join(__dirname,'..','webpanel','static'),output=path.join(__dirname,'..','outputs');
  const commands=[],requests=[],errors=[],browserErrors=[];
  const agent=(id,name,folder,qualities)=>({instance_id:id,name,online:true,last_seen:'08.10.2026 12:00:00',folders:[folder],status:{capabilities:{folder_browser:1,remote_render:1},remote_browse_enabled:true,folder,qualities,mode:'single',hardware:'auto',load:'balanced',catalog_section:'movies',message:'Ожидает команду',busy:false,prepared_count:0}});
  const agents=[agent('pc-alpha','Ноутбук', 'D:/Laptop',['720p']),agent('pc-bravo','ПК <img src=x onerror="window.__xss=1">','E:/Desktop',['360p'])];
  let sequence=0,rejectPrepare=false,holdBrowse=true,holdPrepare=false,polls=0;
  function runningJob(target,body){target.status={...target.status,folder:body.payload.folder,busy:true,prepared_count:0,message:'Кодирование · 18%',job:{id:'render-'+sequence,kind:'prepare',folder:body.payload.folder,mode:body.payload.mode,state:'running',message:'Кодирование 720p',started_at:'08.10.2026 12:01:00',ready:2,total:16,current:'Episode 3.mkv',percent:18}};}
  const listings={
    '':{folder:'',parent:null,entries:[{name:'Диск C:',path:'C:/'},{name:'Диск D:',path:'D:/'}],offset:0,next_offset:null},
    'C:/':{folder:'C:/',parent:'',entries:[{name:'Проекты',path:'C:/Projects'},{name:'<img src=x onerror="window.__xss=1">',path:'C:/Safe <folder>'}],offset:0,next_offset:null},
    'C:/Projects':{folder:'C:/Projects',parent:'C:/',entries:[{name:'Первый проект',path:'C:/Projects/First'}],offset:0,next_offset:100},
    'C:/Projects:100':{folder:'C:/Projects',parent:'C:/',entries:[{name:'Второй проект',path:'C:/Projects/Second'}],offset:100,next_offset:null},
    'Z:/Sources':{folder:'Z:/Sources',parent:'Z:/',entries:[{name:'Сезон 1',path:'Z:/Sources/Season 1'}],offset:0,next_offset:null},
  };
  const browser=await chromium.launch({headless:true,executablePath:process.env.KINOTIME_CHROMIUM||'C:/Users/Vertex/AppData/Local/ms-playwright/chromium_headless_shell-1208/chrome-headless-shell-win64/chrome-headless-shell.exe'});
  try{
    const page=await browser.newPage({viewport:{width:390,height:844}});
    page.on('pageerror',error=>errors.push(error.message));
    page.on('requestfailed',request=>browserErrors.push(request.url()));
    await page.route('http://127.0.0.1:9876/**',async route=>{
      const request=route.request(),url=new URL(request.url());let data;
      if(!url.pathname.startsWith('/api/')){
        const name=url.pathname==='/'?'index.html':url.pathname.replace(/^\/static\//,'');
        assert.ok(['index.html','app.js','contests.js','style.css','styles.css'].includes(name));
        return route.fulfill({body:fs.readFileSync(path.join(root,name)),contentType:name.endsWith('.js')?'application/javascript':name.endsWith('.css')?'text/css':'text/html'});
      }
      if(url.pathname==='/api/session')data={admin_id:99,csrf:'fixture',max_file_bytes:2e9};
      else if(url.pathname==='/api/catalog-sections')data={sections:[{key:'movies',title:'Фильмы',enabled:true,required_content_type:'movie'},{key:'cartoons',title:'Мультсериалы',enabled:true,required_content_type:'series'}]};
      else if(url.pathname==='/api/assets')data={assets:[]};
      else if(url.pathname==='/api/drafts')data={drafts:[]};
      else if(url.pathname==='/api/launcher-control'){polls++;data={agents,commands};}
      else if(url.pathname==='/api/launcher-control/commands'){
        const body=request.postDataJSON();requests.push(body);
        if(rejectPrepare&&body.action==='prepare')return route.fulfill({status:400,json:{error:'Папка не найдена на выбранном компьютере.'}});
        const command={id:++sequence,...body,status:'done',created_at:'08.10.2026 12:01:00',result:'Команда принята'};
        if(body.action==='browse_folder'){command.data=listings[body.payload.folder+(body.payload.offset?':'+body.payload.offset:'')];if(holdBrowse){command.status='claimed';holdBrowse=false;}}
        if(body.action==='prepare'){
          const target=agents.find(item=>item.instance_id===body.instance_id);
          if(!holdPrepare)runningJob(target,body);
        }
        if(body.action==='stop'){
          const target=agents.find(item=>item.instance_id===body.instance_id);target.status.busy=false;target.status.job.state='paused';target.status.job.message='Подготовка остановлена. Готовые копии сохранены.';
        }
        if(body.action==='send'){const target=agents.find(item=>item.instance_id===body.instance_id);target.status.busy=true;target.status.job={...target.status.job,id:'send-'+sequence,kind:'send',state:'running',ready:2,message:'Передача файла в Telegram'};}
        commands.unshift(command);data={id:command.id,status:'pending'};
      }else throw new Error('Unexpected fixture request: '+request.url());
      return route.fulfill({json:data});
    });
    await page.goto('http://127.0.0.1:9876/');await page.locator('#workspace').waitFor({state:'visible'});await page.evaluate(()=>showTab('remote'));
    await page.locator('#remote-device option').first().waitFor({state:'attached'});
    await page.locator('#remote-folder-path').fill('D:/New series');await page.locator('#remote-hardware').selectOption('intel');await page.locator('#remote-load').selectOption('eco');await page.locator('#remote-mode').selectOption('batch');await page.locator('#remote-section').selectOption('cartoons');
    await page.locator('#remote-qualities input[value="360p"]').check();
    const previousPolls=polls;await page.waitForFunction(()=>document.querySelector('#remote-folder-path').value==='D:/New series');await page.waitForTimeout(3200);assert.ok(polls>previousPolls,'Visible remote page polls automatically');
    assert.equal(await page.locator('#remote-folder-path').inputValue(),'D:/New series');assert.equal(await page.locator('#remote-hardware').inputValue(),'intel');assert.equal(await page.locator('#remote-qualities input[value="360p"]').isChecked(),true);
    await page.locator('#remote-device').selectOption('pc-bravo');assert.equal(await page.locator('#remote-folder-path').inputValue(),'E:/Desktop');assert.equal(await page.locator('#remote-hardware').inputValue(),'auto');assert.equal(await page.locator('#remote-qualities input[value="720p"]').isChecked(),false);
    await page.locator('#remote-device').selectOption('pc-alpha');assert.equal(await page.locator('#remote-folder-path').inputValue(),'D:/New series');assert.equal(await page.locator('#remote-hardware').inputValue(),'intel');
    await page.locator('#remote-browse').click();assert.match(await page.locator('#remote-browser-status').innerText(),/читает список/);assert.match(await page.locator('#remote-history').innerText(),/получил команду/);commands[0].status='done';await page.locator('#remote-refresh').click();await page.locator('[data-remote-path="C:/"]').waitFor();await page.locator('[data-remote-path="C:/"]').click();await page.locator('[data-remote-path="C:/Projects"]').waitFor();
    assert.equal(await page.locator('#remote-browser-entries img').count(),0);assert.equal(await page.evaluate(()=>window.__xss),undefined);
    await page.locator('[data-remote-path="C:/Projects"]').click();await page.locator('#remote-browse-next').waitFor({state:'visible'});await page.locator('#remote-browse-next').click();await page.locator('[data-remote-path="C:/Projects/Second"]').waitFor();
    assert.deepEqual(requests.at(-1).payload,{folder:'C:/Projects',offset:100});
    await page.locator('#remote-browse-up').click();await page.locator('[data-remote-path="C:/Projects"]').waitFor();assert.deepEqual(requests.at(-1).payload,{folder:'C:/',offset:0});
    await page.locator('#remote-browse-roots').click();await page.locator('[data-remote-path="D:/"]').waitFor();assert.deepEqual(requests.at(-1).payload,{folder:'',offset:0});
    await page.locator('#remote-browse-close').click();await page.locator('#remote-folder-path').fill('Z:/Sources');await page.locator('#remote-open-path').click();await page.locator('[data-remote-path="Z:/Sources/Season 1"]').waitFor();await page.locator('#remote-browse-select').click();
    assert.equal(await page.locator('#remote-folder-path').inputValue(),'Z:/Sources');assert.equal(await page.locator('#remote-send').isDisabled(),true);
    assert.match(await page.locator('#remote-start-summary').innerText(),/Z:\/Sources/);
    await page.locator('#remote-prepare').click();await page.waitForFunction(()=>document.querySelector('#remote-job').textContent.includes('2 / 16 MP4'));
    await page.waitForFunction(()=>{const box=document.querySelector('#remote-job').getBoundingClientRect();return box.top>=-1&&box.top<80&&box.bottom<=window.innerHeight;});
    const prepare=requests.find(item=>item.action==='prepare');assert.deepEqual(prepare,{action:'prepare',payload:{folder:'Z:/Sources',mode:'batch',qualities:['720p','360p'],hardware:'intel',load:'eco',catalog_section:'cartoons'},instance_id:'pc-alpha'});
    assert.equal(await page.locator('#remote-prepare').isDisabled(),true);assert.equal(await page.locator('#remote-hardware').isDisabled(),true);assert.equal(await page.locator('#remote-qualities input[value="source"]').isDisabled(),true);assert.match(await page.locator('#remote-job').innerText(),/текущий файл 18%/);
    await page.locator('#remote-browse').click();await page.locator('[data-remote-path="C:/"]').waitFor();await page.locator('[data-remote-path="C:/"]').click();await page.locator('[data-remote-path="C:/Projects"]').waitFor();assert.equal(await page.locator('#remote-browse-select').isDisabled(),true);await page.locator('#remote-browse-close').click();
    await page.locator('#remote-stop').click();await page.waitForFunction(()=>document.querySelector('#remote-job').textContent.includes('Задача остановлена'));assert.equal(await page.locator('#remote-prepare').isDisabled(),false);
    agents[0].status.job.state='failed';agents[0].status.job.message='Не удалось запустить NVENC <script>window.__xss=1</script>';agents[0].status.job.finished_at='08.10.2026 12:05:00';commands.unshift({id:999,instance_id:'pc-alpha',action:'prepare',status:'error',result:'NVENC недоступен'});
    await page.locator('#remote-refresh').click();await page.waitForFunction(()=>document.querySelector('#remote-job').textContent.includes('Задача завершилась с ошибкой'));assert.match(await page.locator('#remote-history').innerText(),/NVENC недоступен/);assert.equal(await page.locator('#remote-job script').count(),0);
    rejectPrepare=true;await page.locator('#remote-prepare').click();await page.waitForFunction(()=>document.querySelector('#remote-feedback').textContent.includes('Папка не найдена'));assert.equal(await page.locator('#remote-prepare').isDisabled(),false);rejectPrepare=false;
    agents[0].status.prepared_count=16;agents[0].status.job={...agents[0].status.job,state:'completed',ready:16,message:'Все MP4 подготовлены'};await page.locator('#remote-refresh').click();await page.waitForFunction(()=>!document.querySelector('#remote-send').disabled);
    await page.locator('#remote-folder-path').fill('D:/Other project');assert.equal(await page.locator('#remote-send').isDisabled(),true);await page.locator('#remote-folder-path').fill('Z:/Sources');assert.equal(await page.locator('#remote-send').isDisabled(),false);
    await page.locator('#remote-send').click();await page.waitForFunction(()=>document.querySelector('#remote-job').textContent.includes('2 / 16 MP4 отправлено'));assert.deepEqual(requests.at(-1),{action:'send',payload:prepare.payload,instance_id:'pc-alpha'});await page.locator('#remote-stop').click();await page.waitForFunction(()=>!document.querySelector('#remote-prepare').disabled);
    holdPrepare=true;await page.locator('#remote-folder-path').fill('D:/Another project');await page.locator('#remote-prepare').click();await page.waitForFunction(()=>document.querySelector('#remote-job').textContent.includes('Ожидаю запуск на компьютере'));assert.doesNotMatch(await page.locator('#remote-job').innerText(),/Подготовка завершена/);assert.equal(await page.locator('#remote-prepare').isDisabled(),true);runningJob(agents[0],requests.at(-1));holdPrepare=false;await page.locator('#remote-refresh').click();await page.waitForFunction(()=>document.querySelector('#remote-job').textContent.includes('2 / 16 MP4'));await page.locator('#remote-stop').click();await page.waitForFunction(()=>!document.querySelector('#remote-prepare').disabled);
    agents[0].status.remote_browse_enabled=false;await page.locator('#remote-refresh').click();await page.waitForFunction(()=>document.querySelector('#remote-browse').disabled);assert.match(await page.locator('#remote-browse-hint').innerText(),/выключен/);
    agents[0].online=false;await page.locator('#remote-refresh').click();await page.waitForFunction(()=>document.querySelector('#remote-prepare').disabled);assert.equal(await page.locator('#remote-send').isDisabled(),true);
    agents[0].online=true;agents[0].status.remote_browse_enabled=true;await page.locator('#remote-refresh').click();await page.waitForFunction(()=>!document.querySelector('#remote-prepare').disabled);
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth),true,'Mobile viewport has no horizontal overflow');
    await page.locator('#remote-prepare').scrollIntoViewIfNeeded();fs.mkdirSync(output,{recursive:true});await page.screenshot({path:path.join(output,'remote-render-mobile.png'),fullPage:true});
    await page.setViewportSize({width:1440,height:1100});await page.evaluate(()=>window.scrollTo(0,0));assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth),true);await page.screenshot({path:path.join(output,'remote-render-desktop.png'),fullPage:true});
    assert.deepEqual(errors,[]);assert.deepEqual(browserErrors,[]);
    const report={ok:true,mobile_width:390,desktop_width:1440,checks:['automatic polling preserves pending choices','device isolation','root, parent, page and direct-path browsing','claimed browse waits for completion','explicit folder and render options in one start command','phone scrolls to task once after start','pending command does not present previous completed job as new','busy controls and browse inspection','current file versus MP4 progress','errors, offline and disabled filesystem access','sending only prepared selected folder with explicit payload','HTML text escaped','mobile and desktop fit'],requests:requests.map(request=>({action:request.action,instance_id:request.instance_id})),polls};
    fs.writeFileSync(path.join(output,'remote-render-ui.json'),JSON.stringify(report,null,2));console.log('Remote render UI: mobile/desktop, folder browsing, exact start payload, polling, isolation, busy/error/offline controls and escaped text OK.');
  }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
