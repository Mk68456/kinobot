import contextlib
import io
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from database.connection import database
from database.migrations import migrate
from tools import desktop_launcher as launcher, disk_space, stream_pipeline
from webpanel import imports, repository, streaming, launcher_transfers

INSTANCE = '12345678-1234-4234-8234-123456789abc'


class StreamTests(unittest.TestCase):
    def setUp(self):
        database.open(':memory:')
        self.addCleanup(database.close)
        with contextlib.redirect_stdout(io.StringIO()):
            migrate()
        self.manifest = {'version': 1, 'title': 'Series', 'root': 'D:/KinoTime-Ready/stream-test',
                         'stream_expected': 2, 'files': [self.entry('1.mp4')]}

    def entry(self, path):
        return {'path': path, 'size': 10, 'mtime': '1', 'content_sha256': 'a' * 64}

    def append(self, manifest=None, instance=INSTANCE):
        return streaming.append(99, instance, manifest or self.manifest)

    def accept(self, current):
        for item in current['body']['items']:
            database.execute("UPDATE WebAssets SET status='ready',file_id=? WHERE id=?",
                             ('telegram-' + item['asset_id'], item['asset_id']))

    def test_incremental_one_card_preserves_edits_and_is_idempotent(self):
        first = self.append()
        body = {**first['body'], 'title': 'Edited', 'description': 'Description'}
        body['items'][0]['name'] = 'Episode 1'
        repository.save_draft(99, body, first['id'])
        second = self.append({**self.manifest, 'files': [self.entry('2.mp4')]})
        self.assertEqual(second['id'], first['id'])
        self.assertEqual(second['body']['title'], 'Edited')
        self.assertEqual(second['body']['items'][0]['name'], 'Episode 1')
        self.assertEqual(len(second['body']['items']), 2)
        again = self.append({**self.manifest, 'files': [self.entry('2.mp4')]})
        self.assertEqual(again['body'], second['body'])
        self.assertEqual(database.execute('SELECT count(*) FROM WebAssets').fetchone()[0], 2)

    def test_partial_card_cannot_publish_package_or_group(self):
        current = self.append()
        self.accept(current)
        with self.assertRaisesRegex(ValueError, 'поточной'):
            repository.publish(current['id'], 99)
        with self.assertRaisesRegex(ValueError, 'поточной'):
            repository.package_add(99, current['id'], repository.review_token(current))
        self.assertTrue(repository.drafts(99)[0]['stream_pending'])
        self.assertFalse(repository.review_queue(99)['items'][0]['can_publish'])
        with self.assertRaises(ValueError):
            repository.group_files(99, [{'draft_id': current['id'],
                'asset_id': current['body']['items'][0]['asset_id'],
                'review_token': repository.review_token(current)}], {})

    def test_finish_requires_count_and_all_receipts(self):
        current = self.append()
        self.accept(current)
        with self.assertRaisesRegex(ValueError, 'всех файлов'):
            streaming.finish(99, INSTANCE, current['id'])
        current = self.append({**self.manifest, 'files': [self.entry('2.mp4')]})
        with self.assertRaisesRegex(ValueError, 'всех файлов'):
            streaming.finish(99, INSTANCE, current['id'])
        self.accept(current)
        self.assertEqual(streaming.finish(99, INSTANCE, current['id'])['stream_pending'], 0)
        self.assertEqual(streaming.finish(99, INSTANCE, current['id'])['stream_pending'], 0)

    def test_changed_plan_or_file_rejected_without_partial_changes(self):
        current = self.append()
        for delta in ({'stream_expected': 3}, {'title': 'Changed'},
                      {'files': [{**self.entry('1.mp4'), 'size': 11}]},
                      {'files': [self.entry('2.mp4'), self.entry('3.mp4')]}):
            with self.assertRaises(ValueError):
                self.append({**self.manifest, **delta})
        self.assertEqual(len(repository.draft(current['id'], 99)['body']['items']), 1)

    def test_device_isolation_and_rejected_card_stops(self):
        current = self.append()
        with self.assertRaises(ValueError):
            streaming.finish(99, '87654321-1234-4234-8234-123456789abc', current['id'])
        repository.review(current['id'], 99, 'rejected', 'Check')
        with self.assertRaisesRegex(ValueError, 'закрыта'):
            self.append({**self.manifest, 'files': [self.entry('2.mp4')]})

    def test_malformed_delta_rejected(self):
        for files in (None, [], 'file', [None], [{}], [self.entry('1.mp4')] * 2):
            with self.assertRaises(ValueError):
                self.append({**self.manifest, 'files': files})
        with self.assertRaises(ValueError):
            self.append(instance='')

    def test_normal_import_cannot_append(self):
        current = imports.create(99, self.manifest, INSTANCE)
        with self.assertRaisesRegex(ValueError, 'Состав'):
            imports.create(99, {**self.manifest, 'files': [*self.manifest['files'], self.entry('2.mp4')]}, INSTANCE)
        self.assertEqual(len(repository.draft(current['id'], 99)['body']['items']), 1)

    def test_thousand_file_plan_and_limit(self):
        with self.assertRaises(ValueError):
            self.append({**self.manifest, 'stream_expected': 1001})
        current = self.append({**self.manifest, 'stream_expected': 1000,
                               'files': [self.entry(f'{i}.mp4') for i in range(999)]})
        current = self.append({**self.manifest, 'stream_expected': 1000, 'files': [self.entry('999.mp4')]})
        self.assertEqual(len(current['body']['items']), 1000)
        self.accept(current)
        self.assertEqual(streaming.finish(99, INSTANCE, current['id'])['stream_pending'], 0)


class FastStop(threading.Event):
    def wait(self, timeout=None):
        return self.is_set()


class PipelineTests(unittest.TestCase):
    def setUp(self):
        database.open(':memory:')
        self.addCleanup(database.close)
        with contextlib.redirect_stdout(io.StringIO()):
            migrate()
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        env = patch.dict(os.environ, KINOTIME_STATE_DIR=str(self.root / 'state'))
        env.start()
        self.addCleanup(env.stop)
        self.source = self.root / 'Series'
        self.source.mkdir()
        self.paths = [self.source / 'S01E01.mkv', self.source / 'S01E02.mkv']
        for path in self.paths:
            path.write_bytes(b'source')
        self.stop = FastStop()
        self.sent = []
        self.uncertain = False
        self.report = Mock()
        self.client = SimpleNamespace(url='https://example.test', instance_id=INSTANCE, request=self.request)
        self.desktop = SimpleNamespace(send_file=self.send_file)
        self.generated = []

    def request(self, path, body=None):
        if path == 'session':
            return {'capabilities': {'streaming_import': 1}}
        if path == 'stream':
            return {'draft': streaming.append(99, INSTANCE, body), 'bot_username': 'ExampleBot', 'bot_name': 'Example'}
        parts = path.split('/')
        if parts[0] == 'stream':
            return {'draft': streaming.finish(99, INSTANCE, parts[1])}
        if len(parts) == 5:
            return launcher_transfers.operate(99, INSTANCE, parts[1], parts[3], body['action'], body.get('verified_not_sent'))
        current = imports.device_draft(99, parts[1], INSTANCE)
        return {'draft_status': current['status'], 'files': [
            {k: repository.asset(i['asset_id'], 99)[k] for k in ('id', 'status', 'error')}
            for i in current['body']['items']]}

    def receive(self, path, caption):
        imports.receive(99, SimpleNamespace(caption=caption, video=None, chat=SimpleNamespace(id=99),
            message_id=int(caption.split(':')[1][:8], 16),
            document=SimpleNamespace(file_size=path.stat().st_size, file_id='tg-' + caption, file_name=path.name)))

    def send_file(self, path, caption, before_send, **kwargs):
        before_send()
        self.sent.append((path, caption))
        if self.uncertain:
            raise ValueError('Send outcome uncertain')
        self.receive(path, caption)

    def fake_prepare(self, folder, report, stop, **kwargs):
        destination = self.root / 'KinoTime-Ready' / 'mobile-fake'
        destination.mkdir(parents=True, exist_ok=True)
        for source in self.paths:
            for quality in ('source', '360p'):
                name = '01 - Studio.mp4'
                if stop.variant_skip(source, quality, name):
                    continue
                target = destination / quality / str(self.paths.index(source)) / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(f'{source.name}/{quality}'.encode())
                self.generated.append(target)
                disk_space.register(target)
                info = target.stat()
                stop.variant_ready(source, destination, {'path': target.relative_to(destination).as_posix(),
                    'size': info.st_size, 'mtime': str(info.st_mtime_ns), 'quality': quality, 'voice': 'Studio'})

    def run_pipeline(self, fake=True):
        original = launcher.scan(self.source, enforce_upload_limit=False)
        plans = [{'entry': e, 'qualities': ['source', '360p'], 'mode': 'fast',
                  'audio': [{'index': 0, 'name': 'Studio', 'enabled': True}]} for e in original['files']]
        with contextlib.ExitStack() as stack:
            if fake:
                stack.enter_context(patch.object(launcher, 'plan_folder_qualities', return_value=(original, plans)))
                stack.enter_context(patch.object(launcher, 'prepare_media', side_effect=self.fake_prepare))
            return stream_pipeline.run(self.source, self.client, self.stop, self.report,
                                       lambda data: self.desktop, qualities=['source', '360p'])

    def test_trailing_dots_episode_streams_and_resumes_without_resending(self):
        source = self.paths[0]
        renamed = source.with_name('2x01 - Мужик приходит к психиатру....mp4')
        source.rename(renamed)
        self.paths[0] = renamed
        result = self.run_pipeline()
        self.assertEqual(len(self.sent), 4)
        self.assertEqual(result['draft']['stream_pending'], 0)
        self.assertTrue(all(not p.exists() for p in self.generated))
        self.run_pipeline()
        self.assertEqual(len(self.sent), 4)
        self.assertEqual(len(self.generated), 4)

    def test_four_variants_one_card_cleaned_only_after_receipts(self):
        result = self.run_pipeline()
        self.assertEqual(len(self.sent), 4)
        self.assertEqual(len(result['draft']['body']['items']), 4)
        self.assertEqual(result['draft']['stream_pending'], 0)
        self.assertEqual(database.execute('SELECT count(*) FROM WebDrafts').fetchone()[0], 1)
        self.assertTrue(all(not path.exists() for path in self.generated))
        self.assertEqual([p.read_bytes() for p in self.paths], [b'source', b'source'])
        self.assertNotIn('variant_ready', self.stop.__dict__)
        self.run_pipeline()
        self.assertEqual(len(self.sent), 4)
        self.assertEqual(len(self.generated), 4)

    def test_uncertain_send_preserves_output_and_restart_checks_receipt(self):
        self.uncertain = True
        with self.assertRaisesRegex(ValueError, 'uncertain'):
            self.run_pipeline()
        self.assertEqual(len(self.generated), 1)
        self.assertTrue(self.generated[0].exists())
        self.assertTrue(self.sent[0][0].exists())
        self.assertNotIn('variant_ready', self.stop.__dict__)
        self.receive(*self.sent[0])
        self.uncertain = False
        self.run_pipeline()
        self.assertEqual(len(self.sent), 4)
        self.assertEqual(len(self.generated), 4)
        self.assertTrue(all(not path.exists() for path in self.generated))

    def test_old_server_refused_before_render(self):
        self.client.request = lambda *args: {'capabilities': {}}
        with patch.object(launcher, 'prepare_media') as prepare:
            with self.assertRaisesRegex(ValueError, 'обновите серверную'):
                self.run_pipeline()
            prepare.assert_not_called()

    @unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe'), 'FFmpeg required')
    def test_real_render_two_episodes_two_qualities_two_voices_and_resume(self):
        for index, path in enumerate(self.paths):
            subprocess.run(['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i', f'color=c={"red" if index else "blue"}:size=1280x720:rate=5',
                '-f', 'lavfi', '-i', 'sine=frequency=440', '-t', '0.4', '-map', '0:v', '-map', '1:a', '-map', '1:a',
                '-c:v', 'libx264', '-preset', 'ultrafast', '-pix_fmt', 'yuv420p', '-c:a', 'aac',
                '-metadata:s:a:0', 'title=Studio', '-metadata:s:a:1', 'title=Original', str(path)],
                check=True, capture_output=True, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        original = {p: p.read_bytes() for p in self.paths}
        result = self.run_pipeline(fake=False)
        sent_count = len(self.sent)
        self.assertGreater(sent_count, 0)
        self.assertEqual(len(result['draft']['body']['items']), 8)
        self.assertEqual(result['draft']['stream_pending'], 0)
        self.assertFalse(list((self.root / 'KinoTime-Ready').rglob('*.mp4')))
        with patch.object(launcher, '_prepare_quality_video') as encoder:
            self.run_pipeline(fake=False)
        encoder.assert_not_called()
        self.assertEqual(len(self.sent), sent_count)
        self.assertEqual({p: p.read_bytes() for p in self.paths}, original)

    @unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe'), 'FFmpeg required')
    def test_existing_ready_voice_sent_before_recreating_video(self):
        source = self.paths[0]
        subprocess.run(['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i', 'color=size=1280x720:rate=5',
            '-f', 'lavfi', '-i', 'sine=frequency=440', '-t', '0.4', '-map', '0:v', '-map', '1:a', '-map', '1:a',
            '-c:v', 'libx264', '-preset', 'ultrafast', '-pix_fmt', 'yuv420p', '-c:a', 'aac',
            '-metadata:s:a:0', 'title=Studio', '-metadata:s:a:1', 'title=Original', str(source)],
            check=True, capture_output=True, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        self.paths[1].unlink()
        initial = launcher.prepare_media(source, Mock(), self.stop, qualities=['source', '360p'])
        for entry in initial['files'][1:]:
            (Path(initial['root']) / entry['path']).unlink()
        original_encoder = launcher._prepare_quality_video

        def encoder(*args, **kwargs):
            self.assertGreater(len(self.sent), 0, 'Ready MP4 must be uploaded before more disk is needed')
            return original_encoder(*args, **kwargs)

        with patch.object(launcher, '_prepare_quality_video', side_effect=encoder):
            result = self.run_pipeline(fake=False)
        self.assertEqual(len(result['draft']['body']['items']), 4)
        self.assertEqual(result['draft']['stream_pending'], 0)


if __name__ == '__main__':
    unittest.main()
