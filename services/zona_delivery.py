"""Single-worker pilot: resolve an exact chosen source, download, upload, cache file_id."""

import asyncio
import json
import logging
from pathlib import Path
import shutil
import threading
import uuid
from urllib.parse import urlsplit, urlunsplit

from aiogram import types

from database.connection import database
from database import zona_jobs as jobs
from services.access import is_admin
from tools.zona_prototype import discover, download
from tools.zona_prototype import Cancelled as DownloadCancelled
from tools.zona_source import is_zona_page

logger = logging.getLogger(__name__)
STATUSES = {'queued': 'В очереди', 'downloading': 'Загружается', 'sending': 'Отправляется в Telegram',
            'sent': 'Отправлено', 'error': 'Ошибка', 'interrupted': 'Отправка прервана'}


def normalize_page(value):
    value = value.strip()
    parts = urlsplit(value)
    if (len(value) > 1000 or not is_zona_page(value) or parts.username or parts.password
            or parts.port is not None or parts.scheme != 'https'):
        raise ValueError('Нужна HTTPS-ссылка на страницу фильма или сериала Zona, например '
                         'https://w1.zona.im/tvseries/dzhek-richer-2022')
    return urlunsplit(('https', parts.hostname.lower(), parts.path.rstrip('/'), '', ''))


def upload_limit():
    from data.settings import BOT_API_BASE
    # api.telegram.org only accepts a new upload up to 50 MB. A custom server must run --local.
    return 50_000_000 if urlsplit(BOT_API_BASE).hostname == 'api.telegram.org' else 2_000_000_000


def size_error(size, limit):
    return (f'Файл занимает {size / 1_000_000:.0f} МБ, лимит текущего подключения — '
            f'{limit / 1_000_000:.0f} МБ. Для больших файлов настройте локальный Telegram Bot API '
            'и BOT_API_BASE. Загрузка не запущена.')


class ZonaDelivery:
    def __init__(self):
        self.bot = self.task = self.event = None
        self.directory = None
        self.cancel = None
        self.max_bytes = 50_000_000
        self.quota_bytes = 20_000_000_000

    def start(self, bot):
        if self.task is not None:
            return
        from data.settings import DATA_DIR
        self.directory = DATA_DIR / 'zona'
        self.max_bytes = upload_limit()
        self.bot, self.event = bot, asyncio.Event()
        jobs.recover()
        self.task = asyncio.create_task(self.run())

    def notify(self):
        if self.event:
            self.event.set()

    async def stop(self):
        if self.cancel is not None:
            self.cancel.set()
        if self.task is not None:
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                pass
            self.task = None

    async def tell(self, admin_id, text):
        try:
            await self.bot.send_message(admin_id, text, parse_mode='')
        except Exception:
            logger.warning('Zona notification failed for administrator %s', admin_id)

    def prepare_file(self, job):
        # No database access in this thread. Always refresh expiring source URLs.
        source = next((source for source in discover(job['page_url'])
                       if source.get('source_id') == job['source_id']), None)
        if self.cancel is not None and self.cancel.is_set():
            raise DownloadCancelled('Загрузка остановлена')
        if source is None:
            raise ValueError('Выбранный вариант больше не доступен. Заново откройте страницу через /zona.')
        self.directory.mkdir(parents=True, exist_ok=True)
        total = sum(path.stat().st_size for path in self.directory.iterdir() if path.is_file())
        expected = int(source.get('metadata', {}).get('expected_size') or 0)
        if expected > self.max_bytes:
            raise ValueError(size_error(expected, self.max_bytes))
        if total + expected > self.quota_bytes or shutil.disk_usage(self.directory).free < expected + 512 * 1024 * 1024:
            raise ValueError('Недостаточно свободного места для загрузки.')
        return download(source['url'], self.directory, job['page_url'], cancel=self.cancel,
                        headers=source.get('headers'), source_id=job['source_id'],
                        metadata=source.get('metadata'), max_bytes=self.max_bytes)

    async def process(self, job):
        if not is_admin(job['admin_id']):
            raise ValueError('Администратор больше не имеет доступа.')
        path = Path(job['path']) if job.get('path') else None
        # Use a complete saved file after an upload failure; do not re-fetch an expired link.
        if path is not None:
            root = self.directory.resolve()
            if not path.resolve().is_relative_to(root):
                raise ValueError('Неверный путь сохранённого файла.')
            record = path.with_suffix(path.suffix + '.json')
            try:
                info = json.loads(record.read_text(encoding='utf-8'))
                valid = info.get('status') == 'ready' and path.stat().st_size == info['size']
            except (OSError, KeyError, ValueError):
                valid = False
            if not valid:
                path = None
        if path is None:
            jobs.update(job['id'], status='downloading', error=None)
            await self.tell(job['admin_id'], 'Загружаю: ' + job['label'])
            self.cancel = threading.Event()
            task = asyncio.create_task(asyncio.to_thread(self.prepare_file, job))
            try:
                path = await asyncio.shield(task)
            except asyncio.CancelledError:
                self.cancel.set()
                try:
                    await task
                except Exception:
                    pass
                raise
            finally:
                self.cancel = None
            jobs.update(job['id'], path=str(path))
        if path.stat().st_size > self.max_bytes:
            raise ValueError(size_error(path.stat().st_size, self.max_bytes))
        if not is_admin(job['admin_id']):
            raise ValueError('Администратор больше не имеет доступа.')
        jobs.update(job['id'], status='sending')
        await self.tell(job['admin_id'], 'Видео готово. Отправляю в Telegram…')
        with path.open('rb') as stream, self.bot.request_timeout(7200):
            message = await self.bot.send_video(job['admin_id'], types.InputFile(stream, filename=path.name),
                                                caption=job['label'][:900], parse_mode='', supports_streaming=True)
        if not message.video or not message.video.file_id:
            raise ValueError('Telegram не вернул идентификатор видео. Проверьте чат перед повтором.')
        asset_id = uuid.uuid4().hex
        with database.transaction():
            database.execute('''INSERT INTO WebAssets
                (id,admin_id,source,filename,size,file_type,file_id,status,received,source_chat_id,source_message_id)
                VALUES (?,?,'zona',?,?,'video',?,'ready',?,?,?)''',
                (asset_id, job['admin_id'], path.name, path.stat().st_size, message.video.file_id,
                 path.stat().st_size, message.chat.id, message.message_id))
            jobs.update(job['id'], status='sent', error=None, file_id=message.video.file_id,
                        asset_id=asset_id, message_id=message.message_id)
        from services.storage_backup import backup
        backup.notify()

    async def run(self):
        while True:
            self.event.clear()
            row = database.execute("SELECT id FROM ZonaJobs WHERE status='queued' ORDER BY created_at,rowid LIMIT 1").fetchone()
            if not row:
                await self.event.wait()
                continue
            job = jobs.get(row[0])
            try:
                await self.process(job)
            except asyncio.CancelledError:
                current = jobs.get(job['id'])
                jobs.update(job['id'], status='interrupted' if current['status'] == 'sending' else 'queued',
                            error='Обработка прервана остановкой. Перед повторной отправкой проверьте чат.')
                raise
            except Exception as exc:
                sending = jobs.get(job['id'])['status'] == 'sending'
                text = (str(exc) if isinstance(exc, ValueError) else
                        'Не удалось отправить видео. Проверьте чат и подключение к Telegram.' if sending else
                        'Источник временно недоступен или загрузка прервалась. Повторите позже.')
                # Raw exceptions may contain expiring source URLs or credentials: don't log them.
                logger.warning('Zona job %s failed: %s', job['id'], type(exc).__name__)
                jobs.update(job['id'], status='interrupted' if sending else 'error', error=text)
                await self.tell(job['admin_id'], text + '\nСостояние и повтор: /zona_status')


delivery = ZonaDelivery()
