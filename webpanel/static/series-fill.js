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
