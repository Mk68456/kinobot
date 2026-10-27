"""Season quality generation and bounded acknowledgement recovery regressions."""
import contextlib
import io
import json
import shutil
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from database.connection import database
from database.migrations import migrate
from database.movies import add_new_movie
from tools import desktop_launcher as launcher
from webpanel import imports, repository


class FolderQualityValidationTests(unittest.TestCase):
    def test_unavailable_quality_stops_before_encoding(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / 'season'
            root.mkdir()
            (root / 'Episode.mp4').write_bytes(b'source')
            media = {'width': 320, 'height': 180, 'eligible': ['source'], 'mode': 'fast'}
            with patch.object(launcher, 'quality_choices', return_value=media), \
                 patch.object(launcher, '_prepare_quality_video') as encoder:
                with self.assertRaisesRegex(ValueError, 'недостаточно разрешения'):
                    launcher.prepare_media(root, Mock(), threading.Event(), qualities=['720p'])
                encoder.assert_not_called()


@unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe'), 'FFmpeg required')
class SeasonQualityTests(unittest.TestCase):
    def video(self, path, size='1920x1080', audio=True):
        path.parent.mkdir(parents=True, exist_ok=True)
        command = ['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', f'color=size={size}:rate=5']
        if audio:
            command += ['-f', 'lavfi', '-i', 'sine=frequency=440']
        command += ['-t', '0.4', '-c:v', 'libx264', '-preset', 'ultrafast', '-pix_fmt', 'yuv420p']
        if audio:
            command += ['-c:a', 'aac', '-metadata:s:a:0', 'title=Studio']
        subprocess.run(command + [str(path)], check=True, capture_output=True)

    def test_season_makes_four_qualities_per_episode_and_publishes_as_two_episodes(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(launcher, 'qsv_available', return_value=False):
            root = Path(directory) / 'season'
            # Nested files must not cause generated caches to be scanned as sources.
            sources = [root / 'release' / 'S01E01.mkv', root / 'release' / 'S01E02.mkv']
            for source in sources:
                self.video(source)
            before = {source: source.read_bytes() for source in sources}
            requested = ['source', '720p', '480p', '360p']
            result = launcher.prepare_media(root, Mock(), threading.Event(), qualities=requested)
            self.assertEqual(len(result['files']), 8)
            self.assertNotIn('selection', result)
            for episode in ('S01E01', 'S01E02'):
                self.assertEqual({file['quality'] for file in result['files'] if file['name'] == episode}, set(requested))
            mtimes = {}
            for entry in result['files']:
                path = Path(result['root']) / entry['path']
                mtimes[entry['path']] = path.stat().st_mtime_ns
                info = launcher.probe_video(path)
                self.assertTrue(launcher.player_compatible(info))
                video = next(stream for stream in info['streams'] if stream['codec_type'] == 'video')
                expected_height = 1080 if entry['quality'] == 'source' else int(entry['quality'][:-1])
                self.assertEqual(video['height'], expected_height)
                self.assertEqual(entry['voice'], 'Studio')
            self.assertEqual(list(Path(result['root']).parent.rglob('.video-*.mp4')), [])
            with patch.object(launcher, '_prepare_quality_video', wraps=launcher._prepare_quality_video) as encoder:
                again = launcher.prepare_media(root, Mock(), threading.Event(), qualities=requested)
            encoder.assert_not_called()
            self.assertEqual(again, result)
            self.assertEqual(mtimes, {entry['path']: (Path(result['root']) / entry['path']).stat().st_mtime_ns
                                      for entry in again['files']})
            self.assertEqual({source: source.read_bytes() for source in sources}, before)
            self.assertEqual(len(launcher.scan(root)['files']), 2)
            database.open(':memory:')
            try:
                with contextlib.redirect_stdout(io.StringIO()):
                    migrate()
                show = add_new_movie('Series', content_type='series')
                manifest = {**result, 'content_type': 'series', 'catalog_section': 'series',
                            'movie_number': show, 'season_number': 1}
                draft = imports.create(99, manifest)
                self.assertEqual(len(draft['body']['items']), 8)
                with self.assertRaises(ValueError):
                    repository.publish(draft['id'], 99)
                for item in draft['body']['items']:
                    database.execute("UPDATE WebAssets SET status='ready',file_id=?,file_type='video' WHERE id=?",
                                     (item['asset_id'], item['asset_id']))
                database.commit()
                self.assertEqual(repository.publish(draft['id'], 99), show)
                self.assertEqual(database.execute('SELECT COUNT(*) FROM Movies').fetchone()[0], 1)
                self.assertEqual(database.execute('SELECT COUNT(*) FROM MovieSubcategories').fetchone()[0], 2)
                self.assertEqual(database.execute('SELECT COUNT(*) FROM EpisodeVariants').fetchone()[0], 8)
                self.assertEqual(imports.create(99, manifest)['id'], draft['id'])
            finally:
                database.close()

    def test_silent_source_also_gets_selected_lower_qualities_without_upscaling(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(launcher, 'qsv_available', return_value=False):
            source = Path(directory) / 'videos' / 'Silent.mp4'
            self.video(source, '1280x720', audio=False)
            launcher.save_media_choices(source, ['source', '1080p', '720p', '480p', '360p'])
            # Batch mode applies saved choices without passing a keyword to the encoder.
            result = launcher.prepare_media(source, Mock(), threading.Event())
            explicit = launcher.prepare_media(source, Mock(), threading.Event(),
                                              qualities=['source', '1080p', '720p', '480p', '360p'])
            self.assertEqual(result, explicit)
            self.assertEqual({file['quality'] for file in result['files']}, {'source', '480p', '360p'})
            for file in result['files']:
                info = launcher.probe_video(Path(result['root']) / file['path'])
                self.assertFalse(any(stream['codec_type'] == 'audio' for stream in info['streams']))
                self.assertTrue(launcher.player_compatible(info))


class TransferRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        source = self.root / 'Episode.mp4'
        source.write_bytes(b'video')
        self.asset = 'a' * 32
        self.manifest = launcher.scan(source)
        self.prepared = {'draft': {'id': 'draft', 'body': {'items': [
            {'asset_id': self.asset, 'relative_path': source.name}]}}}
        self.waiting = {'draft_status': 'draft', 'files': [{'id': self.asset, 'status': 'waiting_telegram'}]}
        self.ready = {'draft_status': 'draft', 'files': [{'id': self.asset, 'status': 'ready'}]}
        self.client = Mock()
        self.stop = Mock()
        self.stop.is_set.return_value = False
        self.stop.wait.return_value = False
        self.desktop = Mock()
        self.desktop.send_file.side_effect = lambda *args, **kwargs: kwargs['before_send']()
        self.report = Mock()

    def save_uncertain(self):
        (self.root / '.kinotime-transfer.json').write_text(json.dumps({self.asset: 'sent_unconfirmed'}))

    def journal(self):
        return json.loads((self.root / '.kinotime-transfer.json').read_text())

    def test_old_unconfirmed_send_pauses_after_two_minutes_and_never_resends(self):
        self.save_uncertain()
        self.client.request.return_value = self.waiting
        with patch.object(launcher.time, 'monotonic', side_effect=[0, 121]):
            with self.assertRaisesRegex(ValueError, 'Разрешить безопасный повтор'):
                launcher.transfer(self.client, self.prepared, self.manifest, self.desktop, self.stop, self.report)
        self.desktop.send_file.assert_not_called()
        self.assertEqual(self.journal()[self.asset], 'sent_unconfirmed')
        self.assertIn('02:01', self.report.call_args.args[0])
        self.assertIn('Принято 0/1', self.report.call_args.args[0])

    def test_late_acknowledgement_confirms_without_duplicate_send(self):
        self.save_uncertain()
        self.client.request.side_effect = [self.waiting, self.ready]
        launcher.transfer(self.client, self.prepared, self.manifest, self.desktop, self.stop, self.report)
        self.desktop.send_file.assert_not_called()
        self.assertEqual(self.journal()[self.asset], 'confirmed')

    def test_fresh_upload_is_not_cut_off_by_resume_timeout(self):
        self.client.request.side_effect = [self.waiting, self.waiting, self.ready]
        with patch.object(launcher.time, 'monotonic', side_effect=[0, 121]):
            launcher.transfer(self.client, self.prepared, self.manifest, self.desktop, self.stop, self.report)
        self.desktop.send_file.assert_called_once()
        self.assertEqual(self.journal()[self.asset], 'confirmed')
        self.assertTrue(any('02:01' in call.args[0] for call in self.report.call_args_list))

    def test_manual_retry_still_skips_server_confirmed_files(self):
        self.client.request.return_value = self.ready
        launcher.transfer(self.client, self.prepared, self.manifest, self.desktop, self.stop, self.report)
        self.desktop.send_file.assert_not_called()
        self.assertEqual(self.journal()[self.asset], 'confirmed')

    def test_missing_asset_pauses_before_sending(self):
        self.client.request.return_value = {'draft_status': 'draft', 'files': []}
        with self.assertRaisesRegex(ValueError, 'не вернула состояние'):
            launcher.transfer(self.client, self.prepared, self.manifest, self.desktop, self.stop, self.report)
        self.desktop.send_file.assert_not_called()

    def test_stop_during_wait_never_reports_all_files_accepted(self):
        self.save_uncertain()
        self.client.request.return_value = self.waiting
        self.stop.wait.return_value = True
        with self.assertRaises(InterruptedError):
            launcher.transfer(self.client, self.prepared, self.manifest, self.desktop, self.stop, self.report)
        self.assertEqual(self.journal()[self.asset], 'sent_unconfirmed')
        self.assertFalse(any('Все файлы приняты' in call.args[0] for call in self.report.call_args_list))
