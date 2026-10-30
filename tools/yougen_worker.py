"""One queued request -> exact video -> filled KinoTime draft; never publishes."""

import argparse
import asyncio
import json
import os
from pathlib import Path
import re
import sys
import time
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import Request, HTTPRedirectHandler, build_opener

from tools.yougen_bridge import (BOT, BridgeError, choose, connection, message_by_id,
                                 open_result, plain_label, safe_button, session_path, snapshot, video_record)
from tools.yougen_queue import Queue


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise BridgeError('Перенаправление API запрещено.')


class Panel:
    def __init__(self, url, token, instance):
        parsed = urlsplit(url)
        if (parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password
                or parsed.path not in ('', '/') or parsed.query or parsed.fragment):
            raise BridgeError('Нужен основной HTTPS-адрес вашей панели без пути.')
        if not re.fullmatch(r'[A-Za-z0-9_-]{30,100}', token):
            raise BridgeError('Некорректный код /desktop.')
        if not re.fullmatch(r'[A-Za-z0-9_-]{8,100}', instance):
            raise BridgeError('Некорректный идентификатор клиента.')
        self.url, self.token, self.instance = url.rstrip('/'), token, instance
        self.opener = build_opener(NoRedirect)

    def request(self, path, body=None):
        data = json.dumps(body, ensure_ascii=False).encode() if body is not None else None
        request = Request(self.url + '/api/desktop/' + path, data=data,
                          headers={'Authorization': 'Bearer ' + self.token,
                                   'X-KinoTime-Instance': self.instance, 'Content-Type': 'application/json'})
        try:
            with self.opener.open(request, timeout=40) as response:
                raw = response.read(2 * 1024**2 + 1)
            if len(raw) > 2 * 1024**2:
                raise BridgeError('Ответ панели слишком большой.')
            return json.loads(raw)
        except HTTPError as error:
            raise BridgeError(f'API панели вернул HTTP {error.code}. Проверьте подключение /desktop.') from None


def exact_video(message, payload):
    record = video_record(message)
    if not record or getattr(message, 'noforwards', False):
        return None
    label = f"{payload['title']} ({payload['voice']} [{payload['quality']}])"
    if (record['label'].casefold() != label.casefold() or record['season'] != payload['season']
            or record['episode'] != payload['episode'] or record['quality'] != payload['quality']):
        return None
    return record


def episode_name_matches(name, episode):
    from database.categories import episode_order
    return episode_order((0, name))[:2] == (0, episode)


def manifest(payload, record):
    if payload.get('content_type') == 'movie':
        path = f"{payload['voice']} {payload['quality']}/Видео.mp4"
        return {'version': 1, 'title': payload['title'], 'content_type': 'movie',
                'catalog_section': 'movies', 'selection': path,
                'root': f"yougen/{BOT}/{record['message_id']}",
                'files': [{'path': path, 'size': record['size'], 'mtime': '', 'quality': payload['quality']}]}
    path = f"S{payload['season']:02d}E{payload['episode']:02d}_{payload['quality']}.mp4"
    return {'version': 1, 'title': payload['title'], 'content_type': 'series',
            'catalog_section': 'series', 'new_series': True, 'season_number': payload['season'],
            'root': f"yougen/{BOT}/{record['message_id']}",
            'files': [{'path': path, 'size': record['size'], 'mtime': '',
                       'name': f"Серия {payload['episode']}", 'season_number': payload['season'],
                       'voice': payload['voice'], 'quality': payload['quality']}]}


async def recent_cards(client, source, after):
    return [m for m in await client.get_messages(source, limit=15, min_id=after)
            if not m.out and m.sender_id == source.id]


async def wait_card(client, source, after, payload, timeout=30, exact=False, voice=None, target_episode=False):
    deadline = time.monotonic() + timeout
    while True:
        for message in await recent_cards(client, source, after):
            candidate = exact_video(message, payload) if exact else video_record(message)
            if candidate and voice and f" ({voice} [" not in candidate['label']:
                continue
            if candidate and target_episode and (candidate['season'], candidate['episode']) != (payload['season'], payload['episode']):
                continue
            if candidate:
                if getattr(message, 'noforwards', False):
                    raise BridgeError('Источник запретил передачу этого материала.')
                return message
            if snapshot(message)['access_required']:
                raise BridgeError('Источник требует доступ или завершение настройки. Проверьте Telegram.')
        if time.monotonic() >= deadline:
            raise BridgeError('Не получено видео с выбранными параметрами. Нужна проверка.')
        await asyncio.sleep(2)


async def select_variant(client, source, card, menu, label):
    await choose(client, source, card.id, menu)
    deadline = time.monotonic() + 20
    while True:
        current = await message_by_id(client, source, card.id)
        matches = [b for row in current.buttons or [] for b in row
                   if plain_label(b.text) == label and safe_button(b)]
        if len(matches) == 1:
            await choose(client, source, card.id, label)
            return
        if time.monotonic() >= deadline:
            raise BridgeError('Выбранный вариант отсутствует или меню изменилось.')
        await asyncio.sleep(2)


async def process(queue, job, config, panel_override=None):
    payload, job_id = job['payload'], job['id']
    panel = panel_override or Panel(config['panel_url'], config['panel_token'], config['instance_id'])
    # Verify server identity and access before making any request at the provider.
    info = await asyncio.to_thread(panel.request, 'session')
    if (info.get('bot_username', '').lower() != config['target'].lstrip('@').lower()
            or info.get('admin_id') != config['user_id']):
        raise BridgeError('API панели не соответствует выбранному боту или администратору.')
    attach = config.get('link_existing_catalog', False)
    if attach and info.get('capabilities', {}).get('provider_import') != 1:
        raise BridgeError('Для привязки к каталогу сначала установите серверное обновление provider_import.')
    async with connection(session_path(config['session']), config.get('api_id'), config.get('api_hash'),
                          config.get('profile', 'desktop-copy'), config['user_id']) as (client, source):
        target = await client.get_entity(config['target'])
        if not target.bot or target.id == source.id or target.username.lower() != config['target'].lstrip('@').lower():
            raise BridgeError('Не подтверждён бот-получатель.')
        last = await client.get_messages(source, limit=1)
        before = last[0].id if last else 0
        queue.update(job_id, 'searching')
        results = await client.inline_query(source, payload['query'])
        indices = [i for i, row in enumerate(results[:20]) if row.title == payload['result_title']]
        if len(indices) != 1:
            raise BridgeError('Не найден единственный результат с выбранным названием.')
        queue.update(job_id, 'opening')
        await open_result(client, source, payload['query'], indices[0], payload['result_title'])
        card = await wait_card(client, source, before, payload)
        record = video_record(card)
        if record['season'] != payload['season'] or record['episode'] != payload['episode']:
            queue.update(job_id, 'selecting_episode')
            from tools.yougen_discovery import menu, pages
            await menu(client, source, card.id, 'Серии')
            await pages(client, source, card.id, r'Сезон [1-9][0-9]{0,2}', select=f"Сезон {payload['season']}")
            current = await message_by_id(client, source, card.id)
            if f"Сезон {payload['season']}" not in [plain_label(b.text) for row in current.buttons or [] for b in row]:
                raise BridgeError('Не подтверждён сезон в меню серий.')
            await pages(client, source, card.id, r'[1-9][0-9]{0,2}', select=str(payload['episode']))
            card = await wait_card(client, source, before, payload, target_episode=True)
            record = video_record(card)
        if not exact_video(card, payload):
            # Known navigation only, then require the complete exact caption again.
            current = re.fullmatch(r'.+ \((.+) \[\d{3,4}p\]\)', record['label'])
            if not current or current[1] != payload['voice']:
                queue.update(job_id, 'selecting_voice')
                await select_variant(client, source, card, 'Озвучка', payload['voice'])
                card = await wait_card(client, source, before, payload, voice=payload['voice'], target_episode=True)
            if video_record(card)['quality'] != payload['quality']:
                queue.update(job_id, 'selecting_quality')
                await select_variant(client, source, card, 'Качество', payload['quality'])
            card = await wait_card(client, source, before, payload, exact=True)
        record = exact_video(card, payload)
        queue.update(job_id, 'video_verified', details={'video': record})
        queue.update(job_id, 'registering_draft')
        registered = await asyncio.to_thread(panel.request, 'provider-import' if attach else 'import',
                                              manifest(payload, record))
        draft = registered['draft']
        items = draft['body']['items']
        if (registered.get('bot_username', '').lower() != target.username.lower()
                or draft['status'] not in ('draft', 'published') or draft['body']['content_type'] != payload.get('content_type', 'series')
                or re.sub(r'\s*\((?:19|20)\d{2}\)$', '', ' '.join(draft['body']['title'].split())).casefold()
                != ' '.join(payload['title'].split()).casefold()
                or len(items) != 1 or not re.fullmatch(r'[a-f0-9]{32}', items[0]['asset_id'])
                or items[0].get('quality') != payload['quality']
                or payload.get('content_type', 'series') == 'series' and (
                    items[0].get('voice') != payload['voice'] or items[0].get('season_number') != payload['season']
                    or not episode_name_matches(items[0].get('name'), payload['episode']))):
            raise BridgeError('Панель вернула неожиданный черновик. Ничего не отправлено.')
        draft_id, asset_id = draft['id'], items[0]['asset_id']
        queue.update(job_id, 'draft_registered', details={'draft_id': draft_id, 'asset_id': asset_id,
                                                        'linked_existing': registered.get('linked_existing', False)})
        status = await asyncio.to_thread(panel.request, f'import/{draft_id}')
        ready = any(f['id'] == asset_id and f['status'] == 'ready' for f in status['files'])
        if not ready:
            # Persist before the network side effect. An uncertain send is never automatically replayed.
            queue.update(job_id, 'sending_to_kinotime')
            sent = await client.send_file(target, card.media, caption='KTFILE:' + asset_id,
                                          supports_streaming=True)
            queue.update(job_id, 'waiting_receipt', details={'target_message_id': sent.id})
            deadline = time.monotonic() + 40
            while True:
                status = await asyncio.to_thread(panel.request, f'import/{draft_id}')
                if any(f['id'] == asset_id and f['status'] == 'ready' for f in status['files']):
                    break
                if time.monotonic() >= deadline:
                    raise BridgeError('Нет подтверждения привязки файла. Проверьте черновик, не повторяйте отправку.')
                await asyncio.sleep(3)
        queue.update(job_id, 'draft_ready', status='complete', details={'published': False})
    return queue.get(job_id)


def parser():
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument('--queue', type=Path, default=Path('runtime/yougen-client/jobs.sqlite'))
    sub = result.add_subparsers(dest='command', required=True)
    add = sub.add_parser('enqueue')
    for name in ['query', 'result-title', 'title', 'voice', 'quality']:
        add.add_argument('--' + name, required=True)
    add.add_argument('--season', type=int, default=1)
    add.add_argument('--episode', type=int, default=1)
    sub.add_parser('work-once')
    status = sub.add_parser('status')
    status.add_argument('id')
    return result


def main():
    args = parser().parse_args()
    queue = Queue(args.queue)
    if args.command == 'enqueue':
        payload = {n: getattr(args, n) for n in ['query', 'result_title', 'title', 'voice', 'quality', 'season', 'episode']}
        print(json.dumps({'job_id': queue.enqueue(payload)}))
        return
    if args.command == 'status':
        print(json.dumps(queue.get(args.id), ensure_ascii=False, indent=2))
        return
    config_path = Path(os.environ.get('YOUGEN_CONFIG', 'runtime/yougen-client/bridge.json'))
    config = json.loads(config_path.read_text(encoding='utf-8'))
    job = queue.claim()
    if not job:
        print('Нет свободного задания; другой обработчик может уже работать.')
        return
    try:
        result = asyncio.run(asyncio.wait_for(process(queue, job, config), timeout=240))
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except Exception as error:
        queue.update(job['id'], 'stopped', status='needs_review', error=type(error).__name__,
                     details={'stopped_at': queue.get(job['id'])['phase']})
        # Never print raw network errors, private config or session credentials.
        print('Задание остановлено: ' + type(error).__name__ + '. Проверьте журнал и Telegram.', file=sys.stderr)
        raise SystemExit(1)


if __name__ == '__main__':
    main()
