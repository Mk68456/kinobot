from contextlib import closing
import io
import os
from pathlib import Path
import runpy
import sqlite3
import subprocess
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError

from deploy import bothost_supervisor as supervisor


TOKEN = '123456:FAKE_TOKEN_FOR_OFFLINE_TESTS'


class BothostTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def database(self, path, title='Existing'):
        path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(path)) as db:
            db.execute('CREATE TABLE Movies(title TEXT)')
            db.execute('INSERT INTO Movies VALUES (?)', (title,))
            db.commit()

    def test_existing_root_database_is_copied_consistently_and_never_overwritten(self):
        source = self.root / 'bot.sqlite'
        self.database(source)
        data = self.root / 'persistent'
        env = supervisor.configure_storage({'DATA_DIR': str(data)}, self.root)
        target = Path(env['DATABASE_PATH'])
        with closing(sqlite3.connect(target)) as db:
            self.assertEqual(db.execute('SELECT title FROM Movies').fetchone()[0], 'Existing')
        with closing(sqlite3.connect(source)) as db:
            db.execute("UPDATE Movies SET title='Changed source'")
            db.commit()
        supervisor.configure_storage({'DATA_DIR': str(data)}, self.root)
        with closing(sqlite3.connect(target)) as db:
            self.assertEqual(db.execute('SELECT title FROM Movies').fetchone()[0], 'Existing')

    def test_explicit_database_and_missing_database_fail_closed(self):
        path = self.root / 'persistent' / 'actual.sqlite'
        self.database(path)
        env = supervisor.configure_storage({'DATA_DIR': str(path.parent), 'DATABASE_PATH': str(path)}, self.root)
        self.assertEqual(Path(env['DATABASE_PATH']), path)
        for values in [{'DATA_DIR': str(self.root / 'empty')},
                       {'DATA_DIR': str(self.root / 'empty'), 'DATABASE_PATH': str(self.root / 'missing.sqlite')}]:
            with self.assertRaises(supervisor.StartupError):
                supervisor.configure_storage(values, self.root)
        env = supervisor.configure_storage({'DATA_DIR': str(self.root / 'new'),
                                             'BOTHOST_ALLOW_EMPTY_DB': '1'}, self.root)
        self.assertFalse(Path(env['DATABASE_PATH']).exists())

    def test_corrupt_database_is_not_silently_replaced(self):
        data = self.root / 'persistent'
        data.mkdir()
        (data / 'bot.sqlite').write_bytes(b'not sqlite')
        with self.assertRaises(sqlite3.DatabaseError):
            supervisor.configure_storage({'DATA_DIR': str(data)}, self.root)
        self.assertEqual((data / 'bot.sqlite').read_bytes(), b'not sqlite')

    def test_cloud_migration_requires_opt_in_and_runs_once(self):
        call = Mock(return_value=True)
        with self.assertRaises(supervisor.StartupError):
            supervisor.migrate_cloud(self.root, TOKEN, False, call)
        call.assert_not_called()
        self.assertTrue(supervisor.migrate_cloud(self.root, TOKEN, True, call))
        self.assertFalse(supervisor.migrate_cloud(self.root, TOKEN, True, call))
        call.assert_called_once_with('https://api.telegram.org', TOKEN, 'logOut')
        self.assertTrue(supervisor.marker_ready(self.root, TOKEN))
        self.assertNotIn(TOKEN, (self.root / 'kinotime-migration.json').read_text())
        self.assertFalse(supervisor.marker_ready(self.root, '654321:ANOTHER_TOKEN'))

    def test_failed_logout_does_not_write_marker(self):
        with self.assertRaises(supervisor.StartupError):
            supervisor.migrate_cloud(self.root, TOKEN, True, Mock(return_value=False))
        self.assertFalse(supervisor.marker_ready(self.root, TOKEN))

    def test_copied_marker_without_instance_is_not_accepted(self):
        supervisor.save_marker(self.root, TOKEN)
        (self.root / 'kinotime-instance.json').unlink()
        self.assertFalse(supervisor.marker_ready(self.root, TOKEN))
        call = Mock(return_value=True)
        with self.assertRaises(supervisor.StartupError):
            supervisor.migrate_cloud(self.root, TOKEN, True, call)
        call.assert_not_called()

    def test_web_port_from_platform_and_credentials_validation(self):
        values = {'DATA_DIR': str(self.root / 'new'), 'BOTHOST_ALLOW_EMPTY_DB': '1',
                  'WEB_ENABLED': '1', 'PORT': '8000'}
        env = supervisor.configure_storage(values, self.root)
        self.assertEqual(env['WEB_PORT'], '8000')
        self.assertEqual(env['WEB_HOST'], '0.0.0.0')
        supervisor.validate_credentials({'TELEGRAM_API_ID': '123', 'TELEGRAM_API_HASH': 'a' * 32})
        for value in ['', '2147483648', 'abc']:
            with self.assertRaises(supervisor.StartupError):
                supervisor.validate_credentials({'TELEGRAM_API_ID': value, 'TELEGRAM_API_HASH': 'a' * 32})

    def test_api_404_readiness_and_dead_process(self):
        error = HTTPError('http://127.0.0.1:8081/', 404, 'Not Found', {},
                          io.BytesIO(b'{"ok":false,"error_code":404}'))
        opener = SimpleNamespace(open=Mock(side_effect=error))
        supervisor.wait_api(SimpleNamespace(poll=lambda: None), threading.Event(), opener=opener)
        with self.assertRaises(supervisor.StartupError):
            supervisor.wait_api(SimpleNamespace(poll=lambda: 1), threading.Event(), opener=opener)

    def test_network_error_never_exposes_token(self):
        opener = SimpleNamespace(open=Mock(side_effect=OSError('URL contains ' + TOKEN)))
        with patch.object(supervisor, 'build_opener', return_value=opener):
            with self.assertRaises(supervisor.StartupError) as result:
                supervisor.telegram_json('http://127.0.0.1:8081', TOKEN, 'getMe')
        self.assertNotIn(TOKEN, str(result.exception))

    def test_stop_escalates_after_timeout(self):
        process = Mock()
        process.poll.return_value = None
        process.wait.side_effect = [subprocess.TimeoutExpired('child', 1), 0]
        supervisor.stop_process(process, 1)
        process.terminate.assert_called_once()
        process.kill.assert_called_once()

    def test_one_failed_child_ends_supervision(self):
        stopped = Mock()
        stopped.wait.return_value = False
        api = SimpleNamespace(poll=lambda: 1)
        bot = SimpleNamespace(poll=lambda: None)
        with patch('builtins.print'):
            self.assertEqual(supervisor.monitor(api, bot, stopped), 1)

    def test_startup_order_and_shutdown_both_children(self):
        directory = self.root / 'persistent'
        path = directory / 'bot.sqlite'
        self.database(path)
        api, bot = Mock(), Mock()
        api.poll.return_value = None
        env = {'KINOTIME_APP_ROOT': str(self.root), 'DATA_DIR': str(directory),
               'DATABASE_PATH': str(path), 'BOTHOST_LOCAL_API': '1', 'BOTHOST_MIGRATE_CLOUD': '1',
               'TELEGRAM_API_ID': '123', 'TELEGRAM_API_HASH': 'a' * 32}
        events = []
        def spawn(command, **kwargs):
            if command[0] == '/usr/local/bin/telegram-bot-api':
                events.append('start_api')
                self.assertIn('--http-ip-address=127.0.0.1', command)
                self.assertFalse(any('api-hash=' in value for value in command))
                return api
            events.append('start_bot')
            self.assertEqual(kwargs['env']['BOT_API_BASE'], 'http://127.0.0.1:8081')
            return bot
        with (patch.dict(os.environ, env, clear=True), patch.object(supervisor, 'prepare_permissions'),
              patch.object(supervisor, 'read_token', return_value=TOKEN),
              patch.object(supervisor.signal, 'signal'), patch.object(supervisor, 'marker_ready', return_value=False),
              patch.object(supervisor.subprocess, 'Popen', side_effect=spawn),
              patch.object(supervisor, 'wait_api', side_effect=lambda *args: events.append('api_ready')),
              patch.object(supervisor, 'migrate_cloud', side_effect=lambda *args: events.append('logout')),
              patch.object(supervisor, 'telegram_json', side_effect=lambda *args: (events.append('get_me') or {'id': 123456})),
              patch.object(supervisor, 'monitor', return_value=0),
              patch.object(supervisor, 'stop_process') as stop, patch('builtins.print')):
            self.assertEqual(supervisor.main(['python', 'app.py']), 0)
        self.assertEqual(events, ['start_api', 'api_ready', 'logout', 'get_me', 'start_bot'])
        self.assertEqual(stop.call_args_list[0].args, (bot, 40))
        self.assertEqual(stop.call_args_list[1].args, (api, 15))

    def test_environment_configuration_and_no_secrets_in_error(self):
        with patch.dict(os.environ, {'BOT_TOKEN': TOKEN, 'ADMIN_IDS': '99,100'}, clear=True):
            data = runpy.run_path('deploy/bothost_config.py')
            self.assertEqual(data['ADMIN'], [99, 100])
        with patch.dict(os.environ, {'BOT_TOKEN': TOKEN}, clear=True):
            with self.assertRaises(ValueError) as error:
                runpy.run_path('deploy/bothost_config.py')
            self.assertNotIn(TOKEN, str(error.exception))

    def test_image_paths_are_outside_bothost_source_mount(self):
        source = Path('deploy/Dockerfile.bothost').read_text()
        self.assertIn('WORKDIR /opt/kinotime', source)
        self.assertIn('BOTHOST_LOCAL_API=0', source)
        self.assertIn('DATA_DIR=/app/data', source)
        self.assertIn('/usr/local/bin/kinotime-bothost.py', source)
        self.assertIn('/opt/kinotime/app.py', source)
        self.assertNotIn('EXPOSE 8081', source)


if __name__ == '__main__':
    unittest.main()
