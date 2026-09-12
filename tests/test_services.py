import asyncio
import contextlib
import io
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from aiogram.utils.exceptions import BotBlocked, NetworkError, RetryAfter, TelegramAPIError
from database.connection import database
from database.migrations import migrate
from database.people import add_user
from database import roles, broadcasts as jobs
from services.subscriptions import check_subscription
from services.broadcasts import BroadcastWorker


class ServiceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        database.open(":memory:")
        with contextlib.redirect_stdout(io.StringIO()):
            migrate()
        add_user(123)
        database.execute("INSERT INTO channels_info VALUES ('test','https://t.me/test',-100123)")
        database.commit()

    def tearDown(self):
        database.close()

    async def test_bypass_does_not_call_telegram_and_revocation_is_immediate(self):
        bot = SimpleNamespace(get_chat_member=AsyncMock(return_value=SimpleNamespace(status="left")))
        role = roles.save_role("VIP")
        roles.set_bypass(role, True)
        roles.assign_role(123, role)
        self.assertTrue(await check_subscription(123, bot))
        bot.get_chat_member.assert_not_awaited()
        roles.set_bypass(role, False)
        self.assertFalse(await check_subscription(123, bot))
        bot.get_chat_member.assert_awaited_once_with(-100123, user_id=123)

    async def test_membership_statuses_and_network_failure(self):
        for status, member, expected in [
            ("member", True, True),
            ("creator", True, True),
            ("administrator", True, True),
            ("left", False, False),
            ("kicked", False, False),
            ("restricted", False, False),
            ("restricted", True, True),
        ]:
            with self.subTest(status=status, member=member):
                bot = SimpleNamespace(
                    get_chat_member=AsyncMock(return_value=SimpleNamespace(status=status, is_member=member))
                )
                self.assertEqual(await check_subscription(123, bot), expected)
        bot.get_chat_member.side_effect = TelegramAPIError("test")
        with self.assertLogs("services.subscriptions", level="WARNING"):
            self.assertFalse(await check_subscription(123, bot))

    def worker(self):
        worker = BroadcastWorker()
        worker.bot = SimpleNamespace(copy_message=AsyncMock(), send_message=AsyncMock())
        return worker

    async def test_broadcast_network_failure_keeps_user_and_role(self):
        role = roles.save_role("VIP")
        roles.assign_role(123, role)
        worker = self.worker()
        worker.bot.copy_message.side_effect = NetworkError("test")
        job_id = jobs.create_job(99, 1, 99)
        with patch("services.broadcasts.asyncio.sleep", new_callable=AsyncMock):
            await worker.process(jobs.get_job(job_id))
        self.assertEqual(jobs.job_counts(job_id), {"failed": 1})
        self.assertEqual(database.execute("SELECT is_active FROM Users WHERE id=123").fetchone()[0], 1)
        self.assertEqual(roles.user_role(123)[0], role)
        self.assertTrue(jobs.retry_job(job_id))
        self.assertEqual(jobs.job_counts(job_id), {"pending": 1})

    async def test_blocked_user_is_disabled_without_deleting(self):
        worker = self.worker()
        worker.bot.copy_message.side_effect = BotBlocked("blocked")
        job_id = jobs.create_job(99, 1, 99)
        with patch("services.broadcasts.asyncio.sleep", new_callable=AsyncMock):
            await worker.process(jobs.get_job(job_id))
        self.assertEqual(jobs.job_counts(job_id), {"inactive": 1})
        self.assertEqual(database.execute("SELECT is_active FROM Users WHERE id=123").fetchone()[0], 0)

    async def test_rate_limit_retries_without_duplicate_success(self):
        worker = self.worker()
        worker.bot.copy_message.side_effect = [RetryAfter(2), None]
        job_id = jobs.create_job(99, 1, 99)
        with patch("services.broadcasts.asyncio.sleep", new_callable=AsyncMock) as sleep:
            await worker.process(jobs.get_job(job_id))
        sleep.assert_any_await(2.1)
        self.assertEqual(jobs.job_counts(job_id), {"sent": 1})
        self.assertEqual(worker.bot.copy_message.await_count, 2)

    async def test_resume_skips_delivered_recipients_and_cancel_stops(self):
        add_user(124)
        job_id = jobs.create_job(99, 1, 99)
        database.execute("UPDATE BroadcastRecipients SET status='sent' WHERE user_id=123")
        database.execute("UPDATE BroadcastJobs SET status='running' WHERE id=?", (job_id,))
        database.commit()
        worker = self.worker()
        with patch("services.broadcasts.asyncio.sleep", new_callable=AsyncMock):
            await worker.process(jobs.get_job(job_id))
        worker.bot.copy_message.assert_awaited_once_with(124, 99, 1)
        self.assertEqual(jobs.job_counts(job_id), {"sent": 2})
        other_job = jobs.create_job(99, 2, 99)
        jobs.cancel_job(other_job)
        worker.bot.copy_message.reset_mock()
        self.assertIsNone(await worker.deliver(jobs.get_job(other_job), 123))
        worker.bot.copy_message.assert_not_awaited()

    async def test_shutdown_preserves_pending_recipient(self):
        job_id = jobs.create_job(99, 1, 99)
        worker = self.worker()
        worker.bot.copy_message.side_effect = asyncio.CancelledError
        with self.assertRaises(asyncio.CancelledError):
            await worker.process(jobs.get_job(job_id))
        self.assertEqual(jobs.job_counts(job_id), {"pending": 1})
