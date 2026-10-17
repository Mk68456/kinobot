"""Keep reusable Telegram copies of catalog files in an admin-owned private channel."""

import asyncio
import logging

from aiogram.utils.exceptions import RetryAfter

from database.connection import database

logger = logging.getLogger(__name__)


def configuration():
    row = database.execute(
        'SELECT admin_id,chat_id,title,enabled,updated_at FROM TelegramStorage ORDER BY updated_at DESC LIMIT 1'
    ).fetchone()
    if not row:
        return None
    return {'admin_id': row[0], 'chat_id': row[1], 'title': row[2],
            'enabled': bool(row[3]), 'updated_at': row[4]}


def configure(admin_id, chat_id, title):
    with database.transaction():
        database.execute('DELETE FROM TelegramStorage')
        database.execute('''INSERT INTO TelegramStorage(admin_id,chat_id,title,enabled)
            VALUES (?,?,?,1)''', (admin_id, chat_id, str(title)[:200]))
        database.execute("UPDATE TelegramStorageFiles SET status='pending',error=NULL")
    backup.notify()
    return configuration()


def set_enabled(enabled):
    current = configuration()
    if not current:
        raise ValueError('Сначала подключите закрытый Storage-канал через Telegram.')
    with database.transaction():
        database.execute('UPDATE TelegramStorage SET enabled=?,updated_at=CURRENT_TIMESTAMP',
                         (1 if enabled else 0,))
    backup.notify()


def _register(file_id, file_type):
    if not file_id:
        return
    kind = 'video' if file_type == 'video' else 'document'
    database.execute('''INSERT INTO TelegramStorageFiles(file_id,file_type)
        VALUES (?,?) ON CONFLICT(file_id) DO UPDATE SET file_type=excluded.file_type''',
                     (file_id, kind))


def discover():
    """Register every downloadable catalog file and every ready imported asset."""
    before = database.execute('SELECT count(*) FROM TelegramStorageFiles').fetchone()[0]
    with database.transaction():
        for file_id, file_type in database.execute(
                "SELECT file_id,file_type FROM MovieSubcategories WHERE file_id IS NOT NULL AND file_id!=''"):
            _register(file_id, file_type)
        for file_id, file_type in database.execute(
                "SELECT movie_file,movie_file_type FROM Movies WHERE movie_file IS NOT NULL AND movie_file!=''"):
            _register(file_id, file_type)
        for (file_id,) in database.execute(
                "SELECT file_id FROM MovieTorrents WHERE file_id IS NOT NULL AND file_id!=''"):
            _register(file_id, 'document')
        for file_id, file_type in database.execute(
                "SELECT file_id,file_type FROM WebAssets WHERE status='ready' AND file_id IS NOT NULL AND file_id!=''"):
            _register(file_id, file_type)
    after = database.execute('SELECT count(*) FROM TelegramStorageFiles').fetchone()[0]
    backup.notify()
    return after - before


def status():
    discover()
    counts = {row[0]: row[1] for row in database.execute(
        'SELECT status,count(*) FROM TelegramStorageFiles GROUP BY status').fetchall()}
    recent = [{'file_id': row[0][-12:], 'file_type': row[1], 'status': row[2],
               'attempts': row[3], 'error': row[4], 'updated_at': row[5]}
              for row in database.execute('''SELECT file_id,file_type,status,attempts,error,updated_at
                  FROM TelegramStorageFiles ORDER BY updated_at DESC LIMIT 20''').fetchall()]
    return {'configuration': configuration(), 'counts': {
        'total': sum(counts.values()), 'ready': counts.get('ready', 0),
        'pending': counts.get('pending', 0), 'error': counts.get('error', 0)},
        'recent': recent}


def retry_errors():
    with database.transaction():
        database.execute("UPDATE TelegramStorageFiles SET status='pending',error=NULL WHERE status='error'")
    backup.notify()


def storage_reference(file_id):
    row = database.execute('''SELECT storage_chat_id,storage_message_id FROM TelegramStorageFiles
        WHERE file_id=? AND status='ready' ''', (file_id,)).fetchone()
    return {'chat_id': row[0], 'message_id': row[1]} if row else None


async def send_catalog_file(bot, chat_id, file_id, file_type, caption=None, reply_markup=None):
    """Use the bot-independent Storage message when available, then fall back to file_id."""
    # Catalog labels are stored in full; only the outgoing media caption has
    # Telegram's 1024-character limit. Keep surrogate pairs intact when clipping.
    if caption is not None:
        encoded = caption.encode('utf-16-le')
        if len(encoded) > 2048:
            caption = encoded[:2046].decode('utf-16-le', errors='ignore') + '…'
    reference = storage_reference(file_id)
    if reference:
        try:
            return await bot.copy_message(
                chat_id, reference['chat_id'], reference['message_id'], caption=caption,
                parse_mode='', reply_markup=reply_markup)
        except Exception:
            logger.warning('Storage copy failed; falling back to file_id %s', file_id[-12:], exc_info=True)
            if file_id.startswith('storage:'):
                raise ValueError('Не удалось скопировать видео из Storage. Проверьте доступ бота к каналу и наличие сообщения.')
    if file_id.startswith('storage:'):
        raise ValueError('Ссылка на сообщение Storage отсутствует в базе резервирования.')
    if file_type == 'video':
        return await bot.send_video(chat_id, video=file_id, caption=caption,
                                    parse_mode='', reply_markup=reply_markup)
    return await bot.send_document(chat_id, document=file_id, caption=caption,
                                   parse_mode='', reply_markup=reply_markup)


class StorageBackup:
    def __init__(self):
        self.bot = None
        self.task = None
        self.wakeup = asyncio.Event()

    def start(self, bot):
        self.bot = bot
        if not self.task:
            self.task = asyncio.create_task(self._run())

    async def stop(self):
        if self.task:
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                pass
        self.task = None
        self.bot = None

    def notify(self):
        if self.task:
            self.wakeup.set()

    async def _run(self):
        while True:
            try:
                await self._drain()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception('Telegram Storage worker failed')
            self.wakeup.clear()
            try:
                await asyncio.wait_for(self.wakeup.wait(), timeout=30)
            except asyncio.TimeoutError:
                pass

    async def _drain(self):
        current = configuration()
        if not current or not current['enabled']:
            return
        discover()
        while True:
            row = database.execute('''SELECT file_id,file_type FROM TelegramStorageFiles
                WHERE status='pending' ORDER BY updated_at,file_id LIMIT 1''').fetchone()
            if not row:
                return
            file_id, file_type = row
            try:
                caption = 'KinoTime Storage · резервная копия'
                if file_type == 'video':
                    message = await self.bot.send_video(current['chat_id'], file_id, caption=caption)
                else:
                    message = await self.bot.send_document(current['chat_id'], file_id, caption=caption)
                with database.transaction():
                    database.execute('''UPDATE TelegramStorageFiles SET status='ready',attempts=attempts+1,
                        storage_chat_id=?,storage_message_id=?,error=NULL,updated_at=CURRENT_TIMESTAMP
                        WHERE file_id=?''', (current['chat_id'], message.message_id, file_id))
            except RetryAfter as error:
                await asyncio.sleep(min(int(error.timeout) + 1, 60))
            except Exception as error:
                logger.warning('Unable to back up Telegram file %s', file_id[-12:], exc_info=True)
                with database.transaction():
                    database.execute('''UPDATE TelegramStorageFiles SET status='error',attempts=attempts+1,
                        error=?,updated_at=CURRENT_TIMESTAMP WHERE file_id=?''',
                                     (str(error)[:500], file_id))


backup = StorageBackup()
