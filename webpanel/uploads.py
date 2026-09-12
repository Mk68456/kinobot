import asyncio
import hashlib
import shutil
import uuid
from pathlib import Path
from aiogram import types
from aiogram.utils.exceptions import RetryAfter, TelegramAPIError
from database.connection import database
from services.access import is_admin
from webpanel.repository import asset


class Uploads:
    def __init__(self, bot, directory, max_bytes, quota_bytes, chunk_bytes):
        self.bot = bot
        self.directory = Path(directory).resolve()
        self.max_bytes = max_bytes
        self.quota_bytes = quota_bytes
        self.chunk_bytes = chunk_bytes
        self.task = None
        self.event = asyncio.Event()
        self.locks = {}

    def path(self, asset_id):
        if len(asset_id) != 32 or any(c not in "0123456789abcdef" for c in asset_id):
            raise ValueError("Неверный идентификатор файла.")
        return self.directory / f"{asset_id}.part"

    def create(self, admin_id, data):
        name = str(data.get("filename", "")).replace("\\", "/").split("/")[-1].strip()
        size = data.get("size")
        first_hash = str(data.get("first_hash", ""))
        kind = data.get("file_type")
        if not name or len(name) > 240 or type(size) is not int or not 0 < size <= self.max_bytes:
            raise ValueError(f"Файл должен быть от 1 байта до {self.max_bytes // 1_000_000} МБ.")
        if (
            kind not in ("video", "document")
            or len(first_hash) != 64
            or any(c not in "0123456789abcdef" for c in first_hash)
        ):
            raise ValueError("Некорректный тип или контрольная сумма файла.")
        self.directory.mkdir(parents=True, exist_ok=True)
        used, remaining = database.execute(
            "SELECT COALESCE(SUM(size),0),COALESCE(SUM(size-received),0) FROM WebAssets WHERE source='web' AND status IN ('uploading','queued','sending','error')"
        ).fetchone()
        if (
            used + size > self.quota_bytes
            or shutil.disk_usage(self.directory).free < remaining + size + 512 * 1024 * 1024
        ):
            raise ValueError("На сервере недостаточно места. Удалите ненужные незавершённые загрузки.")
        asset_id = uuid.uuid4().hex
        with database.transaction():
            database.execute(
                """INSERT INTO WebAssets(id,admin_id,source,filename,size,file_type,status,first_hash)
                VALUES (?,?,'web',?,?,?,'uploading',?)""",
                (asset_id, admin_id, name, size, kind, first_hash),
            )
        return asset(asset_id, admin_id)

    async def append(self, asset_id, admin_id, offset, chunk, checksum):
        async with self.locks.setdefault(asset_id, asyncio.Lock()):
            file = asset(asset_id, admin_id)
            if not file or file["status"] != "uploading":
                raise ValueError("Загрузка недоступна.")
            if offset != file["received"]:
                raise ValueError("Позиция загрузки изменилась. Обновите состояние и продолжите.")
            if not 0 < len(chunk) <= self.chunk_bytes or offset + len(chunk) > file["size"]:
                raise ValueError("Неверный размер части файла.")
            actual_hash = hashlib.sha256(chunk).hexdigest()
            if actual_hash != checksum or (offset == 0 and actual_hash != file["first_hash"]):
                raise ValueError("Контрольная сумма не совпала. Выберите исходный файл повторно.")
            path = self.path(asset_id)

            def write_chunk():
                if not path.exists() and offset:
                    raise ValueError("Временный файл удалён. Начните новую загрузку.")
                with path.open("r+b" if path.exists() else "wb") as output:
                    output.seek(0, 2)
                    if output.tell() < offset:
                        raise ValueError("Временный файл повреждён. Начните новую загрузку.")
                    output.truncate(offset)
                    output.seek(offset)
                    output.write(chunk)
                    output.flush()
                    import os

                    os.fsync(output.fileno())

            # Shield disk I/O so cancellation cannot release the lock while the writer is still running.
            task = asyncio.create_task(asyncio.to_thread(write_chunk))
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                await task
                raise
            with database.transaction():
                database.execute(
                    "UPDATE WebAssets SET received=? WHERE id=?", (offset + len(chunk), asset_id)
                )
            return offset + len(chunk)

    def enqueue(self, asset_id, admin_id):
        file = asset(asset_id, admin_id)
        if not file or file["source"] != "web":
            raise ValueError("Загрузка не найдена.")
        if file["status"] in ("ready", "queued", "sending"):
            return
        if file["status"] not in ("uploading", "error") or file["received"] != file["size"]:
            raise ValueError("Файл ещё не загружен полностью.")
        with database.transaction():
            database.execute("UPDATE WebAssets SET status='queued',error=NULL WHERE id=?", (asset_id,))
        self.event.set()

    async def discard(self, asset_id, admin_id):
        async with self.locks.setdefault(asset_id, asyncio.Lock()):
            file = asset(asset_id, admin_id)
            if not file or file["status"] == "sending":
                raise ValueError("Дождитесь завершения передачи в Telegram.")
            if file["status"] == "ready":
                raise ValueError("Готовый файл можно убрать из черновика; каталог не будет изменён.")
            with database.transaction():
                database.execute("UPDATE WebAssets SET status='cancelled' WHERE id=?", (asset_id,))
            await asyncio.to_thread(self.path(asset_id).unlink, missing_ok=True)

    async def send(self, file):
        if not is_admin(file["admin_id"]):
            raise ValueError("Администратор больше не имеет доступа.")
        with database.transaction():
            database.execute("UPDATE WebAssets SET status='sending',error=NULL WHERE id=?", (file["id"],))
        path = self.path(file["id"])
        if not path.exists() or path.stat().st_size != file["size"]:
            raise ValueError("Временный файл отсутствует или повреждён.")
        # File is streamed to this bot's API server. Never load a multi-GB file into RAM.
        with path.open("rb") as stream, self.bot.request_timeout(7200):
            content = types.InputFile(stream, filename=file["filename"])
            caption = f"Веб-загрузка · {file['filename'][:200]}"
            if file["file_type"] == "video":
                message = await self.bot.send_video(
                    file["admin_id"], content, caption=caption, parse_mode="", supports_streaming=True
                )
                media = message.video
            else:
                message = await self.bot.send_document(
                    file["admin_id"], content, caption=caption, parse_mode=""
                )
                media = message.document
        if not media:
            raise ValueError("Telegram не вернул идентификатор файла.")
        with database.transaction():
            database.execute(
                "UPDATE WebAssets SET status='ready',file_id=?,error=NULL,source_chat_id=?,source_message_id=? WHERE id=?",
                (media.file_id, message.chat.id, message.message_id, file["id"]),
            )
        await asyncio.to_thread(path.unlink, missing_ok=True)

    def start(self):
        self.directory.mkdir(parents=True, exist_ok=True)
        with database.transaction():
            database.execute(
                "UPDATE WebAssets SET status='error',error='Передача прервана перезапуском. Проверьте чат и повторите при необходимости.' WHERE status='sending'"
            )
        self.task = asyncio.create_task(self.run())

    async def run(self):
        while True:
            self.event.clear()
            row = database.execute(
                "SELECT id,admin_id FROM WebAssets WHERE status='queued' ORDER BY created_at LIMIT 1"
            ).fetchone()
            if not row:
                await self.event.wait()
                continue
            try:
                await self.send(asset(*row))
            except asyncio.CancelledError:
                with database.transaction():
                    database.execute(
                        "UPDATE WebAssets SET status='error',error='Передача прервана остановкой. Повторите после проверки чата.' WHERE id=? AND status='sending'",
                        (row[0],),
                    )
                raise
            except RetryAfter as error:
                with database.transaction():
                    database.execute("UPDATE WebAssets SET status='queued' WHERE id=?", (row[0],))
                await asyncio.sleep(error.timeout + 1)
            except Exception as error:
                text = (
                    str(error)
                    if isinstance(error, ValueError)
                    else (
                        "Ошибка Telegram. Проверьте Bot API и повторите передачу."
                        if isinstance(error, TelegramAPIError)
                        else "Ошибка передачи. Проверьте место на диске и соединение."
                    )
                )
                with database.transaction():
                    database.execute(
                        "UPDATE WebAssets SET status='error',error=? WHERE id=? AND status!='ready'",
                        (text, row[0]),
                    )

    async def stop(self):
        if self.task:
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                pass
            self.task = None
