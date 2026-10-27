import contextlib
import io
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from aiohttp.test_utils import TestClient, TestServer
from data import settings
from database.connection import database
from database.migrations import migrate
from tests.age_fixtures import reviewed_import
from webpanel import repository
from webpanel.media_preview import byte_range
from webpanel.server import create_app
from webpanel.uploads import Uploads


class RangeTests(unittest.TestCase):
    def test_ranges_and_invalid_ranges(self):
        for text, expected in [(None, (0, 99, 200)), ('bytes=10-19', (10, 19, 206)),
                               ('bytes=90-', (90, 99, 206)), ('bytes=-7', (93, 99, 206)),
                               ('bytes=90-999', (90, 99, 206))]:
            self.assertEqual(byte_range(text, 100), expected)
        for text in ('bytes=100-', 'bytes=10-2', 'bytes=-0', 'bytes=1-2,3-4', 'nonsense'):
            with self.assertRaises(ValueError):
                byte_range(text, 100)


class StreamTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        database.open(':memory:')
        with contextlib.redirect_stdout(io.StringIO()):
            migrate()
        draft = reviewed_import(99, {'version':1,'root':'D:/Film','title':'Film',
                                    'files':[{'path':'film.mp4','size':100}]})
        self.asset = draft['body']['items'][0]['asset_id']
        database.execute("UPDATE WebAssets SET status='ready',file_id='storage:test',source_chat_id=-100123,source_message_id=45 WHERE id=?", (self.asset,))
        database.commit()
        self.temp = tempfile.TemporaryDirectory()
        with patch.object(settings, 'WEB_PUBLIC_URL', 'http://127.0.0.1'), patch.object(settings, 'UPLOAD_DIR', Path(self.temp.name)):
            app = create_app(None)
        self.provider = app['media_preview']
        self.client = TestClient(TestServer(app))
        self.auth = patch('webpanel.auth.get_session', return_value={'admin_id':99, 'csrf':'test'})
        self.auth.start()
        self.starts = patch.object(Uploads, 'start')
        self.starts.start()
        await self.client.start_server()
        self.payload = bytes(range(100))
        owner = self

        class Chunks:
            def __init__(self, offset):
                self.offset = offset
            def __aiter__(self):
                return self
            async def __anext__(self):
                if self.offset >= len(owner.payload):
                    raise StopAsyncIteration
                result = owner.payload[self.offset:self.offset+13]
                self.offset += len(result)
                return result
            async def close(self):
                owner.closed = True

        self.closed = False
        message = SimpleNamespace(document=SimpleNamespace(size=100, mime_type='video/mp4'), media='media')
        self.provider.connected = AsyncMock(return_value=SimpleNamespace(
            get_messages=AsyncMock(return_value=message),
            iter_download=lambda media, offset, request_size: Chunks(offset)))

    async def asyncTearDown(self):
        await self.client.close()
        self.starts.stop()
        self.auth.stop()
        database.close()
        self.temp.cleanup()

    async def test_stream_full_range_seek_and_head(self):
        url = f'/api/uploads/{self.asset}/stream'
        async with self.client.get(url, headers={'Range':'bytes=11-29'}) as res:
            self.assertEqual(res.status, 206)
            self.assertEqual(res.headers['Content-Range'], 'bytes 11-29/100')
            self.assertEqual(await res.read(), self.payload[11:30])
        self.assertTrue(self.closed)
        async with self.client.head(url) as res:
            self.assertEqual(res.status, 200)
            self.assertEqual(await res.read(), b'')
        async with self.client.get(url) as res:
            self.assertEqual(await res.read(), self.payload)
        async with self.client.get(url, headers={'Range':'bytes=999-'}) as res:
            self.assertEqual(res.status, 416)

    async def test_other_administrator_cannot_read_asset(self):
        with patch('webpanel.auth.get_session', return_value={'admin_id':100, 'csrf':'test'}):
            async with self.client.get(f'/api/uploads/{self.asset}/stream') as res:
                self.assertEqual(res.status, 404)
        self.provider.connected.assert_not_awaited()

    async def test_preview_metadata_and_missing_setup(self):
        info = repository.asset_preview(self.asset, 99)
        self.assertEqual(info['telegram_url'], 'https://t.me/c/123/45')
        with self.assertRaises(ValueError):
            repository.asset_preview(self.asset, 100)
