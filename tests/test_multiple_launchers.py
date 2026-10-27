"""Multi-PC regression checks use only isolated databases, files and local HTTP."""
import copy
import contextlib
import io
import hashlib
import json
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from database.connection import database
from database.migrations import MIGRATIONS, migrate, _multiple_launchers
from tools import launcher_device
from tools.desktop_launcher import Client, RetryingClient, queue_lock, scan, transfer
from webpanel import imports, launcher_control as control, launcher_transfers as uploads, repository

A, B = 'computer-alpha', 'computer-bravo'


def manifest(digest='a' * 64):
    return {'version': 1, 'root': 'D:/Films/Test', 'title': 'Test',
            'files': [{'path': '720p/movie.mp4', 'size': 123, 'mtime': '1', 'content_sha256': digest}]}


class MultiComputerTests(unittest.TestCase):
    def setUp(self):
        database.open(':memory:')
        with contextlib.redirect_stdout(io.StringIO()):
            migrate()

    def tearDown(self):
        database.close()

    def heartbeat(self, instance, **kwargs):
        return control.heartbeat(99, {'instance_id': instance, 'protocol': 3, 'name': instance,
                                     'folders': ['D:/' + instance], 'status': {'message': instance}, **kwargs})

    def file(self, instance, digest='a' * 64):
        draft = imports.create(99, manifest(digest), instance)
        return draft['id'], draft['body']['items'][0]['asset_id']

    def receive(self, asset):
        imports.receive(99, SimpleNamespace(caption='KTFILE:' + asset, video=None,
            document=SimpleNamespace(file_size=123, file_id='telegram-file'),
            chat=SimpleNamespace(id=99), message_id=42))

    def test_verified_retry_releases_same_bytes_from_previous_import_mode(self):
        old_draft, old_asset = self.file(A)
        uploads.operate(99, A, old_draft, old_asset, 'claim')
        uploads.operate(99, A, old_draft, old_asset, 'sent')
        stream_manifest = manifest()
        stream_manifest['root'] = 'D:/KinoTime-Ready/stream-switch'
        new_draft = imports.create(99, stream_manifest, A)
        new_asset = new_draft['body']['items'][0]['asset_id']
        self.assertNotEqual(new_asset, old_asset)
        with self.assertRaises(ValueError):
            uploads.operate(99, A, new_draft['id'], new_asset, 'claim')
        self.assertEqual(uploads.reset(99, B, [new_asset], True)['released'], 0)
        with self.assertRaises(ValueError):
            uploads.reset(99, A, [new_asset], False)
        self.assertEqual(uploads.reset(99, A, [new_asset], True)['released'], 1)
        self.assertEqual(uploads.operate(99, A, new_draft['id'], new_asset, 'claim')['status'], 'claimed')

    def test_commands_and_status_are_independent_under_one_account(self):
        self.heartbeat(A)
        self.heartbeat(B)
        ca = control.enqueue(99, 'prepare', {'qualities': ['720p']}, A)
        cb = control.enqueue(99, 'stop', {}, B)
        self.assertEqual(self.heartbeat(B)['command']['id'], cb['id'])
        self.assertEqual(self.heartbeat(A)['command']['id'], ca['id'])
        with self.assertRaises(ValueError):
            control.complete(99, ca['id'], True, instance_id=B)
        with self.assertRaises(ValueError):
            control.complete(99, ca['id'], True)
        control.complete(99, ca['id'], True, instance_id=A)
        self.assertEqual({a['status']['message'] for a in control.state(99)['agents']}, {A, B})

    def test_multiple_computers_require_explicit_target(self):
        self.heartbeat(A)
        self.heartbeat(B)
        with self.assertRaisesRegex(ValueError, 'Выберите компьютер'):
            control.enqueue(99, 'stop', {})
        with self.assertRaisesRegex(ValueError, 'папка'):
            control.enqueue(99, 'switch_folder', {'folder': 'D:/' + A}, B)
        database.execute("UPDATE LauncherAgents SET last_seen=datetime('now','-1 minute') WHERE instance_id=?", (B,))
        with self.assertRaisesRegex(ValueError, 'не в сети'):
            control.enqueue(99, 'stop', {}, B)

    def test_old_heartbeat_cannot_downgrade_modern_computer(self):
        self.heartbeat(A)
        with self.assertRaises(ValueError):
            control.heartbeat(99, {'instance_id': A, 'status': {}})

    def test_late_command_is_not_started_after_reconnection(self):
        self.heartbeat(A)
        command = control.enqueue(99, 'send', {'qualities': ['720p']}, A)
        database.execute("UPDATE LauncherCommands SET created_at=datetime('now','-3 minutes') WHERE id=?", (command['id'],))
        self.assertIsNone(self.heartbeat(A)['command'])
        self.assertEqual(control.state(99)['commands'][0]['status'], 'failed')

    def test_pairing_code_binds_to_only_one_computer(self):
        with patch('webpanel.imports.is_admin', return_value=True):
            token = imports.pair(99)
            self.assertEqual(imports.authenticate(token, A), 99)
            self.assertIsNone(imports.authenticate(token, B))
            self.assertIsNone(imports.authenticate(token))
            self.assertEqual(imports.authenticate(imports.pair(99), B), 99)

    def test_same_windows_path_does_not_merge_computers(self):
        da, aa = self.file(A)
        db, ab = self.file(B)
        self.assertNotEqual(da, db)
        self.assertNotEqual(aa, ab)
        self.assertEqual(self.file(A)[0], da)
        with self.assertRaises(ValueError):
            imports.device_draft(99, da, B)
        with self.assertRaises(ValueError):
            imports.reopen(99, da, 'rejected', B)

    def test_unowned_legacy_draft_is_adopted_without_losing_ready_file(self):
        old = imports.create(99, manifest())
        asset = old['body']['items'][0]['asset_id']
        self.receive(asset)
        modern = imports.create(99, manifest(), A)
        self.assertEqual(modern['id'], old['id'])
        self.assertEqual(repository.asset(asset, 99)['file_id'], 'telegram-file')
        self.assertNotEqual(imports.create(99, manifest(), B)['id'], old['id'])

    def test_changed_digest_cannot_reuse_old_draft(self):
        self.file(A)
        with self.assertRaisesRegex(ValueError, 'Содержимое'):
            imports.create(99, manifest('b' * 64), A)

    def test_exact_same_file_has_only_one_active_sender(self):
        da, aa = self.file(A)
        db, ab = self.file(B)
        self.assertEqual(uploads.operate(99, A, da, aa, 'claim')['status'], 'claimed')
        with self.assertRaisesRegex(ValueError, 'уже отправляется'):
            uploads.operate(99, B, db, ab, 'claim')
        uploads.operate(99, A, da, aa, 'sent')
        database.execute('UPDATE LauncherTransfers SET expires_at=0')
        with self.assertRaises(ValueError):
            uploads.operate(99, B, db, ab, 'claim')
        self.assertEqual(uploads.operate(99, A, da, aa, 'claim')['status'], 'sent')

    def test_completed_same_file_reuses_telegram_id_on_other_computer(self):
        da, aa = self.file(A)
        db, ab = self.file(B)
        uploads.operate(99, A, da, aa, 'claim')
        uploads.operate(99, A, da, aa, 'sent')
        self.receive(aa)
        self.assertEqual(database.execute('SELECT count(*) FROM LauncherTransfers').fetchone()[0], 0)
        self.assertTrue(uploads.operate(99, B, db, ab, 'claim')['reused'])
        self.assertEqual(repository.asset(ab, 99)['file_id'], 'telegram-file')

    def test_different_bytes_same_size_do_not_share_upload(self):
        da, aa = self.file(A)
        db, ab = self.file(B, 'b' * 64)
        uploads.operate(99, A, da, aa, 'claim')
        self.assertEqual(uploads.operate(99, B, db, ab, 'claim')['status'], 'claimed')

    def test_expired_preparation_reservation_can_move_but_stale_send_cannot(self):
        da, aa = self.file(A)
        db, ab = self.file(B)
        uploads.operate(99, A, da, aa, 'claim')
        database.execute('UPDATE LauncherTransfers SET expires_at=0')
        uploads.operate(99, B, db, ab, 'claim')
        with self.assertRaises(ValueError):
            uploads.operate(99, A, da, aa, 'sent')

    def test_heartbeat_renews_only_own_pre_send_reservation(self):
        da, aa = self.file(A)
        db, ab = self.file(B, 'b' * 64)
        uploads.operate(99, A, da, aa, 'claim')
        uploads.operate(99, B, db, ab, 'claim')
        database.execute('UPDATE LauncherTransfers SET expires_at=1')
        self.heartbeat(A)
        self.assertGreater(database.execute('SELECT expires_at FROM LauncherTransfers WHERE asset_id=?', (aa,)).fetchone()[0], time.time())
        self.assertEqual(database.execute('SELECT expires_at FROM LauncherTransfers WHERE asset_id=?', (ab,)).fetchone()[0], 1)

    def test_safe_repeat_requires_confirmation_and_cannot_release_other_pc(self):
        da, aa = self.file(A)
        uploads.operate(99, A, da, aa, 'claim')
        uploads.operate(99, A, da, aa, 'sent')
        with self.assertRaises(ValueError):
            uploads.reset(99, A, [aa], False)
        self.assertEqual(uploads.reset(99, B, [aa], True)['released'], 0)
        self.assertEqual(uploads.reset(99, A, [aa], True)['released'], 1)


class DeviceLocalTests(unittest.TestCase):
    def test_identity_is_stable_and_copied_settings_get_a_new_id(self):
        with patch.object(launcher_device, 'machine_key', return_value='one'):
            old = launcher_device.identity({'instance_id': A, 'name': 'Ноутбук'})
            self.assertEqual(launcher_device.identity(old), old)
        with patch.object(launcher_device, 'machine_key', return_value='two'):
            self.assertNotEqual(launcher_device.identity(old)['instance_id'], A)

    def test_full_content_hash_is_cached_but_changes_after_file_edit(self):
        with tempfile.TemporaryDirectory() as folder:
            file = Path(folder) / 'file.mp4'
            file.write_bytes(b'abcd')
            before = scan(folder)
            self.assertEqual(launcher_device.hash_manifest(before)['files'][0]['content_sha256'], hashlib.sha256(b'abcd').hexdigest())
            with patch('hashlib.file_digest', side_effect=AssertionError('Should use unchanged cache')):
                launcher_device.hash_manifest(before)
            file.write_bytes(b'changed-file')
            with self.assertRaises(ValueError):
                launcher_device.hash_manifest(before)
            self.assertEqual(launcher_device.hash_manifest(scan(folder))['files'][0]['content_sha256'], hashlib.sha256(b'changed-file').hexdigest())
            self.assertNotIn('content_sha256', before['files'][0])

    def test_profile_lock_prevents_two_windows_of_one_device(self):
        with tempfile.TemporaryDirectory() as folder:
            with queue_lock(folder):
                with self.assertRaises(ValueError):
                    with queue_lock(folder):
                        self.fail('Two launchers acquired the same profile')
            with queue_lock(folder):
                pass

    def test_old_server_rejected_before_modern_import(self):
        client = Client('https://example.com', 'a' * 40, instance_id=A)
        with patch.object(client, '_request', return_value={'protocol': 2}) as request:
            with self.assertRaisesRegex(ValueError, 'Сначала обновите'):
                client.request('import', {})
        request.assert_called_once_with('session', None)

    def test_modern_client_attaches_device_header(self):
        client = Client('https://example.com', 'a' * 40, instance_id=A)
        response = Mock()
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        response.read.return_value = b'{"capabilities":{"multi_launcher":1}}'
        client.opener.open = Mock(return_value=response)
        client.request('session')
        self.assertEqual(client.opener.open.call_args[0][0].get_header('X-kinotime-instance'), A)

    def test_server_sent_marker_resumes_without_local_journal_or_resending(self):
        import threading
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'file.mp4'
            path.write_bytes(b'abcd')
            m = scan(folder)
            draft = {'id': 'draft', 'body': {'items': [{'asset_id': 'asset', 'relative_path': 'file.mp4'}]}}
            waiting = {'draft_status': 'draft', 'files': [{'id': 'asset', 'status': 'waiting_telegram', 'error': None}]}
            ready = copy.deepcopy(waiting)
            ready['files'][0]['status'] = 'ready'
            client = Mock(instance_id=A)
            client.request.side_effect = [waiting, {'status': 'sent'}, ready]
            stop = Mock()
            stop.is_set.return_value = False
            stop.wait.return_value = False
            desktop = Mock()
            transfer(client, {'draft': draft}, m, desktop, stop, Mock())
            desktop.send_file.assert_not_called()
            self.assertEqual(json.loads((Path(folder) / '.kinotime-transfer.json').read_text())['asset'], 'confirmed')
            self.assertEqual(RetryingClient(client, threading.Event(), Mock()).instance_id, A)


class MultiMigrationTests(unittest.TestCase):
    def test_migration_preserves_draft_file_users_catalog_and_contest(self):
        from database.movies import add_new_movie
        database.open(':memory:')
        try:
            for index, migration in enumerate(MIGRATIONS[:MIGRATIONS.index(_multiple_launchers)], 1):
                with database.transaction():
                    with contextlib.redirect_stdout(io.StringIO()):
                        migration()
                    database.execute(f'PRAGMA user_version={index}')
            database.execute("INSERT INTO LauncherAgents VALUES (99,?,'{}','[]',CURRENT_TIMESTAMP)", (A,))
            database.execute("INSERT INTO Users(id) VALUES (123)")
            number = add_new_movie('Keep Movie', 'Keep poster', 'Keep description', movie_file='Keep file', movie_file_type='video')
            database.execute("INSERT INTO Contests(body,status,created_by,created_at) VALUES ('{}','active',99,1)")
            # Create old desktop data without calling the new repository against the old schema.
            database.execute("INSERT INTO WebAssets(id,admin_id,source,filename,size,file_type,status,file_id) VALUES ('keep',99,'desktop','movie.mp4',123,'video','ready','tg-keep')")
            database.execute("INSERT INTO WebDrafts(id,admin_id,body,status,import_key) VALUES ('draft',99,?,'draft','old-key')", (json.dumps({'title': 'Keep', 'items': [{'asset_id': 'keep'}]}),))
            with contextlib.redirect_stdout(io.StringIO()):
                migrate()
            self.assertEqual(repository.draft('draft', 99)['launcher_instance'], A)
            self.assertEqual(repository.asset('keep', 99)['file_id'], 'tg-keep')
            self.assertEqual(database.execute('SELECT id FROM Users').fetchone()[0], 123)
            self.assertEqual(database.execute('SELECT movie_title FROM Movies WHERE movie_number=?', (number,)).fetchone()[0], 'Keep Movie')
            self.assertEqual(database.execute('SELECT status FROM Contests').fetchone()[0], 'active')
            migrate()  # Idempotent startup.
        finally:
            database.close()


class MultiComputerAPITests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from aiohttp.test_utils import TestClient, TestServer
        from data import settings
        from webpanel.server import create_app
        database.open(':memory:')
        with contextlib.redirect_stdout(io.StringIO()):
            migrate()
        self.patches = [patch('webpanel.imports.is_admin', return_value=True),
                        patch('webpanel.uploads.Uploads.start'), patch('webpanel.uploads.Uploads.stop', new_callable=AsyncMock),
                        patch('services.tmdb.search', AsyncMock(return_value=[]))]
        for item in self.patches:
            item.start()
        config = SimpleNamespace(**{key: getattr(settings, key) for key in dir(settings) if key.isupper()})
        config.WEB_PUBLIC_URL = 'http://localhost'
        bot = SimpleNamespace(get_me=AsyncMock(return_value=SimpleNamespace(username='ExampleBot', full_name='Example')))
        self.client = TestClient(TestServer(create_app(bot, config)))
        await self.client.start_server()
        self.headers = {instance: {'Authorization': 'Bearer ' + imports.pair(99), 'X-KinoTime-Instance': instance} for instance in (A, B)}

    async def asyncTearDown(self):
        await self.client.close()
        for item in reversed(self.patches):
            item.stop()
        database.close()

    async def test_token_reuse_device_spoof_and_draft_access_are_blocked(self):
        response = await self.client.get('/api/desktop/session', headers=self.headers[A])
        self.assertEqual((await response.json())['capabilities']['multi_launcher'], 1)
        response = await self.client.get('/api/desktop/session', headers={**self.headers[A], 'X-KinoTime-Instance': B})
        self.assertEqual(response.status, 401)
        response = await self.client.post('/api/desktop/control/heartbeat', headers=self.headers[A], json={'instance_id': B, 'status': {}})
        self.assertEqual(response.status, 400)
        response = await self.client.post('/api/desktop/import', headers=self.headers[A], json=manifest())
        draft = (await response.json())['draft']
        response = await self.client.get('/api/desktop/import/' + draft['id'], headers=self.headers[B])
        self.assertEqual(response.status, 400)
        response = await self.client.post('/api/desktop/import/' + draft['id'] + '/reopen', headers=self.headers[B], json={'kind': 'rejected'})
        self.assertEqual(response.status, 400)

    async def test_upload_endpoints_reserve_mark_and_require_explicit_repeat(self):
        response = await self.client.post('/api/desktop/import', headers=self.headers[A], json=manifest())
        draft = (await response.json())['draft']
        asset = draft['body']['items'][0]['asset_id']
        endpoint = f'/api/desktop/import/{draft["id"]}/files/{asset}/transfer'
        response = await self.client.post(endpoint, headers=self.headers[A], json={'action': 'claim'})
        self.assertEqual((await response.json())['status'], 'claimed')
        response = await self.client.post(endpoint, headers=self.headers[A], json={'action': 'sent'})
        self.assertEqual((await response.json())['status'], 'sent')
        response = await self.client.post('/api/desktop/transfers/reset', headers=self.headers[A], json={'asset_ids': [asset]})
        self.assertEqual(response.status, 400)
        response = await self.client.post('/api/desktop/transfers/reset', headers=self.headers[A], json={'asset_ids': [asset], 'verified_not_sent': True})
        self.assertEqual((await response.json())['released'], 1)

    async def test_simultaneous_requests_for_same_bytes_allow_only_one_sender(self):
        import asyncio
        files = {}
        for instance in (A, B):
            response = await self.client.post('/api/desktop/import', headers=self.headers[instance], json=manifest())
            draft = (await response.json())['draft']
            files[instance] = f'/api/desktop/import/{draft["id"]}/files/{draft["body"]["items"][0]["asset_id"]}/transfer'
        responses = await asyncio.gather(*(self.client.post(files[i], headers=self.headers[i], json={'action': 'claim'}) for i in (A, B)))
        self.assertEqual(sorted(r.status for r in responses), [200, 400])
        self.assertEqual(database.execute('SELECT count(*) FROM LauncherTransfers').fetchone()[0], 1)

    async def test_legacy_launcher_cannot_bypass_reservations_after_upgrade(self):
        await self.client.post('/api/desktop/control/heartbeat', headers=self.headers[A],
            json={'instance_id': A, 'protocol': 3, 'status': {}, 'folders': []})
        response = await self.client.post('/api/desktop/import',
            headers={'Authorization': 'Bearer ' + imports.pair(99)}, json=manifest())
        self.assertEqual(response.status, 400)
        self.assertIn('Обновите все', (await response.json())['error'])


if __name__ == '__main__':
    unittest.main()
