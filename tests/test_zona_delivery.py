import asyncio
import contextlib
import importlib
import io
import json
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from aiogram import Bot, Dispatcher, types
from aiogram.dispatcher.filters import StateFilter
import loader
from database.connection import database
from database.migrations import migrate
from database import zona_jobs as jobs
from services.zona_delivery import ZonaDelivery, normalize_page, upload_limit


SOURCE = {'source_id': 'zona-test-LostFilm-480p', 'label': 'Тест — S01E01 · LostFilm · 480p',
          'metadata': {'title': 'Тест', 'season': 1, 'episode': 1, 'translation': 'LostFilm',
                       'expected_size': 1000}}
PAGE = 'https://w1.zona.im/tvseries/test'


class ZonaDeliveryTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        if loader.dp is None:
            loader.initialize('123456:FAKE_TOKEN_FOR_OFFLINE_TESTS')
        importlib.import_module('handlers')
        cls.dp = loader.dp

    async def asyncSetUp(self):
        database.open(':memory:')
        self.addCleanup(database.close)
        with contextlib.redirect_stdout(io.StringIO()):
            migrate()
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        patcher = patch('data.config.ADMIN', [99, 100])
        patcher.start()
        self.addCleanup(patcher.stop)
        self.bot = loader.bot
        Bot.set_current(self.bot)
        Dispatcher.set_current(self.dp)
        self.dp.storage.data.clear()
        self.sent = AsyncMock(return_value=SimpleNamespace(
            video=SimpleNamespace(file_id='cached-video-id'), chat=SimpleNamespace(id=99), message_id=42))
        self.messages = AsyncMock()
        self.answers = AsyncMock()
        for name, mock in [('send_video', self.sent), ('send_message', self.messages),
                           ('answer_callback_query', self.answers)]:
            patcher = patch.object(self.bot, name, mock)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.worker = ZonaDelivery()
        self.worker.bot = self.bot
        self.worker.directory = Path(self.directory.name)
        self.worker.max_bytes = 2_000_000_000

    async def asyncTearDown(self):
        database.close()

    async def send(self, user=99, text=None, callback=None):
        StateFilter.ctx_state.set(await self.dp.storage.get_state(chat=user, user=user))
        message = {'message_id': 1, 'date': 1, 'chat': {'id': user, 'type': 'private'},
                   'from': {'id': user, 'is_bot': False, 'first_name': 'Test'}, 'text': text or 'menu'}
        payload = {'update_id': 1, 'message': message} if callback is None else {
            'update_id': 1, 'callback_query': {'id': 'test', 'from': message['from'],
                                             'chat_instance': 'test', 'message': message, 'data': callback}}
        await self.dp.process_update(types.Update(**payload))

    def cached_file(self):
        path = self.worker.directory / 'test.mp4'
        path.write_bytes(b'\x00\x00\x00\x18ftypmp42' + b'0' * 100)
        path.with_suffix('.mp4.json').write_text(json.dumps({'status': 'ready', 'size': path.stat().st_size}))
        return path

    async def test_command_selection_queue_and_delivery(self):
        with patch('handlers.admin.zona.discover', return_value=[SOURCE]), patch(
                'handlers.admin.zona.upload_limit', return_value=2_000_000_000), patch(
                'handlers.admin.zona.delivery', self.worker):
            # A running worker is required to accept a new choice. process() is driven explicitly in this test.
            self.worker.task = object()
            await self.send(text='/zona ' + PAGE)
            data = await self.dp.current_state(chat=99, user=99).get_data()
            self.assertNotIn('url', data['zona_sources'][0])
            callback = 'zona:pick:' + data['zona_nonce'] + ':0'
            await self.send(callback=callback)
            await self.send(callback=callback)
            self.assertEqual(database.execute('SELECT COUNT(*) FROM ZonaJobs').fetchone()[0], 1)
            job = jobs.recent(99)[0]
            path = self.cached_file()
            with patch.object(self.worker, 'prepare_file', return_value=path):
                await self.worker.process(job)
        self.sent.assert_awaited_once()
        complete = jobs.get(job['id'])
        self.assertEqual(complete['status'], 'sent')
        self.assertEqual(complete['file_id'], 'cached-video-id')
        self.assertEqual(database.execute('SELECT source,status,file_id FROM WebAssets').fetchone(),
                         ('zona', 'ready', 'cached-video-id'))
        self.assertEqual(database.execute('SELECT count(*) FROM Movies').fetchone()[0], 0)

    async def test_non_admin_cannot_start_and_other_admin_cannot_resend(self):
        with patch('handlers.admin.zona.discover') as discover:
            await self.send(user=123, text='/zona ' + PAGE)
            discover.assert_not_called()
        job = jobs.enqueue(99, PAGE, SOURCE)
        jobs.update(job['id'], status='sent', file_id='cached')
        await self.send(user=100, callback='zona:get:' + job['id'])
        self.sent.assert_not_awaited()
        self.assertIn('не найдена', str(self.answers.call_args))

    async def test_expired_buttons_and_large_cloud_upload_do_not_queue(self):
        with patch('handlers.admin.zona.discover', return_value=[{**SOURCE, 'metadata': {'expected_size': 318286273}}]):
            await self.send(text='/zona ' + PAGE)
        data = await self.dp.current_state(chat=99, user=99).get_data()
        with patch('handlers.admin.zona.upload_limit', return_value=50_000_000):
            await self.send(callback='zona:pick:' + data['zona_nonce'] + ':0')
        self.assertEqual(jobs.recent(99), [])
        self.assertIn('лимит', str(self.messages.call_args).lower())
        await self.send(callback='zona:pick:stale:0')
        self.assertIn('устарел', str(self.answers.call_args))

    async def test_commands_leave_url_prompt_usable(self):
        await self.send(text='/zona')
        self.assertEqual(await self.dp.current_state(chat=99, user=99).get_state(), 'Zona:url')
        await self.send(text='/zona_status')
        self.assertIn('Загрузок пока нет', str(self.messages.call_args))

    def test_upgrade_preserves_catalog(self):
        database.execute("INSERT INTO Movies(movie_title,movie_number) VALUES ('Existing',777)")
        database.execute('DROP TABLE ZonaJobs')
        from database.migrations import MIGRATIONS
        database.execute(f'PRAGMA user_version={len(MIGRATIONS) - 1}')
        database.commit()
        migrate()
        self.assertEqual(database.execute('SELECT movie_title FROM Movies WHERE movie_number=777').fetchone()[0],
                         'Existing')
        self.assertEqual(jobs.recent(99), [])

    async def test_restart_recovers_download_but_not_ambiguous_upload(self):
        first = jobs.enqueue(99, PAGE, SOURCE)
        second = jobs.enqueue(100, PAGE, SOURCE)
        jobs.update(first['id'], status='downloading')
        jobs.update(second['id'], status='sending')
        jobs.recover()
        self.assertEqual(jobs.get(first['id'])['status'], 'queued')
        self.assertEqual(jobs.get(second['id'])['status'], 'interrupted')
        self.assertIsNone(jobs.get(first['id'], 100))

    async def test_upload_retry_uses_saved_file_without_resolving_again(self):
        job = jobs.enqueue(99, PAGE, SOURCE)
        jobs.update(job['id'], status='error', path=str(self.cached_file()))
        job = jobs.retry(job['id'], 99)
        with patch('services.zona_delivery.discover') as discover:
            await self.worker.process(job)
            discover.assert_not_called()
        self.sent.assert_awaited_once()

    async def test_shutdown_waits_for_download_thread_before_database_close(self):
        job = jobs.enqueue(99, PAGE, SOURCE)
        started, finished = threading.Event(), threading.Event()
        def blocking_download(record):
            started.set()
            self.worker.cancel.wait(3)
            finished.set()
            raise ValueError('Stopped')
        self.worker.event = asyncio.Event()
        with patch.object(self.worker, 'prepare_file', side_effect=blocking_download):
            self.worker.task = asyncio.create_task(self.worker.run())
            self.assertTrue(await asyncio.to_thread(started.wait, 2))
            await self.worker.stop()
        self.assertTrue(finished.is_set())
        self.assertEqual(jobs.get(job['id'])['status'], 'queued')

    async def test_upload_failure_never_marks_sent_and_requires_explicit_retry(self):
        job = jobs.enqueue(99, PAGE, SOURCE)
        jobs.update(job['id'], path=str(self.cached_file()))
        self.sent.side_effect = RuntimeError('network error with sensitive URL')
        self.worker.event = asyncio.Event()
        self.worker.task = asyncio.create_task(self.worker.run())
        try:
            for _ in range(100):
                if jobs.get(job['id'])['status'] == 'interrupted':
                    break
                await asyncio.sleep(.01)
            self.assertEqual(jobs.get(job['id'])['status'], 'interrupted')
            self.assertNotIn('sensitive', jobs.get(job['id'])['error'])
            self.assertEqual(database.execute('SELECT count(*) FROM WebAssets').fetchone()[0], 0)
        finally:
            await self.worker.stop()

    async def test_application_lifecycle_starts_and_stops_worker(self):
        import app
        from services.zona_delivery import delivery
        database.close()
        dispatcher = SimpleNamespace(bot=SimpleNamespace(get_me=AsyncMock(
            return_value=SimpleNamespace(username='ExampleBot'))),
            storage=SimpleNamespace(close=AsyncMock(), wait_closed=AsyncMock()))
        root = Path(self.directory.name)
        with (patch('app.DATABASE_PATH', root / 'isolated.sqlite'), patch('app.BACKUP_DIR', root / 'backups'),
              patch('data.settings.DATA_DIR', root), patch('data.settings.WEB_ENABLED', False),
              patch('services.tmdb.check_connectivity', AsyncMock()),
              patch('utils.set_bot_commands.set_default_commands', AsyncMock()),
              patch('services.broadcasts.broadcasts.start'),
              patch('services.broadcasts.broadcasts.stop', AsyncMock()),
              patch('services.storage_backup.backup.start'),
              patch('services.storage_backup.backup.stop', AsyncMock()),
              patch('services.tmdb.close_session', AsyncMock()), contextlib.redirect_stdout(io.StringIO())):
            try:
                await app.on_startup(dispatcher)
                self.assertIsNotNone(delivery.task)
            finally:
                await app.on_shutdown(dispatcher)
            self.assertIsNone(delivery.task)
            self.assertIsNone(database.connection)

    def test_page_validation_and_transport_limit(self):
        self.assertEqual(normalize_page(PAGE + '?tracking=1#x'), PAGE)
        for value in ['http://w1.zona.im/tvseries/test', 'https://w1.zona.im.evil.test/tvseries/test',
                      'https://user:pass@w1.zona.im/tvseries/test', 'https://w1.zona.im:444/tvseries/test']:
            with self.assertRaises(ValueError):
                normalize_page(value)
        with patch('data.settings.BOT_API_BASE', 'https://api.telegram.org'):
            self.assertEqual(upload_limit(), 50_000_000)
        with patch('data.settings.BOT_API_BASE', 'http://127.0.0.1:8081'):
            self.assertEqual(upload_limit(), 2_000_000_000)


if __name__ == '__main__':
    unittest.main()
