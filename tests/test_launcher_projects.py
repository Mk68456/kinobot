"""Concurrency, fairness, durable resume and real project media regressions."""
import copy
import gc
import shutil
import sqlite3
import subprocess
import tempfile
import threading
import time
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import Mock, patch

from tools import desktop_launcher as launcher, disk_space, launcher_projects as projects, video_hardware


def wait_for(predicate, timeout=6):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(.01)
    raise AssertionError('Timed out waiting for project state')


def fake_project(name, tasks=3):
    return {'id': name, 'title': name, 'folder': name, 'output': name, 'mode': 'single',
            'section': 'movies', 'state': 'queued', 'message': '', 'priority': 0, 'turn': 0,
            'created': time.time(), 'cards': {}, 'tasks': [
                {'id': str(i), 'state': 'pending', 'source': f'{name}/{i}.mkv', 'count': 1,
                 'files': [], 'group': '0', 'title': name} for i in range(tasks)]}


class SchedulerTests(unittest.TestCase):
    def setUp(self):
        # Dispose interpreters from earlier UI fixtures on their owning main thread,
        # before a rendering worker can trigger cyclic garbage collection.
        gc.collect()
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / 'projects.sqlite'
        self.managers = []

    def manager(self, runner):
        manager = projects.ProjectManager(self.path, runner)
        self.managers.append(manager)
        return manager

    def tearDown(self):
        for manager in self.managers:
            manager.close()
            self.assertTrue(manager.wait_closed())
        self.temp.cleanup()

    def test_two_projects_really_overlap_and_third_waits(self):
        entered = []
        release = threading.Event()
        lock = threading.Lock()
        def runner(project, task, stop, report):
            with lock:
                entered.append(project['id'])
            while not release.wait(.01):
                if stop.is_set():
                    raise InterruptedError()
            return [{}]
        manager = self.manager(runner)
        for name in ('A', 'B', 'C'):
            manager.add(fake_project(name, 1))
        wait_for(lambda: len(entered) == 2)
        self.assertEqual(set(entered), {'A', 'B'})
        time.sleep(.05)
        self.assertEqual(len(entered), 2)
        release.set()
        wait_for(lambda: not manager.busy)
        self.assertEqual(entered[-1], 'C')
        self.assertEqual(set(entered), {'A', 'B', 'C'})
        self.assertTrue(all(p['state'] == 'prepared' for p in manager.snapshots()))

    def test_one_slot_rotates_between_projects_instead_of_draining_large_series(self):
        entered, release = [], threading.Event()
        def runner(project, task, stop, report):
            entered.append(project['id'])
            if len(entered) == 1:
                release.wait(3)
            return [{}]
        manager = self.manager(runner)
        manager.set_parallelism(1)
        manager.add(fake_project('A', 3))
        wait_for(lambda: len(entered) == 1)
        manager.add(fake_project('B', 2))
        release.set()
        wait_for(lambda: not manager.busy)
        self.assertEqual(entered, ['A', 'B', 'A', 'B', 'A'])

    def test_pause_is_independent_and_completed_units_do_not_run_again(self):
        entered = []
        def runner(project, task, stop, report):
            entered.append((project['id'], task['id']))
            if project['id'] == 'A' and task['id'] == '1' and entered.count(('A', '1')) == 1:
                stop.wait(3)
                raise InterruptedError('paused')
            return [{}]
        manager = self.manager(runner)
        manager.add(fake_project('A', 3))
        wait_for(lambda: ('A', '1') in entered)
        manager.add(fake_project('B', 2))
        manager.pause('A')
        wait_for(lambda: manager.get('A')['state'] == 'paused' and manager.get('B')['state'] == 'prepared')
        manager.resume('A')
        wait_for(lambda: not manager.busy)
        self.assertEqual(entered.count(('A', '0')), 1)
        self.assertEqual(entered.count(('A', '1')), 2)
        self.assertEqual(manager.get('A')['state'], 'prepared')

    def test_failed_project_does_not_stop_other_projects(self):
        def runner(project, task, stop, report):
            if project['id'] == 'A':
                raise ValueError('bad source')
            return [{}]
        manager = self.manager(runner)
        manager.add(fake_project('A'))
        manager.add(fake_project('B'))
        wait_for(lambda: not manager.busy)
        self.assertEqual(manager.get('A')['state'], 'error')
        self.assertEqual(manager.get('B')['state'], 'prepared')

    def test_restart_restores_on_pause_without_starting_workers(self):
        project = fake_project('A')
        project['state'] = 'running'
        project['tasks'][0].update(state='done', files=[{'path': 'ready.mp4'}])
        project['tasks'][1]['state'] = 'running'
        runner = Mock(return_value=[{}])
        manager = self.manager(runner)
        manager._save(project)  # Simulate a crash with a durable active task.
        restored = self.manager(runner)
        self.assertFalse(restored.busy)
        runner.assert_not_called()
        self.assertEqual(restored.get('A')['state'], 'paused')
        self.assertEqual(restored.get('A')['tasks'][1]['state'], 'pending')
        restored.resume('A')
        wait_for(lambda: not restored.busy)
        self.assertEqual(runner.call_count, 2)

    def test_close_stops_all_workers_and_keeps_pending_work(self):
        def runner(project, task, stop, report):
            stop.wait(3)
            raise InterruptedError('closed')
        manager = self.manager(runner)
        manager.add(fake_project('A'))
        manager.add(fake_project('B'))
        wait_for(lambda: all(p['state'] == 'running' for p in manager.snapshots()))
        manager.close()
        self.assertTrue(manager.wait_closed())
        self.assertTrue(all(p['state'] == 'paused' for p in manager.snapshots()))
        self.assertTrue(all(t['state'] == 'pending' for p in manager.snapshots(True) for t in p['tasks']))

    def test_duplicate_sources_rejected_even_if_original_is_paused(self):
        manager = self.manager(Mock(return_value=[{}]))
        manager.add(fake_project('A', 1))
        wait_for(lambda: not manager.busy)
        duplicate = fake_project('B', 1)
        duplicate['tasks'][0]['source'] = 'a/0.MKV'
        with self.assertRaisesRegex(ValueError, 'уже есть'):
            manager.add(duplicate)

    def test_limit_saved_and_cannot_change_during_render(self):
        def runner(project, task, stop, report):
            stop.wait(3)
            raise InterruptedError()
        manager = self.manager(runner)
        manager.set_parallelism(3)
        self.assertEqual(self.manager(Mock()).parallelism, 3)
        manager.add(fake_project('A'))
        with self.assertRaises(ValueError):
            manager.set_parallelism(1)
        with self.assertRaises(ValueError):
            manager.set_parallelism(5)

    def test_priority_takes_next_turn_and_can_be_removed(self):
        order, release = [], threading.Event()
        def runner(project, task, stop, report):
            order.append(project['id'])
            if len(order) == 1:
                release.wait(3)
            return [{}]
        manager = self.manager(runner)
        manager.set_parallelism(1)
        manager.add(fake_project('A', 2))
        wait_for(lambda: bool(order))
        manager.add(fake_project('B', 1))
        manager.add(fake_project('C', 1))
        manager.prioritize('C')
        release.set()
        wait_for(lambda: not manager.busy)
        self.assertEqual(order[:2], ['A', 'C'])
        manager.prioritize('C')
        self.assertEqual(manager.get('C')['priority'], 0)

    def test_registration_binding_and_partial_card_ids_survive_restart(self):
        manager = self.manager(Mock(return_value=[{}]))
        manager.add(fake_project('A', 1))
        wait_for(lambda: not manager.busy)
        manager.bind('A', 'https://panel.example', 'instance-123')
        manager.phase('A', 'registering', card=('0', 'draft1'))
        restored = self.manager(Mock())
        self.assertEqual(restored.get('A')['cards'], {'0': 'draft1'})
        with self.assertRaises(ValueError):
            restored.bind('A', 'https://other.example', 'instance-123')
        with closing(sqlite3.connect(self.path)) as db:
            self.assertNotIn('token', db.execute('SELECT body FROM projects').fetchone()[0])

    def test_disk_journal_failure_does_not_launch_unjournalled_work(self):
        manager = self.manager(Mock(return_value=[{}]))
        original_save = manager._save
        def save(project):
            if project['state'] == 'running':
                raise sqlite3.OperationalError('disk full')
            original_save(project)
        with patch.object(manager, '_save', side_effect=save):
            manager.add(fake_project('A', 1))
            wait_for(lambda: manager.get('A')['state'] == 'error')
        manager.runner.assert_not_called()


class ResourceTests(unittest.TestCase):
    def test_cpu_budget_is_split_only_in_worker_thread(self):
        with video_hardware.worker_budget(2):
            self.assertEqual(video_hardware.cpu_threads('balanced', 16), 4)
            result = []
            thread = threading.Thread(target=lambda: result.append(video_hardware.cpu_threads('balanced', 16)))
            thread.start()
            thread.join()
            self.assertEqual(result, [8])
        self.assertEqual(video_hardware.cpu_threads('balanced', 16), 8)

    def test_disk_reservations_include_other_workers_and_release_on_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            entered, release = threading.Event(), threading.Event()
            def worker():
                try:
                    with disk_space.reservation(directory, 4096, threading.Event(), Mock()):
                        entered.set()
                        release.wait(3)
                        raise ValueError()
                except ValueError:
                    pass
            thread = threading.Thread(target=worker)
            thread.start()
            self.assertTrue(entered.wait(3))
            self.assertEqual(disk_space.reserved(directory), 4096)
            release.set()
            thread.join()
            self.assertEqual(disk_space.reserved(directory), 0)


class ProjectUITests(unittest.TestCase):
    def test_add_while_rendering_and_pause_selected_project(self):
        import tkinter as tk
        from tkinter import ttk
        from tools import launcher_projects_ui
        root = tk.Tk()
        root.withdraw()
        self.addCleanup(gc.collect)
        errors = []
        root.report_callback_exception = lambda *args: errors.append(args[1])
        def runner(project, task, stop, report):
            stop.wait(5)
            raise InterruptedError('paused')
        with tempfile.TemporaryDirectory() as directory:
            manager = projects.ProjectManager(Path(directory) / 'projects.sqlite', runner)
            try:
                frame = ttk.Frame(root)
                frame.pack(fill='both', expand=True)
                calls = []
                def background(fn, **options):
                    calls.append(options)
                    fn()
                    return True
                add = launcher_projects_ui.mount(frame, root, manager, background, Mock(), threading.Event(),
                    lambda: ('source', ['source'], 'single', 'movies'), Mock(), Mock(), lambda: False)
                planned = []
                for name in ('A', 'B'):
                    project = fake_project(name, 1)
                    project['tasks'][0].update(entry={'path': name + '.mkv'},
                                               plan={'qualities': ['source'], 'audio': []})
                    planned.append(project)
                with patch.object(launcher_projects_ui, 'build_project', side_effect=planned):
                    add()
                    wait_for(lambda: manager.get('A')['state'] == 'running')
                    add()
                wait_for(lambda: manager.get('B')['state'] == 'running')
                deadline = time.monotonic() + 2
                while time.monotonic() < deadline:
                    root.update()
                    if any(w.winfo_class() == 'Treeview' and len(w.get_children()) == 2 for w in frame.winfo_children()):
                        break
                    time.sleep(.02)
                tree = next(w for w in frame.winfo_children() if w.winfo_class() == 'Treeview')
                self.assertEqual(set(tree.get_children()), {'A', 'B'})
                tree.selection_set('A')
                actions = [w for row in frame.winfo_children() for w in row.winfo_children() if 'text' in w.keys()]
                next(w for w in actions if w.cget('text') == 'Пауза проекта').invoke()
                wait_for(lambda: manager.get('A')['state'] == 'paused')
                self.assertEqual(manager.get('B')['state'], 'running')
                self.assertTrue(all(c['allow_projects'] for c in calls))
                self.assertEqual(errors, [])
                root.update_idletasks()
                self.assertLessEqual(frame.winfo_reqwidth(), 970)
            finally:
                manager.close()
                manager.wait_closed()
                root.destroy()


class ProjectTransferTests(unittest.TestCase):
    # Reuse the isolated manager fixture without inheriting scheduler test methods.
    setUp = SchedulerTests.setUp
    tearDown = SchedulerTests.tearDown
    manager = SchedulerTests.manager
    def prepared(self, groups=1):
        manager = self.manager(Mock(return_value=[{'path': 'source/voice.mp4', 'size': 12, 'mtime': '1'}]))
        project = fake_project('A', groups)
        for index, task in enumerate(project['tasks']):
            task['group'] = str(index)
        manager.add(project)
        wait_for(lambda: not manager.busy)
        return manager

    def client(self):
        client = Mock(url='https://panel.example', instance_id='computer-123')
        client.ready = False
        client.fail_import = False
        def request(path, body=None):
            if path == 'session':
                return {'bot_username': 'TestBot', 'bot_name': 'Test Bot'}
            if path == 'import':
                if client.fail_import:
                    raise ValueError('registration unavailable')
                group = Path(body['root']).name
                return {'draft': {'id': 'draft-' + group, 'status': 'draft', 'body': {'items': [
                    {'asset_id': 'asset-' + group, 'relative_path': 'source/voice.mp4', 'name': 'voice'}]}}}
            if path.startswith('import/draft-'):
                group = path.removeprefix('import/draft-')
                return {'draft_status': 'draft', 'files': [
                    {'id': 'asset-' + group, 'status': 'ready' if client.ready else 'waiting_telegram'}]}
            raise AssertionError('unexpected API request ' + path)
        client.request.side_effect = request
        return client

    def test_registration_is_durable_and_repeating_it_does_not_reimport_files(self):
        manager, client = self.prepared(), self.client()
        for _ in range(2):
            projects.register_or_send(manager, 'A', client, threading.Event(), Mock())
        self.assertEqual([call.args[0] for call in client.request.call_args_list].count('import'), 1)
        self.assertEqual(manager.get('A')['cards'], {'0': 'draft-0'})
        self.assertEqual(manager.get('A')['card_data']['0']['draft']['body']['items'][0]['asset_id'], 'asset-0')
        self.assertEqual(manager.get('A')['state'], 'cards_ready')

    def test_send_resumes_from_saved_ids_without_hashing_or_reimporting_reclaimed_files(self):
        manager, client = self.prepared(), self.client()
        projects.register_or_send(manager, 'A', client, threading.Event(), Mock())
        client.request.reset_mock()
        def send(*args):
            client.ready = True
            return Mock()
        with patch.object(launcher, 'send_with_recovery', side_effect=send) as sender:
            projects.register_or_send(manager, 'A', client, threading.Event(), Mock(), Mock(), sending=True)
        sender.assert_called_once()
        self.assertNotIn('import', [call.args[0] for call in client.request.call_args_list])
        self.assertEqual(manager.get('A')['state'], 'complete')

    def test_unconfirmed_send_pauses_instead_of_reporting_success(self):
        manager, client = self.prepared(), self.client()
        projects.register_or_send(manager, 'A', client, threading.Event(), Mock())
        with patch.object(launcher, 'send_with_recovery', return_value=Mock()):
            with self.assertRaisesRegex(ValueError, 'подтвердил не все'):
                projects.register_or_send(manager, 'A', client, threading.Event(), Mock(), Mock(), sending=True)
        self.assertEqual(manager.get('A')['state'], 'send_paused')

    def test_changed_card_asset_set_stops_before_touching_telegram(self):
        manager, client = self.prepared(), self.client()
        projects.register_or_send(manager, 'A', client, threading.Event(), Mock())
        original = client.request.side_effect
        def request(path, body=None):
            result = original(path, body)
            if path.startswith('import/'):
                result['files'][0]['id'] = 'different'
            return result
        client.request.side_effect = request
        with patch.object(launcher, 'send_with_recovery') as sender:
            with self.assertRaisesRegex(ValueError, 'Состав карточки'):
                projects.register_or_send(manager, 'A', client, threading.Event(), Mock(), Mock(), sending=True)
            sender.assert_not_called()

    def test_partial_registration_resumes_only_missing_card(self):
        manager, client = self.prepared(2), self.client()
        original = client.request.side_effect
        calls = []
        def request(path, body=None):
            if path == 'import':
                calls.append(Path(body['root']).name)
                if calls == ['0', '1']:
                    raise ValueError('network failure')
            return original(path, body)
        client.request.side_effect = request
        with self.assertRaises(ValueError):
            projects.register_or_send(manager, 'A', client, threading.Event(), Mock())
        self.assertEqual(manager.get('A')['cards'], {'0': 'draft-0'})
        projects.register_or_send(manager, 'A', client, threading.Event(), Mock())
        self.assertEqual(calls, ['0', '1', '1'])
        self.assertEqual(manager.get('A')['state'], 'cards_ready')

    def test_changed_account_is_rejected_before_registration_or_send(self):
        manager, client = self.prepared(), self.client()
        projects.register_or_send(manager, 'A', client, threading.Event(), Mock())
        original = client.request.side_effect
        def request(path, body=None):
            if path == 'session':
                return {'admin_id': 999, 'bot_username': 'OtherBot', 'bot_name': 'Other Bot'}
            return original(path, body)
        client.request.side_effect = request
        with patch.object(launcher, 'send_with_recovery') as sender:
            with self.assertRaisesRegex(ValueError, 'другому администратору'):
                projects.register_or_send(manager, 'A', client, threading.Event(), Mock(), Mock(), sending=True)
            sender.assert_not_called()


@unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe'), 'FFmpeg required')
class RealProjectTests(unittest.TestCase):
    def setUp(self):
        gc.collect()

    def test_two_series_render_with_frozen_choices_and_resume_without_encoding(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in ('Сопрано', 'Другой сериал'):
                folder = root / name
                folder.mkdir()
                for episode in ('S01E01', 'S01E02'):
                    subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', 'color=size=854x480:rate=5',
                                    '-f', 'lavfi', '-i', 'sine=frequency=440', '-t', '0.3', '-c:v', 'libx264',
                                    '-map', '0:v', '-map', '1:a', '-map', '1:a',
                                    '-preset', 'ultrafast', '-pix_fmt', 'yuv420p', '-c:a', 'aac',
                                    str(folder / (episode + '.mkv'))], check=True, capture_output=True)
            manager = projects.ProjectManager(root / 'projects.sqlite')
            try:
                planned = []
                for name in ('Сопрано', 'Другой сериал'):
                    project = projects.build_project(root / name, ['source', '360p'],
                                                     series={'movie_number': 10, 'season_number': 1})
                    self.assertEqual(len(project['tasks']), 4)
                    manager.add(project)
                    planned.append(project)
                wait_for(lambda: not manager.busy, 30)
                for project in planned:
                    self.assertEqual(manager.get(project['id'])['state'], 'prepared', manager.get(project['id'])['message'])
                    manifest = manager.manifests(project['id'])['0']
                    self.assertEqual(manifest['content_type'], 'series')
                    self.assertEqual(manifest['season_number'], 1)
                    self.assertEqual(len(manifest['files']), 8)
                    for entry in manifest['files']:
                        self.assertTrue(launcher.player_compatible(launcher.probe_video(Path(manifest['root']) / entry['path'])))
                    self.assertFalse((root / project['title'] / '.kinotime-audio.json').exists())
                    self.assertFalse((root / project['title'] / '.kinotime-media.json').exists())
                    with patch.object(launcher, '_prepare_quality_video') as encoder:
                        again = projects.render_task(project, project['tasks'][0], threading.Event(), Mock())
                        self.assertEqual(len(again), 2)
                        encoder.assert_not_called()
            finally:
                manager.close()
                manager.wait_closed()

    def test_frozen_audio_plan_does_not_rewrite_shared_settings(self):
        plan = {'audio': [{'index': 0, 'name': 'Frozen', 'enabled': True}], 'qualities': ['source'], 'mode': 'fast'}
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'episode.mkv'
            source.write_bytes(b'source')
            stop = threading.Event()
            stop.set()
            # The normal path still checks cancellation before reading or saving any choices.
            with patch.object(launcher, 'save_audio_choices') as audio, patch.object(launcher, 'save_media_choices') as media:
                with self.assertRaises(InterruptedError):
                    launcher.prepare_media(source, Mock(), stop, media_plan=copy.deepcopy(plan))
                audio.assert_not_called()
                media.assert_not_called()


if __name__ == '__main__':
    unittest.main()
