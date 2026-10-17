function seriesFileIdentity(item){
  const path=(item.relative_path||'').replace(/\\/g,'/');
  const parts=path.split('/');
  const source=parts.length>1?parts[parts.length-2]:(path||item.name||'');
  const match=source.match(/(?:^|[^a-z0-9])s(\d{1,3})[ ._-]*e(\d{1,3})(?!\d)/i)||source.match(/(?:^|[^a-z0-9])(\d{1,3})x(\d{1,3})(?!\d)/i);
  if(!match)return null;
  const season=Number(match[1]),episode=Number(match[2]);
  if(!season||!episode)return null;
  let tail=source.slice(match.index+match[0].length).replace(/^[ ._-]+/,'').replace(/\.[a-z0-9]{2,4}$/i,'').trim();
  if(/(?:\b(?:19|20)\d{2}\b|web[ ._-]*dl|bluray|\d{3,4}p|exkinoray)/i.test(tail))tail='';
  const filename=parts.at(-1)||'';
  const voice=/^\d+\s*-\s*/.test(filename)?filename.replace(/\.[^.]+$/,'').replace(/^\d+\s*-\s*/,'').trim():'';
  return {key:`${season}:${episode}`,season,episode,name:tail?`Серия ${episode} — ${tail}`:`Серия ${episode}`,voice};
}
function seriesFillGroups(items){
  const groups=new Map();let skipped=0;
  for(const item of items){
    const identity=seriesFileIdentity(item);
    if(!identity){skipped++;continue;}
    if(!groups.has(identity.key))groups.set(identity.key,{...identity,items:[]});
    const group=groups.get(identity.key);group.items.push(item);
    const existing=(item.name||'').trim();
    if(existing&&!/s\d+[ ._-]*e\d+|\d+x\d+|\s\/\s/i.test(existing))group.name=existing;
  }
  return {groups:[...groups.values()].sort((a,b)=>a.season-b.season||a.episode-b.episode),skipped};
}
function applySeriesFill(groups,names,replace=false){
  for(const group of groups)for(const item of group.items){
    const identity=seriesFileIdentity(item);
    item.name=names[group.key]||group.name;
    if(replace||!item.season_number)item.season_number=group.season;
    if(replace||!(item.quality||'').trim())item.quality=inferSeriesQuality({...item,quality:''});
    if(replace||!(item.voice||'').trim())item.voice=identity.voice;
  }
}
function openSeriesFill(){
  const {groups,skipped}=seriesFillGroups(state.items);
  if(!groups.length){notice('Номера серий не найдены. Используйте имена S03E01 или 2x01.');return;}
  const dialog=document.createElement('dialog');dialog.className='series-fill-dialog';
  dialog.innerHTML=`<h2>Заполнить серии по файлам</h2><p>Распознано ${groups.length} серий из ${groups.reduce((n,g)=>n+g.items.length,0)} файлов. Нераспознанных файлов: ${skipped}.</p><p>Название задаётся один раз для всех качеств и озвучек серии. Проверьте названия перед применением.</p><form><div class="series-fill-list">${groups.map(g=>`<label>Сезон ${g.season} · серия ${g.episode} · файлов: ${g.items.length}<input data-series-name="${g.key}" maxlength="200" required value="${esc(g.name)}"></label>`).join('')}</div><label><input type="checkbox" data-replace> Заменить заполненные сезон, качество и озвучку данными из файлов</label><p>Без этой отметки заполняются только пустые поля. Названия будут применены ко всем вариантам показанных серий. Черновик сохраните после проверки.</p><div class="batch-toolbar"><button class="primary" type="submit">Применить к файлам</button><button class="secondary" type="button" data-close>Отмена</button></div></form>`;
  document.body.append(dialog);dialog.addEventListener('close',()=>dialog.remove());
  dialog.querySelector('[data-close]').onclick=()=>dialog.close();
  dialog.querySelector('form').onsubmit=event=>{
    event.preventDefault();const names={};
    for(const input of dialog.querySelectorAll('[data-series-name]')){names[input.dataset.seriesName]=input.value.trim();if(!names[input.dataset.seriesName]){input.focus();return;}}
    applySeriesFill(groups,names,dialog.querySelector('[data-replace]').checked);
    dirty();renderQueue();renderReview();dialog.close();notice(`Заполнено серий: ${groups.length}. Проверьте и сохраните черновик.`);
  };
  dialog.showModal();
}

'use strict';
const $ = id => document.getElementById(id);
const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const state = {session:null,assets:[],drafts:[],items:[],id:null,published:false,movie:null,tab:'upload',catalogPage:0,catalog:[],sections:[],busy:false,controller:null,paused:false,dirty:false,pendingAction:false,requestOffset:0,requests:[]};
const statusNames = {uploading:'На сервер',queued:'В очереди Telegram',sending:'В Telegram…',ready:'Готов',error:'Ошибка передачи',cancelled:'Отменён',waiting_telegram:'Ожидается из Desktop'};
const bytes = value => value>=1e9 ? `${(value/1e9).toFixed(2)} ГБ` : `${(value/1e6).toFixed(1)} МБ`;
function notice(text,error=false){$('notice').hidden=false;$('notice').textContent=text;$('notice').classList.toggle('error',error);}
function dirty(){state.seriesServerConflicts=[];state.dirty=true;$('save-state').textContent='Есть несохранённые изменения';}
state.seriesServerConflicts=[];
function seriesVariantConflicts(){
  if($('content-type').value!=='series')return [];
  const groups=new Map(),individual=state.items.some(item=>item.season_number);
  for(const [index,item] of state.items.entries()){
    let quality=(item.quality||'').trim();
    if(!quality)continue;
    if(['source','2160p','1080p','720p','480p','360p'].includes(quality.toLowerCase()))quality=quality.toLowerCase();
    const number=item.season_number||Number($('season-number').value)||null;
    const key=JSON.stringify([individual?number:$('season-select').value,(item.name||'').trim().toLowerCase(),quality,(item.voice||'').trim()]);
    if(!groups.has(key))groups.set(key,{rows:[],asset_ids:[],season:number?`Сезон ${number}`:$('season-name').value||'Выбранный сезон',name:item.name,quality,voice:(item.voice||'').trim(),existing:false});
    const group=groups.get(key);group.rows.push(index+1);group.asset_ids.push(item.asset_id);
  }
  return [...groups.values()].filter(group=>group.rows.length>1);
}
function seriesConflictText(group){
  return `${group.season} · ${group.name} · ${group.quality} · ${group.voice||'озвучка не указана'}: строки ${group.rows.join(', ')} ${group.existing?'совпадают с уже опубликованным вариантом':'повторяют один вариант'}.`;
}
function displaySeriesConflicts(conflicts){
  const byAsset=new Map();for(const group of conflicts)for(const id of group.asset_ids)byAsset.set(id,seriesConflictText(group));
  for(const row of $('queue').querySelectorAll('.queue-row')){
    const message=byAsset.get(row.dataset.id)||'';
    row.classList.toggle('variant-conflict',Boolean(message));
    const note=row.querySelector('[data-conflict-note]');if(note){note.textContent=message;note.hidden=!message;}
    for(const input of row.querySelectorAll('[data-name],[data-quality],[data-voice],[data-item-season]')){
      if(message)input.setAttribute('aria-invalid','true');else input.removeAttribute('aria-invalid');
    }
  }
  $('show-series-conflict').hidden=!conflicts.length;
}
function jumpToSeriesConflict(){
  const row=$('queue').querySelector('.variant-conflict');
  if(row){row.scrollIntoView({behavior:'smooth',block:'center'});row.querySelector('[data-name]')?.focus({preventScroll:true});}
}
async function api(path, options={}){
  const headers = {...options.headers};
  if(state.session)headers['X-CSRF-Token']=state.session.csrf;
  if(options.json!==undefined){headers['Content-Type']='application/json';options.body=JSON.stringify(options.json);delete options.json;}
  const response = await fetch(path,{...options,headers,credentials:'same-origin'});
  const data = await response.json();
  if(!response.ok){if(response.status===401){$('login').hidden=false;$('workspace').hidden=true;state.session=null;}const error=new Error(data.error || 'Ошибка запроса');error.conflicts=data.conflicts||[];throw error;}
  return data;
}
function action(fn){return async event=>{try{await fn(event);}catch(error){notice(error.message,true);}};}
function showTab(tab){state.tab=tab;document.querySelectorAll('.tab').forEach(el=>el.hidden=el.id!==`tab-${tab}`);document.querySelectorAll('[data-tab]').forEach(el=>el.classList.toggle('active',el.dataset.tab===tab));$('breadcrumb').textContent=`Библиотека / ${{upload:'Загрузка',batch:'Проверка и пакет',inbox:'Из Telegram',catalog:'Каталог',requests:'Запросы пользователей',remote:'Лаунчер',storage:'Резервирование',guide:'Инструкция',contests:'Конкурсы'}[tab]}`;if(tab==='inbox')renderInbox();if(tab==='catalog')loadCatalog().catch(e=>notice(e.message,true));if(tab==='batch')loadBatch().catch(e=>notice(e.message,true));if(tab==='requests')loadRequests().catch(e=>notice(e.message,true));if(tab==='remote')loadRemote().catch(e=>notice(e.message,true));if(tab==='storage')loadStorage().catch(e=>notice(e.message,true));if(tab==='contests')loadContests().catch(e=>notice(e.message,true));}
function draftBody(){return {rating_source_id:Number($('rating-source').value)||null,rating_kp:$('rating-kp').value,rating_imdb:$('rating-imdb').value,age_rating:$('age-rating').value,catalog_section:$('catalog-section').value,content_type:$('content-type').value,title:$('title').value,description:$('description').value,poster_url:$('poster-url').value,movie_number:state.movie?.id || null,category_id:$('season-select').value?Number($('season-select').value):null,season_name:$('season-name').value,season_number:$('season-number').value?Number($('season-number').value):null,items:state.items};}
let saveChain=Promise.resolve();
function saveDraft(){
  const run=async()=>{if(state.published)throw new Error('Этот черновик уже опубликован. Создайте новый.');const snapshot=JSON.stringify(draftBody());const saved=await api('/api/drafts',{method:'POST',json:{id:state.id,body:JSON.parse(snapshot)}});state.id=saved.id;state.dirty=JSON.stringify(draftBody())!==snapshot;$('save-state').textContent=state.dirty?'Есть несохранённые изменения':'Черновик сохранён';const old=state.drafts.findIndex(d=>d.id===saved.id);if(old>=0)state.drafts[old]=saved;else state.drafts.unshift(saved);renderDrafts();return saved;};
  saveChain=saveChain.catch(()=>{}).then(run);return saveChain;
}
function renderDrafts(){const selected=state.id;$('draft-select').innerHTML='<option value="">Новая публикация</option>'+state.drafts.map(d=>`<option value="${esc(d.id)}">${esc(d.body.title || 'Без названия')} · ${({published:'опубликован',draft:'на проверке',revision:'на доработке',rejected:'отклонён'}[d.status]||d.status)}</option>`).join('');$('draft-select').value=selected || '';}
function resetDraft(){state.seriesServerConflicts=[];$('rating-source').value='';$('rating-source-info').textContent='';for(const id of ['rating-kp','rating-imdb','age-rating'])$(id).value='';for(const id of ['draft-feedback','review-feedback'])feedback(id,'');$('review-comment').removeAttribute('aria-invalid');$('promo-text').value='';$('review-comment').value='';state.id=null;state.items=[];state.published=false;state.movie=null;state.dirty=false;$('catalog-section').value='movies';$('content-type').value='movie';$('title').value='';$('description').value='';$('poster-url').value='';$('season-name').value='';$('season-number').value='1';$('season-select').innerHTML='<option value="">Создать новый сезон</option>';$('existing-target').hidden=true;$('season-select-wrap').hidden=true;$('title').disabled=false;$('content-type').disabled=false;$('catalog-section').disabled=false;$('extra-fields').hidden=false;$('save-state').textContent='Не сохранён';$('draft-select').value='';renderQueue();syncMode();renderReview();}
async function openDraft(draft){
  resetDraft();state.id=draft.id;state.items=draft.body.items.map(item=>({...item}));
  const files=await api(`/api/assets?draft_id=${encodeURIComponent(draft.id)}`);
  if(state.id!==draft.id)return;
  state.assets=files.assets;
  state.seriesServerConflicts=[];state.published=draft.status==='published';const b=draft.body;$('rating-source-info').textContent=ratingSourceText(b);$('catalog-section').value=b.catalog_section||(b.content_type==='series'?'series':'movies');$('content-type').value=b.content_type;for(const [key,id] of [['rating_source_id','rating-source'],['rating_kp','rating-kp'],['rating_imdb','rating-imdb'],['age_rating','age-rating'],['title','title'],['description','description'],['poster_url','poster-url'],['season_name','season-name'],['season_number','season-number']])$(id).value=b[key]??'';if(b.movie_number){state.movie={id:b.movie_number,title:b.title,content_type:b.content_type,catalog_section:b.catalog_section};await fillSeasons();$('season-select').value=b.category_id || '';}$('draft-select').value=draft.id;$('save-state').textContent=state.published?`Опубликован · код ${draft.movie_number}`:'Черновик сохранён';syncMode();renderQueue();renderReview();showTab('upload');
}
function syncMode(){const readOnly=state.published||['rejected','grouped'].includes(state.drafts.find(d=>d.id===state.id)?.status);const section=state.sections.find(s=>s.key===$('catalog-section').value);if(section?.required_content_type&&!state.movie)$('content-type').value=section.required_content_type;const series=$('content-type').value==='series';$('season-fields').hidden=!series;$('content-type').disabled=Boolean(state.movie)||readOnly||Boolean(section?.required_content_type);$('catalog-section').disabled=Boolean(state.movie)||readOnly;$('title').disabled=Boolean(state.movie)||readOnly;$('rating-fields').disabled=Boolean(state.movie)||readOnly;$('extra-fields').hidden=Boolean(state.movie);$('existing-target').hidden=!state.movie;$('existing-target').textContent=state.movie?`Каталог · код ${state.movie.id} · ${state.movie.title}`:'';$('season-select-wrap').hidden=!state.movie||!series;const existing=Boolean($('season-select').value);$('season-name').disabled=existing||readOnly;$('season-number').disabled=existing||readOnly;$('save-draft').disabled=readOnly||state.busy||state.pendingAction||['rejected','grouped'].includes(state.drafts.find(d=>d.id===state.id)?.status);$('file-input').disabled=readOnly||state.busy||state.pendingAction;for(const id of ['description','poster-url'])$(id).disabled=readOnly;updatePublish();}
function asset(id){return state.assets.find(a=>a.id===id);}
function statusHtml(file){if(!file)return '<span class="state error">Файл недоступен</span>';const percent=file.size?Math.round(file.received/file.size*100):0;return `<span class="state ${file.status==='ready'?'ready':file.status==='error'?'error':''}">${esc(statusNames[file.status]||file.status)}${file.status==='uploading'?` · ${percent}%`:''}</span>${file.status==='uploading'?`<progress value="${file.received}" max="${file.size}" aria-label="Передано ${percent}%"></progress>`:''}${file.error?`<p class="file-meta error">${esc(file.error)}</p>`:''}`;}
function inferSeriesQuality(item){
  if((item.quality||'').trim())return item.quality;
  const known=value=>/^(?:source|2160p|1080p|720p|480p|360p)$/i.test((value||'').trim());
  // A release name may contain 1080p even for a downscaled 720p variant.
  // Read complete directory components, never a resolution inside the title.
  const directories=(item.relative_path||'').split(/[\\/]/).slice(0,-1);
  const quality=directories.find(known)||(known(item.category)?item.category:'');
  return quality.trim().toLowerCase();
}
function renderQueue(){
  const readOnly=state.published||['rejected','grouped'].includes(state.drafts.find(d=>d.id===state.id)?.status);
  if(!readOnly&&$('content-type').value==='series'){
    for(const item of state.items){
      if(!(item.quality||'').trim())item.quality=inferSeriesQuality(item);
    }
  }
  let fillTools=$('series-fill-tools');
  if(!fillTools){
    fillTools=document.createElement('div');fillTools.id='series-fill-tools';fillTools.className='batch-toolbar';
    fillTools.innerHTML='<button type="button" id="series-fill" class="secondary">Заполнить серии по файлам</button><span>Одно название для всех качеств и озвучек серии</span>';
    $('queue').before(fillTools);
  }
  fillTools.hidden=readOnly||$('content-type').value!=='series'||!state.items.length;
  $('series-fill').onclick=openSeriesFill;
  const desktopImport=state.items.some(item=>asset(item.asset_id)?.source==='desktop');
  $('dropzone').hidden=desktopImport;
  $('files-heading').textContent=$('content-type').value==='series'?'Серии: сезон → серия → качество':'Варианты фильма';
  $('file-count').textContent=state.items.length;
  $('queue').innerHTML=state.items.length?state.items.map((item,index)=>{const file=asset(item.asset_id);return `<div class="queue-row" data-id="${esc(item.asset_id)}"><span class="row-number">${!readOnly?`<span class="drag-handle" draggable="true" data-drag="${esc(item.asset_id)}" title="Перетащите, чтобы изменить порядок" aria-label="Перетащить файл">⠿</span>`:''}${String(index+1).padStart(2,'0')}</span><div><input data-name="${esc(item.asset_id)}" aria-label="Название файла ${index+1} в боте" value="${esc(item.name)}" ${readOnly?'disabled':''}>${$('content-type').value==='movie'?`<label>Категория<input data-category="${esc(item.asset_id)}" value="${esc(item.category||'')}" ${readOnly?'disabled':''}></label>`:''}${$('content-type').value==='series'?`<p class="field-note">Одинаковое название серии и сезон объединяют файлы в варианты качества.</p><label>Качество<input list="series-quality-options" data-quality="${esc(item.asset_id)}" maxlength="30" placeholder="1080p, 720p…" value="${esc(item.quality||'')}" ${readOnly?'disabled':''}></label><label>Озвучка<input data-voice="${esc(item.asset_id)}" maxlength="80" placeholder="Необязательно" value="${esc(item.voice||'')}" ${readOnly?'disabled':''}></label><label>Сезон этого файла<input type="number" min="1" max="999" data-item-season="${esc(item.asset_id)}" value="${item.season_number||''}" placeholder="Общий сезон" ${readOnly?'disabled':''}></label>`:''}<p class="queue-conflict-note error" data-conflict-note="${esc(item.asset_id)}" hidden></p><p class="file-meta">${esc(item.relative_path||file?.filename||'Файл')} · ${bytes(file?.size||0)} · ${['telegram','desktop'].includes(file?.source)?'из Telegram':'с компьютера'}</p></div><div class="file-status"><div data-status="${esc(item.asset_id)}">${statusHtml(file)}</div><div class="row-actions">${file?.status==='error'?`<button data-retry="${esc(item.asset_id)}">Повторить</button>`:''}${!readOnly?`<button data-up="${index}" aria-label="Выше" ${index===0?'disabled':''}>↑</button><button data-down="${index}" aria-label="Ниже" ${index===state.items.length-1?'disabled':''}>↓</button><button data-remove="${index}" aria-label="Убрать из черновика">×</button>`:''}</div>${file&&['uploading','error','queued'].includes(file.status)?`<button class="text-button" data-discard="${esc(file.id)}">Удалить загрузку</button>`:''}</div></div>`;}).join(''):'<div class="empty">Добавьте файлы — здесь можно будет изменить названия и порядок.</div>';
  updatePublish();
}
function updateStatuses(){for(const item of state.items){const element=document.querySelector(`[data-status="${item.asset_id}"]`);if(element)element.innerHTML=statusHtml(asset(item.asset_id));}updatePublish();}
function updatePublish(){
  const current=state.drafts.find(d=>d.id===state.id);
  const files=state.items.map(item=>asset(item.asset_id));
  const conflicts=[...seriesVariantConflicts(),...state.seriesServerConflicts];displaySeriesConflicts(conflicts);
  let reason='';
  if(state.published||current?.status==='published')reason='Фильм уже опубликован. Повторная публикация не требуется.';
  else if(current?.status==='grouped')reason='Файлы перенесены в общую карточку. Откройте её для настройки.';
  else if(current?.status==='rejected')reason='Черновик отклонён. Нажмите «Вернуть на проверку», чтобы продолжить работу.';
  else if(current?.status==='revision')reason='Черновик на доработке. Внесите исправления и нажмите «Вернуть на проверку».';
  else if(current?.stream_pending)reason=`Лаунчер ещё готовит проект: получено ${files.filter(f=>f?.status==='ready').length} из ${current.stream_pending} файлов. Публикация станет доступна после завершения подготовки и отправки.`;
  else if(state.pendingAction)reason='Дождитесь завершения текущего действия.';
  else if(state.busy)reason='Дождитесь завершения или приостановки загрузки.';
  else if(!state.movie&&!$('age-rating').value)reason='Укажите возрастную маркировку перед публикацией.';
  else if(!files.length)reason='Добавьте хотя бы один файл в черновик.';
  else if(files.some(file=>!file||file.status==='cancelled'))reason='В черновике есть недоступный или удалённый файл. Уберите его или добавьте заново.';
  else if(files.some(file=>file.status==='error'))reason='Не удалось передать часть файлов. Нажмите «Повторить» у файлов с ошибкой.';
  else if(files.some(file=>file.status==='waiting_telegram')){
    const waiting=files.filter(file=>file.status==='waiting_telegram').length;
    reason=`Не получено из Telegram Desktop: ${waiting} из ${files.length}. В лаунчере нажмите «Отправить через Telegram Desktop» и дождитесь подтверждения бота.`;
  }else if(files.some(file=>file.status!=='ready')){
    reason=`Готово файлов: ${files.filter(file=>file.status==='ready').length} из ${files.length}. Дождитесь завершения передачи в Telegram.`;
  }else if(!$('title').value.trim()&&!state.movie)reason='Укажите название фильма или сериала.';
  else if(state.movie&&$('content-type').value==='movie'&&state.items.some(item=>!item.category.trim()))reason='Для добавления в опубликованный фильм укажите категорию каждого файла.';
  else if($('content-type').value==='series'&&!$('season-select').value&&!$('season-name').value.trim()&&!$('season-number').value&&!state.items.every(item=>item.season_number))reason='Укажите название или номер сезона.';
  else if(conflicts.length)reason=seriesConflictText(conflicts[0])+' Исправьте поля или уберите повтор из черновика.';
  else if(state.items.some(item=>!item.name.trim()))reason='Заполните названия всех вариантов или серий.';
  $('publish').disabled=Boolean(reason);
  $('add-package').disabled=Boolean(reason)||$('content-type').value!=='movie'||Boolean(state.movie);
  $('publish').textContent=state.movie?'Добавить файлы в карточку':'Одобрить и опубликовать';
  $('publish').title=reason||(state.movie?'Добавить проверенные файлы в выбранную карточку':'Одобрить карточку и добавить фильм в каталог бота');
  $('publish-hint').textContent=reason
    ? (state.published||current?.status==='published'?'':'Публикация недоступна: ')+reason
    : state.movie?'Всё готово. Файлы будут добавлены в существующую карточку без удаления опубликованных файлов.':'Всё готово к публикации. Нажмите «Одобрить и опубликовать», чтобы фильм появился в боте.';
  $('publish-hint').classList.toggle('blocked',Boolean(reason)&&!state.published);
}
function feedback(id,text,error=false){
  const element=$(id);element.textContent=text;element.hidden=!text;element.classList.toggle('error',error);
}
function draftAction(buttonId,feedbackId,pendingText,fn){
  return async()=>{
    if(state.pendingAction)return;
    const button=$(buttonId),label=button.textContent;
    state.pendingAction=true;button.textContent=pendingText;button.setAttribute('aria-busy','true');
    feedback(feedbackId,pendingText);lockDraftNavigation(true);syncMode();renderReview();
    try{const result=await fn();feedback(feedbackId,result||'Готово.');}
    catch(error){feedback(feedbackId,error.message,true);}
    finally{
      state.pendingAction=false;button.textContent=label;button.removeAttribute('aria-busy');
      lockDraftNavigation(false);syncMode();renderReview();
      if($('review-comment').getAttribute('aria-invalid')==='true')$('review-comment').focus();
    }
  };
}
function lockDraftNavigation(locked){
  for(const id of ['draft-select','new-draft','logout'])$(id).disabled=locked;
}

async function refresh(){
  const opened=state.id;
  const [files,drafts]=await Promise.all([api(opened?`/api/assets?draft_id=${encodeURIComponent(opened)}`:'/api/assets'),api('/api/drafts')]);
  if(state.id!==opened)return;
  const previous=JSON.stringify(state.assets.map(a=>[a.id,a.status]));
  if(opened&&!drafts.drafts.some(d=>d.id===opened)){
    const current=await api(`/api/drafts/${opened}`).catch(()=>null);
    if(current&&state.id===opened)drafts.drafts.push(current);
  }
  const missing=state.items.filter(i=>!files.assets.some(a=>a.id===i.asset_id));
  const extra=await Promise.all(missing.map(i=>api(`/api/uploads/${i.asset_id}`).catch(()=>null)));
  if(state.id!==opened)return;
  state.assets=files.assets.concat(extra.filter(Boolean));state.drafts=drafts.drafts;
  if(state.drafts.find(d=>d.id===state.id)?.status==='published')state.published=true;
  renderDrafts();$('inbox-count').textContent=state.assets.filter(a=>a.source==='telegram'&&a.status==='ready').length;
  if(previous!==JSON.stringify(state.assets.map(a=>[a.id,a.status]))&&!$('queue').contains(document.activeElement))renderQueue();else updateStatuses();
  if(state.tab==='inbox')renderInbox();renderReview();
}
function renderInbox(){const chosen=new Set([...document.querySelectorAll('[data-inbox]:checked')].map(e=>e.dataset.inbox));const files=state.assets.filter(a=>a.source==='telegram'&&a.status==='ready');$('inbox-list').innerHTML=files.length?files.map(file=>`<label class="library-row"><input type="checkbox" data-inbox="${esc(file.id)}" ${chosen.has(file.id)?'checked':''}><div><h3>${esc(file.filename)}</h3><p>${bytes(file.size)} · ${file.file_type==='video'?'Видео':'Документ'} · ${esc(file.created_at)}</p></div><span class="ready">Готов</span></label>`).join(''):'<div class="empty">Здесь пока нет файлов. Отправьте боту /webfiles и загрузите видео или документ.</div>';}
function suggestName(filename,index){const stem=filename.replace(/\.[^.]+$/,'').replace(/[_.]+/g,' ').trim();if($('content-type').value==='movie')return stem;const match=filename.match(/S\d{1,3}E(\d{1,3})/i)||filename.match(/(?:серия|episode|ep)[\s._-]*(\d{1,3})/i);const n=match?Number(match[1]):index+1;return `Серия ${n} — ${stem}`;}
async function sha(buffer){const digest=await crypto.subtle.digest('SHA-256',buffer);return [...new Uint8Array(digest)].map(n=>n.toString(16).padStart(2,'0')).join('');}
async function uploadFiles(selected){
  const files=[...selected].sort((a,b)=>a.name.localeCompare(b.name,'ru',{numeric:true}));
  if(!files.length)return;if(state.published)throw new Error('Создайте новый черновик.');if(state.busy)throw new Error('Дождитесь текущей загрузки или приостановите её.');
  if($('content-type').value==='movie' && (files.length>1 || (state.items.length && !state.items.some(i=>{const a=asset(i.asset_id);return a?.filename===files[0].name&&a?.status==='uploading';}))))throw new Error('Для фильма выберите один файл. Для другого фильма создайте новый черновик.');
  for(const file of files)if(!file.size||file.size>state.session.max_file_bytes)throw new Error(`${file.name}: максимальный размер ${state.session.max_file_bytes/1e6} МБ. Загрузите большой файл через Telegram Desktop и /webfiles.`);
  if(!crypto.subtle)throw new Error('Загрузка требует HTTPS или адреса localhost.');
  if(!$('title').value&&$('content-type').value==='movie')$('title').value=suggestName(files[0].name,0);
  state.busy=true;state.paused=false;syncMode();$('upload-activity').hidden=false;
  try{await saveDraft();for(const file of files){if(state.paused)break;$('activity-text').textContent=`${file.name} · подготовка`;const first=await file.slice(0,state.session.chunk_bytes).arrayBuffer();const firstHash=await sha(first);let uploaded=state.assets.find(a=>a.source==='web'&&a.status==='uploading'&&a.filename===file.name&&a.size===file.size&&a.first_hash===firstHash);
      if(!uploaded){uploaded=await api('/api/uploads',{method:'POST',json:{filename:file.name,size:file.size,file_type:file.name.toLowerCase().endsWith('.mp4')?'video':'document',first_hash:firstHash}});state.assets.unshift(uploaded);}
      if(!state.items.some(i=>i.asset_id===uploaded.id)){state.items.push({asset_id:uploaded.id,name:suggestName(file.name,state.items.length)});dirty();await saveDraft();}renderQueue();
      let offset=(await api(`/api/uploads/${uploaded.id}`)).received;
      while(offset<file.size&&!state.paused){const chunk=await file.slice(offset,offset+state.session.chunk_bytes).arrayBuffer();const checksum=await sha(chunk);state.controller=new AbortController();const result=await api(`/api/uploads/${uploaded.id}/chunk`,{method:'PUT',body:chunk,signal:state.controller.signal,headers:{'Content-Type':'application/octet-stream','X-Upload-Offset':String(offset),'X-Chunk-SHA256':checksum}});offset=result.received;uploaded.received=offset;$('activity-text').textContent=`${file.name} · ${Math.round(offset/file.size*100)}% на сервер`;updateStatuses();}
      if(!state.paused){await api(`/api/uploads/${uploaded.id}/complete`,{method:'POST'});uploaded.status='queued';renderQueue();}
    }
    notice(state.paused?'Передача приостановлена. Выберите те же файлы, чтобы продолжить.':'Файлы приняты сервером. Дождитесь статуса «Готов», затем опубликуйте черновик.');
  }catch(error){if(error.name==='AbortError')notice('Передача приостановлена. Выберите те же файлы, чтобы продолжить.');else notice(`${error.message} Для продолжения выберите те же файлы повторно.`,true);}
  finally{state.busy=false;state.controller=null;$('upload-activity').hidden=true;$('file-input').value='';syncMode();await refresh().catch(()=>{});}
}
function sectionTitle(key){const section=state.sections.find(s=>s.key===key);return section?`${section.emoji} ${section.title}`:key;}
async function loadSections(){const data=await api('/api/catalog-sections');state.sections=data.sections;$('catalog-section').innerHTML=state.sections.map(s=>`<option value="${esc(s.key)}">${esc(s.emoji+' '+s.title)}${s.enabled?'':' · скрыт'}</option>`).join('');if(!$('catalog-section').value)$('catalog-section').value='movies';$('catalog-sections').innerHTML=state.sections.map(s=>`<label><input type="checkbox" data-section-toggle="${esc(s.key)}" ${s.enabled?'checked':''}> <span>${esc(s.emoji+' '+s.title)}</span><small>${s.required_content_type==='series'?'Сезоны и серии':s.required_content_type==='movie'?'Полнометражный':'Оба формата'}</small></label>`).join('');}
async function loadCatalog(){await loadSections();const data=await api(`/api/catalog?q=${encodeURIComponent($('catalog-query').value)}&page=${state.catalogPage}&unrated=${$('catalog-unrated').checked?'1':'0'}`);state.catalog=data.movies;$('catalog-list').innerHTML=data.movies.length?data.movies.map(movie=>`<div class="library-row"><span class="step">${movie.content_type==='series'?'С':'Ф'}</span><div><h3>${esc(movie.title)}</h3><p>Код ${movie.id} · ${esc(sectionTitle(movie.catalog_section))} · ${esc(movie.age_rating||'Возраст не указан')} · ${movie.content_type==='series'?'Сезоны и серии':'Полнометражный'}</p><select data-movie-section="${movie.id}">${state.sections.filter(s=>!s.required_content_type||s.required_content_type===movie.content_type).map(s=>`<option value="${esc(s.key)}" ${s.key===movie.catalog_section?'selected':''}>${esc(s.title)}${s.enabled?'':' · скрыт'}</option>`).join('')}</select></div><button class="secondary" data-folders="${movie.id}">Папки и файлы</button><button class="secondary" data-renumber="${movie.id}">Изменить ID</button><button class="secondary" data-ratings="${movie.id}">Рейтинги и возраст</button>${movie.content_type==='series'?`<button class="secondary" data-merge-card="${movie.id}">Объединить карточки</button>`:''}<button class="secondary" data-movie="${movie.id}">${movie.content_type==='series'?'Добавить серии':'Добавить файлы'}</button></div>`).join(''):'<div class="empty">Ничего не найдено.</div>';$('catalog-prev').disabled=state.catalogPage===0;$('catalog-next').disabled=!data.has_more;$('catalog-page').textContent=`Страница ${state.catalogPage+1}`;}
async function fillSeasons(){if(!state.movie)return;const data=await api(`/api/catalog/${state.movie.id}/seasons`);$('season-select').innerHTML='<option value="">Создать новый сезон</option>'+data.seasons.map(s=>`<option value="${s.id}">${esc(s.name)}</option>`).join('');}
function mayReset(){if(state.pendingAction)return false;if(state.busy){notice('Сначала завершите или приостановите загрузку.',true);return false;}return !state.dirty||confirm('В черновике есть несохранённые изменения. Продолжить без сохранения?');}
document.querySelectorAll('[data-tab]').forEach(button=>button.addEventListener('click',()=>showTab(button.dataset.tab)));
$('choose-inbox').onclick=()=>showTab('inbox');
$('new-draft').onclick=()=>{if(mayReset())resetDraft();};
$('draft-select').onchange=action(async()=>{const id=$('draft-select').value;if(!mayReset()){$('draft-select').value=state.id||'';return;}if(!id)resetDraft();else await openDraft(state.drafts.find(d=>d.id===id));});
for(const id of ['rating-kp','rating-imdb','age-rating','catalog-section','content-type','title','description','poster-url','season-name','season-number','season-select'])$(id).addEventListener('input',()=>{dirty();syncMode();if(id==='content-type'||id==='catalog-section')renderQueue();});
$('save-draft').onclick=draftAction('save-draft','draft-feedback','Сохраняем…',async()=>{await saveDraft();return state.dirty?'Сохранено. Остались новые изменения — сохраните их ещё раз.':`Черновик сохранён в ${new Date().toLocaleTimeString('ru-RU',{hour:'2-digit',minute:'2-digit'})}. Фильм ещё не опубликован.`;});
$('show-series-conflict').onclick=jumpToSeriesConflict;
$('publish').onclick=async()=>{
  if(!confirm(state.movie?'Добавить проверенные файлы в выбранную карточку каталога?':'Опубликовать проверенные файлы в каталоге бота?'))return;
  await draftAction('publish','draft-feedback','Публикуем…',async()=>{
    await saveDraft();let result;
    try{result=await api(`/api/drafts/${state.id}/publish`,{method:'POST'});}
    catch(error){state.seriesServerConflicts=error.conflicts||[];displaySeriesConflicts([...seriesVariantConflicts(),...state.seriesServerConflicts]);if(state.seriesServerConflicts.length)jumpToSeriesConflict();throw error;}
    state.published=true;state.dirty=false;
    $('save-state').textContent=`Опубликован · код ${result.movie_number}`;
    // Successful publication stays successful even if the subsequent list refresh fails.
    await refresh().catch(()=>{});renderQueue();
    return state.movie?`Файлы добавлены в карточку. Код в боте: ${result.movie_number}.`:`Фильм опубликован. Код в боте: ${result.movie_number}. Ниже можно подготовить рекламный пост.`;
  })();
};
$('file-input').onchange=action(event=>uploadFiles(event.target.files));
$('pause-upload').onclick=()=>{state.paused=true;state.controller?.abort();};
for(const name of ['dragenter','dragover'])$('dropzone').addEventListener(name,event=>{event.preventDefault();$('dropzone').classList.add('drag');});
for(const name of ['dragleave','drop'])$('dropzone').addEventListener(name,event=>{event.preventDefault();$('dropzone').classList.remove('drag');});
$('dropzone').addEventListener('drop',action(event=>uploadFiles(event.dataTransfer.files)));
$('queue').oninput=event=>{for(const field of ['quality','voice']){const id=event.target.dataset[field];if(id){state.items.find(i=>i.asset_id===id)[field]=event.target.value;dirty();updatePublish();}}const seasonId=event.target.dataset.itemSeason;if(seasonId){state.items.find(i=>i.asset_id===seasonId).season_number=event.target.value?Number(event.target.value):null;dirty();updatePublish();}const categoryId=event.target.dataset.category;if(categoryId){state.items.find(i=>i.asset_id===categoryId).category=event.target.value;dirty();}const id=event.target.dataset.name;if(id){state.items.find(i=>i.asset_id===id).name=event.target.value;dirty();updatePublish();}};
$('queue').onclick=action(async event=>{const b=event.target.closest('button');if(!b)return;if(b.dataset.retry){await api(`/api/uploads/${b.dataset.retry}/complete`,{method:'POST'});await refresh();return;}if(b.dataset.discard){if(confirm('Удалить незавершённую загрузку с сервера?')){await api(`/api/uploads/${b.dataset.discard}`,{method:'DELETE'});state.items=state.items.filter(i=>i.asset_id!==b.dataset.discard);dirty();await saveDraft();await refresh();renderQueue();}return;}if(state.published)return;if(b.dataset.remove!==undefined){state.items.splice(Number(b.dataset.remove),1);}else{const up=b.dataset.up!==undefined;const index=Number(up?b.dataset.up:b.dataset.down);const other=index+(up?-1:1);if(other<0||other>=state.items.length)return;[state.items[index],state.items[other]]=[state.items[other],state.items[index]];}dirty();renderQueue();});
$('refresh-inbox').onclick=action(refresh);
$('add-inbox').onclick=action(async()=>{if(state.published)throw new Error('Сначала создайте новый черновик.');const selected=[...document.querySelectorAll('[data-inbox]:checked')].map(e=>asset(e.dataset.inbox)).filter(Boolean);if(!selected.length)throw new Error('Выберите файлы.');const additions=selected.filter(a=>!state.items.some(i=>i.asset_id===a.id));if($('content-type').value==='movie'&&state.items.length+additions.length>1)throw new Error('Для фильма выберите один файл. Для нескольких серий выберите тип «Сериал».');for(const file of additions)state.items.push({asset_id:file.id,name:suggestName(file.filename,state.items.length)});if(!$('title').value&&$('content-type').value==='movie')$('title').value=state.items[0].name;dirty();await saveDraft();renderQueue();showTab('upload');notice('Файлы добавлены. Проверьте названия и порядок.');});
$('catalog-search').onsubmit=action(async event=>{event.preventDefault();state.catalogPage=0;await loadCatalog();});
$('catalog-prev').onclick=action(async()=>{state.catalogPage=Math.max(0,state.catalogPage-1);await loadCatalog();});
$('catalog-next').onclick=action(async()=>{state.catalogPage++;await loadCatalog();});
$('catalog-list').onclick=action(async event=>{const renumberId=event.target.closest('[data-renumber]')?.dataset.renumber;if(renumberId){if(!mayReset())return;const raw=prompt('Новый свободный ID фильма/сериала:');if(raw===null)return;const number=Number(raw);if(!Number.isSafeInteger(number)||number<1)throw new Error('Введите положительный целый ID.');if(!confirm(`Изменить ID ${renumberId} на ${number}? Старые кнопки и ссылки с числовым кодом потребуется открыть заново.`))return;await api(`/api/catalog/${renumberId}/renumber`,{method:'POST',json:{new_movie_number:number}});resetDraft();await refresh();await loadCatalog();notice('ID и связи каталога обновлены.');return;}const foldersId=event.target.closest('[data-folders]')?.dataset.folders;if(foldersId){await editCatalogFolders(foldersId);return;}const ratingId=event.target.closest('[data-ratings]')?.dataset.ratings;if(ratingId){await editCatalogRatings(ratingId);return;}const id=event.target.closest('[data-movie]')?.dataset.movie;if(!id||!mayReset())return;const movie=state.catalog.find(m=>m.id===Number(id));resetDraft();state.movie=movie;$('catalog-section').value=movie.catalog_section;$('content-type').value=movie.content_type;$('title').value=movie.title;await fillSeasons();syncMode();dirty();showTab('upload');});
$('catalog-sections').onchange=action(async event=>{const input=event.target.closest('[data-section-toggle]');if(!input)return;await api('/api/catalog-sections',{method:'POST',json:{key:input.dataset.sectionToggle,enabled:input.checked}});await loadSections();notice(input.checked?'Раздел появился в меню бота.':'Раздел скрыт. Его карточки и файлы сохранены.');});
$('catalog-list').onchange=action(async event=>{const select=event.target.closest('[data-movie-section]');if(!select)return;await api(`/api/catalog/${select.dataset.movieSection}/section`,{method:'POST',json:{catalog_section:select.value}});await loadCatalog();notice('Карточка перенесена в выбранный раздел.');});
async function loadRequests(){
  const data=await api(`/api/content-requests?offset=${state.requestOffset}`);state.requests=data.items;
  if(!data.items.length&&state.requestOffset){state.requestOffset=Math.max(0,state.requestOffset-data.limit);return loadRequests();}
  const status={new:'Новая',planned:'В работе',rejected:'Отклонена',added:'Добавлено'};
  $('requests-page').textContent=data.total?`${data.offset+1}–${data.offset+data.items.length} из ${data.total}`:'Заявок пока нет';
  $('requests-prev').disabled=!data.offset;$('requests-next').disabled=data.offset+data.limit>=data.total;
  $('requests-list').innerHTML=data.items.length?data.items.map(r=>`<article class="panel request-card">
    ${r.poster_url?`<img src="${esc(r.poster_url)}" alt="" loading="lazy">`:''}<div><div class="request-heading"><span class="count">${r.votes} 👤</span><span class="state ${r.status==='added'?'ready':''}">${esc(status[r.status]||r.status)}</span></div>
    <h2>${esc(r.title)}</h2><p class="muted">${r.content_type==='series'?'Сериал':'Фильм'}${r.season?` · сезон ${r.season}`:''}</p><p>${esc(r.overview||'Описание отсутствует.')}</p>
    <div class="request-actions">${r.status!=='added'?`<button class="secondary" data-request-status="planned" data-request-id="${r.id}">В работу</button><button class="secondary" data-request-status="rejected" data-request-id="${r.id}">Отклонить</button><button class="primary" data-request-draft="${r.id}">${r.draft_id?'Открыть черновик':'Создать карточку'}</button>`:`<strong>Код в боте: ${r.movie_number}</strong>`}</div></div></article>`).join(''):'<div class="panel empty">Пользовательские запросы появятся здесь после поиска отсутствующего фильма или сериала.</div>';
}
$('requests-refresh').onclick=action(loadRequests);
$('requests-prev').onclick=action(async()=>{state.requestOffset=Math.max(0,state.requestOffset-50);await loadRequests();});
$('requests-next').onclick=action(async()=>{state.requestOffset+=50;await loadRequests();});
$('requests-list').onclick=action(async event=>{
  const statusButton=event.target.closest('[data-request-status]');
  if(statusButton){await api(`/api/content-requests/${statusButton.dataset.requestId}`,{method:'POST',json:{action:statusButton.dataset.requestStatus}});await loadRequests();return;}
  const draftButton=event.target.closest('[data-request-draft]');if(!draftButton)return;if(!mayReset())return;
  const result=await api(`/api/content-requests/${draftButton.dataset.requestDraft}`,{method:'POST',json:{action:'create_draft'}});
  if(!state.drafts.some(d=>d.id===result.draft.id))state.drafts.unshift(result.draft);else state.drafts[state.drafts.findIndex(d=>d.id===result.draft.id)]=result.draft;
  renderDrafts();await openDraft(result.draft);notice('Карточка создана из запроса. Добавьте файлы, проверьте оформление и опубликуйте её.');
});
// Pending form values and browse results belong to one computer, not to a heartbeat.
const remote={agents:[],commands:[],drafts:new Map(),loading:null};
function remoteAgent(){return remote.agents.find(agent=>agent.instance_id===$('remote-device').value);}
function remoteDraft(agent=remoteAgent()){
  if(!agent)return null;
  if(!remote.drafts.has(agent.instance_id)){
    const status=agent.status||{};
    remote.drafts.set(agent.instance_id,{folder:status.folder||agent.folders?.[0]||'',qualities:[...(status.qualities||['1080p','720p'])],mode:status.mode||'single',hardware:status.hardware||'auto',load:status.load||'balanced',catalog_section:['movies','cartoons','anime'].includes(status.catalog_section)?status.catalog_section:'movies',browser:null,browsePending:null,pending:null,feedback:'',feedbackError:false});
  }
  return remote.drafts.get(agent.instance_id);
}
function remoteRenderCapable(agent=remoteAgent()){return Boolean(agent?.status?.capabilities?.remote_render);}
function remoteCanBrowse(agent=remoteAgent()){return Boolean(agent?.online&&agent.status?.capabilities?.folder_browser&&agent.status.remote_browse_enabled);}
function remoteSameFolder(first,second){const normalize=value=>String(value||'').replaceAll('\\','/').replace(/\/+$/,'').toLowerCase();return Boolean(first&&second&&normalize(first)===normalize(second));}
function remoteBusy(agent=remoteAgent(),draft=remoteDraft(agent)){return Boolean(agent?.status?.busy||agent?.status?.job?.state==='running'||draft?.pending||draft?.awaitedJob);}
function remoteFeedback(draft,message,error=false){if(!draft)return;draft.feedback=message;draft.feedbackError=error;if(draft===remoteDraft()){$('remote-feedback').textContent=message;$('remote-feedback').classList.toggle('error',error);}}
function remoteJob(agent,draft){
  const job=agent?.status?.job;
  if(draft?.awaitedJob&&(!job?.id||job.id===draft.previousJobId)){$('remote-job').innerHTML=`<strong>Ожидаю запуск на компьютере</strong><p class="remote-path">Папка: ${esc(draft.launchFolder)}</p><p>Команда отправлена. Компьютер ещё не сообщил о запуске новой задачи.</p>`;return;}
  if(!job?.state){$('remote-job').innerHTML='<p class="muted">Задача ещё не запускалась. Выберите папку и нажмите «Подготовить MP4».</p>';return;}
  const labels={running:'Выполняется',completed:job.kind==='send'?'Передача завершена':'Подготовка завершена',failed:'Задача завершилась с ошибкой',paused:'Задача остановлена'};
  const ready=Math.max(0,Number(job.ready)||0),total=Math.max(0,Number(job.total)||0),percent=Number(job.percent);
  const progress=total?`<p class="remote-job-count">${ready} / ${total} MP4 ${job.kind==='send'?'отправлено':'готово'}</p><progress max="${Math.max(total,ready)}" value="${ready}" aria-label="${job.kind==='send'?'Отправлено':'Готово'} MP4"></progress>`:'';
  $('remote-job').innerHTML=`<strong class="${job.state==='failed'?'error':job.state==='completed'?'ready':''}">${esc(labels[job.state]||job.state)}</strong><p class="remote-path">Папка: ${esc(job.folder||'не указана')}</p>${progress}<p>${esc(job.message||'Ожидаю состояние задачи')}</p>${job.current&&job.state!=='completed'?`<p class="muted remote-path">${job.state==='running'?'Сейчас':'Последний файл'}: ${esc(job.current)}${job.state==='running'&&Number.isFinite(percent)&&job.percent!==null&&job.percent!==undefined?` · текущий файл ${Math.min(100,Math.max(0,percent))}%`:''}</p>`:''}<small>Запуск: ${esc(job.started_at||'—')}${job.finished_at?`<br>Окончание: ${esc(job.finished_at)}`:''}</small>`;
}
function remoteBrowser(agent,draft){
  const browser=draft?.browser,allowed=remoteCanBrowse(agent),pending=Boolean(draft?.browsePending);
  $('remote-browse').disabled=!allowed;$('remote-open-path').disabled=!allowed||!draft?.folder.trim();
  $('remote-browse-hint').textContent=allowed?'Просмотр папок можно выключить в настройках лаунчера на компьютере.':agent?.status?.capabilities?.folder_browser?'Просмотр папок выключен в настройках лаунчера. Включите доступ на компьютере.':'Для просмотра дисков и любых папок обновите лаунчер.';
  $('remote-browser').hidden=!draft?.browserOpen;
  $('remote-browser-path').textContent=browser?.folder||'Диски компьютера';
  $('remote-browser-status').textContent=pending?'Компьютер читает список папок…':draft?.browseError||(!browser?'Нажмите «Диски», чтобы открыть список.':browser.entries?.length?'':'В этой папке нет доступных подпапок.');
  $('remote-browser-status').classList.toggle('error',Boolean(draft?.browseError));
  $('remote-browser-entries').innerHTML=(browser?.entries||[]).map(entry=>`<button class="remote-folder-entry" type="button" data-remote-path="${esc(entry.path)}" ${pending||!allowed?'disabled':''}><span aria-hidden="true">📁</span><span>${esc(entry.name)}</span><span aria-hidden="true">›</span></button>`).join('');
  $('remote-browse-roots').disabled=!allowed||pending;
  $('remote-browse-up').disabled=!allowed||pending||!browser||browser.parent===null||browser.parent===undefined;
  $('remote-browse-next').hidden=browser?.next_offset===null||browser?.next_offset===undefined;$('remote-browse-next').disabled=!allowed||pending;
  $('remote-browse-select').disabled=!allowed||pending||!browser?.folder||remoteBusy(agent,draft);
}
function renderRemote(){
  const agent=remoteAgent(),draft=remoteDraft(agent),status=agent?.status||{},online=Boolean(agent?.online),capable=remoteRenderCapable(agent),busy=remoteBusy(agent,draft);
  const message=draft?.awaitedJob?'Ожидаю запуск новой задачи':status.job?.state?status.job.message||status.message:status.message;
  $('remote-status').innerHTML=agent?`<strong>${online?'● Лаунчер подключён':'○ Компьютер не отвечает'}</strong><p>${esc(message||'Ожидает команду')}<br>Папка на компьютере: <span class="remote-path">${esc(status.folder||'не выбрана')}</span><br>Последний ответ: ${esc(agent.last_seen||'—')}</p>`:'<p>Лаунчер ещё не подключался. Запустите его на компьютере и вставьте свежий код /desktop.</p>';
  $('remote-update-hint').hidden=!agent||capable;$('remote-update-hint').textContent='Этот лаунчер поддерживает только заранее разрешённые папки и создание карточек. Обновите его для выбора любой папки и запуска рендера из панели.';
  $('remote-path-controls').hidden=!capable;$('remote-render-settings').hidden=!capable;$('remote-switch').hidden=capable;
  $('remote-folder-label').firstChild.textContent=capable?'Недавние папки':'Разрешённая папка';
  const folders=[...new Set([...(agent?.folders||[]),...(draft?.folder?[draft.folder]:[])])];
  $('remote-folder').innerHTML=(capable?'<option value="">Выбрать из недавних…</option>':'')+folders.map(folder=>`<option value="${esc(folder)}">${esc(folder)}</option>`).join('');$('remote-folder').value=draft?.folder||'';
  $('remote-folder-path').value=draft?.folder||'';
  $('remote-section').innerHTML=state.sections.filter(section=>['movies','cartoons','anime'].includes(section.key)).map(section=>`<option value="${esc(section.key)}">${esc(section.title)}${section.enabled?'':' · скрыт'}</option>`).join('');
  for(const [id,key] of [['remote-mode','mode'],['remote-hardware','hardware'],['remote-load','load'],['remote-section','catalog_section']])$(id).value=draft?.[key]||'';
  for(const input of document.querySelectorAll('#remote-qualities input'))input.checked=Boolean(draft?.qualities.includes(input.value));
  for(const id of ['remote-folder','remote-folder-path','remote-mode','remote-hardware','remote-load','remote-section'])$(id).disabled=!online||busy;
  $('remote-qualities').disabled=!online||busy;
  $('remote-switch').disabled=!online||busy;
  $('remote-prepare').textContent=capable?'Подготовить MP4':'Создать карточки';
  $('remote-prepare').disabled=!online||busy||!draft?.folder.trim()||!draft?.qualities.length;
  $('remote-stop').disabled=!online||!busy;$('remote-stop').textContent=status.job?.kind==='send'?'Остановить после текущего файла':'Остановить подготовку';
  const prepared=Number(status.prepared_count)||((status.job?.kind==='prepare'&&status.job?.state==='completed')?Number(status.job.ready)||0:0);
  const sameFolder=remoteSameFolder(draft?.folder,status.folder||status.job?.folder);
  $('remote-send').disabled=!online||busy||(capable&&(!sameFolder||prepared<1));
  $('remote-send-hint').textContent=capable&&(!sameFolder||prepared<1)?'Сначала подготовьте выбранную папку. Передача сама не запускается.':'Передача сама не запускается. Перед отправкой настройте и сохраните карточки в панели.';
  $('remote-start-summary').innerHTML=draft?`<strong>${capable?'Будет подготовлена папка':'Выбрана папка'}:</strong><p class="remote-path">${esc(draft.folder||'Выберите папку с видео')}</p><p>${esc(draft.qualities.map(quality=>quality==='source'?'Исходное':quality).join(' · ')||'Выберите качества')}${capable?` · ${esc($('remote-mode').selectedOptions[0]?.textContent||'')}`:''}</p>${busy?'<p class="muted">На компьютере уже выполняется задача. Новую можно запустить после завершения или остановки.</p>':''}`:'';
  remoteFeedback(draft,draft?.feedback||'',draft?.feedbackError);remoteJob(agent,draft);remoteBrowser(agent,draft);
  const names={browse_folder:'Список папок',prepare:capable?'Запуск подготовки MP4':'Создать карточки',send:'Передача в Telegram',stop:'Остановка',refresh:'Обновление очереди',switch_folder:'Смена папки'},statuses={pending:'Ожидает компьютер',claimed:'Компьютер получил команду',running:'Компьютер выполняет команду',done:'Команда принята / выполнена',completed:'Команда выполнена',error:'Ошибка',failed:'Ошибка',cancelled:'Отменена',expired:'Истёк срок ожидания'};
  const ownCommands=remote.commands.filter(command=>command.instance_id===agent?.instance_id);
  const tasks=ownCommands.filter(command=>command.action!=='browse_folder'||['error','failed'].includes(command.status));
  const commands=(tasks.length?tasks:ownCommands).slice(0,5);
  $('remote-history').innerHTML=commands.length?commands.map(command=>`<p class="${['error','failed'].includes(command.status)?'error':''}"><strong>${esc(names[command.action]||command.action)}</strong> · ${esc(statuses[command.status]||command.status)}${command.created_at?`<br><small>${esc(command.created_at)}</small>`:''}${command.result?`<br><span>${esc(command.result)}</span>`:''}</p>`).join(''):'<p class="muted">Команд для этого компьютера пока не было.</p>';
}
async function loadRemote(){
  if(remote.loading)return remote.loading;
  remote.loading=(async()=>{
    const data=await api('/api/launcher-control');remote.agents=data.agents||[];remote.commands=data.commands||[];
    const selected=$('remote-device').value;
    $('remote-device').innerHTML=remote.agents.map(agent=>`<option value="${esc(agent.instance_id)}">${esc(agent.name)} · ${agent.online?'в сети':'не в сети'} · ${esc(agent.instance_id.slice(0,6))}</option>`).join('');
    if(remote.agents.some(agent=>agent.instance_id===selected))$('remote-device').value=selected;
    for(const agent of remote.agents){
      const draft=remoteDraft(agent);
      if(draft.awaitedJob&&agent.status?.job?.id&&agent.status.job.id!==draft.previousJobId)draft.awaitedJob=null;
      for(const key of ['pending','browsePending']){
        if(!draft[key])continue;
        const command=remote.commands.find(item=>String(item.id)===String(draft[key])&&item.instance_id===agent.instance_id);
        if(!command||['pending','claimed','running'].includes(command.status))continue;
        draft[key]=null;
        if(key==='browsePending'){
          if(['done','completed'].includes(command.status)&&command.data){draft.browser=command.data;draft.browseError='';}
          else draft.browseError=command.result||'Не удалось прочитать папку. Проверьте путь и доступ на компьютере.';
        }else if(['error','failed','expired','cancelled'].includes(command.status)){draft.awaitedJob=null;remoteFeedback(draft,command.result||'Компьютер не выполнил команду.',true);}
        else remoteFeedback(draft,command.result||(command.action==='prepare'?'Компьютер принял запуск. Ход подготовки показан в текущей задаче.':'Компьютер выполнил команду.'));
      }
    }
    renderRemote();
  })();
  try{return await remote.loading;}finally{remote.loading=null;}
}
function remoteQualities(){const values=[...document.querySelectorAll('#remote-qualities input:checked')].map(input=>input.value);if(!values.length)throw new Error('Выберите хотя бы одно качество.');return values;}
function remoteAction(fn){return async event=>{try{await fn(event);}catch(error){remoteFeedback(remoteDraft(),error.message,true);}};}
function remotePayload(){const draft=remoteDraft();if(!draft?.folder.trim())throw new Error('Выберите папку с исходными видео на компьютере.');return {folder:draft.folder.trim(),mode:draft.mode,qualities:remoteQualities(),hardware:draft.hardware,load:draft.load,catalog_section:draft.catalog_section};}
async function remoteCommand(action,payload={}){
  const agent=remoteAgent(),draft=remoteDraft(agent);if(!agent)throw new Error('Выберите компьютер.');if(!agent.online)throw new Error('Компьютер не отвечает. Проверьте лаунчер.');
  if(['prepare','send','switch_folder'].includes(action)&&remoteBusy(agent,draft))throw new Error('На компьютере уже выполняется задача.');
  remoteFeedback(draft,'Отправляю команду выбранному компьютеру…');if(action!=='browse_folder'&&remoteRenderCapable(agent)){draft.pending='sending';if(['prepare','send'].includes(action)){draft.awaitedJob=action;draft.previousJobId=agent.status?.job?.id;draft.launchFolder=payload.folder;}renderRemote();}
  if(['prepare','send'].includes(action)&&remoteRenderCapable(agent)&&matchMedia('(max-width:700px)').matches)$('remote-job').scrollIntoView({behavior:'smooth',block:'start'});
  try{
    const command=await api('/api/launcher-control/commands',{method:'POST',json:{action,payload,instance_id:agent.instance_id}});
    if(action==='browse_folder')draft.browsePending=command.id;else if(remoteRenderCapable(agent))draft.pending=command.id;
    remoteFeedback(draft,'Выбранный компьютер получит команду в течение нескольких секунд. Статус задачи обновляется автоматически.');
    await loadRemote();return command;
  }catch(error){if(action==='browse_folder')draft.browsePending=null;else{draft.pending=null;draft.awaitedJob=null;}remoteFeedback(draft,error.message,true);renderRemote();throw error;}
}
async function remoteBrowse(folder='',offset=0){
  const agent=remoteAgent(),draft=remoteDraft(agent);if(!remoteCanBrowse(agent))throw new Error('Просмотр папок недоступен. Проверьте настройки лаунчера на компьютере.');
  draft.browserOpen=true;draft.browseError='';draft.browsePending='sending';renderRemote();
  try{await remoteCommand('browse_folder',{folder,offset});}catch(error){draft.browseError=error.message;renderRemote();}
}
function remoteSaveInput(){
  const draft=remoteDraft();if(!draft)return;
  draft.folder=$('remote-folder-path').value;
  draft.qualities=[...document.querySelectorAll('#remote-qualities input:checked')].map(input=>input.value);
  for(const [id,key] of [['remote-mode','mode'],['remote-hardware','hardware'],['remote-load','load'],['remote-section','catalog_section']])draft[key]=$(id).value;
  // Rendering the form here would reset the caret while someone types a path.
  const agent=remoteAgent(),busy=remoteBusy(agent,draft);
  $('remote-prepare').disabled=!agent?.online||busy||!draft.folder.trim()||!draft.qualities.length;
  $('remote-open-path').disabled=!remoteCanBrowse(agent)||!draft.folder.trim();
  const prepared=Number(agent?.status?.prepared_count)||(agent?.status?.job?.kind==='prepare'&&agent.status.job.state==='completed'?Number(agent.status.job.ready)||0:0);
  $('remote-send').disabled=!agent?.online||busy||(remoteRenderCapable(agent)&&(!remoteSameFolder(draft.folder,agent.status?.folder||agent.status?.job?.folder)||prepared<1));
  $('remote-send-hint').textContent=remoteRenderCapable(agent)&&(!remoteSameFolder(draft.folder,agent.status?.folder||agent.status?.job?.folder)||prepared<1)?'Сначала подготовьте выбранную папку. Передача сама не запускается.':'Передача сама не запускается. Перед отправкой настройте и сохраните карточки в панели.';
  $('remote-start-summary').innerHTML=`<strong>Будет подготовлена папка:</strong><p class="remote-path">${esc(draft.folder||'Выберите папку с видео')}</p><p>${esc(draft.qualities.map(quality=>quality==='source'?'Исходное':quality).join(' · ')||'Выберите качества')} · ${esc($('remote-mode').selectedOptions[0]?.textContent||'')}</p>`;
}
$('remote-device').onchange=()=>renderRemote();
$('remote-refresh').onclick=remoteAction(loadRemote);
$('remote-folder').onchange=()=>{const draft=remoteDraft();if(draft&&$('remote-folder').value){draft.folder=$('remote-folder').value;renderRemote();}};
for(const id of ['remote-folder-path','remote-mode','remote-hardware','remote-load','remote-section','remote-qualities'])$(id).addEventListener('input',remoteSaveInput);
$('remote-switch').onclick=remoteAction(()=>remoteCommand('switch_folder',{folder:$('remote-folder').value}));
$('remote-prepare').onclick=remoteAction(()=>remoteCommand('prepare',remoteRenderCapable()?remotePayload():{qualities:remoteQualities()}));
$('remote-send').onclick=remoteAction(()=>remoteCommand('send',remoteRenderCapable()?remotePayload():{qualities:remoteQualities()}));
$('remote-stop').onclick=remoteAction(()=>remoteCommand('stop'));
$('remote-browse').onclick=remoteAction(()=>remoteBrowse());
$('remote-open-path').onclick=remoteAction(()=>remoteBrowse(remoteDraft().folder.trim()));
$('remote-browse-roots').onclick=remoteAction(()=>remoteBrowse());
$('remote-browse-up').onclick=remoteAction(()=>remoteBrowse(remoteDraft().browser.parent));
$('remote-browse-next').onclick=remoteAction(()=>remoteBrowse(remoteDraft().browser.folder,remoteDraft().browser.next_offset));
$('remote-browser-entries').onclick=remoteAction(event=>{const entry=event.target.closest('[data-remote-path]');if(entry)return remoteBrowse(entry.dataset.remotePath);});
$('remote-browse-select').onclick=()=>{const draft=remoteDraft();draft.folder=draft.browser.folder;draft.browserOpen=false;renderRemote();};
$('remote-browse-close').onclick=()=>{remoteDraft().browserOpen=false;renderRemote();};
setInterval(()=>{if(state.session&&state.tab==='remote')loadRemote().catch(error=>{const draft=remoteDraft();remoteFeedback(draft,`Не удалось обновить состояние: ${error.message}`,true);});},3000);
async function loadStorage(){
  const data=await api('/api/storage'),config=data.configuration,counts=data.counts;
  $('storage-config').innerHTML=config?`<strong>${config.enabled?'● Резервирование работает':'○ Резервирование приостановлено'}</strong><p>Канал: ${esc(config.title)}<br><small>ID: ${esc(config.chat_id)}</small></p>`:'<strong>Storage-канал ещё не подключён</strong><p>Создайте закрытый канал, добавьте бота администратором, отправьте боту <code>/storage</code> и перешлите сообщение из канала.</p>';
  $('storage-ready').textContent=counts.ready;$('storage-pending').textContent=counts.pending;$('storage-errors').textContent=counts.error;$('storage-total').textContent=counts.total;
  $('storage-toggle').textContent=config?.enabled?'Приостановить':'Продолжить';$('storage-toggle').dataset.action=config?.enabled?'pause':'resume';
  for(const id of ['storage-check','storage-scan','storage-retry','storage-toggle'])$(id).disabled=!config;
  $('storage-retry').disabled=!config||!counts.error;
  const labels={ready:'Защищён',pending:'В очереди',error:'Ошибка'};
  $('storage-recent').innerHTML=data.recent.length?data.recent.map(item=>`<p><strong>${esc(item.file_type==='video'?'Видео':'Документ')} · …${esc(item.file_id)}</strong><span class="state ${item.status==='ready'?'ready':item.status==='error'?'error':''}">${esc(labels[item.status]||item.status)}</span>${item.error?`<small>${esc(item.error)}</small>`:''}</p>`).join(''):'<p class="muted">Файлы появятся после подключения и сканирования каталога.</p>';
}
async function storageAction(action){$('storage-feedback').textContent='Выполняется…';const data=await api('/api/storage',{method:'POST',json:{action}});$('storage-feedback').textContent=action==='check'?'Канал доступен, права бота проверены.':action==='scan'?`Сканирование завершено. Новых файлов: ${data.added||0}.`:'Настройка сохранена.';await loadStorage();}
$('storage-refresh').onclick=action(loadStorage);
$('storage-check').onclick=action(()=>storageAction('check'));
$('storage-scan').onclick=action(()=>storageAction('scan'));
$('storage-retry').onclick=action(()=>storageAction('retry'));
$('storage-toggle').onclick=action(()=>storageAction($('storage-toggle').dataset.action));
$('logout').onclick=action(async()=>{await api('/api/logout',{method:'POST'});state.session=null;$('workspace').hidden=true;$('login').hidden=false;});
window.addEventListener('beforeunload',event=>{if(state.busy||state.dirty){event.preventDefault();event.returnValue='';}});
async function boot(){try{const fragment=new URLSearchParams(location.hash.slice(1));const token=fragment.get('login');if(token){history.replaceState(null,'',location.pathname);await api('/api/login',{method:'POST',json:{token}});}state.session=await api('/api/session');$('account').textContent=`Администратор ${state.session.admin_id}`;document.querySelectorAll('.max-mb').forEach(e=>e.textContent=state.session.max_file_bytes/1e6);$('login').hidden=true;$('workspace').hidden=false;await loadSections();await refresh();if(state.session.max_file_bytes<2e9)notice('Сейчас доступна загрузка до 50 МБ. Для файлов до 2000 МБ подключите локальный Telegram Bot API.');setInterval(()=>{if(state.session)refresh().catch(e=>notice(e.message,true));},5000);}catch(error){$('login-error').textContent=error.message;}}
boot();
// Optional structured access to the same authenticated library, without publishing or uploading side effects.
if(document.modelContext?.registerTool){try{Promise.resolve(document.modelContext.registerTool({name:'list_kinotime_drafts',title:'Список черновиков KinoTime',description:'Read the signed-in administrator’s saved drafts. Does not publish anything.',inputSchema:{type:'object',properties:{},additionalProperties:false},annotations:{readOnlyHint:true,untrustedContentHint:true},execute:async input=>{if(!input||Object.keys(input).length)throw new Error('Ожидается пустой объект.');const data=await api('/api/drafts');await refresh();return data;}})).catch(()=>{});}catch{}}


function renderReview(){
  const current=state.drafts.find(d=>d.id===state.id);
  $('review-actions').hidden=!current;
  $('review-status').textContent='Статус: '+({draft:'на проверке',revision:'на доработке',rejected:'отклонён',published:'опубликован'}[current?.status]||'не сохранён');
  $('review-history').textContent=(current?.review_history||[]).map(e=>`${e.at.slice(0,16).replace('T',' ')} · ${{revision:'На доработку',rejected:'Отклонено',draft:'На проверке',published:'Одобрено и опубликовано'}[e.status]||e.status}: ${e.comment}`).join('\n');
  for(const id of ['reject-draft','revise-draft','resubmit-draft'])$(id).disabled=!current||state.published||state.busy||state.pendingAction;
  $('find-card').disabled=state.published||current?.status==='rejected';
  $('review-comment').disabled=state.published;
  $('resubmit-draft').hidden=!current||!['revision','rejected'].includes(current.status);
  $('promo-panel').hidden=!state.published;
}
async function decide(status){
  if(!state.id)throw new Error('Сначала сохраните черновик.');
  if(status==='revision'&&!$('review-comment').value.trim()){$('review-comment').setAttribute('aria-invalid','true');throw new Error('Не удалось отправить на доработку: напишите в поле выше, что нужно исправить.');}
  if(state.dirty)await saveDraft();
  const saved=await api(`/api/drafts/${state.id}/review`,{method:'POST',json:{status,comment:$('review-comment').value}});
  $('review-comment').value='';
  const index=state.drafts.findIndex(d=>d.id===saved.id);if(index>=0)state.drafts[index]=saved;
  await openDraft(saved);renderDrafts();
  return status==='revision'?'Черновик возвращён на доработку с комментарием.':status==='rejected'?'Черновик отклонён.':'Черновик снова на проверке.';
}
$('revise-draft').onclick=draftAction('revise-draft','review-feedback','Отправляем…',()=>decide('revision'));
$('reject-draft').onclick=draftAction('reject-draft','review-feedback','Отклоняем…',()=>decide('rejected'));
$('resubmit-draft').onclick=draftAction('resubmit-draft','review-feedback','Возвращаем…',()=>decide('draft'));
$('review-comment').addEventListener('input',()=>{$('review-comment').removeAttribute('aria-invalid');feedback('review-feedback','');});
$('load-promo').onclick=action(async()=>{const result=await api(`/api/drafts/${state.id}/promo`);$('promo-text').value=result.text;});
$('copy-promo').onclick=action(async()=>{await navigator.clipboard.writeText($('promo-text').value);notice('Рекламный пост скопирован.');});
$('find-card').onclick=action(async()=>{
  const data=await api('/api/card-candidates',{method:'POST',json:{title:$('title').value,content_type:$('content-type').value}});
  $('card-candidates').replaceChildren();
  if(!data.candidates.length){notice('Не найдено вариантов. Проверьте название или настройку TMDB_API_KEY.');return;}
  for(const candidate of data.candidates){const button=document.createElement('button');button.className='secondary';button.textContent=`${candidate.title} ${candidate.year||''}`;button.onclick=()=>{if(state.published)return;$('title').value=candidate.title;$('description').value=(candidate.overview||'').slice(0,2500);$('poster-url').value=candidate.poster_url||'';dirty();notice('Описание и постер добавлены. Проверьте, что выбран нужный фильм.');};$('card-candidates').append(button);}
});

const groupedSelection=new Map();
const batch = {offset:0,limit:20,total:0,items:[],selected:new Set(),edits:new Map(),package:[],busy:false,loading:false};
function confirmBatch(chosen){
  const dialog=$('batch-confirm');
  $('batch-confirm-title').textContent=`Опубликовать карточки: ${chosen.length}?`;
  $('batch-confirm-list').innerHTML=chosen.map(d=>`<li>${esc(d.body.title)}</li>`).join('');
  dialog.returnValue='';dialog.showModal();
  return new Promise(resolve=>dialog.addEventListener('close',()=>resolve(dialog.returnValue==='publish'),{once:true}));
}
function batchControls(){
  for(const el of document.querySelectorAll('[data-batch-field], [data-batch-save], [data-batch-open], [data-batch-attach]'))el.disabled=batch.busy||batch.loading;

  $('batch-publish').disabled=batch.busy||batch.loading||batch.edits.size>0||!batch.package.length;
  $('batch-publish').textContent=batch.busy?'Публикуем…':`Опубликовать пакет (${batch.package.length})`;
  for(const id of ['batch-select','batch-clear','batch-refresh','batch-reject-all'])$(id).disabled=batch.busy||batch.loading;
  $('batch-prev').disabled=batch.busy||batch.loading||batch.offset===0;
  $('batch-next').disabled=batch.busy||batch.loading||batch.offset+batch.limit>=batch.total;
  for(const input of document.querySelectorAll('[data-batch-check]'))input.disabled=batch.busy||batch.loading||!batch.items.find(d=>d.id===input.dataset.batchCheck)?.can_publish;
}
async function loadBatch(){
  if(batch.loading||batch.busy)return;
  batch.loading=true;batchControls();
  try{
    let data=await api(`/api/review-queue?offset=${batch.offset}`);
    if(!data.items.length&&batch.offset>0){batch.offset=Math.max(0,Math.floor(Math.max(0,data.total-1)/data.limit)*data.limit);data=await api(`/api/review-queue?offset=${batch.offset}`);}
    const pack=await api('/api/publication-package');batch.package=pack.items;renderPackage();
    batch.items=data.items;batch.total=data.total;batch.limit=data.limit;batch.selected.clear();
    $('batch-page').textContent=data.total?`${batch.offset+1}–${batch.offset+data.items.length} из ${data.total}`:'Нет черновиков на проверке';
    $('batch-list').innerHTML=data.items.length?data.items.map(d=>`<article class="panel batch-card">
      <div class="batch-card-heading"><label><strong>${esc(d.body.title||'Без названия')}</strong></label><div><button class="secondary" data-batch-open="${esc(d.id)}">Проверить / изменить</button> <button class="secondary" data-batch-attach="${esc(d.id)}">В существующую карточку</button></div></div>
      ${d.launcher_instance?`<p class="muted">Компьютер: ${esc(d.launcher_name||'Лаунчер')} · ${esc(d.launcher_instance.slice(0,6))}</p>`:''}
      <p class="${d.can_publish?'batch-ready':'muted'}">${d.can_publish?(batch.package.some(p=>p.id===d.id)?'Проверен · добавлен в пакет':'Файлы готовы · откройте карточку для отдельной проверки'):esc(d.reason)}</p>
      <p class="muted">${d.body.content_type==='movie'?'Фильм':'Сериал'} · Файлы: ${d.ready_files}/${d.total_files} · Постер: ${d.body.poster_url?'указан':'не выбран — проверьте карточку'}</p>
      ${!d.body.movie_number?`<div data-batch-editor="${esc(d.id)}">
        <label>Постер — ссылка на изображение (HTTPS)<input type="url" data-batch-field="poster_url" maxlength="1000" placeholder="https://…" value="${esc((batch.edits.get(d.id)||d.body).poster_url)}"></label>
        <label>Описание фильма<textarea data-batch-field="description" rows="5" maxlength="2500">${esc((batch.edits.get(d.id)||d.body).description)}</textarea></label>
        <button class="secondary" data-batch-save="${esc(d.id)}">Сохранить постер и описание</button>
        <p data-batch-message role="status">${batch.edits.has(d.id)?'Есть несохранённые изменения':''}</p>
      </div>`:''}
      <div class="group-file-picks">${d.body.items.map(i=>`<label><input type="checkbox" data-group-asset="${esc(i.asset_id)}" data-group-draft="${esc(d.id)}" ${(d.grouped_assets||[]).includes(i.asset_id)||d.status!=='draft'||d.body.movie_number?'disabled':''} ${groupedSelection.has(i.asset_id)?'checked':''}> ${esc(i.relative_path||i.name)} ${(d.grouped_assets||[]).includes(i.asset_id)?'· уже объединён':''}</label>`).join('')}</div><details><summary>Описание и варианты</summary><p class="batch-description">${esc(d.body.description||'Описание не заполнено. Проверьте название и выберите фильм в TMDB.')}</p><ul>${d.body.items.map(i=>`<li>${esc(i.category||d.body.season_name||'Основной файл')} → ${esc(i.name)}<br><small>${esc(i.relative_path||'')}</small></li>`).join('')}</ul>${d.body.poster_url?`<p>Постер: <a href="${esc(d.body.poster_url)}" target="_blank" rel="noopener noreferrer">Открыть изображение</a></p>`:''}</details>
    </article>`).join(''):'<div class="panel batch-card">Здесь появятся черновики из очереди лаунчера. Уже опубликованные и отклонённые карточки сюда не входят.</div>';
  }finally{batch.loading=false;batchControls();}
}
$('batch-list').oninput=event=>{
  const field=event.target.dataset.batchField;
  if(!field)return;
  const editor=event.target.closest('[data-batch-editor]'),id=editor.dataset.batchEditor;
  batch.edits.set(id,{poster_url:editor.querySelector('[data-batch-field="poster_url"]').value,
                     description:editor.querySelector('[data-batch-field="description"]').value});
  editor.querySelector('[data-batch-message]').textContent='Есть несохранённые изменения. Сохраните перед публикацией.';
  batchControls();
};
window.addEventListener('beforeunload',event=>{if(batch.edits.size){event.preventDefault();event.returnValue='';}});
$('batch-list').onchange=event=>{const assetId=event.target.dataset.groupAsset;if(assetId){const d=batch.items.find(d=>d.id===event.target.dataset.groupDraft);if(event.target.checked)groupedSelection.set(assetId,{asset_id:assetId,draft_id:d.id,review_token:d.review_token});else groupedSelection.delete(assetId);$('group-count').textContent=groupedSelection.size;return;}
  const id=event.target.dataset.batchCheck;if(!id||batch.busy)return;
  if(event.target.checked)batch.selected.add(id);else batch.selected.delete(id);batchControls();
};
async function loadBatchDraft(d){
  if(!state.drafts.some(item=>item.id===d.id))state.drafts.push(d);
  const extra=await Promise.all(d.body.items.filter(i=>!asset(i.asset_id)).map(i=>api(`/api/uploads/${i.asset_id}`)));
  state.assets.push(...extra);renderDrafts();await openDraft(d);
}
async function chooseExistingTarget(){
  const kind=$('content-type').value;
  const dialog=document.createElement('dialog');
  dialog.innerHTML=`<h2>Добавить файлы в существующую карточку</h2><p>Выберите ${kind==='series'?'сериал':'фильм'} из каталога. После выбора настройте ${kind==='series'?'сезон и названия серий':'категории и названия вариантов'} и опубликуйте изменения.</p><form data-search class="search"><input name="query" maxlength="120" value="${esc($('title').value)}" placeholder="Название или код"><button class="secondary">Найти</button></form><div data-results class="panel"></div><form method="dialog"><button class="secondary" value="cancel">Отмена</button></form>`;
  document.body.append(dialog);dialog.addEventListener('close',()=>dialog.remove());dialog.showModal();
  const search=async()=>{
    const query=dialog.querySelector('[name=query]').value.trim();
    const data=await api(`/api/catalog?q=${encodeURIComponent(query)}&page=0`);
    const movies=data.movies.filter(movie=>movie.content_type===kind);
    const results=dialog.querySelector('[data-results]');
    results.innerHTML=movies.length?movies.map(movie=>`<button type="button" class="library-row" data-target="${movie.id}"><span class="step">${kind==='series'?'С':'Ф'}</span><span><strong>${esc(movie.title)}</strong><br><small>Код ${movie.id}</small></span><span>Выбрать</span></button>`).join(''):'<p class="muted">Подходящая карточка не найдена.</p>';
    results.onclick=async event=>{
      const id=event.target.closest('[data-target]')?.dataset.target;if(!id)return;
      const movie=movies.find(item=>item.id===Number(id));
      state.movie=movie;$('catalog-section').value=movie.catalog_section;$('content-type').value=movie.content_type;$('title').value=movie.title;
      if(kind==='movie')for(const item of state.items)if(!item.category)item.category='Варианты';
      await fillSeasons();dirty();syncMode();renderQueue();dialog.close();showTab('upload');
      notice(`Выбрана карточка «${movie.title}» · код ${movie.id}. Проверьте категории и названия, затем нажмите «Добавить файлы в карточку».`);
    };
  };
  dialog.querySelector('[data-search]').onsubmit=action(async event=>{event.preventDefault();await search();});
  await search();
}
$('batch-list').onclick=action(async event=>{
  const save=event.target.closest('[data-batch-save]');
  if(save){
    if(batch.busy||batch.loading)return;
    const id=save.dataset.batchSave,editor=save.closest('[data-batch-editor]');
    const message=editor.querySelector('[data-batch-message]');
    if(state.id===id&&state.dirty)throw new Error('Сначала сохраните изменения этого фильма на вкладке «Загрузка».');
    const fields={poster_url:editor.querySelector('[data-batch-field="poster_url"]').value.trim(),
                  description:editor.querySelector('[data-batch-field="description"]').value};
    batch.busy=true;batchControls();message.textContent='Сохраняем…';
    try{
      const current=await api(`/api/drafts/${id}`);
      const saved=await api('/api/drafts',{method:'POST',json:{id,body:{...current.body,...fields}}});
      batch.edits.delete(id);
      const index=state.drafts.findIndex(d=>d.id===id);if(index>=0)state.drafts[index]=saved;
      if(state.id===id){$('description').value=saved.body.description;$('poster-url').value=saved.body.poster_url;}
      batch.busy=false;await loadBatch();
      const updated=document.querySelector(`[data-batch-editor="${id}"] [data-batch-message]`);
      if(updated)updated.textContent='Постер и описание сохранены. Фильм ещё не опубликован.';
    }catch(error){message.textContent=error.message;}
    finally{batch.busy=false;batchControls();}
    return;
  }

  const attachId=event.target.closest('[data-batch-attach]')?.dataset.batchAttach;
  if(attachId){
    if(batch.busy||!mayReset())return;
    if(batch.edits.has(attachId))throw new Error('Сначала сохраните постер и описание этого фильма.');
    const draft=batch.items.find(item=>item.id===attachId);await loadBatchDraft(draft);await chooseExistingTarget();return;
  }
  const id=event.target.closest('[data-batch-open]')?.dataset.batchOpen;
  if(!id||batch.busy||!mayReset())return;
  if(batch.edits.has(id))throw new Error('Сначала сохраните постер и описание этого фильма.');
  const d=batch.items.find(item=>item.id===id);
  await loadBatchDraft(d);
});
$('batch-select').onclick=()=>{batch.selected=new Set(batch.items.filter(d=>d.can_publish).map(d=>d.id));for(const input of document.querySelectorAll('[data-batch-check]'))input.checked=batch.selected.has(input.dataset.batchCheck);batchControls();};
$('batch-clear').onclick=()=>{batch.selected.clear();for(const input of document.querySelectorAll('[data-batch-check]'))input.checked=false;batchControls();};
$('batch-refresh').onclick=action(loadBatch);
$('batch-prev').onclick=action(async()=>{batch.offset=Math.max(0,batch.offset-batch.limit);await loadBatch();});
$('batch-next').onclick=action(async()=>{batch.offset+=batch.limit;await loadBatch();});
$('batch-publish').onclick=action(async()=>{
  if(batch.busy||batch.loading||batch.edits.size||!batch.package.length)return;
  if(state.dirty)throw new Error('Сначала сохраните изменения открытого черновика.');
  const chosen=batch.package.slice();
  if(!await confirmBatch(chosen))return;
  batch.busy=true;batchControls();$('batch-feedback').textContent='Публикуем выбранные карточки…';
  try{
    const data=await api('/api/publication-package/publish',{method:'POST',json:{items:chosen.map(d=>({id:d.id,review_token:d.review_token}))}});
    const ok=data.results.filter(r=>r.ok),errors=data.results.filter(r=>!r.ok);
    $('batch-feedback').textContent=`Опубликовано: ${ok.length} из ${chosen.length}.`+errors.map(r=>`\n${chosen.find(d=>d.id===r.id)?.body.title}: ${r.error}`).join('');
    batch.busy=false;await loadBatch();await refresh();
    if(ok.some(r=>r.id===state.id)){state.published=true;syncMode();renderReview();}
  }catch(error){$('batch-feedback').textContent=error.message+' Обновите список перед повтором: часть карточек могла уже опубликоваться.';}
  finally{batch.busy=false;batchControls();}
});

function renderPackage(){
  $('package-list').innerHTML=batch.package.length?batch.package.map(d=>`<div class="batch-card-heading"><strong>${esc(d.body.title)}</strong><button class="secondary" data-package-remove="${esc(d.id)}">Убрать из пакета</button></div>`).join(''):'<p class="muted">Пакет пуст. Откройте фильм ниже, проверьте оформление и нажмите «Проверено — добавить в пакет».</p>';
}
$('package-list').onclick=action(async event=>{
  const id=event.target.closest('[data-package-remove]')?.dataset.packageRemove;
  if(!id||batch.busy||batch.loading)return;
  batch.busy=true;batchControls();
  try{const result=await api(`/api/publication-package/${id}`,{method:'POST',json:{action:'remove'}});batch.package=result.items;renderPackage();}
  finally{batch.busy=false;batchControls();}
});
$('add-package').onclick=draftAction('add-package','draft-feedback','Добавляем…',async()=>{
  const saved=await saveDraft();
  if(state.dirty)throw new Error('Карточка изменилась во время сохранения. Проверьте её и добавьте ещё раз.');
  await api(`/api/publication-package/${saved.id}`,{method:'POST',json:{review_token:saved.review_token}});
  showTab('batch');
  notice(`«${saved.body.title}» проверен и добавлен в пакет. Публикация ещё не выполнена.`);
  return 'Карточка добавлена в пакет.';
});

async function editCatalogFolders(id){
  let data=await api(`/api/catalog/${id}/folders`);
  const dialog=document.createElement('dialog');
  dialog.className='catalog-explorer-dialog';
  dialog.innerHTML=`<h2>Папки и файлы · ${esc(data.title)}</h2><p>Выберите папку слева. Справа можно изменить название папки и каждой кнопки файла в боте. Папки с одинаковым названием при сохранении объединятся.${data.content_type==='series'?' Чтобы собрать качества и озвучки одной серии, отметьте её отдельные строки в одной папке.':''}</p><form><div class="catalog-explorer"><nav data-folder-list aria-label="Папки карточки"></nav><section data-folder-content></section></div><p data-catalog-feedback role="status" aria-live="polite"></p><div class="batch-toolbar"><button class="primary" type="submit">Сохранить изменения</button><button class="secondary" type="button" data-close>Закрыть</button></div></form>`;
  let selected=data.folders[0]?.id??null,busy='',plan=null,target=null,mergeName='',nameEdited=false,revision=0,mergeMessage='';
  let picked=new Set();
  const folderList=dialog.querySelector('[data-folder-list]');
  const content=dialog.querySelector('[data-folder-content]');
  const feedback=dialog.querySelector('[data-catalog-feedback]');
  const submit=dialog.querySelector('[type=submit]');
  const renameBody=()=>data.folders.map(folder=>({id:folder.id,name:folder.name.trim(),files:folder.files.map(file=>({id:file.id,name:file.name.trim()}))}));
  let baseline=JSON.stringify(renameBody());
  const namesDirty=()=>JSON.stringify(renameBody())!==baseline;
  const currentFolder=()=>data.folders.find(folder=>folder.id===selected);
  const mergeBody=()=>({folder_id:selected,target_id:target,episode_ids:[...picked].sort((a,b)=>a-b),name:mergeName.trim()});
  const invalidate=()=>{revision++;plan=null;mergeMessage='';};
  const syncCurrent=()=>{
    const folder=currentFolder();if(!folder)return;
    const folderInput=content.querySelector('[data-folder-name]');if(folderInput)folder.name=folderInput.value;
    for(const input of content.querySelectorAll('[data-file-name]'))folder.files.find(file=>file.id===Number(input.dataset.fileName)).name=input.value;
  };
  const updateMerge=()=>{
    const panel=content.querySelector('[data-episode-merge]');if(!panel)return;
    const files=currentFolder()?.files.filter(file=>picked.has(file.id))||[];
    const main=panel.querySelector('[data-merge-target]');
    if(!files.some(file=>file.id===target)){target=files[0]?.id??null;if(!files.length){mergeName='';nameEdited=false;}}
    if(!nameEdited)mergeName=files.find(file=>file.id===target)?.name||'';
    main.innerHTML=files.map(file=>`<option value="${file.id}">${file.id} · ${esc(file.name)}</option>`).join('');main.value=target??'';
    const name=panel.querySelector('[data-merge-name]');if(name.value!==mergeName)name.value=mergeName;
    panel.querySelector('[data-merge-count]').textContent=`Выбрано серий: ${files.length}`;
    panel.querySelector('[data-merge-options]').hidden=files.length<2;
    const hint=panel.querySelector('[data-merge-hint]');
    hint.textContent=namesDirty()?'Сохраните изменения названий, затем объедините серии.':files.length<2?'Отметьте минимум две строки одной серии в этой папке.':'Останется выбранная основная серия и её ID. Качества, озвучки и отметки просмотра перенесутся в неё.';
    hint.classList.toggle('error',namesDirty());
    panel.querySelector('[data-merge-preview]').disabled=Boolean(busy)||namesDirty()||files.length<2||!mergeName.trim();
    panel.querySelector('[data-merge-apply]').disabled=Boolean(busy)||namesDirty()||!plan?.token||Boolean(plan.conflicts?.length);
    panel.querySelector('[data-merge-preview]').textContent=busy==='preview'?'Проверяем…':'Проверить объединение';
    panel.querySelector('[data-merge-apply]').textContent=busy==='apply'?'Объединяем…':'Объединить серии';
    const preview=panel.querySelector('[data-merge-result]');
    preview.classList.toggle('error',Boolean(mergeMessage)&&!plan);
    if(!plan){preview.textContent=mergeMessage;return;}
    const counts=plan.counts||{};
    preview.innerHTML=`${mergeMessage?`<p class="error">${esc(mergeMessage)}</p>`:''}<p><strong>После объединения: ${esc(plan.name)}</strong><br>Основная серия: ID ${esc(plan.target_id)}. Остальные выбранные строки перестанут быть отдельными сериями.</p><p>Серии: ${esc(counts.episodes_before??files.length)} → ${esc(counts.episodes_after??1)}.<br>Варианты: ${esc(counts.variants_before??'—')} → ${esc(counts.variants_after??'—')}. Одинаковых файлов: ${esc(counts.duplicates??0)}.<br>Пользователей с отметкой просмотра: ${esc(counts.watched_users??0)}.</p>${plan.conflicts?.length?`<div class="episode-merge-conflicts"><strong>Объединение недоступно:</strong><ul>${plan.conflicts.map(conflict=>`<li>${esc(conflict.quality||'качество не указано')} · ${esc(conflict.voice||'озвучка не указана')}<br>${esc((conflict.episodes||[]).map(episode=>`ID ${episode.id}: ${episode.name}`).join('; '))}<br>${esc(conflict.reason||'Варианты конфликтуют.')}</li>`).join('')}</ul></div>`:'<p class="ready">Конфликтов нет. Проверьте основную серию и итоговое название перед подтверждением.</p>'}`;
  };
  const renderFolderList=()=>{folderList.innerHTML=data.folders.map(folder=>`<button type="button" class="catalog-explorer-folder ${folder.id===selected?'active':''}" data-open-folder="${folder.id}"><span>📁</span><span><strong>${esc(folder.name)}</strong><small>${folder.files.length} файл(ов)</small></span></button>`).join('');};
  const renderContent=()=>{
    const folder=currentFolder();
    if(!folder){content.innerHTML='<div class="empty">В этой карточке пока нет папок.</div>';return;}
    const series=data.content_type==='series';
    content.innerHTML=`<div class="catalog-explorer-path">📁 ${esc(data.title)} <span>›</span> ${esc(folder.name)}</div><label>Название папки<input data-folder-name required value="${esc(folder.name)}"></label><h3>${series?'Серии в папке':'Файлы в папке'}</h3><div class="catalog-explorer-files">${folder.files.length?folder.files.map(file=>`<div class="catalog-explorer-file">${series?`<input type="checkbox" data-merge-episode="${file.id}" aria-label="Выбрать серию ${esc(file.name)}" ${picked.has(file.id)?'checked':''}>`:'<span>🎞️</span>'}<span><input data-file-name="${file.id}" required aria-label="Название ${series?'серии':'файла'}" value="${esc(file.name)}">${file.qualities?.length>3?`<details class="catalog-explorer-variants"><summary>Вариантов: ${file.qualities.length}</summary><small>${esc(file.qualities.join('\n'))}</small></details>`:`<small>${file.qualities?.length?esc('Качества: '+file.qualities.join(', ')):'Название кнопки файла в боте'}</small>`}</span></div>`).join(''):'<div class="empty">Папка пока пустая.</div>'}</div>${series?'<div class="episode-merge" data-episode-merge><h3>Объединить серии</h3><p data-merge-count></p><p data-merge-hint class="field-note"></p><div data-merge-options hidden><label>Основная серия (её ID сохранится)<select data-merge-target></select></label><label>Итоговое название серии<input data-merge-name autocomplete="off"></label><div class="episode-merge-actions"><button type="button" class="secondary" data-merge-preview>Проверить объединение</button><button type="button" class="primary" data-merge-apply disabled>Объединить серии</button></div><div data-merge-result role="status" aria-live="polite"></div></div></div>':''}`;
    updateMerge();
  };
  const setBusy=value=>{
    busy=value;
    for(const control of dialog.querySelectorAll('input,select,button'))control.disabled=Boolean(busy);
    submit.disabled=Boolean(busy)||!data.folders.length;submit.textContent=busy==='save'?'Сохраняем…':'Сохранить изменения';
    updateMerge();
  };
  const replaceData=(catalog,afterMerge=false)=>{
    data=catalog;baseline=JSON.stringify(renameBody());
    if(!data.folders.some(folder=>folder.id===selected))selected=data.folders.find(folder=>folder.files.some(file=>picked.has(file.id)))?.id??data.folders[0]?.id??null;
    picked=afterMerge?new Set():new Set(currentFolder()?.files.filter(file=>picked.has(file.id)).map(file=>file.id)||[]);
    if(afterMerge){target=null;nameEdited=false;mergeName='';}else if(!picked.has(target)){target=null;}
    invalidate();renderFolderList();renderContent();
  };
  renderFolderList();renderContent();
  folderList.onclick=event=>{const button=event.target.closest('[data-open-folder]');if(!button||busy)return;syncCurrent();selected=Number(button.dataset.openFolder);picked.clear();target=null;mergeName='';nameEdited=false;invalidate();renderFolderList();renderContent();};
  content.oninput=event=>{
    if(event.target.matches('[data-merge-name]')){mergeName=event.target.value;nameEdited=true;invalidate();updateMerge();return;}
    if(event.target.matches('[data-file-name],[data-folder-name]')){syncCurrent();invalidate();renderFolderList();updateMerge();}
  };
  content.onchange=event=>{
    if(busy)return;
    if(event.target.matches('[data-merge-episode]')){const episode=Number(event.target.dataset.mergeEpisode);if(event.target.checked)picked.add(episode);else picked.delete(episode);invalidate();updateMerge();}
    if(event.target.matches('[data-merge-target]')){target=Number(event.target.value);if(!nameEdited)mergeName=currentFolder().files.find(file=>file.id===target)?.name||'';invalidate();updateMerge();}
  };
  content.onclick=async event=>{
    const preview=event.target.closest('[data-merge-preview]'),apply=event.target.closest('[data-merge-apply]');
    if((!preview&&!apply)||busy||namesDirty()||picked.size<2||!mergeName.trim())return;
    if(apply&&(!plan?.token||plan.conflicts?.length))return;
    const body=mergeBody(),version=revision;
    setBusy(apply?'apply':'preview');
    try{
      const result=await api(`/api/catalog/${id}/episodes/merge`,{method:'POST',json:{action:apply?'apply':'preview',...body,...(apply?{expected_token:plan.token}:{})}});
      if(!dialog.isConnected||version!==revision)return;
      if(preview){plan=result;mergeMessage='';updateMerge();}
      else{
        const catalog=result.catalog||await api(`/api/catalog/${id}/folders`);
        replaceData(catalog,true);
        feedback.textContent=`Серии объединены. Сохранён ID ${body.target_id}: ${body.name}. Откройте список серий в боте заново.`;
        feedback.classList.remove('error');
      }
    }catch(error){const previous=plan;invalidate();mergeMessage=error.message;if(error.conflicts?.length)plan={...body,counts:previous?.counts||{},conflicts:error.conflicts};updateMerge();}
    finally{setBusy('');}
  };
  submit.disabled=!data.folders.length;
  document.body.append(dialog);dialog.showModal();
  dialog.querySelector('[data-close]').onclick=()=>dialog.close();
  dialog.addEventListener('cancel',event=>{if(busy)event.preventDefault();});
  dialog.addEventListener('close',()=>dialog.remove());
  dialog.querySelector('form').onsubmit=async event=>{
    event.preventDefault();if(busy)return;syncCurrent();setBusy('save');
    try{
      const result=await api(`/api/catalog/${id}/folders`,{method:'POST',json:{folders:renameBody()}});
      const continueMerge=data.content_type==='series'&&picked.size>=2;
      if(continueMerge){replaceData(result);feedback.textContent='Изменения названий сохранены. Теперь можно проверить объединение серий.';feedback.classList.remove('error');}
      else{dialog.close();notice(result.merged_folders?`Изменения сохранены. Объединено папок: ${result.merged_folders}.`:'Названия папок и файлов сохранены. Они появятся при следующем открытии карточки.');}
    }catch(error){feedback.textContent=error.message;feedback.classList.add('error');}
    finally{setBusy('');}
  };
}
async function editCatalogRatings(id){
  const values=await api(`/api/catalog/${id}/ratings`);
  const movie=state.catalog.find(m=>m.id===Number(id));
  const dialog=document.createElement('dialog');
  dialog.innerHTML=`<h2>Рейтинги · ${esc(movie.title)}</h2><form method="dialog"><input type="hidden" name="rating_source_id" value="${esc(values.rating_source_id??'')}"><p data-source-info>${esc(ratingSourceText(values))}</p><button type="button" data-auto>Заполнить из ПоискКино</button><button type="button" data-refresh>Обновить из API</button><label>КиноПоиск (0–10)<input name="rating_kp" inputmode="decimal" value="${esc(values.rating_kp??'')}"></label><label>IMDb (0–10)<input name="rating_imdb" inputmode="decimal" value="${esc(values.rating_imdb??'')}"></label><label>Возраст<select name="age_rating">${['','0+','6+','12+','16+','18+'].map(v=>`<option value="${v}" ${v===(values.age_rating||'')?'selected':''}>${v||'Не указан'}</option>`).join('')}</select></label><p role="status"></p><button type="submit">Сохранить</button><button type="button" data-close>Отмена</button></form>`;
  document.body.append(dialog);dialog.showModal();
  for(const [selector,refresh] of [['[data-auto]',false],['[data-refresh]',true]])dialog.querySelector(selector).onclick=action(async()=>{await ratingPicker(movie.title,movie.content_type,Number(dialog.querySelector('[name=rating_source_id]').value)||null,refresh, record=>{for(const key of ['rating_kp','rating_imdb','age_rating']){const input=dialog.querySelector(`[name=${key}]`);if(!input.value&&record[key]!=null)input.value=record[key];}dialog.querySelector('[name=rating_source_id]').value=record.rating_source_id;dialog.querySelector('[data-source-info]').textContent=ratingSourceText(record);});});
  dialog.addEventListener('close',()=>dialog.remove());
  dialog.querySelector('[data-close]').onclick=()=>dialog.close();
  dialog.querySelector('form').onsubmit=async event=>{
    event.preventDefault();const button=dialog.querySelector('[type="submit"]');button.disabled=true;
    try{await api(`/api/catalog/${id}/ratings`,{method:'POST',json:{...Object.fromEntries(new FormData(event.target)),rating_source_id:Number(new FormData(event.target).get('rating_source_id'))||null}});dialog.close();notice('Рейтинги и возраст сохранены. Откройте карточку в боте заново.');}
    catch(error){dialog.querySelector('[role="status"]').textContent=error.message;}
    finally{button.disabled=false;}
  };
}

function ratingSourceText(record){return record.rating_source_id?`ПоискКино · ID ${record.rating_source_id} · получено ${record.ratings_fetched_at||'ранее'}. Оценки могут быть изменены вручную.`:'';}
async function ratingPicker(title,kind,source,refresh,apply){
  const data=await api('/api/ratings/lookup',{method:'POST',json:{query:title,content_type:kind,source_id:source,refresh}});
  if(!data.items.length)throw new Error('Совпадений нет. Уточните название; ручной ввод остаётся доступен.');
  const dialog=document.createElement('dialog');
  dialog.innerHTML='<h2>Выберите произведение</h2><p>Заполняются только пустые поля. Чтобы заменить оценку, сначала очистите её. Проверьте год и тип.</p><div data-results></div><button type="button">Отмена</button>';
  for(const record of data.items){const button=document.createElement('button');button.type='button';button.className='secondary';button.textContent=`${record.title} (${record.year||'год неизвестен'}) · КП ${record.rating_kp??'—'} · IMDb ${record.rating_imdb??'—'} · ${record.age_rating||'возраст неизвестен'} · ${data.cached?'из базы':'из API'}`;button.onclick=()=>{apply(record);dialog.close();};dialog.querySelector('[data-results]').append(button);}
  dialog.querySelector(':scope > button').onclick=()=>dialog.close();dialog.onclose=()=>dialog.remove();document.body.append(dialog);dialog.showModal();
}
for(const [id,refresh] of [['ratings-auto',false],['ratings-refresh',true]])$(id).onclick=action(async()=>{
  await ratingPicker($('title').value,$('content-type').value,Number($('rating-source').value)||null,refresh,record=>{
    for(const [key,target] of [['rating_kp','rating-kp'],['rating_imdb','rating-imdb'],['age_rating','age-rating']])if(!$(target).value&&record[key]!=null)$(target).value=record[key];
    $('rating-source').value=record.rating_source_id;$('rating-source-info').textContent=ratingSourceText(record);dirty();
  });
});

$('group-files').onclick=action(async()=>{
  if(!groupedSelection.size)throw new Error('Отметьте файлы для одной карточки.');
  if(batch.edits.size||state.dirty)throw new Error('Сначала сохраните изменения открытых карточек.');
  await refresh();
  const targets=state.drafts.filter(d=>d.status==='draft'&&!d.body.movie_number&&![...groupedSelection.values()].some(i=>i.draft_id===d.id));
  const dialog=document.createElement('dialog');
  dialog.innerHTML='<h2>Одна карточка из выбранных файлов</h2><p>У карточки будет общее название, постер и описание. Исходные файлы повторно не отправляются. После объединения настройте категории или сезоны каждого файла.</p><form><label>Название<input name="title" required maxlength="200"></label><label>Тип<select name="content_type"><option value="movie">Фильм</option><option value="series">Сериал</option></select></label><p role="status"></p><button>Создать карточку</button><button type="button">Отмена</button></form>';
  const destination=document.createElement('label');destination.innerHTML=`Добавить файлы в<select name="target_id"><option value="">Новую карточку</option>${targets.map(d=>`<option value="${esc(d.id)}">${esc(d.body.title)}</option>`).join('')}</select>`;
  dialog.querySelector('form').prepend(destination);
  destination.querySelector('select').onchange=event=>{const existing=Boolean(event.target.value);for(const name of ['title','content_type'])dialog.querySelector(`[name="${name}"]`).disabled=existing;dialog.querySelector('button').textContent=existing?'Добавить в карточку':'Создать карточку';};
  dialog.querySelector('[type=button]').onclick=()=>dialog.close();dialog.onclose=()=>dialog.remove();document.body.append(dialog);dialog.showModal();
  dialog.querySelector('form').onsubmit=async event=>{event.preventDefault();const button=dialog.querySelector('button');button.disabled=true;
    try{const body=Object.fromEntries(new FormData(event.target));body.season_number=body.content_type==='series'?1:null;if(body.target_id)body.target_token=targets.find(d=>d.id===body.target_id).review_token;const d=await api('/api/drafts/group-files',{method:'POST',json:{items:[...groupedSelection.values()],body}});groupedSelection.clear();$('group-count').textContent='0';await refresh();state.assets.push(...await Promise.all(d.body.items.filter(i=>!asset(i.asset_id)).map(i=>api(`/api/uploads/${i.asset_id}`))));dialog.close();await openDraft(d);notice('Файлы объединены. Настройте названия подкатегорий и сезоны, описание и постер.');}
    catch(error){dialog.querySelector('[role=status]').textContent=error.message;button.disabled=false;}
  };
});

// Drag only from the handle, leaving text fields selectable and editable.
let queueDrag=null;
function clearQueueDrop(){document.querySelectorAll('#queue .drop-before,#queue .drop-after').forEach(row=>row.classList.remove('drop-before','drop-after'));}
function finishQueueDrag(){
  if(queueDrag)cancelAnimationFrame(queueDrag.frame);
  queueDrag=null;clearQueueDrop();
  document.querySelectorAll('#queue .dragging').forEach(row=>row.classList.remove('dragging'));
}
function locateQueueDrop(y){
  clearQueueDrop();if(!queueDrag)return;
  const rows=[...document.querySelectorAll('#queue .queue-row')].filter(row=>row.dataset.id!==queueDrag.id);
  if(!rows.length)return;
  const row=rows.find(row=>y<row.getBoundingClientRect().bottom)||rows[rows.length-1];
  const bounds=row.getBoundingClientRect();const after=y>bounds.top+bounds.height/2;
  row.classList.add(after?'drop-after':'drop-before');queueDrag.target=row.dataset.id;queueDrag.after=after;
}
function scrollQueueDrag(){
  if(!queueDrag)return;
  const y=queueDrag.y,edge=85;
  const step=y<edge?-Math.ceil((edge-y)/5):y>innerHeight-edge?Math.ceil((y-innerHeight+edge)/5):0;
  if(step){window.scrollBy(0,step);locateQueueDrop(y);}
  queueDrag.frame=requestAnimationFrame(scrollQueueDrag);
}
$('queue').ondragstart=event=>{
  const handle=event.target.closest('[data-drag]');
  if(!handle||state.published||state.pendingAction){event.preventDefault();return;}
  finishQueueDrag();queueDrag={id:handle.dataset.drag,y:event.clientY,frame:0};
  event.dataTransfer.effectAllowed='move';event.dataTransfer.setData('text/plain',handle.dataset.drag);
  handle.closest('.queue-row').classList.add('dragging');scrollQueueDrag();
};
$('queue').ondragover=event=>{
  if(!queueDrag)return;event.preventDefault();event.dataTransfer.dropEffect='move';
  queueDrag.y=event.clientY;locateQueueDrop(event.clientY);
};
$('queue').ondrop=event=>{
  if(!queueDrag)return;event.preventDefault();event.stopPropagation();
  const {id,target,after}=queueDrag;finishQueueDrag();if(!target)return;
  const from=state.items.findIndex(item=>item.asset_id===id);
  if(from<0||!state.items.some(item=>item.asset_id===target))return;
  const [item]=state.items.splice(from,1);
  const to=state.items.findIndex(item=>item.asset_id===target)+(after?1:0);
  state.items.splice(to,0,item);if(from!==to)dirty();renderQueue();
};
$('queue').ondragend=finishQueueDrag;
document.addEventListener('drop',finishQueueDrag);
document.addEventListener('keydown',event=>{if(event.key==='Escape')finishQueueDrag();});

$('batch-reject-all').onclick=action(async()=>{
  if(batch.busy||batch.loading)return;
  if(state.dirty||batch.edits.size)throw new Error('Сначала сохраните изменения карточек.');
  if(!batch.total){$('batch-feedback').textContent='Нет карточек для отклонения.';return;}
  if(!window.confirm(`Отклонить все ${batch.total} карточек в очереди проверки, включая другие страницы? Файлы сохранятся, опубликованные карточки не изменятся.`))return;
  batch.busy=true;batchControls();
  try{
    const result=await api('/api/review-queue/reject-all',{method:'POST',json:{expected:batch.total}});
    $('batch-feedback').textContent=`Отклонено карточек: ${result.rejected}.`;
    batch.busy=false;batch.offset=0;await loadBatch();await refresh();syncMode();renderReview();
  }finally{batch.busy=false;batchControls();}
});

$('catalog-list').addEventListener('click', action(async event=>{
  const button=event.target.closest('[data-merge-card]');if(!button)return;
  if(!mayReset())return;
  const target=Number(button.dataset.mergeCard);
  const dialog=document.createElement('dialog');
  dialog.innerHTML=`<h2>Объединить карточки сериала</h2><p>Основной код: <strong>${target}</strong>. Его название, постер и описание сохранятся. Вторая карточка исчезнет из каталога после переноса сезонов и качеств.</p><p>Старые ссылки с удаляемым кодом перестанут работать. Если у обеих карточек есть отдельные ссылки доступа, сохранится ссылка основной карточки.</p><form><label>Код переносимой карточки<input name="source" type="number" min="1" required></label><button type="submit" class="secondary">Проверить объединение</button><div data-preview role="status"></div><button type="button" data-commit class="primary" disabled>Подтвердить объединение</button><button type="button" data-close class="secondary">Отмена</button></form>`;
  document.body.append(dialog);dialog.showModal();let plan=null;
  const input=dialog.querySelector('input'),commit=dialog.querySelector('[data-commit]'),output=dialog.querySelector('[data-preview]');
  input.oninput=()=>{plan=null;commit.disabled=true;output.textContent='';};
  dialog.querySelector('[data-close]').onclick=()=>dialog.close();
  dialog.addEventListener('close',()=>dialog.remove());
  dialog.querySelector('form').onsubmit=async event=>{
    event.preventDefault();commit.disabled=true;plan=null;output.textContent='Проверяем…';
    const source=Number(input.value);input.disabled=true;
    try{plan=await api('/api/catalog-merge',{method:'POST',json:{action:'preview',source,target}});
      output.textContent=`${plan.source.title} (${source}) → ${plan.target.title} (${target}). Сезонов: ${plan.seasons}, серий: ${plan.episodes}, совпадающих серий: ${plan.merged_episodes}. `+(plan.conflicts.length?'Конфликты: '+plan.conflicts.join(' '):'Конфликтов нет. Файлы и отметки просмотра сохраняются.');commit.disabled=!plan.can_merge;
    }catch(error){output.textContent=error.message;}finally{input.disabled=false;}
  };
  commit.onclick=async()=>{
    if(!plan?.can_merge)return;
    commit.disabled=true;input.disabled=true;
    try{await api('/api/catalog-merge',{method:'POST',json:{action:'merge',source:plan.source.code,target,token:plan.token}});dialog.close();await loadCatalog();await refresh();notice(`Карточки объединены. Основной код: ${target}.`);}
    catch(error){output.textContent=error.message;plan=null;input.disabled=false;}
  };
}));

$('catalog-unrated').onchange=action(async()=>{state.catalogPage=0;await loadCatalog();});
