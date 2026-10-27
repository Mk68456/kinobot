from tests.age_fixtures import reviewed_import
import contextlib
import copy
import io
import json
import shutil
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from database.connection import database
from database.migrations import migrate
from webpanel import imports, repository
from tools.desktop_launcher import (scan, Client, transfer, matches_chat_label, matches_window_title,
                                    TelegramDesktop, player_compatible, BeforeSendError, prepare_media)


class DesktopMediaTests(unittest.TestCase):
    def test_large_source_allowed_but_upload_size_checked(self):
        import os
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'large.mkv';path.write_bytes(b'x')
            real_stat=Path.stat
            def fake_stat(candidate,*args,**kwargs):
                result=real_stat(candidate,*args,**kwargs)
                if candidate==path:
                    values=list(result);values[6]=5*1024**3
                    return os.stat_result(values)
                return result
            with patch.object(Path,'stat',fake_stat):
                self.assertEqual(scan(path,enforce_upload_limit=False)['files'][0]['size'],5*1024**3)
                with self.assertRaisesRegex(ValueError,'файл для отправки'):
                    scan(path)


    @unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe'), 'FFmpeg is optional in CI')
    def test_misnamed_avi_becomes_cached_mp4_without_changing_source(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder) / 'Film' / 'Dub' / '720p'
            root.mkdir(parents=True)
            source = root / 'sample.mp4'
            subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', 'color=size=64x64:rate=5',
                            '-t', '0.4', '-c:v', 'mpeg4', '-f', 'avi', str(source)], check=True,
                           capture_output=True, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            before = source.read_bytes()
            with patch.dict('os.environ', {'LOCALAPPDATA': str(Path(folder) / 'cache')}):
                manifest = prepare_media(root.parents[1], Mock(), threading.Event())
                self.assertEqual(manifest['title'], 'Film')
                self.assertEqual(manifest['files'][0]['path'], 'Dub/720p/sample.mp4')
                self.assertNotEqual(manifest['root'], str(root.parents[1]))
                self.assertEqual(prepare_media(root.parents[1], Mock(), threading.Event()), manifest)
                single = prepare_media(source, Mock(), threading.Event())
                self.assertEqual(single['selection'], 'sample.mp4')
                self.assertEqual(len(single['files']), 1)
            self.assertEqual(source.read_bytes(), before)

    def test_copy_video_and_only_incompatible_audio(self):
        from tools.desktop_launcher import video_options, audio_options
        info={'streams':[{'codec_type':'video','codec_name':'h264','pix_fmt':'yuv420p'},
                         {'codec_type':'audio','codec_name':'aac'},
                         {'codec_type':'audio','codec_name':'ac3'}]}
        self.assertEqual(video_options(info,hardware=True),['-c:v','copy'])
        self.assertEqual(audio_options(info),['-c:a','copy','-c:a:1','aac','-b:a:1','192k'])
        info['streams'][0]['codec_name']='hevc'
        self.assertIn('h264_qsv',video_options(info,hardware=True))
        self.assertIn('libx264',video_options(info))

    def test_actual_container_and_codecs_determine_compatibility(self):
        info = {'format': {'format_name': 'mov,mp4,m4a,3gp,3g2,mj2'}, 'streams': [
            {'codec_type': 'video', 'codec_name': 'h264', 'pix_fmt': 'yuv420p'},
            {'codec_type': 'audio', 'codec_name': 'aac'}]}
        self.assertTrue(player_compatible(info))
        info['format']['format_name'] = 'avi'
        self.assertFalse(player_compatible(info))
        info['format']['format_name'] = 'mp4'
        info['streams'][0]['codec_name'] = 'mpeg4'
        self.assertFalse(player_compatible(info))
        info['streams'][0]['codec_name'] = 'h264'
        info['streams'][1]['codec_name'] = 'ac3'
        self.assertFalse(player_compatible(info))

    def test_ready_media_keeps_original_manifest(self):
        with tempfile.TemporaryDirectory() as folder:
            (Path(folder) / 'film.mp4').write_bytes(b'video')
            with patch('tools.desktop_launcher.probe_video'), patch(
                    'tools.desktop_launcher.player_compatible', return_value=True):
                self.assertEqual(prepare_media(folder, Mock(), threading.Event()), scan(folder))

    def test_prepare_failure_never_marks_send_but_invoke_failure_does(self):
        desktop = TelegramDesktop('ExampleBot', 'KinoTime')
        desktop.prepare_attachment = Mock(side_effect=ValueError('unsupported video'))
        mark = Mock()
        with self.assertRaises(BeforeSendError):
            desktop.send_file(Path('film.mp4'), 'marker', before_send=mark)
        mark.assert_not_called()
        button = Mock()
        button.invoke.side_effect = RuntimeError('unknown outcome')
        desktop.prepare_attachment = Mock(return_value=button)
        desktop.guard = Mock()
        with self.assertRaises(RuntimeError):
            desktop.send_file(Path('film.mp4'), 'marker', before_send=mark)
        mark.assert_called_once()

    def test_composer_focus_requires_unique_named_bottom_editor(self):
        desktop = TelegramDesktop('ExampleBot', 'KinoTime')
        desktop.window = Mock()
        desktop.window.rectangle.return_value = SimpleNamespace(top=0, height=lambda: 1000)
        edit = Mock()
        edit.rectangle.return_value = SimpleNamespace(top=900)
        edit.window_text.return_value = 'Write a message...'
        edit.element_info.name = 'Write a message...'
        edit.legacy_properties.return_value = {}
        edit.descendants.return_value = []
        desktop.window.descendants.return_value = [edit]
        self.assertIs(desktop.focus_composer(), edit)
        edit.set_focus.assert_called_once()
        edit.reset_mock()
        desktop.window.descendants.return_value = [edit, edit]
        with self.assertRaises(ValueError):
            desktop.focus_composer()
        edit.set_focus.assert_not_called()

    def test_post_send_refresh_clicks_identified_composer(self):
        desktop = TelegramDesktop('ExampleBot', 'KinoTime')
        desktop.window = Mock()
        editor = Mock()
        desktop.focus_composer = Mock(return_value=editor)
        with patch('tools.desktop_launcher.time.sleep'):
            self.assertIs(desktop.refresh_chat_surface(), editor)
        desktop.window.set_focus.assert_called_once()
        desktop.focus_composer.assert_called_once()
        editor.click_input.assert_called_once_with(button='left')

    def test_video_preview_uses_leaf_caption_not_filename_or_parent_edit(self):
        def control(kind, cls='', name=''):
            result = Mock()
            result.element_info = SimpleNamespace(control_type=kind, class_name=cls)
            result.is_visible.return_value = True
            result.window_text.return_value = name
            result.descendants.return_value = []
            return result
        box = control('Window', 'SendFilesBox')
        leaf = control('Edit', name='Caption')
        parent = control('Edit', name='Caption')
        parent.descendants.return_value = [leaf]
        preview = control('Group', 'Ui::SingleMediaPreview')
        document = control('CheckBox', name='Send as a document')
        document.get_toggle_state.return_value = 0
        send = control('Button', name='Send')
        box.descendants.return_value = [parent, leaf, preview, document]
        box.parent.return_value.descendants.return_value = [send]
        desktop = TelegramDesktop('ExampleBot', 'KinoTime')
        desktop.window = Mock()
        desktop.window.descendants.return_value = [box]
        self.assertEqual(desktop.attachment_controls(), (leaf, send, document))
        preview.element_info.class_name = 'Ui::SingleFilePreview'
        with self.assertRaisesRegex(ValueError, 'не распознал'):
            desktop.attachment_controls()

    def test_same_preview_exposed_by_two_uia_surfaces_is_not_treated_as_two_windows(self):
        def control(kind, cls='', name='', handle=None):
            result = Mock()
            result.element_info = SimpleNamespace(control_type=kind, class_name=cls)
            result.handle = handle if handle is not None else Mock()
            result.is_visible.return_value = True
            result.window_text.return_value = name
            result.descendants.return_value = []
            return result

        leaf = control('Edit', name='Caption')
        preview = control('Group', 'Ui::SingleMediaPreview')
        document = control('CheckBox', name='Send as a document')
        document.get_toggle_state.return_value = 0
        send = control('Button', name='Send')

        boxes = []
        surfaces = []
        for _ in range(2):
            box = control('Window', 'SendFilesBox', handle=7001)
            box.descendants.return_value = [leaf, preview, document]
            box.parent.return_value.descendants.return_value = [send]
            surface = Mock()
            surface.descendants.return_value = [box]
            boxes.append(box)
            surfaces.append(surface)

        desktop = TelegramDesktop('ExampleBot', 'KinoTime')
        desktop.window = Mock()
        desktop.window.handle = 1
        desktop.telegram_surfaces = Mock(return_value=surfaces)
        self.assertEqual(desktop.attachment_controls(), (leaf, send, document))


class DesktopHeaderTests(unittest.TestCase):
    def test_native_title_verifies_chat_not_account_or_partial_name(self):
        self.assertTrue(matches_window_title('\u200eKinoTime | Фильмы @ \u200eAccount (23)',
                                            'KinoTime | Фильмы', 'ExampleBot'))
        self.assertTrue(matches_window_title('KinoTime', 'KinoTime', 'ExampleBot'))
        for title in ('Telegram', 'Other @ KinoTime', 'Other KinoTime @ Account',
                      'KinoTime extra @ Account', 'KinoTime\nOther', 'KinoTime @ '):
            self.assertFalse(matches_window_title(title, 'KinoTime', 'ExampleBot'), title)

    def test_native_title_fallback_without_matching_header(self):
        desktop = TelegramDesktop('ExampleBot', 'KinoTime')
        desktop.window = Mock()
        desktop.window.window_text.return_value = '\u200eKinoTime @ Account (12)'
        desktop.window.rectangle.return_value = SimpleNamespace(top=0, left=0, width=lambda: 958, height=lambda: 1031)
        desktop.window.descendants.return_value = []
        report = desktop.inspect_header()
        self.assertFalse(report['header_matched'])
        self.assertTrue(report['chat_verified'])
        self.assertEqual(report['match_source'], 'window_title')
        self.assertNotIn('Account', json.dumps(report))

    def test_title_normalization_accepts_multiline_status_and_invisible_marks(self):
        self.assertTrue(matches_chat_label('\u200eKinoTime\u00a0 | Фильмы\nbot', 'KinoTime | Фильмы', 'ExampleBot'))
        self.assertTrue(matches_chat_label('@ExampleBot', 'KinoTime', 'ExampleBot'))
        self.assertFalse(matches_chat_label('Other KinoTime', 'KinoTime', 'ExampleBot'))
        self.assertFalse(matches_chat_label('Other chat\nKinoTime', 'KinoTime', 'ExampleBot'))

    def test_header_uses_accessible_name_and_report_does_not_contain_text(self):
        desktop = TelegramDesktop('ExampleBot', 'KinoTime')
        bounds = SimpleNamespace(top=0, left=0, width=lambda: 1000, height=lambda: 800)
        control = Mock()
        control.is_visible.return_value = True
        control.element_info = SimpleNamespace(control_type='Text', name='KinoTime\nbot')
        control.window_text.return_value = ''
        control.legacy_properties.return_value = {}
        control.rectangle.return_value = SimpleNamespace(top=35, left=350, right=500, bottom=65)
        desktop.window = Mock()
        desktop.window.rectangle.return_value = bounds
        desktop.window.descendants.return_value = [control]
        report = desktop.inspect_header()
        self.assertTrue(report['header_matched'])
        self.assertNotIn('KinoTime', json.dumps(report))
        control.rectangle.return_value = SimpleNamespace(top=35, left=30, right=200, bottom=65)
        self.assertFalse(desktop.inspect_header()['header_matched'])
        control.rectangle.return_value = SimpleNamespace(top=400, left=350, right=500, bottom=430)
        self.assertFalse(desktop.inspect_header()['header_matched'])


class DesktopAPITests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from aiohttp.test_utils import TestClient, TestServer
        from data import settings
        from webpanel.server import create_app
        database.open(':memory:')
        with contextlib.redirect_stdout(io.StringIO()):
            migrate()
        self.patches = [patch('webpanel.imports.is_admin', return_value=True),
                        patch('webpanel.uploads.Uploads.start'),
                        patch('webpanel.uploads.Uploads.stop', new_callable=AsyncMock)]
        for item in self.patches:
            item.start()
        config = SimpleNamespace(**{key: getattr(settings, key) for key in dir(settings) if key.isupper()})
        config.WEB_PUBLIC_URL = 'http://localhost'
        bot = SimpleNamespace(get_me=AsyncMock(return_value=SimpleNamespace(username='ExampleBot', full_name='Example')))
        self.client = TestClient(TestServer(create_app(bot, config)))
        await self.client.start_server()
        self.token = imports.pair(99)
        self.headers = {'Authorization': 'Bearer ' + self.token}

    async def asyncTearDown(self):
        await self.client.close()
        for item in reversed(self.patches):
            item.stop()
        database.close()

    async def test_batch_requires_web_session_and_csrf_not_desktop_token(self):
        response = await self.client.get('/api/desktop/session', headers=self.headers)
        self.assertEqual((await response.json())['protocol'], 2)
        response = await self.client.post('/api/publish-batch', headers={**self.headers, 'Origin': 'http://localhost'},
                                          json={'items': []})
        self.assertEqual(response.status, 401)
        with patch('webpanel.auth.get_session', return_value={'admin_id': 99, 'csrf': 'checked'}):
            response = await self.client.post('/api/publish-batch', headers={'Origin': 'http://localhost'},
                                              json={'items': []})
            self.assertEqual(response.status, 403)
            current = reviewed_import(99, {'version': 1, 'root': 'D:/File', 'title': 'Ready',
                'files': [{'path': 'video.mp4', 'size': 10, 'mtime': '1'}]})
            database.execute("UPDATE WebAssets SET status='ready',file_id='test' WHERE id=?",
                             (current['body']['items'][0]['asset_id'],))
            database.commit()
            response = await self.client.get('/api/review-queue')
            item = (await response.json())['items'][0]
            response = await self.client.get('/api/drafts/' + item['id'])
            self.assertEqual((await response.json())['id'], item['id'])
            response = await self.client.post('/api/publish-batch',
                headers={'Origin': 'http://localhost', 'X-CSRF-Token': 'checked'},
                json={'items': [{'id': item['id'], 'review_token': item['review_token']}]})
            self.assertTrue((await response.json())['results'][0]['ok'])

    async def test_desktop_token_cannot_publish_or_read_other_admin(self):
        manifest = {'version': 1, 'root': 'D:/Movie', 'title': 'Movie', 'files': [
            {'path': 'movie.mkv', 'size': 10, 'mtime': '1'}]}
        with patch('services.tmdb.search', AsyncMock(return_value=[])):
            response = await self.client.post('/api/desktop/import', headers=self.headers, json=manifest)
        self.assertEqual(response.status, 200)
        draft_id = (await response.json())['draft']['id']
        response = await self.client.get('/api/desktop/import/' + draft_id, headers=self.headers)
        self.assertEqual((await response.json())['files'][0]['status'], 'waiting_telegram')
        response = await self.client.post('/api/drafts/' + draft_id + '/publish',
            headers={**self.headers, 'Origin': 'http://localhost'})
        self.assertEqual(response.status, 401)
        response = await self.client.get('/api/desktop/import/' + draft_id,
            headers={'Authorization': 'Bearer ' + imports.pair(88)})
        self.assertEqual(response.status, 404)

    async def test_unique_metadata_match_prepares_card_without_publishing(self):
        manifest = {'version': 1, 'root': 'D:/Movie', 'title': 'Movie (2020)', 'files': [
            {'path': 'movie.mkv', 'size': 10, 'mtime': '1'}]}
        candidate = {'title': 'Movie', 'year': '2020', 'overview': 'Description',
                     'poster_url': 'https://example.com/poster.jpg'}
        with patch('services.tmdb.search', AsyncMock(return_value=[candidate])):
            response = await self.client.post('/api/desktop/import', headers=self.headers, json=manifest)
        current = (await response.json())['draft']
        self.assertEqual(current['body']['description'], 'Description')
        self.assertEqual(current['status'], 'draft')
        self.assertEqual(database.execute('SELECT count(*) FROM Movies').fetchone()[0], 0)

    async def test_stream_routes_require_device_and_finish_all_receipts(self):
        headers = {**self.headers, 'X-KinoTime-Instance': 'stream-test-pc'}
        manifest = {'version': 1, 'root': 'D:/KinoTime-Ready/stream-test', 'title': 'Stream',
                    'stream_expected': 2, 'files': [{'path': '1.mp4', 'size': 10, 'mtime': '1'}]}
        response = await self.client.post('/api/desktop/stream', json=manifest)
        self.assertEqual(response.status, 401)
        response = await self.client.post('/api/desktop/stream', headers=self.headers, json=manifest)
        self.assertEqual(response.status, 400)
        response = await self.client.get('/api/desktop/session', headers=headers)
        self.assertEqual((await response.json())['capabilities']['streaming_import'], 1)
        response = await self.client.post('/api/desktop/stream', headers=headers, json=manifest)
        self.assertEqual(response.status, 200)
        current = (await response.json())['draft']
        endpoint = '/api/desktop/stream/' + current['id'] + '/finish'
        response = await self.client.post(endpoint, headers=headers, json={})
        self.assertEqual(response.status, 400)
        response = await self.client.post('/api/desktop/stream', headers=headers,
            json={**manifest, 'files': [{'path': '2.mp4', 'size': 10, 'mtime': '1'}]})
        current = (await response.json())['draft']
        for item in current['body']['items']:
            database.execute("UPDATE WebAssets SET status='ready',file_id=? WHERE id=?",
                             (item['asset_id'], item['asset_id']))
        database.commit()
        response = await self.client.post(endpoint, headers=headers, json={})
        self.assertEqual(response.status, 200)
        self.assertEqual((await response.json())['draft']['stream_pending'], 0)
        response = await self.client.post(endpoint, headers={
            'Authorization': 'Bearer ' + imports.pair(99), 'X-KinoTime-Instance': 'another-pc'}, json={})
        self.assertEqual(response.status, 400)


class DesktopImportTests(unittest.TestCase):
    def setUp(self):
        database.open(':memory:')
        with contextlib.redirect_stdout(io.StringIO()):
            migrate()
        self.manifest = {'version': 1, 'root': 'D:/Films/Test', 'title': 'Test', 'files': [
            {'path': '1080p/Дубляж/movie.mkv', 'size': 123, 'mtime': '1'},
            {'path': '720p/Оригинал/movie.mkv', 'size': 123, 'mtime': '1'}]}

    def tearDown(self):
        database.close()

    def receive(self, item, admin_id=99, size=123):
        return imports.receive(admin_id, SimpleNamespace(caption='KTFILE:' + item['asset_id'],
            document=SimpleNamespace(file_size=size, file_name='movie.mkv', file_id='tg-' + item['asset_id']),
            video=None, chat=SimpleNamespace(id=admin_id), message_id=int(item['asset_id'][:8], 16)))

    def test_renamed_media_and_reported_size_rejection(self):
        current = reviewed_import(99, self.manifest)
        item = current['body']['items'][0]
        with self.assertRaisesRegex(ValueError, '123.*122'):
            self.receive(item, size=122)
        self.assertIn('122', repository.asset(item['asset_id'],99)['error'])
        imports.receive(99, SimpleNamespace(caption='KTFILE:'+item['asset_id'],document=None,
            video=SimpleNamespace(file_size=123,file_name='renamed.mp4',file_id='video'),
            chat=SimpleNamespace(id=99),message_id=42))
        file = repository.asset(item['asset_id'],99)
        self.assertEqual(file['status'],'ready')
        self.assertIsNone(file['error'])
        self.assertEqual(file['filename'],'movie.mkv')

    def test_desktop_files_do_not_publish_until_review(self):
        current = reviewed_import(99, self.manifest)
        self.assertEqual(database.execute('SELECT count(*) FROM Movies').fetchone()[0], 0)
        with self.assertRaises(ValueError):
            repository.publish(current['id'], 99)
        for item in current['body']['items']:
            self.receive(item)
        self.assertEqual(database.execute('SELECT count(*) FROM Movies').fetchone()[0], 0)
        number = repository.publish(current['id'], 99)
        self.assertEqual(repository.publish(current['id'], 99), number)
        self.assertEqual(database.execute('SELECT count(*) FROM Movies').fetchone()[0], 1)
        self.assertEqual(database.execute('SELECT count(*) FROM MovieCategories').fetchone()[0], 2)
        self.assertEqual(database.execute('SELECT count(*) FROM MovieSubcategories').fetchone()[0], 2)
        promo = repository.promo(current['id'], 99, 'ExampleBot')
        self.assertIn(f'https://t.me/ExampleBot?start=movie_{number}', promo['text'])

    def test_video_remains_video_in_published_categories(self):
        current = reviewed_import(99, self.manifest)
        for item in current['body']['items']:
            imports.receive(99, SimpleNamespace(caption='KTFILE:' + item['asset_id'], document=None,
                video=SimpleNamespace(file_size=123, file_name='movie.mkv', file_id='video-' + item['asset_id']),
                chat=SimpleNamespace(id=99), message_id=int(item['asset_id'][:8], 16)))
        self.assertEqual(database.execute('SELECT count(*) FROM Movies').fetchone()[0], 0)
        repository.publish(current['id'], 99)
        self.assertEqual([r[0] for r in database.execute('SELECT file_type FROM MovieSubcategories')],
                         ['video', 'video'])

    def test_reject_revision_and_comment_history(self):
        current = reviewed_import(99, self.manifest)
        for item in current['body']['items']:
            self.receive(item)
        with self.assertRaises(ValueError):
            repository.review(current['id'], 99, 'revision')
        repository.review(current['id'], 99, 'revision', 'Исправить описание')
        repository.save_draft(99, current['body'], current['id'])
        with self.assertRaises(ValueError):
            repository.publish(current['id'], 99)
        repository.review(current['id'], 99, 'rejected', 'Не подходит')
        with self.assertRaises(ValueError):
            repository.publish(current['id'], 99)
        current = repository.review(current['id'], 99, 'draft', 'Исправлено')
        self.assertEqual(len(current['review_history']), 3)
        repository.publish(current['id'], 99)
        with self.assertRaises(ValueError):
            repository.review(current['id'], 99, 'draft')

    def test_same_names_are_matched_by_marker_and_other_admin_is_denied(self):
        current = reviewed_import(99, self.manifest)
        first, second = current['body']['items']
        with self.assertRaises(ValueError):
            self.receive(first, admin_id=88)
        with self.assertRaises(ValueError):
            self.receive(first, size=122)
        self.receive(second)
        self.receive(second)  # repeated Telegram updates are idempotent
        self.assertEqual(repository.asset(first['asset_id'], 99)['status'], 'waiting_telegram')
        self.assertEqual(repository.asset(second['asset_id'], 99)['status'], 'ready')

    def test_reimport_is_idempotent_and_changed_sources_rejected(self):
        current = reviewed_import(99, self.manifest)
        self.assertEqual(reviewed_import(99, self.manifest)['id'], current['id'])
        changed = copy.deepcopy(self.manifest)
        changed['files'][0]['mtime'] = '2'
        with self.assertRaises(ValueError):
            reviewed_import(99, changed)
        self.assertEqual(database.execute('SELECT count(*) FROM WebAssets').fetchone()[0], 2)

    def test_reimport_recovers_paths_dropped_by_old_editor(self):
        current = reviewed_import(99, self.manifest)
        self.receive(current['body']['items'][0])
        repository.review(current['id'], 99, 'revision', 'Проверить карточку')
        for item in current['body']['items']:
            item.pop('relative_path')
            item.pop('category')
        current['body']['items'][0]['name'] = 'Моё название'
        database.execute('UPDATE WebDrafts SET body=? WHERE id=?',
                         (json.dumps(current['body']), current['id']))
        database.commit()
        recovered = reviewed_import(99, self.manifest)
        self.assertEqual(recovered['id'], current['id'])
        self.assertEqual(recovered['status'], 'revision')
        self.assertEqual(recovered['body']['items'][0]['name'], 'Моё название')
        self.assertEqual(recovered['body']['items'][0]['relative_path'], self.manifest['files'][0]['path'])
        self.assertEqual(recovered['body']['items'][0]['category'], '1080p')
        self.assertEqual(repository.draft(current['id'], 99)['body'], recovered['body'])
        self.assertEqual(repository.asset(current['body']['items'][0]['asset_id'], 99)['status'], 'ready')
        self.assertEqual(len(recovered['review_history']), 1)
        self.assertEqual(database.execute('SELECT count(*) FROM WebAssets').fetchone()[0], 2)

    def test_recovery_does_not_save_when_source_changed(self):
        current = reviewed_import(99, self.manifest)
        for item in current['body']['items']:
            item.pop('relative_path')
        raw = json.dumps(current['body'])
        database.execute('UPDATE WebDrafts SET body=? WHERE id=?', (raw, current['id']))
        database.commit()
        changed = copy.deepcopy(self.manifest)
        changed['files'][0]['mtime'] = 'different'
        with self.assertRaisesRegex(ValueError, 'Состав'):
            reviewed_import(99, changed)
        self.assertEqual(database.execute('SELECT body FROM WebDrafts WHERE id=?', (current['id'],)).fetchone()[0], raw)

    def test_missing_asset_reports_specific_error(self):
        current = reviewed_import(99, self.manifest)
        database.execute('DELETE FROM WebAssets WHERE id=?', (current['body']['items'][0]['asset_id'],))
        database.commit()
        with self.assertRaisesRegex(ValueError, 'не найден исходный файл'):
            reviewed_import(99, self.manifest)

    def test_bad_manifest_is_atomic(self):
        self.manifest['files'][1]['path'] = '../escape.mkv'
        with self.assertRaises(ValueError):
            reviewed_import(99, self.manifest)
        self.assertEqual(database.execute('SELECT count(*) FROM WebAssets').fetchone()[0], 0)

    def test_desktop_token_expires_and_admin_revocation_takes_effect(self):
        token = imports.pair(99)
        with patch('webpanel.imports.is_admin', return_value=True):
            self.assertEqual(imports.authenticate(token), 99)
            with patch('webpanel.imports.time.time', return_value=10**12):
                self.assertIsNone(imports.authenticate(token))
        with patch('webpanel.imports.is_admin', return_value=False):
            self.assertIsNone(imports.authenticate(token))

    def test_scanner_ignores_nonvideo_and_keeps_relative_structure(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / '1080p' / 'Voice'
            path.mkdir(parents=True)
            (path / 'movie.mkv').write_bytes(b'video')
            (path / 'notes.txt').write_text('ignore')
            result = scan(folder)
            self.assertEqual([e['path'] for e in result['files']], ['1080p/Voice/movie.mkv'])

    def test_launcher_never_auto_resends_uncertain_file(self):
        with tempfile.TemporaryDirectory() as folder:
            (Path(folder) / 'movie.mkv').write_bytes(b'video')
            manifest = scan(folder)
            current = reviewed_import(99, manifest)
            item = current['body']['items'][0]
            client = Mock()
            client.request.return_value = {'draft_status': 'draft', 'files': [
                {'id': item['asset_id'], 'status': 'waiting_telegram'}]}
            desktop = Mock()
            def failed_send(path, caption, before_send):
                before_send()
                raise ValueError('UI unavailable after Send')
            desktop.send_file.side_effect = failed_send
            with self.assertRaises(ValueError):
                transfer(client, {'draft': current}, manifest, desktop, threading.Event(), Mock())
            desktop.send_file.assert_called_once()
            stop = Mock()
            stop.is_set.return_value = False
            stop.wait.return_value = True
            desktop.send_file.reset_mock()
            with self.assertRaises(InterruptedError):
                transfer(client, {'draft': current}, manifest, desktop, stop, Mock())
            desktop.send_file.assert_not_called()

    def test_client_refuses_insecure_remote_url(self):
        for url in ('http://example.com', 'https://user:pass@example.com', 'https://example.com/path'):
            with self.assertRaises(ValueError):
                Client(url, 'token')

    def test_launcher_rejects_message_or_non_ascii_in_connection_code(self):
        for token in ('Код: ' + 'a' * 43, 'Подключение лаунчера Telegram Desktop',
                      'a' * 42 + 'ё', 'a' * 20 + '\r\n' + 'b' * 20, '', 'short'):
            with self.subTest(token_kind=len(token)):
                with self.assertRaisesRegex(ValueError, 'только сам код'):
                    Client('https://example.com', token)

    def test_launcher_accepts_urlsafe_code_and_unicode_manifest(self):
        token = 'A_9-' * 10 + 'abc'
        client = Client(' https://example.com/ ', '  ' + token + '\n')
        self.assertEqual(client.token, token)
        self.assertEqual(client.url, 'https://example.com')
        client.opener = Mock()
        client.opener.open.return_value.__enter__ = Mock(return_value=io.BytesIO(b'{"ok":true}'))
        client.opener.open.return_value.__exit__ = Mock(return_value=False)
        self.assertEqual(client.request('import', {'title': 'Лёд'}), {'ok': True})
        request = client.opener.open.call_args.args[0]
        self.assertEqual(request.headers['Authorization'], 'Bearer ' + token)
        self.assertEqual(json.loads(request.data)['title'], 'Лёд')


if __name__ == '__main__':
    unittest.main()
