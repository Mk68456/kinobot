"""Read-only PC folder navigation and remote render lifecycle regressions."""
import copy
import json
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from tools import desktop_launcher as launcher, remote_render, video_hardware


class FolderBrowserTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.folder = Path(self.temporary.name) / 'Сезоны и проекты'
        self.folder.mkdir()

    def test_explicit_absolute_folder_can_differ_from_last_download_folder(self):
        other = self.folder / 'Новый сериал'
        other.mkdir()
        self.assertEqual(remote_render.existing_folder(str(other)), str(other.resolve()))

    def test_relative_missing_and_file_paths_are_rejected(self):
        movie = self.folder / 'episode.mkv'
        movie.write_bytes(b'video')
        for value in ('', 'relative/folder', 42, str(movie), str(self.folder / 'missing')):
            with self.subTest(value=value), self.assertRaises((ValueError, FileNotFoundError)):
                remote_render.existing_folder(value)

    def test_disabled_selection_keeps_only_previously_allowed_exact_folders(self):
        child = self.folder / 'child'
        child.mkdir()
        allowed = [str(self.folder)]
        self.assertEqual(remote_render.existing_folder(str(self.folder), allowed, False), str(self.folder))
        with self.assertRaisesRegex(ValueError, 'выключен'):
            remote_render.existing_folder(str(child), allowed, False)
        with self.assertRaisesRegex(ValueError, 'выключен'):
            remote_render.browse_folder(str(self.folder), enabled=False)

    def test_lists_only_directories_preserves_cyrillic_and_never_mutates_children(self):
        for name in ('Я сезон', 'alpha', 'Beta'):
            (self.folder / name).mkdir()
        media = self.folder / 'Исходник.mkv'
        media.write_bytes(b'keep this source untouched')
        before = {path.name: path.stat().st_mtime_ns for path in self.folder.iterdir()}
        result = remote_render.browse_folder(str(self.folder))
        self.assertEqual([item['name'] for item in result['entries']], ['alpha', 'Beta', 'Я сезон'])
        self.assertTrue(all(Path(item['path']).is_dir() for item in result['entries']))
        self.assertEqual(result['folder'], str(self.folder))
        self.assertEqual(result['parent'], str(self.folder.parent))
        self.assertEqual(before, {path.name: path.stat().st_mtime_ns for path in self.folder.iterdir()})
        self.assertEqual(media.read_bytes(), b'keep this source untouched')

    def test_pagination_is_stable_and_last_page_has_no_next(self):
        for index in range(205):
            (self.folder / f'project {index:03}').mkdir()
        first = remote_render.browse_folder(str(self.folder))
        second = remote_render.browse_folder(str(self.folder), first['next_offset'])
        last = remote_render.browse_folder(str(self.folder), second['next_offset'])
        self.assertEqual([len(part['entries']) for part in (first, second, last)], [100, 100, 5])
        self.assertEqual(last['next_offset'], None)
        names = [item['name'] for part in (first, second, last) for item in part['entries']]
        self.assertEqual(names, [f'project {index:03}' for index in range(205)])
        self.assertEqual(remote_render.browse_folder(str(self.folder), 500)['entries'], [])

    def test_invalid_page_offsets_and_disabled_roots_are_rejected(self):
        for offset in (-1, 100001, True, '100', 1.5):
            with self.subTest(offset=offset), self.assertRaises(ValueError):
                remote_render.browse_folder(str(self.folder), offset)
        with self.assertRaises(ValueError):
            remote_render.browse_folder(enabled=False)

    def test_root_listing_does_not_probe_or_write_drives(self):
        drives = [{'name': 'D:', 'path': 'D:\\'}]
        with patch.object(remote_render, 'drive_roots', return_value=drives), \
                patch.object(remote_render.os, 'scandir') as scan:
            result = remote_render.browse_folder()
        self.assertEqual(result['entries'], drives)
        self.assertIsNone(result['parent'])
        scan.assert_not_called()


class JobStateTests(unittest.TestCase):
    def setUp(self):
        self.job = remote_render.JobState()

    def test_untouched_state_is_empty_and_start_is_not_completion(self):
        self.assertEqual(self.job.snapshot(), {})
        self.job.start('prepare', 'D:/Projects', 'single')
        state = self.job.snapshot()
        self.assertEqual(state['state'], 'running')
        self.assertIsNone(state['finished_at'])
        self.assertTrue(state['id'])
        self.assertEqual(state['total'], 0)

    def test_progress_counts_variants_and_reads_current_percent(self):
        self.job.start('prepare', 'D:/Projects', 'single')
        self.job.report('Кодирование GPU')
        items = [{'status': 'preparing', 'variants': [
            {'name': 'one', 'status': 'Подготовлен'},
            {'name': 'two', 'status': 'Принят'},
            {'name': 'three', 'status': 'Кодирование · 37%'},
            {'name': 'four', 'status': 'Ожидает'}]}]
        self.job.queue(items)
        state = self.job.snapshot()
        self.assertEqual((state['ready'], state['total'], state['current'], state['percent']), (2, 4, 'three', 37))
        self.assertEqual(state['message'], 'Кодирование GPU')
        items[0]['variants'][2]['status'] = 'Подготовлен'
        self.assertEqual(self.job.snapshot()['percent'], 37)
        self.job.queue(items)
        self.assertIsNone(self.job.snapshot()['percent'])
        self.assertEqual(self.job.snapshot()['ready'], 3)

    def test_completed_prepare_and_send_have_distinct_outcomes(self):
        for kind, message in [('prepare', 'MP4 подготовлены'), ('send', 'Передача завершена')]:
            with self.subTest(kind=kind):
                self.job.start(kind, 'D:/Projects', 'single')
                self.job.queue([{'status': 'prepared' if kind == 'prepare' else 'complete',
                                 'variants': [{'status': 'Подготовлен' if kind == 'prepare' else 'Принят'}]}])
                self.job.finish()
                state = self.job.snapshot()
                self.assertEqual(state['state'], 'completed')
                self.assertIn(message, state['message'])
                self.assertIsNotNone(state['finished_at'])
                self.assertIsNone(state['percent'])

    def test_send_counts_only_accepted_files_as_ready(self):
        self.job.start('send', 'D:/Projects', 'single')
        self.job.queue([{'status': 'uploading', 'variants': [
            {'name': 'local MP4', 'status': 'Подготовлен'},
            {'name': 'uploaded MP4', 'status': 'Принят'}]}])
        self.assertEqual((self.job.snapshot()['ready'], self.job.snapshot()['total']), (1, 2))

    def test_prepared_project_is_not_completed_send(self):
        self.job.start('send', 'D:/Projects', 'single')
        self.job.queue([{'status': 'prepared', 'variants': [{'status': 'Подготовлен'}]}])
        self.job.finish()
        self.assertEqual(self.job.snapshot()['state'], 'failed')

    def test_partial_source_failure_is_failed_despite_other_ready_sources(self):
        self.job.start('prepare', 'D:/Projects', 'batch')
        self.job.queue([{'status': 'prepared', 'variants': [{'status': 'Подготовлен'}]},
                        {'status': 'error', 'error': 'GPU failed', 'variants': [{'status': 'Ошибка'}]}])
        self.job.finish()
        state = self.job.snapshot()
        self.assertEqual(state['state'], 'failed')
        self.assertEqual(state['message'], 'GPU failed')
        self.assertEqual((state['ready'], state['total']), (1, 2))

    def test_unfinished_batch_and_rejected_send_are_not_reported_complete(self):
        for status in ('pending', 'preparing', 'registering', 'rejected'):
            with self.subTest(status=status):
                self.job.start('send', 'D:/Projects', 'batch')
                self.job.queue([{'status': status, 'variants': []}])
                self.job.finish()
                self.assertEqual(self.job.snapshot()['state'], 'failed')

    def test_stop_interruption_and_new_job_reset_old_progress(self):
        self.job.start('prepare', 'D:/old', 'single')
        self.job.queue([{'status': 'preparing', 'variants': [{'name': 'old', 'status': 'Кодирование · 50%'}]}])
        old_id = self.job.snapshot()['id']
        self.job.finish(InterruptedError('Stopped'))
        self.assertEqual(self.job.snapshot()['state'], 'paused')
        self.job.start('prepare', 'D:/new', 'batch')
        state = self.job.snapshot()
        self.assertNotEqual(state['id'], old_id)
        self.assertEqual((state['ready'], state['total'], state['current'], state['percent']), (0, 0, '', None))
        self.job.finish(stopped=True)
        self.assertEqual(self.job.snapshot()['state'], 'paused')

    def test_worker_exception_is_failed_and_completed_snapshots_are_immutable(self):
        self.job.start('prepare', 'D:/Projects', 'single')
        self.job.finish(ValueError('No videos'))
        self.assertEqual(self.job.snapshot()['state'], 'failed')
        self.assertEqual(self.job.snapshot()['message'], 'No videos')
        altered = self.job.snapshot()
        altered['state'] = 'completed'
        self.job.report('late report')
        self.job.queue([{'status': 'prepared', 'variants': []}])
        self.assertEqual(self.job.snapshot()['message'], 'No videos')


class LauncherRemoteIntegrationTests(unittest.TestCase):
    """Exercise actual Tk command dispatch with only fake HTTP and fake encoding."""
    def exercise(self, *, invalid_folder=False, preparation_error=False, bad_settings=False):
        import tkinter as tk
        from tools import torrent_panel

        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            original = directory / 'Last download folder'
            original.mkdir()
            selected = directory / 'Новый сериал с телефона'
            selected.mkdir()
            state_dir = directory / 'state'
            state_dir.mkdir()
            (state_dir / 'launcher-settings.json').write_text(json.dumps({
                'url': 'https://example.invalid', 'folder': str(original),
                'remote_browse_enabled': True, 'hardware': 'auto', 'resource_mode': 'balanced',
            }), encoding='utf-8')
            root = tk.Tk()
            root.withdraw()
            requests, prepared_calls, workers = [], [], []
            command = {'id': 77, 'action': 'prepare', 'payload': {
                'folder': str(selected / 'missing') if invalid_folder else str(selected),
                'mode': 'single', 'qualities': ['source', '360p'], 'hardware': 'cpu',
                'load': 'eco', 'catalog_section': 'cartoons',
            }}
            if bad_settings:
                command['payload']['qualities'] = []
            delivered = False
            failures = []
            original_after = root.after
            original_thread = threading.Thread
            started = time.monotonic()

            class FakeClient:
                def __init__(self, *_args, **_kwargs):
                    pass

                def request(self, endpoint, payload=None):
                    nonlocal delivered
                    requests.append((endpoint, copy.deepcopy(payload)))
                    if endpoint == 'control/heartbeat':
                        if not delivered:
                            delivered = True
                            return {'command': command}
                        return {'command': None}
                    if endpoint == 'session':
                        return {}
                    if endpoint == 'import':
                        return {'bot_username': 'no_real_bot', 'bot_name': 'Fake bot', 'draft': {'id': 'fake'}}
                    if endpoint == 'control/commands/77/complete':
                        return {}
                    raise AssertionError('Unexpected network action: ' + endpoint)

            def fake_prepare(folder, progress, stop, *, qualities=None, **_kwargs):
                prepared_calls.append((folder, qualities, video_hardware.settings()))
                if preparation_error:
                    raise ValueError('Test encode failure')
                progress.plan({'root': folder, 'title': 'Новый сериал'}, [{
                    'entry': {'path': 'S01E01.mkv'}, 'audio': [], 'qualities': qualities}])
                for variant in progress.items[0]['variants']:
                    progress.variant(variant['name'], 'Подготовлен', '')
                return {'title': 'Новый сериал', 'root': folder, 'files': [{'path': 'S01E01.mp4'}]}

            def tracked_thread(*args, **kwargs):
                thread = original_thread(*args, **kwargs)
                workers.append(thread)
                return thread

            def accelerated_after(delay, callback=None, *args):
                if callback and getattr(callback, '__name__', '') == 'remote_poll':
                    delay = 40 if delay == 5000 else 20
                return original_after(delay, callback, *args)

            def all_widgets(parent):
                for child in parent.winfo_children():
                    yield child
                    yield from all_widgets(child)

            def configure_token():
                entries = [widget for widget in all_widgets(root)
                           if widget.winfo_class() == 'TEntry' and widget.cget('show') == '*']
                self.assertEqual(len(entries), 1)
                entries[0].insert(0, 'fake-pairing-code')

            def completed():
                acknowledgements = [payload for endpoint, payload in requests
                                    if endpoint == 'control/commands/77/complete']
                jobs = [payload['status']['job'] for endpoint, payload in requests
                        if endpoint == 'control/heartbeat' and payload['status']['job']]
                invalid = invalid_folder or bad_settings
                seen = bool(acknowledgements and (invalid or any(
                    job['state'] in ('completed', 'failed') for job in jobs)))
                if (seen or time.monotonic() - started > 6) and not any(worker.is_alive() for worker in workers):
                    if not seen:
                        failures.append(AssertionError('Remote command did not reach its reported outcome. '
                            + repr({'acknowledgements': acknowledgements, 'jobs': jobs[-2:],
                                    'prepared_calls': prepared_calls})))
                    root.destroy()
                else:
                    original_after(10, completed)

            with patch.dict(os.environ, {'KINOTIME_STATE_DIR': str(state_dir)}), \
                    patch.object(tk, 'Tk', return_value=root), \
                    patch.object(root, 'after', side_effect=accelerated_after), \
                    patch.object(launcher, 'Client', FakeClient), \
                    patch.object(launcher, 'prepare_media', side_effect=fake_prepare), \
                    patch.object(launcher, 'TelegramDesktop') as telegram, \
                    patch.object(launcher.threading, 'Thread', side_effect=tracked_thread), \
                    patch.object(torrent_panel, 'mount', return_value=lambda: None):
                original_after(5, configure_token)
                original_after(10, completed)
                launcher._main(smoke_test=False)
                telegram.assert_not_called()
            for worker in workers:
                worker.join(timeout=1)
                self.assertFalse(worker.is_alive())
            video_hardware.configure()
            if failures:
                raise failures[0]
            acknowledgements = [payload for endpoint, payload in requests
                                if endpoint == 'control/commands/77/complete']
            self.assertTrue(acknowledgements)
            if invalid_folder or bad_settings:
                self.assertFalse(acknowledgements[0]['ok'])
                self.assertEqual(prepared_calls, [])
            else:
                self.assertTrue(acknowledgements[0]['ok'], acknowledgements)
                self.assertEqual(len(prepared_calls), 1)
                folder, qualities, hardware = prepared_calls[0]
                self.assertEqual(Path(folder), selected)
                self.assertEqual(qualities, ['source', '360p'])
                self.assertEqual(hardware['hardware'], 'cpu')
                self.assertEqual(hardware['resource_mode'], 'eco')
                jobs = [payload['status']['job'] for endpoint, payload in requests
                        if endpoint == 'control/heartbeat' and payload['status']['job']]
                self.assertEqual(jobs[-1]['state'], 'failed' if preparation_error else 'completed')
                self.assertEqual(Path(jobs[-1]['folder']), selected)
                if not preparation_error:
                    self.assertEqual((jobs[-1]['ready'], jobs[-1]['total']), (2, 2))
                    imports = [payload for endpoint, payload in requests if endpoint == 'import']
                    self.assertEqual(imports[0]['catalog_section'], 'cartoons')
            return requests

    def test_web_prepare_uses_new_explicit_pc_folder_and_reports_completion(self):
        self.exercise()

    def test_invalid_folder_is_not_acknowledged_as_started(self):
        self.exercise(invalid_folder=True)

    def test_invalid_quality_selection_is_not_acknowledged_as_started(self):
        self.exercise(bad_settings=True)

    def test_worker_failure_is_reported_after_started_acknowledgement(self):
        self.exercise(preparation_error=True)


if __name__ == '__main__':
    unittest.main()
