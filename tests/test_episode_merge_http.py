"""Local admin API checks; no actual Telegram or persistent database."""
import contextlib
import io
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from aiohttp.test_utils import TestClient, TestServer

from database.categories import add_movie_category
from database.connection import database
from database.episode_variants import add_series_file, choices
from database.migrations import migrate
from database.movies import add_new_movie
from webpanel import auth
from webpanel.server import create_app
from webpanel.uploads import Uploads


class EpisodeMergeHTTPTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        database.open(':memory:')
        self.addCleanup(database.close)
        with contextlib.redirect_stdout(io.StringIO()):
            migrate()
        for patcher in (patch('data.config.ADMIN', [99]), patch.object(Uploads, 'start'),
                        patch.object(Uploads, 'stop', new_callable=AsyncMock)):
            patcher.start()
            self.addCleanup(patcher.stop)
        config = SimpleNamespace(WEB_PUBLIC_URL='http://127.0.0.1', WEB_CHUNK_BYTES=1024,
                                 WEB_MAX_FILE_BYTES=1000, BOT_API_BASE='https://api.telegram.org',
                                 UPLOAD_DIR=Path(tempfile.gettempdir()), UPLOAD_QUOTA_BYTES=10000)
        self.client = TestClient(TestServer(create_app(None, config)))
        await self.client.start_server()
        self.addAsyncCleanup(self.client.close)
        session = auth.exchange_login(auth.create_login(99))
        self.headers = {'Origin': 'http://127.0.0.1', 'Cookie': 'kinobot_session=' + session,
                        'X-CSRF-Token': auth.get_session(session)['csrf']}
        self.movie = add_new_movie('Задорные друзья', content_type='series')
        self.folder = add_movie_category(self.movie, 'Сезон 3', 3)
        with database.transaction():
            self.target = add_series_file(self.folder, {'name': 'Серия 2 Correct', 'quality': '1080p', 'voice': 'A'},
                                          {'file_id': 'full', 'file_type': 'video'})
            self.source = add_series_file(self.folder, {'name': 'Серия 2 Typo', 'quality': '720p', 'voice': 'B'},
                                          {'file_id': 'small', 'file_type': 'video'})
        self.url = f'/api/catalog/{self.movie}/episodes/merge'
        self.data = {'folder_id': self.folder, 'target_id': self.target,
                     'episode_ids': [self.target, self.source], 'name': 'Серия 2 Correct'}

    def snapshot(self):
        return list(database.connection.iterdump())

    async def preview(self):
        response = await self.client.post(self.url, json={'action': 'preview', **self.data}, headers=self.headers)
        self.assertEqual(response.status, 200, await response.text())
        return await response.json()

    async def test_preview_read_only_then_apply_refreshes_catalog_with_same_target_id(self):
        before = self.snapshot()
        preview = await self.preview()
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(preview['conflicts'], [])
        self.assertEqual(preview['counts']['variants_after'], 2)
        response = await self.client.post(self.url,
            json={'action': 'apply', **self.data, 'expected_token': preview['token']}, headers=self.headers)
        self.assertEqual(response.status, 200, await response.text())
        result = await response.json()
        self.assertTrue(result['merged'])
        self.assertEqual([row['id'] for row in result['catalog']['folders'][0]['files']], [self.target])
        self.assertEqual({choice.file_id for choice in choices(self.target)}, {'full', 'small'})

    async def test_missing_session_desktop_token_origin_and_csrf_cannot_modify_catalog(self):
        before = self.snapshot()
        for headers, status in (({'Origin': 'http://127.0.0.1'}, 401),
                                ({'Origin': 'http://127.0.0.1', 'Authorization': 'Bearer desktop'}, 401),
                                ({**self.headers, 'Origin': 'http://wrong.invalid'}, 403),
                                ({**self.headers, 'X-CSRF-Token': 'wrong'}, 403)):
            with self.subTest(headers=headers):
                response = await self.client.post(self.url, json={'action': 'apply', **self.data}, headers=headers)
                self.assertEqual(response.status, status)
                self.assertEqual(self.snapshot(), before)

    async def test_apply_requires_unchanged_preview_and_blocks_replay(self):
        preview = await self.preview()
        data = {'action': 'apply', **self.data, 'expected_token': preview['token']}
        database.execute('UPDATE MovieSubcategories SET name=? WHERE id=?', ('Changed', self.source))
        database.commit()
        before = self.snapshot()
        response = await self.client.post(self.url, json=data, headers=self.headers)
        self.assertEqual(response.status, 400)
        self.assertEqual(self.snapshot(), before)
        preview = await self.preview()
        data['expected_token'] = preview['token']
        response = await self.client.post(self.url, json=data, headers=self.headers)
        self.assertEqual(response.status, 200)
        before = self.snapshot()
        response = await self.client.post(self.url, json=data, headers=self.headers)
        self.assertEqual(response.status, 400)
        self.assertEqual(self.snapshot(), before)

    async def test_different_files_for_same_variant_return_structured_conflict_without_changes(self):
        database.execute('UPDATE EpisodeVariants SET quality=?,voice=? WHERE episode_id=?',
                         ('1080p', 'A', self.source))
        database.commit()
        before = self.snapshot()
        preview = await self.preview()
        self.assertEqual(preview['conflicts'][0]['quality'], '1080p')
        response = await self.client.post(self.url,
            json={'action': 'apply', **self.data, 'expected_token': preview['token']}, headers=self.headers)
        self.assertEqual(response.status, 400)
        result = await response.json()
        self.assertEqual(result['conflicts'][0]['voice'], 'A')
        self.assertEqual(self.snapshot(), before)

    async def test_another_movie_season_and_movie_type_are_rejected(self):
        other = add_new_movie('Other', content_type='series')
        folder = add_movie_category(other, 'Сезон 3', 3)
        for number, values in ((self.movie, {**self.data, 'folder_id': folder}),
                               (other, self.data)):
            with self.subTest(number=number):
                before = self.snapshot()
                response = await self.client.post(f'/api/catalog/{number}/episodes/merge',
                    json={'action': 'preview', **values}, headers=self.headers)
                self.assertEqual(response.status, 400)
                self.assertEqual(self.snapshot(), before)
        database.execute("UPDATE Movies SET content_type='movie' WHERE movie_number=?", (self.movie,))
        database.commit()
        before = self.snapshot()
        response = await self.client.post(self.url, json={'action': 'preview', **self.data}, headers=self.headers)
        self.assertEqual(response.status, 400)
        self.assertEqual(self.snapshot(), before)

    async def test_invalid_action_selection_and_missing_token_do_not_modify_catalog(self):
        before = self.snapshot()
        for values in ({'action': 'unknown', **self.data},
                       {'action': 'preview', **self.data, 'episode_ids': [self.target]},
                       {'action': 'preview', **self.data, 'target_id': True},
                       {'action': 'apply', **self.data},
                       {'action': 'preview', **self.data, 'name': ''}):
            with self.subTest(values=values):
                response = await self.client.post(self.url, json=values, headers=self.headers)
                self.assertEqual(response.status, 400)
                self.assertEqual(self.snapshot(), before)


if __name__ == '__main__':
    unittest.main()
