// Fully isolated panel fixture: no requests reach Telegram, Kinobox or a real panel.
const {chromium}=require(process.env.KINOTIME_PLAYWRIGHT||'playwright');
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
(async()=>{
  const root=path.join(__dirname,'..','webpanel','static'),output=path.join(__dirname,'..','outputs');
  const browser=await chromium.launch({headless:true,...(process.env.KINOTIME_CHROMIUM?{executablePath:process.env.KINOTIME_CHROMIUM}:{})});
  const errors=[],requests=[],malicious='<img src=x onerror="window.__xss=1">';
  try{
    const page=await browser.newPage();
    page.on('pageerror',error=>errors.push(error.message));
    await page.route('**/*',async route=>{
      const url=new URL(route.request().url());requests.push(url.pathname);
      if(!url.pathname.startsWith('/api/')){
        const name=url.pathname==='/'?'index.html':url.pathname.replace(/^\/static\//,'');
        if(name==='favicon.svg')return route.fulfill({body:'',contentType:'image/svg+xml'});
        assert.ok(['index.html','app.js','contests.js','style.css'].includes(name),name);
        return route.fulfill({body:fs.readFileSync(path.join(root,name)),contentType:name.endsWith('.js')?'application/javascript':name.endsWith('.css')?'text/css':'text/html'});
      }
      let data;
      if(url.pathname==='/api/session')data={admin_id:99,csrf:'fixture',max_file_bytes:2e9};
      else if(url.pathname==='/api/catalog-sections')data={sections:[{key:'series',title:'Сериалы',enabled:true,required_content_type:'series'}]};
      else if(url.pathname==='/api/assets')data={assets:[]};
      else if(url.pathname==='/api/drafts')data={drafts:[]};
      else if(url.pathname==='/api/content-requests')data={items:[{
        id:1,title:'Пример сериала',content_type:'series',season:2,episode:3,voice:'Studio A '+malicious,
        verification:'Запрошенные серии вышли по данным TMDB.',overview:'Описание произведения.',
        voice_evidence_note:'Озвучка найдена у произведения · Kinobox: kodik '+malicious+' · проверено 2026-10-25T12:00:00+00:00. Наличие нужных серий в этой озвучке требует отдельной проверки.',
        status:'new',votes:4,
      },{id:2,title:'Заявка с ручным вводом',content_type:'movie',season:0,episode:0,voice:'Другая студия',
         verification:'',voice_evidence_note:'',overview:'',status:'new',votes:1}],total:2,offset:0,limit:50};
      else throw new Error('Unexpected fixture request: '+url.pathname);
      return route.fulfill({json:data});
    });
    await page.goto('http://127.0.0.1:9882/');
    await page.locator('#workspace').waitFor({state:'visible'});
    await page.locator('[data-tab="requests"]').click();
    await page.locator('.request-card').first().waitFor();
    assert.equal(await page.locator('.request-card').count(),2);
    const text=await page.locator('.request-card').first().innerText();
    for(const expected of ['сезон 2','серия 3','Studio A','kodik','отдельной проверки'])assert.ok(text.includes(expected));
    assert.equal(await page.locator('.request-card img').count(),0);
    assert.equal(await page.evaluate(()=>window.__xss),undefined);
    fs.mkdirSync(output,{recursive:true});
    await page.screenshot({path:path.join(output,'request-voices-desktop.png')});
    await page.setViewportSize({width:390,height:844});
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
    assert.ok(await page.locator('.request-card>div').first().evaluate(el=>el.getBoundingClientRect().width)>250,'Text-only card uses the full mobile width');
    await page.screenshot({path:path.join(output,'request-voices-mobile.png'),fullPage:true});
    assert.deepEqual(errors,[]);
    fs.writeFileSync(path.join(output,'request-voices-ui.json'),JSON.stringify({ok:true,errors,requests,checks:['source note visible','manual request visible','untrusted text escaped','mobile fits']},null,2));
    console.log('Request voices panel: desktop, mobile, source evidence and text escaping passed.');
  }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
