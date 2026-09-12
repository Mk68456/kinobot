import asyncio
import logging
from aiogram.utils.exceptions import (
    BotBlocked,
    UserDeactivated,
    ChatNotFound,
    NetworkError,
    RetryAfter,
    TelegramAPIError,
)
from database.connection import database
from database.broadcasts import get_job, job_counts

logger = logging.getLogger(__name__)


class BroadcastWorker:
    def __init__(self):
        self.task = None
        self.bot = None
        self.wakeup = None

    def start(self, bot):
        self.bot = bot
        self.wakeup = asyncio.Event()
        self.task = asyncio.create_task(self.run())

    def notify(self):
        if self.wakeup:
            self.wakeup.set()

    async def stop(self):
        if self.task:
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                pass
            self.task = None

    async def deliver(self, job, user_id):
        for attempt in range(3):
            if get_job(job[0])[4] == "cancelled":
                return None
            try:
                await self.bot.copy_message(user_id, job[1], job[2])
                return "sent", None
            except (BotBlocked, UserDeactivated, ChatNotFound) as error:
                with database.transaction():
                    database.execute("UPDATE Users SET is_active=0 WHERE id=?", (user_id,))
                return "inactive", type(error).__name__
            except RetryAfter as error:
                if attempt < 2:
                    await asyncio.sleep(error.timeout + 0.1)
                else:
                    return "failed", type(error).__name__
            except NetworkError as error:
                if attempt < 2:
                    await asyncio.sleep(2**attempt)
                else:
                    return "failed", type(error).__name__
            except TelegramAPIError as error:
                return "failed", type(error).__name__

    async def process(self, job):
        with database.transaction():
            database.execute("UPDATE BroadcastJobs SET status='running' WHERE id=?", (job[0],))
        while get_job(job[0])[4] == "running":
            recipient = database.execute(
                "SELECT user_id FROM BroadcastRecipients WHERE job_id=? AND status='pending' LIMIT 1",
                (job[0],),
            ).fetchone()
            if not recipient:
                with database.transaction():
                    database.execute("UPDATE BroadcastJobs SET status='completed' WHERE id=?", (job[0],))
                counts = job_counts(job[0])
                try:
                    await self.bot.send_message(
                        job[3],
                        f"Рассылка #{job[0]} завершена. "
                        f"Отправлено: {counts.get('sent', 0)}, "
                        f"ошибок: {counts.get('failed', 0)}, "
                        f"недоступных: {counts.get('inactive', 0)}.",
                    )
                except TelegramAPIError:
                    logger.warning("Unable to notify administrator about broadcast %s", job[0])
                return
            result = await self.deliver(job, recipient[0])
            if result is None:
                return
            with database.transaction():
                database.execute(
                    "UPDATE BroadcastRecipients SET status=?,error=? WHERE job_id=? AND user_id=?",
                    (*result, job[0], recipient[0]),
                )
            await asyncio.sleep(0.3)

    async def run(self):
        while True:
            self.wakeup.clear()
            row = database.execute(
                "SELECT id FROM BroadcastJobs WHERE status IN ('pending','running') ORDER BY id LIMIT 1"
            ).fetchone()
            if row:
                try:
                    await self.process(get_job(row[0]))
                except Exception:
                    logger.exception("Broadcast %s paused after unexpected failure", row[0])
                    with database.transaction():
                        database.execute(
                            "UPDATE BroadcastJobs SET status='paused' WHERE id=? AND status!='cancelled'",
                            (row[0],),
                        )
            else:
                await self.wakeup.wait()


broadcasts = BroadcastWorker()
