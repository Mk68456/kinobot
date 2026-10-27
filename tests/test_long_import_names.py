"""Long labels survive preparation, import, editing, publication and resuming."""
from tests.age_fixtures import reviewed_import
import contextlib
import io
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from database.connection import database
from database.migrations import migrate
from database.movies import add_new_movie
from tools import desktop_launcher as launcher
from webpanel import imports, repository, server


LONG_PREFIX = 'Полное название эпизода и подробное описание ' * 4


class LongNameRepositoryTests(unittest.TestCase):
    def setUp(self):
        database.open(':memory:')
        with contextlib.redirect_stdout(io.StringIO()):
            migrate()

    def tearDown(self):
        database.close()

    def receive(self, item, message_id):
        imports.receive(99, SimpleNamespace(
            caption='KTFILE:' + item['asset_id'], document=None,
            video=SimpleNamespace(file_size=123, file_name='video.mp4', file_id='tg-' + item['asset_id']),
            chat=SimpleNamespace(id=99), message_id=message_id))

    def test_long_nested_movie_labels_survive_import_publish_rename_and_resume(self):
        category = 'Подробное название папки ' * 7
        stem = LONG_PREFIX + 'конец'
        filename = 'в' * 241 + '.mp4'
        path = category + '/' + stem + '/' + filename
        manifest = {'version': 1, 'title': 'Фильм', 'root': 'D:/ready',
                    'files': [{'path': path, 'size': 123, 'mtime': '1'}]}
        current = reviewed_import(99, manifest)
        item = current['body']['items'][0]
        self.assertEqual(item['category'], category.strip())
        self.assertEqual(item['name'], stem + ' / ' + filename[:-4])
        self.assertEqual(repository.asset(item['asset_id'], 99)['filename'], filename)
        self.receive(item, 1)
        number = repository.publish(current['id'], 99)
        folder = repository.catalog_folders(number)['folders'][0]
        self.assertEqual(folder['name'], category.strip())
        self.assertEqual(folder['files'][0]['name'], item['name'])
        renamed_folder = 'Папка ' + LONG_PREFIX * 2
        renamed_file = 'Видео ' + LONG_PREFIX * 3
        result = repository.rename_catalog_folders(number, [{
            'id': folder['id'], 'name': renamed_folder,
            'files': [{'id': folder['files'][0]['id'], 'name': renamed_file}],
        }])
        self.assertEqual(result['folders'][0]['name'], renamed_folder.strip())
        self.assertEqual(result['folders'][0]['files'][0]['name'], renamed_file.strip())
        resumed = reviewed_import(99, manifest)
        self.assertEqual(resumed['id'], current['id'])
        self.assertEqual(resumed['body']['items'][0]['asset_id'], item['asset_id'])
        self.assertEqual(repository.asset(item['asset_id'], 99)['file_id'], 'tg-' + item['asset_id'])

    def test_single_movie_selection_keeps_full_variant_label(self):
        path = '1080p/' + LONG_PREFIX + 'финал.mp4'
        current = reviewed_import(99, {'version': 1, 'root': 'D:/single', 'title': 'Фильм',
                                     'selection': path, 'files': [{'path': path, 'size': 123}]})
        self.assertEqual(current['body']['items'][0]['name'], LONG_PREFIX + 'финал')

    def test_episode_names_with_same_first_120_characters_remain_distinct(self):
        movie = add_new_movie('Сериал', content_type='series')
        names = [LONG_PREFIX + 'серия 1', LONG_PREFIX + 'серия 2']
        manifest = {'version': 1, 'title': 'Сериал', 'root': 'D:/season', 'content_type': 'series',
                    'movie_number': movie, 'season_number': 1, 'files': [
                        {'path': f'{quality}/episode-{index}.mp4', 'size': 123, 'mtime': '1',
                         'name': name, 'quality': quality, 'voice': 'Студия'}
                        for index, name in enumerate(names) for quality in ('720p', '360p')]}
        current = reviewed_import(99, manifest)
        current['body']['season_name'] = 'Сезон 1 — ' + LONG_PREFIX
        edited = repository.save_draft(99, current['body'], current['id'])
        for index, item in enumerate(edited['body']['items']):
            self.receive(item, index + 1)
        repository.publish(current['id'], 99)
        folder = repository.catalog_folders(movie)['folders'][0]
        self.assertEqual(folder['name'], current['body']['season_name'].strip())
        self.assertEqual([f['name'] for f in folder['files']], names)
        self.assertEqual([f['qualities'] for f in folder['files']], [['720p · Студия', '360p · Студия']] * 2)

    def test_empty_names_and_multiline_catalog_names_still_rejected(self):
        body = {'title': 'Фильм', 'content_type': 'movie', 'items': [
            {'asset_id': 'a' * 32, 'name': '   '}]}
        with self.assertRaises(ValueError):
            repository.normalize_draft(body)
        movie = add_new_movie('Фильм')
        database.execute('INSERT INTO MovieCategories(movie_number,name) VALUES (?,?)', (movie, 'Папка'))
        folder_id = database.execute('SELECT id FROM MovieCategories').fetchone()[0]
        for name in ('', 'Строка\nСтрока', 'Строка\0Строка'):
            with self.subTest(name=name), self.assertRaises(ValueError):
                repository.rename_catalog_folders(movie, [{'id': folder_id, 'name': name}])


class LongNamePreparationTests(unittest.TestCase):
    def test_prepared_manifest_does_not_truncate_or_merge_episode_names(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / 'season'
            root.mkdir()
            names = ['Episode-' + 'x' * 115 + '-1', 'Episode-' + 'x' * 115 + '-2']
            for name in names:
                (root / (name + '.mkv')).write_bytes(b'source')
            prepared = Path(temp) / 'ready'
            prepared.mkdir()
            (prepared / 'voice.mp4').write_bytes(b'ready')
            media = {'width': 1920, 'height': 1080, 'eligible': ['720p'], 'mode': 'fast'}
            audio = [{'index': 0, 'name': 'Studio', 'enabled': True}]
            variant = {'root': str(prepared), 'files': [
                {'path': 'voice.mp4', 'quality': '720p', 'voice': 'Studio'}]}
            with patch.object(launcher, 'quality_choices', return_value=media), \
                 patch.object(launcher, 'audio_choices', return_value=audio), \
                 patch.object(launcher, 'prepare_media', return_value=variant):
                result = launcher.prepare_folder_qualities(root, ['720p'], Mock(), threading.Event())
            self.assertEqual([f['name'] for f in result['files']], names)
            self.assertNotEqual(result['files'][0]['path'], result['files'][1]['path'])


class LongNameAPITests(unittest.IsolatedAsyncioTestCase):
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

    async def test_long_movie_import_and_web_edit_preserve_labels(self):
        category = 'Категория ' + LONG_PREFIX
        name = LONG_PREFIX + 'финал'
        manifest = {'version': 1, 'title': 'Фильм', 'root': 'D:/ready',
                    'files': [{'path': category + '/' + name + '.mp4', 'size': 123, 'mtime': '1'}]}
        response = await self.client.post('/api/desktop/import', headers=self.headers, json=manifest)
        self.assertEqual(response.status, 200, await response.text())
        current = (await response.json())['draft']
        current['body']['items'][0]['name'] = name + ' после редактирования'
        web_headers = {'Origin': 'http://localhost', 'X-CSRF-Token': 'checked'}
        with patch('webpanel.auth.get_session', return_value={'admin_id': 99, 'csrf': 'checked'}):
            response = await self.client.post('/api/drafts', headers=web_headers,
                                              json={'id': current['id'], 'body': current['body']})
        self.assertEqual(response.status, 200, await response.text())
        item = (await response.json())['body']['items'][0]
        self.assertEqual(item['name'], name + ' после редактирования')
        self.assertEqual(item['category'], category.strip())


class LongNameDeliveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_overlong_media_caption_keeps_delivery_and_buttons_working(self):
        from services.storage_backup import send_catalog_file
        original = '🎬' * 900 + ' конец'
        markup = object()
        for kind, reference in [('video', None), ('document', None),
                                ('video', {'chat_id': -1001, 'message_id': 7})]:
            with self.subTest(kind=kind, storage=bool(reference)):
                bot = SimpleNamespace(send_video=AsyncMock(), send_document=AsyncMock(), copy_message=AsyncMock())
                with patch('services.storage_backup.storage_reference', return_value=reference):
                    await send_catalog_file(bot, 99, 'tg-file', kind, caption=original, reply_markup=markup)
                method = bot.copy_message if reference else bot.send_video if kind == 'video' else bot.send_document
                kwargs = method.call_args.kwargs
                self.assertLessEqual(len(kwargs['caption'].encode('utf-16-le')), 2048)
                self.assertTrue(kwargs['caption'].endswith('…'))
                self.assertTrue(original.startswith(kwargs['caption'][:-1]))
                self.assertIs(kwargs['reply_markup'], markup)
                self.assertEqual(kwargs['parse_mode'], '')

    async def test_short_caption_is_unchanged(self):
        from services.storage_backup import send_catalog_file
        bot = SimpleNamespace(send_video=AsyncMock())
        for caption in (None, '', LONG_PREFIX + 'финал', 'x' * 1024):
            with self.subTest(caption_length=len(caption or '')):
                with patch('services.storage_backup.storage_reference', return_value=None):
                    await send_catalog_file(bot, 99, 'tg-file', 'video', caption=caption)
                self.assertEqual(bot.send_video.call_args.kwargs['caption'], caption)
