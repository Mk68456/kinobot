"""Isolated user-client experiment. Never logs in or reads Telegram Desktop credentials.

Run with Python 3.11: python -m tools.yougen_bridge --help
An explicitly supplied, already authorized Telethon session is required.
"""

import argparse
import asyncio
from contextlib import asynccontextmanager
import json
import os
from pathlib import Path
import re
import sys

BOT = 'Kinobooprivate_bot'
VOICES = {'LostFilm', 'AlexFilm', 'HDrezka Studio', 'TVShows', 'NewStudio', 'Jaskier',
          'Кубик в Кубе | Kubik³', '[EN] Original', '[UA] HDrezka Studio'}
MENUS = {'Озвучка', 'Качество', 'Серии', 'Назад', 'Озвучки серии', 'Все озвучки'}


class BridgeError(Exception):
    pass


def plain_label(text):
    label = (text or '').replace('🔟', '10').replace('\ufe0f', '').replace('\u20e3', '')
    label = re.sub(r'^[^\w\[]+', '', label).strip()
    numbered = re.fullmatch(r'([1-9][0-9]{0,2})\s+(Сезон|Серия)', label, re.I)
    return f'{numbered[2].capitalize()} {numbered[1]}' if numbered else label


def safe_button(button):
    """Only observed callback navigation; never URLs, payments or arbitrary labels."""
    label = plain_label(getattr(button, 'text', ''))
    return bool(getattr(button, 'data', None)) and (
        label in VOICES | MENUS
        or re.fullmatch(r'(?:240|360|480|720|1080)p', label) is not None
        or re.fullmatch(r'(?:Сезон|Серия) [1-9][0-9]{0,2}', label) is not None)


def video_record(message):
    document = getattr(message, 'document', None)
    if not document or not getattr(message, 'video', None):
        return None
    caption = getattr(message, 'raw_text', '') or ''
    # Do not mistake the provider's access placeholder or promotional video for a film.
    if re.search(r'смотреть рекламу|открыть видео|VIP.?доступ|бесплатный VIP', caption, re.I):
        return None
    if document.size < 10_000_000:
        return None
    match = re.search(r'(\d+) сезон\s+(\d+) серия', caption, re.I)
    if not match:
        return None
    quality = re.search(r'\[(\d{3,4}p)\]', caption)
    title = caption.splitlines()[0].strip()
    # Keep URLs, referral identifiers and Telegram media credentials out of reports.
    if 'http' in title.lower() or '@' in title:
        return None
    return {'message_id': message.id, 'label': title[:240], 'size': document.size,
            'season': int(match[1]), 'episode': int(match[2]),
            'quality': quality[1] if quality else None}


def snapshot(message):
    caption = getattr(message, 'raw_text', '') or ''
    buttons = []
    for row_index, row in enumerate(getattr(message, 'buttons', None) or []):
        for column, button in enumerate(row):
            buttons.append({'row': row_index, 'column': column,
                            'label': getattr(button, 'text', '')[:120],
                            'allowed_navigation': safe_button(button)})
    gated = bool(re.search(r'смотреть рекламу|открыть видео|бот не настроен|закончите настройку',
                          caption, re.I))
    return {'message_id': message.id, 'video': video_record(message),
            'access_required': gated, 'buttons': buttons}


def session_path(value):
    path = Path(value).expanduser().resolve()
    if path.suffix != '.session' or not path.is_file():
        raise BridgeError('Укажите существующий файл .session отдельного клиента Telethon. '
                          'Сессия Telegram Desktop не подходит. Автоматический вход отключён.')
    return path


@asynccontextmanager
async def connection(path, api_id, api_hash, profile='independent', expected_user=None):
    from telethon import TelegramClient
    from telethon.errors import FloodWaitError
    options = {'flood_sleep_threshold': 0, 'request_retries': 1,
               'connection_retries': 1, 'receive_updates': False}
    if profile == 'desktop-copy':
        # Retain the API identity used for this authorized Desktop session.
        # Never substitute unrelated API credentials from the bot's deployment.
        from opentele2.tl import TelegramClient as DesktopClient
        client = DesktopClient(str(path), auto_post_login=False, **options)
    elif profile == 'independent':
        client = TelegramClient(str(path), api_id, api_hash, **options)
    else:
        raise BridgeError('Неизвестный профиль сессии.')
    try:
        await client.connect()
        if not await client.is_user_authorized():
            raise BridgeError('Сессия не авторизована. Вход должен выполнить владелец аккаунта отдельно.')
        me = await client.get_me()
        if not me or me.bot:
            raise BridgeError('Нужна пользовательская сессия, а не токен Bot API.')
        if expected_user is not None and me.id != expected_user:
            raise BridgeError('Сессия принадлежит другому аккаунту. Запросы к источнику не отправлены.')
        entity = await client.get_entity('@' + BOT)
        if not entity.bot or (entity.username or '').lower() != BOT.lower():
            raise BridgeError('Не удалось подтвердить целевого кинобота.')
        yield client, entity
    except FloodWaitError as error:
        raise BridgeError(f'Telegram ограничил запросы. Повторять не раньше чем через {error.seconds} секунд.') from None
    finally:
        await client.disconnect()


async def message_by_id(client, entity, number):
    message = await client.get_messages(entity, ids=number)
    if message is None or message.out or message.sender_id != entity.id:
        raise BridgeError('Нужно входящее сообщение именно выбранного кинобота.')
    return message


async def search(client, entity, query):
    results = await client.inline_query(entity, query)
    return [{'index': index, 'title': item.title, 'description': item.description}
            for index, item in enumerate(results[:20])]


async def open_result(client, entity, query, index, expected_title):
    results = await client.inline_query(entity, query)
    if index < 0 or index >= min(len(results), 20):
        raise BridgeError('Номер результата отсутствует. Повторите поиск.')
    item = results[index]
    if item.title != expected_title:
        raise BridgeError('Порядок поиска изменился: название не совпало. Ничего не отправлено.')
    await item.click(entity)
    return {'opened': expected_title, 'next': 'inspect'}


async def choose(client, entity, message_id, label):
    message = await message_by_id(client, entity, message_id)
    matches = [(r, c, button) for r, row in enumerate(message.buttons or [])
               for c, button in enumerate(row) if plain_label(button.text) == label]
    if len(matches) != 1 or not safe_button(matches[0][2]):
        raise BridgeError('Кнопка неоднозначна или не входит в проверенную навигацию. Действие остановлено.')
    answer = await message.click(matches[0][0], matches[0][1])
    # Callback popups may be the only indication of access restrictions.
    if answer and getattr(answer, 'message', None):
        return {'status': 'provider_notice', 'action': 'check_in_telegram'}
    return {'status': 'navigation_sent', 'next': 'inspect'}


def episode_position(message, season, episode):
    """Numeric callbacks are allowed only in the observed, season-labelled episode grid."""
    rows = message.buttons or []
    headers = [b for row in rows for b in row if plain_label(b.text) == f'Сезон {season}']
    if len(headers) != 1:
        raise BridgeError('Не подтверждён сезон в меню серий.')
    matches = []
    for r, row in enumerate(rows):
        for c, button in enumerate(row):
            label = button.text.strip().strip('›‹')
            if re.fullmatch(r'[1-9][0-9]{0,2}', label):
                if int(label) == episode and getattr(button, 'data', None):
                    matches.append((r, c))
            elif plain_label(button.text) not in {f'Сезон {season}', 'Назад', ''}:
                raise BridgeError('Неизвестный элемент меню серий. Выбор остановлен.')
    if len(matches) != 1:
        raise BridgeError('Нужная серия отсутствует на текущей странице меню.')
    return matches[0]


async def choose_episode(client, entity, message_id, season, episode):
    message = await message_by_id(client, entity, message_id)
    row, column = episode_position(message, season, episode)
    answer = await message.click(row, column)
    if answer and getattr(answer, 'message', None):
        raise BridgeError('Источник показал уведомление при выборе серии. Проверьте Telegram.')


async def wait_video(client, entity, after, seconds):
    deadline = asyncio.get_running_loop().time() + seconds
    while True:
        messages = await client.get_messages(entity, limit=20, min_id=after)
        for message in sorted(messages, key=lambda item: item.id):
            if message.out or message.sender_id != entity.id:
                continue
            record = video_record(message)
            if record:
                return {'status': 'video_candidate', 'video': record,
                        'note': 'Проверьте соответствие запросу перед импортом; автоматически не пересылается.'}
            if snapshot(message)['access_required']:
                return {'status': 'access_required', 'message_id': message.id}
        if asyncio.get_running_loop().time() >= deadline:
            return {'status': 'timeout', 'automatic_retry': False}
        await asyncio.sleep(3)


async def execute(args):
    path = session_path(os.environ.get('YOUGEN_SESSION', ''))
    profile = os.environ.get('YOUGEN_PROFILE', 'independent')
    try:
        expected_user = int(os.environ['YOUGEN_USER_ID']) if os.environ.get('YOUGEN_USER_ID') else None
        api_id, api_hash = None, None
        if profile == 'independent':
            api_id = int(os.environ.get('TELEGRAM_API_ID', ''))
            api_hash = os.environ.get('TELEGRAM_API_HASH', '')
            if api_id <= 0 or not re.fullmatch(r'[0-9a-fA-F]{32}', api_hash):
                raise ValueError()
        elif profile != 'desktop-copy' or expected_user is None:
            raise ValueError()
    except ValueError:
        raise BridgeError('Проверьте профиль, YOUGEN_USER_ID и параметры API отдельной сессии.') from None
    async with connection(path, api_id, api_hash, profile, expected_user) as (client, entity):
        if args.command == 'search':
            return await search(client, entity, args.query)
        if args.command == 'open':
            return await open_result(client, entity, args.query, args.index, args.title)
        if args.command == 'inspect':
            return [snapshot(message) for message in await client.get_messages(entity, limit=10)
                    if not message.out and message.sender_id == entity.id]
        if args.command == 'choose':
            return await choose(client, entity, args.message_id, args.label)
        return await wait_video(client, entity, args.after, args.seconds)


def parser():
    result = argparse.ArgumentParser(description=__doc__)
    commands = result.add_subparsers(dest='command', required=True)
    find = commands.add_parser('search', help='Поиск без отправки результата в чат')
    find.add_argument('query')
    launch = commands.add_parser('open', help='Открыть точно выбранный результат в чате кинобота')
    launch.add_argument('query')
    launch.add_argument('--index', type=int, required=True)
    launch.add_argument('--title', required=True)
    commands.add_parser('inspect', help='Показать кнопки последних входящих сообщений')
    click = commands.add_parser('choose', help='Нажать разрешённую кнопку озвучки/качества/серии')
    click.add_argument('--message-id', type=int, required=True)
    click.add_argument('--label', required=True)
    watch = commands.add_parser('wait-video', help='Ждать готовое видео, без скачивания и пересылки')
    watch.add_argument('--after', type=int, required=True)
    watch.add_argument('--seconds', type=int, default=30, choices=range(1, 61), metavar='1..60')
    return result


def main():
    args = parser().parse_args()
    try:
        result = asyncio.run(execute(args))
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except BridgeError as error:
        print(str(error), file=sys.stderr)
    except Exception as error:
        # Raw RPC errors may contain credentials or peer details.
        print(f'Клиент остановлен ({type(error).__name__}). Проверьте соединение в Telegram.', file=sys.stderr)
    return 1


if __name__ == '__main__':
    raise SystemExit(main())
