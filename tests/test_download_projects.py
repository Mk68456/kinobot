import contextlib
import gc
import io
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock

from database.connection import database
from database.migrations import migrate
from tools import desktop_launcher as launcher, launcher_projects
from tools.release_description import parse_description
from tools.release_search import parse_seasons, auto_file_ids, automatic_candidates, rank
from tools.torrent_queue import TorrentQueue
from webpanel import imports, repository


class DownloadSelectionTests(unittest.TestCase):
    def test_season_ranges_lists_and_all(self):
        self.assertEqual(parse_seasons('1-3'), (1, 2, 3))
        self.assertEqual(parse_seasons('1, 3,5'), (1, 3, 5))
        self.assertEqual(parse_seasons('все'), 'all')
        self.assertEqual(parse_seasons(0), 0)
        for value in ('', '3-1', '0,1', '100', 'abc'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                parse_seasons(value)

    def test_bundle_download_selects_only_requested_seasons(self):
        files = [{'index': index, 'name': f'S{sn:02}E01.mkv'} for index, sn in enumerate((1, 2, 3, 4))]
        self.assertEqual(auto_file_ids(files, season='1,3'), [0, 2])
        self.assertEqual(auto_file_ids(files, season='all'), [0, 1, 2, 3])
        # Numbered episode files inside season folders are also supported.
        self.assertEqual(auto_file_ids([{'index': 0, 'name': 'Season 2/01.mkv'}], season=2), [0])

    def test_automatic_cover_uses_each_season_once_without_requiring_same_release_year(self):
        from tests.test_release_search import candidate
        first, bundle = candidate(), candidate()
        first.update(id='1', title='Пример [S01] (2020) WEBRip 1080p')
        bundle.update(id='2', title='Пример [S01-03] (2020-2022) WEBRip 1080p')
        first['description'] = parse_description(first['title'] + '\n' + first['description'].text)
        bundle['description'] = parse_description(bundle['title'] + '\n' + bundle['description'].text)
        # Keep the selected release order deterministic; no episode-count claim is needed.
        rows = rank([first, bundle], 'Пример', season='1-3')
        chosen = automatic_candidates(rows, '1-3')
        covered = [number for _, numbers in chosen for number in numbers]
        self.assertEqual(sorted(covered), [1, 2, 3])
        self.assertEqual(len(covered), len(set(covered)))

    def test_torrent_handoff_has_its_own_durable_status_and_is_not_rendered_twice(self):
        with tempfile.TemporaryDirectory() as directory:
            queue = TorrentQueue(directory, reserve_gib=0)
            job = {'id': 'a' * 32, 'status': 'downloaded', 'selected': [0]}
            folder = queue.folder(job) / 'import'
            folder.mkdir(parents=True)
            job['import_folder'] = str(folder)
            queue.jobs = [job]
            callback = Mock(return_value={'queued_project_id': 'b' * 32})
            queue.prepare(job, threading.Event(), callback)
            self.assertEqual(job['status'], 'project_queued')
            self.assertEqual(job['project_id'], 'b' * 32)
            queue.run(Mock(), threading.Event(), Mock(), Mock(), callback)
            callback.assert_called_once()


class NewSeriesImportTests(unittest.TestCase):
    def setUp(self):
        database.open(':memory:')
        self.addCleanup(database.close)
        with contextlib.redirect_stdout(io.StringIO()):
            migrate()

    def manifest(self):
        return {'version': 1, 'root': 'D:/NewSeries', 'title': 'Новый сериал', 'content_type': 'series',
                'new_series': True, 'catalog_section': 'series', 'season_number': None,
                'files': [{'path': f's{sn}.mp4', 'size': 123, 'name': 'Серия 1', 'season_number': sn,
                           'quality': '360p', 'voice': 'Студия'} for sn in (1, 2)]}

    def test_new_series_has_no_catalog_entry_until_reviewed_publication(self):
        manifest = self.manifest()
        current = imports.create(99, manifest)
        self.assertIsNone(current['body']['movie_number'])
        self.assertEqual(database.execute('SELECT count(*) FROM Movies').fetchone()[0], 0)
        self.assertEqual([item['season_number'] for item in current['body']['items']], [1, 2])
        self.assertEqual(imports.create(99, manifest)['id'], current['id'])
        for item in current['body']['items']:
            database.execute("UPDATE WebAssets SET status='ready',file_id=?,file_type='video' WHERE id=?",
                             (item['asset_id'], item['asset_id']))
        repository.save_draft(99, {**current['body'], 'age_rating': '0+'}, current['id'])
        number = repository.publish(current['id'], 99)
        self.assertEqual(database.execute('SELECT count(*) FROM Movies').fetchone()[0], 1)
        self.assertEqual(database.execute('SELECT season_number FROM MovieCategories WHERE movie_number=? ORDER BY season_number',
                                         (number,)).fetchall(), [(1,), (2,)])

    def test_missing_seasons_and_duplicate_episodes_are_rejected_before_assets(self):
        manifest = self.manifest()
        manifest['files'][1]['season_number'] = 1
        with self.assertRaises(ValueError):
            imports.create(99, manifest)
        manifest['files'][1]['season_number'] = None
        with self.assertRaises(ValueError):
            imports.create(99, manifest)
        self.assertEqual(database.execute('SELECT count(*) FROM WebAssets').fetchone()[0], 0)


@unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe'), 'FFmpeg required')
class RealDownloadedProjectTests(unittest.TestCase):
    def test_two_new_seasons_prepare_in_one_project_without_a_server_or_card_code(self):
        gc.collect()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            folder = root / 'downloaded'
            folder.mkdir()
            source = folder / 'Example.S01E01.mkv'
            subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', 'color=size=320x180:rate=5',
                            '-f', 'lavfi', '-i', 'sine=frequency=440', '-t', '0.3', '-c:v', 'libx264',
                            '-preset', 'ultrafast', '-pix_fmt', 'yuv420p', '-c:a', 'aac', str(source)],
                           check=True, capture_output=True)
            shutil.copy2(source, folder / 'Example.S02E01.mkv')
            manager = launcher_projects.ProjectManager(root / 'projects.sqlite')
            job = {'id': 'c' * 32, 'title': 'Example', 'catalog_title': 'Новый сериал',
                   'catalog_section': 'series', 'project_seasons': (1, 2)}
            try:
                project_id = launcher_projects.handoff_download(manager, job, folder, ['source'], threading.Event())
                self.assertEqual(launcher_projects.handoff_download(manager, job, folder, ['source'], threading.Event()), project_id)
                deadline = time.monotonic() + 30
                while manager.busy and time.monotonic() < deadline:
                    time.sleep(.03)
                project = manager.get(project_id)
                self.assertEqual(project['state'], 'prepared', project['message'])
                self.assertEqual(len(manager.snapshots()), 1)
                manifest = manager.manifests(project_id)['0']
                self.assertTrue(manifest['new_series'])
                self.assertNotIn('movie_number', manifest)
                self.assertEqual({entry['season_number'] for entry in manifest['files']}, {1, 2})
                for entry in manifest['files']:
                    self.assertTrue(launcher.player_compatible(launcher.probe_video(Path(manifest['root']) / entry['path'])))
            finally:
                manager.close()
                manager.wait_closed()
