import os
import json
from pathlib import Path
import tempfile
import threading
import tkinter as tk
import unittest
from unittest.mock import Mock, patch

from tools import torrent_panel
from tools.torrent_queue import TorrentQueue
from tests.test_release_search import candidate


class AutoQueueTests(unittest.TestCase):
    def test_automatic_choice_adds_selects_prepares_and_deduplicates(self):
        self.check_pipeline(ambiguous=False)

    def test_ambiguous_files_stop_before_download_and_remain_for_review(self):
        self.check_pipeline(ambiguous=True)

    def test_jackett_torrent_enters_project_pipeline_and_deduplicates_by_actual_hash(self):
        self.check_pipeline(ambiguous=False, private=True)

    def check_pipeline(self, ambiguous, private=False):
        try:
            root = tk.Tk()
        except tk.TclError:
            self.skipTest('Tk display unavailable')
        root.withdraw()
        self.addCleanup(root.destroy)
        with tempfile.TemporaryDirectory() as folder:
            work = Path(folder) / 'downloads'
            work.mkdir()
            captured = {}
            def open_search(parent, choose, prepare, shortcuts):
                captured['prepare'] = prepare
                return Mock()
            def add(queue, client, source, stop, report):
                job = {'id': 'd' * 32, 'title': 'Example', 'status': 'choose_files', 'selected': [],
                       'files': [{'index': 0, 'name': 'Example.mkv', 'size': 2 * 1024**3}]}
                if ambiguous:
                    job['files'].append({'index': 1, 'name': 'Other.mkv', 'size': 1024})
                queue.jobs.append(job)
                queue.save()
                return job
            with patch.dict(os.environ, KINOTIME_STATE_DIR=str(Path(folder) / 'state'), KINOTIME_DOWNLOAD_DIR=str(work)), \
                 patch.object(torrent_panel, 'open_search', side_effect=open_search), \
                 patch.object(torrent_panel, 'QBitClient'), \
                 patch.object(TorrentQueue, 'add', autospec=True, side_effect=add) as added, \
                 patch.object(TorrentQueue, 'run') as run:
                page = tk.Frame(root)
                report = Mock()
                def background(fn):
                    fn()
                    return True
                shutdown = torrent_panel.mount(page, root, background, report, threading.Event(),
                                               Mock(return_value=Mock()), Mock(), lambda: False,
                                               {'Фильмы': 'movies', 'Сериалы': 'series'})
                try:
                    button = next(w for w in page.winfo_children() if 'text' in w.keys()
                                  and w.cget('text') == 'Помощник выбора раздачи')
                    button.invoke()
                    settings = {'query': 'Пример', 'year': '2020', 'season': 0, 'height': 1080,
                                'preferred_voice': '', 'max_gib': 30, 'avoid_ads': True, 'movie_number': None}
                    release = candidate()
                    if private:
                        release.update(source=str(Path(folder) / 'private.torrent'), provider='jackett',
                                       source_available=True, info_hash='c' * 40,
                                       download_path='/dl/lostfilm?jackett_apikey=SECRET')
                    captured['prepare'](release, settings)
                    root.update()
                    jobs = TorrentQueue(work, reserve_gib=0).jobs
                    self.assertEqual(len(jobs), 1)
                    self.assertEqual(jobs[0]['release_hash'], ('c' if private else 'a') * 40)
                    self.assertNotIn('SECRET', json.dumps(jobs))
                    if ambiguous:
                        run.assert_not_called()
                        self.assertEqual(jobs[0]['status'], 'choose_files')
                        self.assertIn('ручной выбор', jobs[0]['error'])
                    else:
                        run.assert_called_once()
                        self.assertEqual(run.call_args.kwargs['job_ids'], {'d' * 32})
                        self.assertEqual(jobs[0]['selected'], [0])
                        self.assertEqual(jobs[0]['mode'], 'project')
                    with self.assertRaisesRegex(ValueError, 'уже добавлена'):
                        captured['prepare'](release, settings)
                    added.assert_called_once()
                finally:
                    shutdown()


if __name__ == '__main__':
    unittest.main()
