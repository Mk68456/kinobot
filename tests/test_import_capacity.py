"""Exact output counts, live folder progress and the 1000-file import boundary."""
import contextlib
import copy
import io
import json
import tempfile
import threading
import unittest
from pathlib import Path
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from database.connection import database
from database.migrations import migrate
from database.movies import add_new_movie
from tools import desktop_launcher as launcher
from webpanel import imports, repository, server


QUALITIES = ['source', '720p', '480p', '360p']
MEDIA = {'width': 1920, 'height': 1080, 'eligible': QUALITIES, 'mode': 'fast'}


class FolderCountTests(unittest.TestCase):
    def sources(self, root, count):
        root.mkdir()
        for index in range(count):
            (root / f'S01E{index + 1:03}.mkv').write_bytes(b'video')

    def audio(self, count):
        return [{'index': i, 'name': f'Studio {i}', 'enabled': True} for i in range(count)]

    def test_25_episodes_one_audio_four_qualities_is_100(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder) / 'season'
            self.sources(root, 25)
            with patch.object(launcher, 'quality_choices', return_value=MEDIA), \
                 patch.object(launcher, 'audio_choices', return_value=self.audio(1)):
                original, plans = launcher.plan_folder_qualities(root, QUALITIES, threading.Event())
            item = launcher.folder_queue_item(original, plans)
            self.assertEqual(item['source_count'], 25)
            self.assertEqual(len(item['variants']), 100)
            self.assertEqual(len({v['source'] for v in item['variants']}), 25)

    def test_multiple_audio_tracks_explain_280_and_disabled_tracks_are_excluded(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder) / 'season'
            self.sources(root, 25)
            def audio(source):
                count = 3 if int(source.stem[-3:]) <= 20 else 2
                return self.audio(count) + [{'index': 9, 'name': 'Disabled', 'enabled': False}]
            with patch.object(launcher, 'quality_choices', return_value=MEDIA), \
                 patch.object(launcher, 'audio_choices', side_effect=audio):
                original, plans = launcher.plan_folder_qualities(root, QUALITIES, threading.Event())
            item = launcher.folder_queue_item(original, plans)
            self.assertEqual(len(item['variants']), 280)  # (20*3 + 5*2) * 4
            self.assertNotIn('Disabled', {v['voice'] for v in item['variants']})

    def test_1000_outputs_accepted_and_1004_rejected_before_encoding(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder) / 'season'
            self.sources(root, 250)
            with patch.object(launcher, 'quality_choices', return_value=MEDIA), \
                 patch.object(launcher, 'audio_choices', return_value=self.audio(1)), \
                 patch.object(launcher, '_prepare_quality_video') as encoder:
                original, plans = launcher.plan_folder_qualities(root, QUALITIES, threading.Event())
                self.assertEqual(len(launcher.folder_queue_item(original, plans)['variants']), 1000)
                (root / 'S01E251.mkv').write_bytes(b'video')
                with self.assertRaisesRegex(ValueError, '1004.*251.*1000'):
                    launcher.prepare_media(root, Mock(), threading.Event(), qualities=QUALITIES)
                encoder.assert_not_called()

    def test_scan_accepts_1000_but_rejects_1001(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder) / 'season'
            self.sources(root, 1000)
            self.assertEqual(len(launcher.scan(root)['files']), 1000)
            (root / 'extra.mkv').write_bytes(b'video')
            with self.assertRaisesRegex(ValueError, '1000'):
                launcher.scan(root)

    def test_progress_routes_same_filename_in_different_folders_and_keeps_tree(self):
        original = {'root': 'D:/season', 'title': 'Series'}
        plans = [{'entry': {'path': f'{season}/episode.mkv'}, 'qualities': QUALITIES,
                  'audio': self.audio(1)} for season in ('S01', 'S02')]
        snapshots = []
        progress = launcher.FolderProgress(Mock(), lambda items: snapshots.append(copy.deepcopy(items)))
        progress.plan(original, plans)
        progress.variant('S02/episode.mkv / 720p / Studio 0', 'Кодирование · 30%', '')
        progress.phase('paused', 'Stopped')
        self.assertEqual(progress.items[0]['source_count'], 2)
        self.assertEqual(progress.items[0]['variants'][5]['status'], 'Пауза')
        self.assertIn('Кодирование · 30%', progress.items[0]['variants'][5]['detail'])
        self.assertEqual(progress.items[0]['variants'][1]['status'], 'Ожидает')
        self.assertEqual(snapshots[-1][0]['status'], 'paused')
        tree = Mock()
        tree.get_children.return_value = []
        view = launcher.PreparationQueueView(tree, {'paused': 'Пауза'})
        view.render(progress.items)
        inserted = tree.insert.call_count
        view.render(progress.items)
        self.assertEqual(tree.insert.call_count, inserted)
        tree.delete.assert_called_once()
        progress.items[0]['variants'][5]['status'] = 'Подготовлен'
        view.render(progress.items)
        self.assertEqual(tree.insert.call_count, inserted)
        self.assertTrue(tree.item.called)
        self.assertEqual(len(set(view.sources.values())), 2)
        rows = launcher.preparation_queue_rows(progress.items, {'paused': 'Пауза'})
        self.assertIn('Исходников: 2 → MP4: 8', rows[0][2][2])
        self.assertTrue(any('Качества: 4 × озвучки: 1 = 4 MP4' in row[2][2] for row in rows))

    def test_mixed_season_warning_is_separate_from_counts(self):
        plans = [{'entry': {'path': f'release/Series.S{season:02}E01.mkv'}, 'qualities': QUALITIES,
                  'audio': self.audio(1)} for season in (3, 1, 2)]
        item = launcher.folder_queue_item({'root': 'D:/series', 'title': 'Series'}, plans)
        self.assertEqual(item['source_count'], 3)
        self.assertEqual(len(item['variants']), 12)
        self.assertIn('1, 2, 3', item['warning'])
        self.assertIn('один сезон', item['warning'])

    def test_1000_file_json_reaches_client_opener_and_oversized_body_does_not(self):
        client = launcher.Client('https://example.com', 'a' * 40)
        client.opener = Mock()
        client.opener.open.return_value.__enter__ = Mock(return_value=io.BytesIO(b'{}'))
        client.opener.open.return_value.__exit__ = Mock(return_value=False)
        body = {'files': [{'path': 'озвучка/' + ('папка/' * 20) + f'{i}.mp4', 'size': 1}
                          for i in range(1000)]}
        self.assertGreater(len(json.dumps(body, ensure_ascii=False).encode()), 100_000)
        self.assertEqual(client.request('import', body), {})
        client.opener.open.assert_called_once()
        with self.assertRaisesRegex(ValueError, 'слишком большой'):
            client.request('import', {'description': 'x' * launcher.MAX_JSON_BYTES})
        client.opener.open.assert_called_once()


class CapacityAPITests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from aiohttp.test_utils import TestClient, TestServer
        from data import settings
        database.open(':memory:')
        with contextlib.redirect_stdout(io.StringIO()):
            migrate()
        self.patches = [patch('webpanel.imports.is_admin', return_value=True),
                        patch('webpanel.uploads.Uploads.start'),
                        patch('webpanel.uploads.Uploads.stop', new_callable=AsyncMock),
                        patch('services.tmdb.search', new_callable=AsyncMock, return_value=[])]
        for item in self.patches:
            item.start()
        config = SimpleNamespace(**{key: getattr(settings, key) for key in dir(settings) if key.isupper()})
        config.WEB_PUBLIC_URL = 'http://localhost'
        bot = SimpleNamespace(get_me=AsyncMock(return_value=SimpleNamespace(username='TestBot', full_name='Test')))
        self.client = TestClient(TestServer(server.create_app(bot, config)))
        await self.client.start_server()
        self.headers = {'Authorization': 'Bearer ' + imports.pair(99)}

    async def asyncTearDown(self):
        await self.client.close()
        for item in reversed(self.patches):
            item.stop()
        database.close()

    def manifest(self, count=1000):
        movie = add_new_movie('Series', content_type='series')
        return {'version': 1, 'title': 'Series', 'root': 'D:/ready', 'content_type': 'series',
                'catalog_section': 'series', 'movie_number': movie, 'season_number': 1,
                'files': [{'path': 'готовые-копии/' + ('длинная-папка/' * 12) + f'video-{i}.mp4',
                           'size': 10, 'mtime': '1', 'name': f'Серия {i//4 + 1}',
                           'quality': QUALITIES[i % 4], 'voice': 'Studio'} for i in range(count)]}

    async def test_1000_unicode_files_import_edit_receive_and_publish_end_to_end(self):
        manifest = self.manifest()
        self.assertGreater(len(json.dumps(manifest, ensure_ascii=False).encode()), 100_000)
        response = await self.client.post('/api/desktop/import', headers=self.headers, json=manifest)
        self.assertEqual(response.status, 200, await response.text())
        current = (await response.json())['draft']
        self.assertEqual(len(current['body']['items']), 1000)
        body = {**current['body'], 'description': 'Edited'}
        web_headers = {'Origin': 'http://localhost', 'X-CSRF-Token': 'checked'}
        with patch('webpanel.auth.get_session', return_value={'admin_id': 99, 'csrf': 'checked'}):
            response = await self.client.post('/api/drafts', headers=web_headers,
                                              json={'id': current['id'], 'body': body})
            self.assertEqual(response.status, 200, await response.text())
            self.assertEqual(len((await response.json())['body']['items']), 1000)
            response = await self.client.get('/api/assets?draft_id=' + current['id'])
            files = (await response.json())['assets']
            self.assertEqual({f['id'] for f in files}, {i['asset_id'] for i in body['items']})
        # Receive one real-shaped Telegram message, then mark the remaining synthetic assets ready.
        first = body['items'][0]['asset_id']
        imports.receive(99, SimpleNamespace(caption='KTFILE:' + first, document=None,
            video=SimpleNamespace(file_size=10, file_name='video.mp4', file_id='video-first'),
            chat=SimpleNamespace(id=99), message_id=1))
        database.execute("UPDATE WebAssets SET status='ready',file_id=id,file_type='video' WHERE admin_id=99")
        database.commit()
        response = await self.client.get('/api/desktop/import/' + current['id'], headers=self.headers)
        self.assertEqual(len((await response.json())['files']), 1000)
        self.assertEqual(repository.publish(current['id'], 99), manifest['movie_number'])
        self.assertEqual(database.execute('SELECT count(*) FROM EpisodeVariants').fetchone()[0], 1000)
        self.assertEqual(database.execute('SELECT count(*) FROM MovieSubcategories').fetchone()[0], 250)
        response = await self.client.get('/api/desktop/session', headers=self.headers)
        self.assertEqual((await response.json())['max_import_files'], 1000)

    async def test_1001_rejection_is_atomic_and_draft_assets_remain_admin_scoped(self):
        manifest = self.manifest(1001)
        response = await self.client.post('/api/desktop/import', headers=self.headers, json=manifest)
        self.assertEqual(response.status, 400)
        self.assertIn('1000', (await response.json())['error'])
        self.assertEqual(database.execute('SELECT count(*) FROM WebAssets').fetchone()[0], 0)
        self.assertEqual(database.execute('SELECT count(*) FROM WebDrafts').fetchone()[0], 0)
        manifest['files'] = manifest['files'][:1000]
        current = imports.create(99, manifest)
        with self.assertRaisesRegex(ValueError, '1000'):
            repository.normalize_draft({**current['body'], 'items': current['body']['items'] +
                                        [{**current['body']['items'][0], 'asset_id': 'a' * 32}]})
        with self.assertRaisesRegex(ValueError, 'недоступен'):
            repository.assets(88, current['id'])

    async def test_json_size_guard_applies_to_unknown_length_chunked_requests(self):
        raw = json.dumps({'files': ['a' * 150_000]}).encode()
        async def parts(_):
            yield raw[:90_000]
            yield raw[90_000:]
        request = SimpleNamespace(content_length=None, content=SimpleNamespace(iter_chunked=parts))
        self.assertEqual(len((await server.body(request))['files'][0]), 150_000)
        with patch.object(server, 'MAX_JSON_BYTES', 100_000):
            with self.assertRaisesRegex(ValueError, 'большой запрос'):
                await server.body(request)


class FolderQueueUITests(unittest.TestCase):
    def test_main_folder_preparation_updates_tree_while_work_is_running(self):
        import tkinter as tk
        from tools import torrent_panel
        try:
            root = tk.Tk()
        except tk.TclError:
            self.skipTest('Tk display unavailable')
        root.withdraw()
        release = threading.Event()
        problems = []
        checked = []
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / 'series'
            source.mkdir()
            for name in ('S01E01.mkv', 'S01E02.mkv'):
                (source / name).write_bytes(b'video')
            (Path(folder) / 'launcher-settings.json').write_text(json.dumps({
                'url': 'https://example.com', 'folder': str(source), 'qualities': QUALITIES}), encoding='utf-8')
            original = launcher.scan(source, enforce_upload_limit=False)
            plans = [{'entry': e, 'qualities': QUALITIES,
                      'audio': [{'index': 0, 'name': 'Studio', 'enabled': True}]} for e in original['files']]
            def prepare(_, report, stop, **kwargs):
                report.plan(original, plans)
                report.variant('S01E01.mkv / source / Studio', 'Копирование видео · 30%', '')
                if not release.wait(5):
                    raise AssertionError('UI did not observe running preparation')
                for variant in launcher.folder_queue_item(original, plans)['variants']:
                    report.variant(variant['name'], 'Подготовлен', '1 МБ')
                return {'version': 1, 'title': 'Series', 'root': folder, 'files': []}
            client = Mock()
            client.request.return_value = {'draft': {'status': 'draft'}, 'bot_username': 'TestBot'}
            def descendants(widget):
                for child in widget.winfo_children():
                    yield child
                    yield from descendants(child)
            deadline = time.monotonic() + 7
            def observe(tree, running=False):
                try:
                    parent = tree.get_children()[0] if tree.get_children() else None
                    values = tree.item(parent, 'values') if parent else ()
                    if values and ((not running and 'Подготовка' in values[1]) or
                                   (running and 'Настройка в WEB' in values[1])):
                        self.assertIn('Исходников: 2 → MP4: 8', values[2])
                        checked.append(values[1])
                        if not running:
                            self.assertIn('Копирование видео · 30%', values[1])
                            release.set()
                            root.after(100, lambda: observe(tree, True))
                        else:
                            root.quit()
                    elif time.monotonic() >= deadline:
                        raise AssertionError('Queue never displayed the expected preparation phase: ' + str(values))
                    else:
                        root.after(100, lambda: observe(tree, running))
                except Exception as error:
                    problems.append(error)
                    release.set()
                    root.quit()
            def begin():
                try:
                    widgets = list(descendants(root))
                    labels = {widget.cget('text'): widget for widget in widgets if 'text' in widget.keys()}
                    labels['Один проект / сериал: содержимое папки — одна карточка'].invoke()
                    tree = next(w for w in widgets if w.winfo_class() == 'Treeview' and
                                'title' in w.cget('columns') and w.heading('title', 'text') == 'Фильм')
                    labels['1. Подготовить без отправки'].invoke()
                    root.after(100, lambda: observe(tree))
                except Exception as error:
                    problems.append(error)
                    release.set()
                    root.quit()
            with patch('tkinter.Tk', return_value=root), \
                 patch.object(launcher, '__file__', str(Path(folder) / 'desktop_launcher.py')), \
                 patch.object(torrent_panel, '__file__', str(Path(folder) / 'torrent_panel.py')), \
                 patch('tools.torrent_panel.Path.home', return_value=Path(folder)), \
                 patch.object(launcher, 'prepare_media', side_effect=prepare), \
                 patch.object(launcher, 'Client', return_value=client), \
                 patch.object(launcher, 'TelegramDesktop') as desktop:
                try:
                    root.after(0, begin)
                    launcher.main()
                    self.assertEqual(problems, [])
                    self.assertEqual(len(checked), 2)
                    desktop.assert_not_called()
                    self.assertEqual([call.args[0] for call in client.request.call_args_list], ['session', 'import'])
                finally:
                    release.set()
                    root.tk.call(root.protocol('WM_DELETE_WINDOW'))


if __name__ == '__main__':
    unittest.main()
