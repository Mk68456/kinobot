'use strict';
const contestState = {id:null,item:null,offset:0,dirty:false,busy:false};
const contestLabels = {draft:'Черновик',active:'Приём приглашений',closed:'Участники зафиксированы',finished:'Розыгрыш завершён',cancelled:'Конкурс отменён'};
const contestFields = ['title','gift-name','gift-url','photo-url','description','ends','cap','channel','organizer'];
function contestFeedback(message,error=false){const el=$('contest-feedback');el.hidden=false;el.textContent=message;el.classList.toggle('error',error);}
function contestLocalDate(timestamp){const date=new Date(timestamp);return new Date(date.getTime()-date.getTimezoneOffset()*60000).toISOString().slice(0,16);}
function contestDate(timestamp){return timestamp?new Date(timestamp*1000).toLocaleString('ru-RU'):'';}
function contestBody(){return {title:$('contest-title').value,gift_name:$('contest-gift-name').value,gift_url:$('contest-gift-url').value,photo_url:$('contest-photo-url').value,description:$('contest-description').value,ends_at:new Date($('contest-ends').value).toISOString(),ticket_cap:Number($('contest-cap').value),channel_url:$('contest-channel').value,organizer_url:$('contest-organizer').value};}
function contestFill(body){for(const [key,id] of [['title','title'],['gift_name','gift-name'],['gift_url','gift-url'],['photo_url','photo-url'],['description','description'],['ticket_cap','cap'],['channel_url','channel'],['organizer_url','organizer']])$('contest-'+id).value=body?.[key]??(id==='cap'?20:id==='channel'?'https://t.me/GramSoprano':'');$('contest-ends').value=contestLocalDate(body?body.ends_at*1000:Date.now()+7*86400000);contestState.dirty=false;}
function contestRender(){
  const item=contestState.item, status=item?.status;
  $('contest-fields').disabled=contestState.busy||Boolean(item&&status!=='draft');
  $('contest-save').hidden=Boolean(item&&status!=='draft');
  $('contest-save').disabled=contestState.busy;
  $('contest-select').disabled=contestState.busy;
  $('contest-new').disabled=contestState.busy;
  $('contest-status').textContent=item?(status==='active'&&!item.accepting?'Срок истёк · зафиксируйте участников':contestLabels[status]):'Новый конкурс';
  $('contest-deadline').textContent=item?`Окончание: ${contestDate(item.body.ends_at)} · лимит ${item.body.ticket_cap} билетов`:'';
  const stats=item?.stats;
  $('contest-counts').innerHTML=stats?[['Всего зарегистрировано',stats.members],['Участники с билетами',stats.eligible],['Билеты',stats.tickets],['Подтверждено друзей',stats.confirmed],['Ожидают карточки',stats.pending],['Исключено',stats.excluded],['Пришли из канала',stats.channel_visits]].map(([name,value])=>`<div><strong>${value}</strong><span>${name}</span></div>`).join(''):'';
  for(const [name,visible] of [['launch',status==='draft'],['close',status==='active'],['draw',status==='closed'&&stats?.tickets>0],['cancel',Boolean(item)&&status!=='finished'&&status!=='cancelled'],['delivered',status==='finished'&&!item.delivered_at]]){const button=$('contest-'+name);button.hidden=!visible;button.disabled=contestState.busy;}
  const winner=item?.draw_result;
  $('contest-winner').textContent=winner?`Победитель: участник №${winner.winner_member_id}; Telegram ID ${winner.winner_user_id}${winner.winner_username?' (@'+winner.winner_username+')':''}. Билет №${winner.ticket} из ${winner.total_tickets}. ${item.claim?'Победитель запросил получение приза.':'Запрос на получение пока не поступил.'}${item.delivered_at?' Приз отмечен как переданный: '+item.delivery_note:''}`:status==='closed'&&!stats.tickets?'Подтверждённых приглашений нет. Победителя выбрать нельзя.':'';
  $('contest-post').value=item?.announcement||'';
  $('contest-link').textContent=item?.campaign_link||'';
  $('contest-copy-post').disabled=!item||contestState.busy;
  $('contest-copy-result').hidden=!winner;
  $('contest-copy-result').disabled=contestState.busy;
  $('contest-lists').hidden=!item;
  $('contest-export').href=item?`/api/contests/${item.id}/export`:'#';
}
async function loadContests(){
  const history=await api('/api/contests');
  $('contest-select').innerHTML='<option value="">Новый конкурс</option>'+history.items.map(item=>`<option value="${item.id}">${esc(item.title)} · ${esc(contestLabels[item.status])}</option>`).join('');
  if(contestState.id===null&&!contestState.dirty&&history.items.length)contestState.id=history.items[0].id;
  $('contest-select').value=contestState.id||'';
  contestState.item=contestState.id?await api(`/api/contests/${contestState.id}`):null;
  if(!contestState.dirty)contestFill(contestState.item?.body);
  contestRender();
  if(contestState.item)await loadContestList();
}
async function loadContestList(){
  if(!contestState.id)return;
  const kind=$('contest-list-kind').value;
  const data=await api(`/api/contests/${contestState.id}/page?kind=${kind}&offset=${contestState.offset}`);
  if(!data.items.length&&contestState.offset){contestState.offset=Math.max(0,contestState.offset-50);return loadContestList();}
  const heads=kind==='members'?['Участник','Telegram ID','Username','Друзья','Билеты']:kind==='referrals'?['Новый пользователь','Пригласивший','Состояние','Причина / проверка']:['Когда','Администратор','Действие','Данные'];
  const rows=data.items.map(row=>{
    if(kind==='members')return [row.member_id,row.user_id,row.username||'—',row.referrals,row.tickets].map(value=>`<td>${esc(value)}</td>`).join('');
    if(kind==='referrals')return `<td>${row.user_id}${row.username?'<br>@'+esc(row.username):''}</td><td>${row.inviter_id}</td><td>${esc({pending:'Ожидает карточки',confirmed:'Подтверждено',excluded:'Исключено'}[row.status])}</td><td>${esc(row.reason)}${contestState.item.status==='active'?`<button class="secondary" data-contest-review="${row.user_id}" data-exclude="${row.status!=='excluded'}">${row.status==='excluded'?'Вернуть':'Исключить'}</button>`:''}</td>`;
    return [contestDate(row.created_at),row.admin_id,row.action,row.detail].map(value=>`<td>${esc(value)}</td>`).join('');
  });
  $('contest-list').innerHTML=rows.length?`<table class="contest-table"><thead><tr>${heads.map(value=>`<th>${value}</th>`).join('')}</tr></thead><tbody>${rows.map(row=>'<tr>'+row+'</tr>').join('')}</tbody></table>`:'<p class="muted">Список пока пуст.</p>';
  $('contest-page').textContent=`${data.total?contestState.offset+1:0}–${Math.min(contestState.offset+50,data.total)} из ${data.total}`;
  $('contest-prev').disabled=contestState.offset===0||contestState.busy;
  $('contest-next').disabled=contestState.offset+50>=data.total||contestState.busy;
}
function contestAction(fn){return async event=>{if(contestState.busy)return;contestState.busy=true;contestRender();try{await fn(event);}catch(error){contestFeedback(error.message,true);}finally{contestState.busy=false;contestRender();}};}
async function contestMutation(action,extra={}){await api(`/api/contests/${contestState.id}`,{method:'POST',json:{action,revision:contestState.item.revision,...extra}});await loadContests();}
for(const id of contestFields)$('contest-'+id).addEventListener('input',()=>{contestState.dirty=true;});
$('contest-form').addEventListener('submit',async event=>{
  event.preventDefault();
  if(!$('contest-form').reportValidity())return;
  await contestAction(async()=>{const body=contestBody();const item=contestState.id?await api(`/api/contests/${contestState.id}`,{method:'POST',json:{action:'save',body,revision:contestState.item.revision}}):await api('/api/contests',{method:'POST',json:{body}});contestState.id=item.id;contestState.dirty=false;await loadContests();contestFeedback('Черновик сохранён. Конкурс ещё не запущен.');})(event);
});
$('contest-new').onclick=()=>{if(contestState.dirty&&!confirm('Оставить несохранённые условия?'))return;contestState.id=null;contestState.item=null;contestFill();$('contest-select').value='';contestState.dirty=true;contestState.offset=0;contestRender();};
$('contest-select').onchange=contestAction(async()=>{if(contestState.dirty&&!confirm('Оставить несохранённые условия?')){$('contest-select').value=contestState.id||'';return;}contestState.id=Number($('contest-select').value)||null;contestState.dirty=false;contestState.offset=0;if(!contestState.id){contestFill();contestState.dirty=true;}await loadContests();});
$('contest-refresh').onclick=contestAction(loadContests);
$('contest-launch').onclick=contestAction(async()=>{if(contestState.dirty)throw new Error('Сначала сохраните изменения.');if(!confirm('Запустить конкурс? Условия больше нельзя будет изменить. Убедитесь, что подарок можно передать к дате итогов.'))return;await contestMutation('launch');contestFeedback('Конкурс запущен. Кнопка и личные ссылки доступны в боте. Анонс можно скопировать ниже.');});
$('contest-close').onclick=contestAction(async()=>{const stats=contestState.item.stats;if(!confirm(`Зафиксировать ${stats.eligible} участников с ${stats.tickets} билетами? Новые приглашения перестанут засчитываться; изменить список после этого нельзя.`))return;await contestMutation('close',{token:contestState.item.token});contestFeedback('Список зафиксирован. Проверьте CSV и выбирайте победителя.');});
$('contest-draw').onclick=contestAction(async()=>{if(!confirm('Выбрать одного победителя по зафиксированным билетам? Результат сохранится, повторного розыгрыша не будет.'))return;await contestMutation('draw');contestFeedback('Победитель выбран. Итоги доступны участникам в боте и готовы для копирования в канал.');});
$('contest-cancel').onclick=contestAction(async()=>{if(!confirm('Отменить этот конкурс? Приглашения и журнал останутся в истории.'))return;await contestMutation('cancel');contestFeedback('Конкурс отменён.');});
$('contest-delivered').onclick=contestAction(async()=>{const note=prompt('Подтвердите фактическую передачу подарка: укажите дату или короткий комментарий.');if(!note?.trim())return;await contestMutation('delivered',{note});contestFeedback('Передача приза отмечена.');});
$('contest-list').onclick=contestAction(async event=>{const button=event.target.closest('[data-contest-review]');if(!button)return;const reason=prompt('Причина исключения или возврата приглашения:');if(!reason?.trim())return;await contestMutation('review_referral',{user_id:Number(button.dataset.contestReview),exclude:button.dataset.exclude==='true',reason});contestFeedback('Приглашение проверено. Действие сохранено в журнале.');});
$('contest-list-kind').onchange=contestAction(async()=>{contestState.offset=0;await loadContestList();});
$('contest-prev').onclick=contestAction(async()=>{contestState.offset=Math.max(0,contestState.offset-50);await loadContestList();});
$('contest-next').onclick=contestAction(async()=>{contestState.offset+=50;await loadContestList();});
async function copyContestText(value){try{await navigator.clipboard.writeText(value);contestFeedback('Текст скопирован.');}catch{$('contest-post').value=value;$('contest-post').focus();$('contest-post').select();contestFeedback('Выделен текст. Нажмите Ctrl+C для копирования.');}}
$('contest-copy-post').onclick=()=>copyContestText(contestState.item.announcement);
$('contest-copy-result').onclick=()=>copyContestText(contestState.item.result_text);
