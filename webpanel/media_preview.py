"""Authenticated HTTP range streaming from a separately connected Telegram account."""

import asyncio
import logging
import os
import re
from pathlib import Path

from aiohttp import web

from data import settings
from database.connection import database
from services.storage_backup import storage_reference
from webpanel import repository

logger = logging.getLogger(__name__)


def byte_range(header, size):
    if not header:
        return 0, size - 1, 200
    match = re.fullmatch(r'bytes=(\d*)-(\d*)', header)
    if not match or not any(match.groups()):
        raise ValueError('Invalid range')
    left, right = match.groups()
    if not left:
        suffix = int(right)
        if suffix <= 0:
            raise ValueError('Invalid range')
        return max(0, size - suffix), size - 1, 206
    start = int(left)
    end = min(int(right), size - 1) if right else size - 1
    if start >= size or start > end:
        raise ValueError('Invalid range')
    return start, end, 206


def connection_settings():
    api_id = os.environ.get('STORAGE_PREVIEW_API_ID', os.environ.get('TELEGRAM_API_ID', ''))
    api_hash = os.environ.get('STORAGE_PREVIEW_API_HASH', os.environ.get('TELEGRAM_API_HASH', ''))
    session = Path(os.environ.get('STORAGE_PREVIEW_SESSION', settings.DATA_DIR / 'storage-preview.session'))
    return api_id, api_hash, session


class PreviewStream:
    def __init__(self):
        self.client = None
        self.lock = asyncio.Lock()
        self.slots = asyncio.Semaphore(2)

    async def connected(self):
        async with self.lock:
            if self.client and self.client.is_connected():
                return self.client
            api_id, api_hash, session = connection_settings()
            if not api_id.isdecimal() or not api_hash or not session.is_file():
                raise ValueError('Плеер ещё не подключён. Настройте Telegram-сессию просмотра на сервере.')
            from telethon import TelegramClient
            client = TelegramClient(str(session), int(api_id), api_hash, receive_updates=False)
            try:
                await client.connect()
                if not await client.is_user_authorized():
                    raise ValueError('Сессия просмотра недействительна. Подключите её заново.')
                if (await client.get_me()).bot:
                    raise ValueError('Для чтения Storage подключите аккаунт с доступом к каналу.')
                # Populate private channel access hashes once, including channels without usernames.
                await client.get_dialogs(limit=None)
            except BaseException:
                await client.disconnect()
                raise
            self.client = client
            return client

    async def close(self):
        if self.client:
            await self.client.disconnect()
            self.client = None

    async def source(self, asset_id, admin_id):
        file = repository.asset(asset_id, admin_id)
        if not file:
            raise web.HTTPNotFound(reason='Файл недоступен.')
        reference = storage_reference(file['file_id']) if file['file_id'] else None
        if not reference:
            row = database.execute('SELECT source_chat_id,source_message_id FROM WebAssets WHERE id=? AND admin_id=?',
                                   (asset_id, admin_id)).fetchone()
            if row and row[0] and row[1] and str(row[0]).startswith('-100'):
                reference = {'chat_id': row[0], 'message_id': row[1]}
        if not reference:
            raise ValueError('Для файла нет ссылки на сообщение Storage.')
        client = await self.connected()
        message = await client.get_messages(reference['chat_id'], ids=reference['message_id'])
        if not message or not message.document:
            raise ValueError('Видео не найдено в Storage либо аккаунт просмотра не имеет доступа.')
        return client, message

    async def handle(self, request):
        async with self.slots:
            client, message = await self.source(request.match_info['id'], request['admin_id'])
            size = message.document.size
            if not size:
                raise ValueError('Видео пустое.')
            try:
                start, end, status = byte_range(request.headers.get('Range'), size)
            except ValueError:
                return web.Response(status=416, headers={'Content-Range': f'bytes */{size}'})
            mime = message.document.mime_type or 'application/octet-stream'
            headers = {'Content-Type': mime, 'Content-Length': str(end-start+1),
                       'Accept-Ranges': 'bytes', 'Cache-Control': 'no-store',
                       'X-Content-Type-Options': 'nosniff'}
            if status == 206:
                headers['Content-Range'] = f'bytes {start}-{end}/{size}'
            if request.method == 'HEAD':
                return web.Response(status=status, headers=headers)
            response = web.StreamResponse(status=status, headers=headers)
            await response.prepare(request)
            stream = client.iter_download(message.media, offset=start, request_size=512*1024)
            remaining = end-start+1
            try:
                async for chunk in stream:
                    data = bytes(chunk[:remaining])
                    await response.write(data)
                    remaining -= len(data)
                    if remaining == 0:
                        break
                if remaining:
                    response.force_close()
                    logger.warning('Preview stream ended before the requested range was complete')
                await response.write_eof()
            except (ConnectionResetError, BrokenPipeError):
                response.force_close()
            except Exception:
                response.force_close()
                logger.exception('Preview stream interrupted')
            finally:
                await stream.close()
            return response
