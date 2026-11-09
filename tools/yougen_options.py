"""Inspect one film/episode, transferring no media to KinoTime."""
from tools.yougen_agent import resolve_result
from tools.yougen_bridge import open_result, message_by_id, video_record, plain_label, BridgeError
from tools.yougen_worker import wait_card
from tools.yougen_series import navigation_record, voice_menu
from tools.yougen_discovery import menu, pages, next_page, select_episode
from tools.provider_identity import valid_voice_name
from tools.provider_matching import binding_for, media_title_matches, identity_score, source_kind_year


async def inspect_options(client, source, payload, *, binding_out=None):
    query,title=await resolve_result(client,source,payload)
    results=await client.inline_query(source,query)
    ranked=[]
    for i,r in enumerate(results):
        kind,years=source_kind_year(r)
        score=identity_score(r,payload)
        if (r.title==title and score is not None and kind==payload['content_type']
                and (not payload.get('year') or str(payload['year']) in years)):
            ranked.append((score,i))
    best=max((score for score,i in ranked),default=None)
    indices=[i for score,i in ranked if score==best]
    if len(indices)!=1:
        raise BridgeError('Результат поиска изменился перед открытием.')
    index=indices[0]
    if binding_out is not None:
        binding_out.update(binding_for(results[index], query))
    payload={**payload,'provider_binding':binding_for(results[index],query)}
    last=await client.get_messages(source,limit=1)
    before=last[0].id if last else 0
    await open_result(client,source,query,index,title,expected_binding=binding_for(results[index],query))
    card=await wait_card(client,source,before,payload,allow_navigation=True)
    initial = navigation_record(card,payload)
    if not initial or not media_title_matches(initial['label'],payload):
        raise BridgeError('Полное название видео не совпадает: варианты не показаны.')
    if payload['content_type']=='series':
        if (initial['season'],initial['episode']) != (payload['season'],payload['episode']):
            await menu(client,source,card.id,'Серии')
            await pages(client,source,card.id,r'Сезон [1-9][0-9]{0,2}',select=f"Сезон {payload['season']}")
            current=await message_by_id(client,source,card.id)
            if f"Сезон {payload['season']}" not in [plain_label(b.text) for r in current.buttons or [] for b in r]:
                raise BridgeError('Сезон не подтверждён.')
            await select_episode(client, source, card.id, payload['episode'])
            card=await wait_card(client,source,before,payload,target_episode=True,allow_navigation=True)
    await voice_menu(client,source,card.id,series=payload['content_type']=='series')
    excluded={'Назад','Озвучка','Все озвучки','Озвучки серии','Вперед','Вперёд'}
    voices=[v for v in await pages(client,source,card.id,r'.+',max_pages=10)
            if valid_voice_name(v) and v not in excluded and not next_page(v) and any(c.isalnum() for c in v)]
    if not voices:
        raise BridgeError('Источник не показал доступных озвучек.')
    voice=payload.get('voice')
    if not voice:
        return voices
    if voice not in voices:
        raise BridgeError('Выбранная озвучка больше недоступна.')
    await voice_menu(client,source,card.id,series=payload['content_type']=='series')
    await pages(client,source,card.id,r'.+',select=voice,max_pages=10)
    card=await wait_card(client,source,before,payload,voice=voice,target_episode=payload['content_type']=='series')
    record=video_record(card)
    if not media_title_matches(record['label'],payload):
        raise BridgeError('Полное название видео изменилось.')
    await menu(client,source,card.id,'Качество')
    return [q for q in await pages(client,source,card.id,r'[0-9]{3,4}p',max_pages=10)
            if q in {'240p','360p','480p','720p','1080p','1440p','2160p'}
            and not (q == record['quality'] and record['size'] > 4 * 1024**3)]
