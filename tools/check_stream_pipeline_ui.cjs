// Local fixture: all HTTP requests are intercepted; no production service is used.
const {chromium}=require('C:/Users/Vertex/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
(async()=>{
  const root=path.join(__dirname,'..','webpanel','static'),output=path.join(__dirname,'..','outputs');
  const errors=[],requests=[];
  const browser=await chromium.launch({headless:true,executablePath:'C:/Users/Vertex/AppData/Local/ms-playwright/chromium_headless_shell-1208/chrome-headless-shell-win64/chrome-headless-shell.exe'});
  try{
    const page=await browser.newPage({viewport:{width:390,height:844}});
    page.on('pageerror',error=>errors.push(error.message));
    await page.route('**/*',async route=>{
      const request=route.request(),url=new URL(request.url());
      assert.equal(url.origin,'http://127.0.0.1:9883');
      requests.push(url.pathname+url.search);
      assert.equal(request.method(),'GET','Fixture must not publish or mutate anything');
      if(!url.pathname.startsWith('/api/')){
        const name=url.pathname==='/'?'index.html':url.pathname.replace(/^\/static\//,'');
        assert.ok(['index.html','app.js','contests.js','style.css'].includes(name));
        return route.fulfill({body:fs.readFileSync(path.join(root,name)),contentType:name.endsWith('.js')?'application/javascript':name.endsWith('.css')?'text/css':'text/html'});
      }
      let data;
      if(url.pathname==='/api/session')data={admin_id:99,csrf:'fixture',max_file_bytes:2e9};
      else if(url.pathname==='/api/catalog-sections')data={sections:[{key:'movies',emoji:'🎬',title:'Фильмы',enabled:true,required_content_type:'movie'}]};
      else if(url.pathname==='/api/assets')data={assets:[]};
      else if(url.pathname==='/api/drafts')data={drafts:[]};
      else if(url.pathname==='/api/catalog'){
        const movies=[{id:1,title:'Возраст указан',content_type:'movie',catalog_section:'movies',age_rating:'12+'},
          {id:2,title:'Нужна проверка',content_type:'movie',catalog_section:'movies',age_rating:null}];
        data={movies:url.searchParams.get('unrated')==='1'?movies.slice(1):movies,has_more:false};
      }else throw new Error('Unexpected request: '+request.url());
      return route.fulfill({json:data});
    });
    await page.goto('http://127.0.0.1:9883/');await page.locator('#workspace').waitFor({state:'visible'});
    await page.evaluate(()=>{
      const id='a'.repeat(32);state.id='fixture';state.items=[{asset_id:id,name:'Файл',category:'1080p',quality:'1080p',voice:''}];
      state.assets=[{id,status:'ready',file_id:'fixture-only',filename:'film.mp4',size:1000,received:1000}];
      $('title').value='Новый фильм';$('content-type').value='movie';$('age-rating').value='';renderQueue();syncMode();
    });
    assert.equal(await page.locator('#publish').isDisabled(),true);
    assert.equal(await page.locator('#add-package').isDisabled(),true);
    assert.match(await page.locator('#publish-hint').innerText(),/возрастную маркировку/);
    await page.locator('#age-rating').selectOption('16+');await page.locator('#age-rating').dispatchEvent('input');
    assert.equal(await page.locator('#publish').isDisabled(),false);
    await page.evaluate(()=>{state.drafts=[{id:'fixture',status:'draft',stream_pending:4}];updatePublish();});
    assert.equal(await page.locator('#publish').isDisabled(),true);
    assert.equal(await page.locator('#add-package').isDisabled(),true);
    assert.match(await page.locator('#publish-hint').innerText(),/получено 1 из 4/);
    await page.screenshot({path:path.join(output,'stream-pending-mobile.png'),fullPage:true});
    await page.evaluate(()=>{state.drafts[0].stream_pending=0;updatePublish();});
    assert.equal(await page.locator('#publish').isDisabled(),false);
    await page.evaluate(()=>{$('age-rating').value='';state.movie={id:7,title:'Существующий фильм',content_type:'movie',catalog_section:'movies'};syncMode();});
    assert.equal(await page.locator('#publish').isDisabled(),false,'Adding files preserves the existing rating');
    await page.locator('[data-tab="catalog"]').click();await page.locator('#catalog-list h3').first().waitFor();
    assert.equal(await page.locator('#catalog-list h3').count(),2);
    assert.match(await page.locator('#catalog-list').innerText(),/12\+/);
    assert.match(await page.locator('#catalog-list').innerText(),/Возраст не указан/);
    await page.evaluate(()=>{state.catalogPage=4;});await page.locator('#catalog-unrated').check();
    await page.waitForFunction(()=>document.querySelectorAll('#catalog-list h3').length===1);
    assert.equal(await page.locator('#catalog-list h3').innerText(),'Нужна проверка');
    assert.ok(requests.some(url=>url.includes('/api/catalog?')&&url.includes('page=0')&&url.includes('unrated=1')));
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true,'Mobile fits');
    fs.mkdirSync(output,{recursive:true});await page.screenshot({path:path.join(output,'stream-pipeline-mobile.png'),fullPage:true});
    await page.setViewportSize({width:1440,height:1000});
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true,'Desktop fits');
    await page.screenshot({path:path.join(output,'stream-pipeline-desktop.png'),fullPage:true});
    await page.locator('#catalog-unrated').uncheck();await page.waitForFunction(()=>document.querySelectorAll('#catalog-list h3').length===2);
    assert.deepEqual(errors,[]);
    fs.writeFileSync(path.join(output,'stream-pipeline-ui.json'),JSON.stringify({ok:true,mobile_width:390,desktop_width:1440,checks:['incomplete stream cannot publish or join package','completed stream unlocks publication','new publication and package require rating','rated draft is publishable','adding files preserves existing movie rating','catalog displays age and unrated status','unrated filter resets pagination and can be disabled','mobile and desktop fit','no production calls or mutations']},null,2));
    console.log('Stream pipeline UI: publication gate, catalog filter and mobile/desktop layout OK.');
  }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
