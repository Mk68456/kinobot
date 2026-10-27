import contextlib
import io
import json
import os
from pathlib import Path
import tempfile
import threading
import tkinter as tk
import unittest
from unittest.mock import Mock, patch

from database.connection import database
from database.migrations import migrate
from webpanel import launcher_control as control
from tools import remote_torrents, torrent_panel, jackett_sources_ui
from tools.torrent_queue import TorrentQueue
from tests.test_release_search import candidate, MAGNET


class RemoteTorrentProtocolTests(unittest.TestCase):
    def setUp(self):
        database.open(':memory:')
        with contextlib.redirect_stdout(io.StringIO()):
            migrate()
        self.status = {'busy': True, 'operation_busy': False, 'capabilities': {'torrent_control': 1},
            'torrents': {'busy': False, 'search': {'state': 'done', 'results': [{'id': 'result-a'}]},
                         'jobs': [{'id': 'job-a', 'status': 'paused', 'files': [{'index': 0}, {'index': 2}]}]}}
        self.beat()

    def tearDown(self):
        database.close()

    def beat(self, instance='computer-a', status=None):
        return control.heartbeat(99, {'instance_id': instance, 'protocol': 3, 'status': self.status if status is None else status})

    def send(self, action, payload, instance='computer-a'):
        return control.enqueue(99, action, payload, instance)

    def test_start_while_projects_render_passes_exact_selection_to_computer(self):
        self.send('torrent_start', {'job_id': 'job-a', 'file_ids': [2], 'qualities': ['720p', '720p'], 'password': 'ignore'})
        command = self.beat()['command']
        self.assertEqual(command['payload'], {'job_id': 'job-a', 'file_ids': [2], 'qualities': ['720p']})
        self.assertEqual(command['instance_id'], 'computer-a')

    def test_rejects_unknown_files_projects_and_raw_urls(self):
        cases = [('torrent_start', {'job_id': 'other', 'file_ids': [0], 'qualities': ['720p']}),
                 ('torrent_start', {'job_id': 'job-a', 'file_ids': [True], 'qualities': ['720p']}),
                 ('torrent_start', {'job_id': 'job-a', 'file_ids': [99], 'qualities': ['720p']}),
                 ('torrent_start', {'job_id': 'job-a', 'file_ids': [0, 0], 'qualities': ['720p']}),
                 ('torrent_add', {'magnet': 'C:/secret.torrent'}),
                 ('torrent_add', {'magnet': 'http://localhost/private'}),
                 ('torrent_add', {'magnet': MAGNET+'\n'+MAGNET}),
                 ('torrent_add', {'result_id': 'other'}),
                 ('torrent_search', {'query': 'a'}),
                 ('torrent_search', {'query': 'Example', 'seasons': '100'})]
        for action, payload in cases:
            with self.subTest(action=action, payload=payload), self.assertRaises(ValueError):
                self.send(action, payload)

    def test_result_ids_are_scoped_to_computer_and_admin(self):
        self.beat('computer-b', {'capabilities': {'torrent_control': 1}, 'torrents': {'search': {'results': []}}})
        with self.assertRaises(ValueError):
            self.send('torrent_add', {'result_id': 'result-a'}, 'computer-b')
        with self.assertRaises(ValueError):
            control.enqueue(88, 'torrent_add', {'result_id': 'result-a'}, 'computer-a')
        self.send('torrent_add', {'result_id': 'result-a', 'seasons': '1-3', 'source': 'ignore'})
        self.assertEqual(self.beat()['command']['payload'], {'result_id': 'result-a', 'seasons': '1-3'})

    def test_active_download_blocks_mutations_but_allows_pause(self):
        self.status['torrents']['busy'] = True
        self.beat()
        with self.assertRaises(ValueError):
            self.send('torrent_add', {'magnet': MAGNET})
        self.send('torrent_pause', {})
        self.assertEqual(self.beat()['command']['action'], 'torrent_pause')

    def test_old_or_offline_launcher_rejects_commands(self):
        self.status['capabilities'] = {}
        self.beat()
        with self.assertRaisesRegex(ValueError, 'Обновите'):
            self.send('torrent_search', {'query': 'Example'})
        database.execute("UPDATE LauncherAgents SET last_seen='2000-01-01 00:00:00'")
        with self.assertRaisesRegex(ValueError, 'не в сети'):
            self.send('torrent_pause', {})


class RemoteSearchTests(unittest.TestCase):
    def test_private_results_are_opaque_and_materialize_only_when_selected(self):
        row = candidate()
        row.update(provider='jackett', source_available=True, download_path='/dl/private?apikey=SECRET', source='')
        client = Mock()
        client.search.return_value = ([row], [])
        client.materialize.return_value = {**row, 'source': 'C:/local/source.torrent'}
        search = remote_torrents.Search()
        with patch.object(remote_torrents, 'load_sources', return_value={'rutor': False, 'indexers': [{'id': 'x'}], 'selected': ['x']}), \
             patch.object(remote_torrents, 'connection', return_value=client):
            search.run('Пример', 0)
        public = search.snapshot()
        self.assertEqual(public['state'], 'done')
        self.assertEqual(len(public['results']), 1)
        self.assertNotIn('SECRET', json.dumps(public))
        self.assertNotIn('download_path', json.dumps(public))
        client.materialize.assert_not_called()
        self.assertEqual(search.resolve(public['results'][0]['id'])['source'], 'C:/local/source.torrent')
        client.materialize.assert_called_once()
        with self.assertRaises(ValueError):
            search.resolve('expired')

    def test_failure_is_reported_and_urls_redacted(self):
        search = remote_torrents.Search()
        with patch.object(remote_torrents, 'load_sources', side_effect=ValueError('Failed https://host/?apikey=SECRET')):
            search.run('Example', 0)
        self.assertEqual(search.snapshot()['state'], 'error')
        self.assertNotIn('SECRET', json.dumps(search.snapshot()))

    def test_magnet_and_job_snapshot_exclude_paths_and_secrets(self):
        self.assertEqual(remote_torrents.validate_magnet(MAGNET), MAGNET)
        for value in ('C:/video.torrent', 'https://host/torrent', MAGNET+'\n'):
            with self.assertRaises(ValueError):
                remote_torrents.validate_magnet(value)
        queue = Mock(jobs=[{'id': 'job', 'status': 'paused', 'title': 'Movie', 'source': 'SECRET',
                          'files': [{'index': 0, 'name': 'video.mkv', 'size': 100},
                                    {'index': 1, 'name': 'setup.exe', 'size': 5}]}])
        result = remote_torrents.jobs_snapshot(queue)
        self.assertEqual(len(result[0]['files']), 1)
        self.assertNotIn('SECRET', json.dumps(result))

    def test_custom_jackett_key_shared_in_memory_only(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, KINOTIME_STATE_DIR=folder):
            options = {'rutor': False, 'url': 'http://127.0.0.1:9117', 'indexers': [], 'selected': [], 'api_key': 'SECRET'}
            jackett_sources_ui.save_sources(options)
            self.assertEqual(jackett_sources_ui.load_sources(session=True)['api_key'], 'SECRET')
            self.assertEqual(jackett_sources_ui.load_sources()['api_key'], '')
            self.assertNotIn('SECRET', (Path(folder)/'search-sources.json').read_text())


class RemoteTorrentBridgeTests(unittest.TestCase):
    def test_add_inspect_download_and_handoff_share_desktop_queue(self):
        root = tk.Tk()
        root.withdraw()
        self.addCleanup(root.destroy)
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, KINOTIME_STATE_DIR=str(Path(folder)/'state'), KINOTIME_DOWNLOAD_DIR=str(Path(folder)/'downloads')):
            def add(queue, client, source, stop, report):
                job = {'id': 'job-a', 'title': 'Пример', 'status': 'choose_files', 'selected': [],
                       'files': [{'index': 0, 'name': 'Example.mkv', 'size': 100}], 'hash': 'a'*40}
                queue.jobs.append(job);queue.save();return job
            bridge, stop, prepare = {}, threading.Event(), Mock(return_value=Mock())
            with patch.object(torrent_panel, 'QBitClient'), patch.object(TorrentQueue, 'add', autospec=True, side_effect=add) as added, \
                 patch.object(TorrentQueue, 'run') as run:
                def background(fn):
                    fn();return True
                shutdown = torrent_panel.mount(tk.Frame(root), root, background, Mock(), stop, prepare,
                    Mock(), lambda: False, {'Фильмы': 'movies'}, remote=bridge)
                try:
                    bridge['command']('torrent_add', {'magnet': MAGNET, 'seasons': '0'})
                    state = bridge['snapshot']()
                    self.assertEqual(len(state['jobs']), 1)
                    self.assertEqual(state['jobs'][0]['selected'], [0])
                    run.assert_not_called()
                    bridge['command']('torrent_start', {'job_id': 'job-a', 'file_ids': [0], 'qualities': ['360p']})
                    prepare.assert_called_with(['360p'])
                    run.assert_called_once()
                    self.assertEqual(run.call_args.kwargs['job_ids'], {'job-a'})
                    bridge['command']('torrent_add', {'magnet': MAGNET})
                    self.assertTrue(bridge['snapshot']()['error'])
                    added.assert_called_once()
                    # Pausing an idle torrent queue must not stop another launcher operation.
                    bridge['command']('torrent_pause', {})
                    self.assertFalse(stop.is_set())
                finally:
                    shutdown()


if __name__ == '__main__':
    unittest.main()
