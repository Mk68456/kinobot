"""One verified provider video -> KinoTime inbox, with durable duplicate protection."""

import asyncio
from contextlib import closing
from pathlib import Path
import sqlite3

from tools.yougen_bridge import BridgeError, message_by_id, video_record


def reserve(path, source_id, message_id, target_id):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(path)) as db:
        db.execute('''CREATE TABLE IF NOT EXISTS Deliveries (
            source_id INTEGER, message_id INTEGER, target_id INTEGER,
            status TEXT NOT NULL, forwarded_id INTEGER,
            PRIMARY KEY(source_id,message_id,target_id))''')
        try:
            db.execute('INSERT INTO Deliveries VALUES (?,?,?,?,NULL)',
                       (source_id, message_id, target_id, 'reserved'))
            db.commit()
        except sqlite3.IntegrityError:
            raise BridgeError('Этот файл уже передавался или попытка осталась незавершённой. '
                              'Проверьте чат и журнал; автоматического повтора нет.') from None


def mark(path, source_id, message_id, target_id, status, forwarded_id=None):
    with closing(sqlite3.connect(path)) as db:
        db.execute('UPDATE Deliveries SET status=?,forwarded_id=COALESCE(?,forwarded_id) '
                   'WHERE source_id=? AND message_id=? AND target_id=?',
                   (status, forwarded_id, source_id, message_id, target_id))
        db.commit()


async def wait_reply(client, target, after, text, timeout=30):
    deadline = asyncio.get_running_loop().time() + timeout
    while True:
        for message in await client.get_messages(target, limit=10, min_id=after):
            if not message.out and message.sender_id == target.id and text in (message.raw_text or ''):
                return message
        if asyncio.get_running_loop().time() >= deadline:
            raise BridgeError('KinoTime не подтвердил нужный этап. Попытка остановлена; проверьте чат.')
        await asyncio.sleep(2)


async def deliver(client, source, target, message_id, expected, ledger):
    """Caller must supply explicitly verified source/own-target entities and metadata."""
    if source.id == target.id or not target.bot:
        raise BridgeError('Получатель должен быть отдельным ботом KinoTime.')
    message = await message_by_id(client, source, message_id)
    record = video_record(message)
    if not record or any(record.get(key) != value for key, value in expected.items()):
        raise BridgeError('Видео не соответствует выбранному заданию. Ничего не переслано.')
    reserve(ledger, source.id, message_id, target.id)
    try:
        # Arm the inbox and require its own acknowledgement, not an arbitrary old response.
        command = await client.send_message(target, '/webfiles')
        await wait_reply(client, target, command.id, 'Отправляйте или пересылайте видео и документы')
        mark(ledger, source.id, message_id, target.id, 'inbox_ready')
        forwarded = await client.forward_messages(target, message, from_peer=source)
        mark(ledger, source.id, message_id, target.id, 'forwarded', forwarded.id)
        await wait_reply(client, target, forwarded.id, 'Файл добавлен в «Из Telegram»')
        # Receipt is already durable before closing collection; never resend after /done failure.
        mark(ledger, source.id, message_id, target.id, 'received')
        await client.send_message(target, '/done')
        mark(ledger, source.id, message_id, target.id, 'complete')
        return {'status': 'received_by_kinotime', 'video': record,
                'published_to_catalog': False}
    except BaseException:
        # Preserve the precise durable phase. Even timeouts/cancellation must not cause a replay.
        raise
