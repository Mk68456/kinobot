"""Enumerate observed menus only. No guessed callback data and no automatic transfers."""
import asyncio
import re

from tools.yougen_bridge import (BridgeError, VOICES, plain_label, message_by_id,
                                 snapshot, open_result, video_record)
from tools.yougen_worker import wait_card


def next_page(label):
    return label.strip().casefold() in {'➡️', '➡', '▶️', '▶', '>', '›', '»', 'следующая',
                                            'следующая страница', 'следующая страница ➡️', 'вперёд', 'вперед'}


async def click(client, source, message, label):
    matches = [b for row in message.buttons or [] for b in row if b.text == label and getattr(b, 'data', None)]
    if len(matches) != 1:
        raise BridgeError('Меню изменилось или вариант неоднозначен.')
    try:
        result = await asyncio.wait_for(matches[0].click(), timeout=8)
    except asyncio.TimeoutError:
        # Some provider buttons edit the menu but never answer the callback.
        # Observe the result; never repeat a click whose outcome is uncertain.
        current = await message_by_id(client, source, message.id)
        before = [(b.text, getattr(b, 'data', None)) for row in message.buttons or [] for b in row]
        after = [(b.text, getattr(b, 'data', None)) for row in current.buttons or [] for b in row]
        if before == after:
            raise BridgeError('Нет подтверждения перехода меню; повтор не выполнен.') from None
        result = None
    if result and getattr(result, 'message', None):
        raise BridgeError('Источник показал ограничение доступа.')
    await asyncio.sleep(1)


async def menu(client, source, card_id, label):
    for _ in range(4):
        card = await message_by_id(client, source, card_id)
        labels = [b.text for row in card.buttons or [] for b in row]
        wanted = [s for s in labels if plain_label(s) == label]
        if len(wanted) == 1:
            await click(client, source, card, wanted[0])
            return
        back = [s for s in labels if plain_label(s) == 'Назад']
        if len(back) != 1:
            break
        await click(client, source, card, back[0])
    raise BridgeError('Не удалось открыть меню: ' + label)


async def pages(client, source, card_id, pattern, *, select=None, max_pages=200):
    """Walk a finite observed next-page chain; repeated pages are an error, not success."""
    values, seen = {}, set()
    for _ in range(max_pages):
        card = await message_by_id(client, source, card_id)
        if snapshot(card)['access_required']:
            raise BridgeError('Источник требует доступ.')
        fingerprint = tuple((b.text, getattr(b, 'data', None)) for row in card.buttons or [] for b in row)
        if fingerprint in seen:
            raise BridgeError('Повтор страницы: обход остановлен без неполного плана.')
        seen.add(fingerprint)
        for row in card.buttons or []:
            for b in row:
                label = plain_label(b.text)
                match = re.fullmatch(pattern, label)
                if match and getattr(b, 'data', None):
                    values[label] = b.text
                    if select == label:
                        await click(client, source, card, b.text)
                        return label
        arrows = [b for row in card.buttons or [] for b in row if next_page(b.text) and getattr(b, 'data', None)]
        if not arrows:
            if select is not None:
                raise BridgeError('Вариант не найден на страницах меню: ' + select)
            return sorted(values, key=lambda label: (0, int(re.search(r'[0-9]+', label)[0]), label)
                          if re.search(r'[0-9]+', label) else (1, 0, label))
        if len(arrows) != 1:
            raise BridgeError('Неоднозначное перелистывание меню.')
        await click(client, source, card, arrows[0].text)
    raise BridgeError('Превышен предел страниц. План не отправлен.')


async def discover(client, source, payload, result_title):
    results = await client.inline_query(source, payload['query'])
    indices = [i for i, item in enumerate(results[:20]) if item.title == result_title]
    if len(indices) != 1:
        raise BridgeError('Результат поиска изменился.')
    last = await client.get_messages(source, limit=1)
    before = last[0].id if last else 0
    await open_result(client, source, payload['query'], indices[0], result_title)
    card = await wait_card(client, source, before, payload)
    positions = [(0, 0)]
    if payload['content_type'] == 'series':
        await menu(client, source, card.id, 'Серии')
        seasons = await pages(client, source, card.id, r'Сезон [1-9][0-9]{0,2}')
        if not seasons:
            raise BridgeError('Нет подтверждённого списка сезонов.')
        positions = []
        for season_label in seasons:
            await menu(client, source, card.id, 'Серии')
            await pages(client, source, card.id, r'Сезон [1-9][0-9]{0,2}', select=season_label)
            season = int(season_label.split()[-1])
            current = await message_by_id(client, source, card.id)
            if season_label not in [plain_label(b.text) for row in current.buttons or [] for b in row]:
                raise BridgeError('Сезон не подтверждён заголовком меню.')
            episodes = await pages(client, source, card.id, r'[1-9][0-9]{0,2}')
            if not episodes:
                raise BridgeError('Пустой сезон или неизвестный формат серий.')
            positions.extend((season, int(e)) for e in episodes)
    plan = []
    for season, episode in positions:
        if season:
            await menu(client, source, card.id, 'Серии')
            await pages(client, source, card.id, r'Сезон [1-9][0-9]{0,2}', select=f'Сезон {season}')
            await pages(client, source, card.id, r'[1-9][0-9]{0,2}', select=str(episode))
            card = await wait_card(client, source, before, {**payload, 'season': season, 'episode': episode}, target_episode=True)
        await menu(client, source, card.id, 'Озвучка')
        voices = await pages(client, source, card.id, r'.+')
        voices = [v for v in voices if v not in {'Назад', 'Озвучка', 'Все озвучки', 'Озвучки серии'}
                  and not next_page(v) and v.strip() not in {'⬅️', '⬅', '◀️', '◀', '<', '‹', '«'}]
        if not voices or any(v not in VOICES for v in voices):
            raise BridgeError('Новая или неизвестная озвучка: проверьте список источника.')
        for voice in voices:
            await menu(client, source, card.id, 'Озвучка')
            await pages(client, source, card.id, r'.+', select=voice)
            card = await wait_card(client, source, before, {**payload, 'season': season, 'episode': episode},
                                   voice=voice, target_episode=bool(season))
            if not video_record(card):
                raise BridgeError('Источник не подтвердил выбранную озвучку.')
            await menu(client, source, card.id, 'Качество')
            qualities = await pages(client, source, card.id, r'[0-9]{3,4}p')
            if not qualities or any(q not in {'240p', '360p', '480p', '720p', '1080p', '1440p', '2160p'} for q in qualities):
                raise BridgeError('Источник предлагает неподдерживаемое качество. Нужна проверка.')
            base = {k: v for k, v in payload.items() if k != 'metadata'}
            plan.extend({**base, 'season': season, 'episode': episode, 'voice': voice, 'quality': q} for q in qualities)
            if len(plan) > 5000:
                raise BridgeError('Более 5000 вариантов. Нужна загрузка частями.')
    return plan
