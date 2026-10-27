import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from aiohttp import web
from aiohttp.test_utils import TestServer

from services import request_voices as voices


class RequestVoiceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.calls = []
        self.payload = [{"source": "kodik", "success": True, "translations": [
            {"id": 1, "name": "Studio A"}, {"id": 2, "name": "Studio B"}]}]
        self.status = 200
        self.raw = None
        self.content_type = 'application/json'

        async def handle(request):
            self.calls.append(dict(request.query))
            if self.raw is not None:
                return web.Response(body=self.raw, status=self.status, content_type=self.content_type)
            return web.json_response(self.payload, status=self.status)

        app = web.Application()
        app.router.add_get('/players', handle)
        self.server = TestServer(app)
        await self.server.start_server()
        voices._cache.clear()
        patches = [patch.object(voices, 'API_URL', str(self.server.make_url('/players'))),
                   patch.object(voices, 'ENABLED', True), patch.object(voices, '_last_request', 0.0),
                   patch.object(voices, '_rate_lock', asyncio.Lock()),
                   patch.object(voices, 'external_imdb_id', AsyncMock(return_value='tt0903747'))]
        for patcher in patches:
            patcher.start()
            self.addCleanup(patcher.stop)

    async def asyncTearDown(self):
        await self.server.close()
        voices._cache.clear()

    async def test_resolves_exact_imdb_and_caches_detached_results(self):
        result = await voices.lookup(1396, 'series')
        self.assertEqual(self.calls, [{'imdb': 'tt0903747'}])
        self.assertEqual(result['status'], 'ok')
        evidence = result['voices'][0]['evidence'][0]
        self.assertEqual((evidence['source'], evidence['translation_id']), ('kodik', '1'))
        self.assertIn('+00:00', evidence['checked_at'])
        result['voices'].clear()
        cached = await voices.lookup(1396, 'series')
        self.assertEqual(len(cached['voices']), 2)
        self.assertEqual(len(self.calls), 1)

    async def test_film_and_series_with_same_tmdb_id_have_separate_mappings(self):
        await voices.lookup(1396, 'movie')
        await voices.lookup(1396, 'series')
        self.assertEqual(len(self.calls), 2)
        voices.external_imdb_id.assert_any_await(1396, 'movie')
        voices.external_imdb_id.assert_any_await(1396, 'series')

    async def test_manual_titles_and_missing_imdb_never_search_by_name(self):
        self.assertEqual((await voices.lookup(None, 'series'))['status'], 'unmapped')
        voices.external_imdb_id.return_value = None
        self.assertEqual((await voices.lookup(1396, 'series'))['status'], 'unmapped')
        self.assertEqual(self.calls, [])

    async def test_unsupported_http_json_and_oversized_responses_fall_back(self):
        scenarios = [(503, b'{}', 'application/json'),
                     (200, b'not json', 'application/json'),
                     (200, b'binary data', 'application/octet-stream'),
                     (200, b'[' + b' ' * voices.MAX_BYTES + b']', 'application/json'),
                     (200, b'{"players": []}', 'application/json')]
        for index, (self.status, self.raw, self.content_type) in enumerate(scenarios):
            with self.subTest(index=index), self.assertLogs(voices.logger, level='WARNING'):
                result = await voices.lookup(100 + index, 'series')
                self.assertEqual(result, {'status': 'unavailable', 'voices': []})

    async def test_timeout_is_cached_briefly(self):
        with patch.object(voices, '_fetch', AsyncMock(side_effect=asyncio.TimeoutError)) as fetch:
            with self.assertLogs(voices.logger, level='WARNING'):
                first = await voices.lookup(1396, 'series')
            self.assertEqual(first['status'], 'unavailable')
            await voices.lookup(1396, 'series')
            fetch.assert_awaited_once()
            with patch.object(voices.time, 'monotonic', return_value=10**12):
                with self.assertLogs(voices.logger, level='WARNING'):
                    await voices.lookup(1396, 'series')
            self.assertEqual(fetch.await_count, 2)

    async def test_disabled_provider_makes_no_requests(self):
        with patch.object(voices, 'ENABLED', False):
            self.assertEqual((await voices.lookup(1396, 'series'))['status'], 'disabled')
            self.assertEqual(self.calls, [])

    async def test_rate_limit_spaces_outgoing_calls(self):
        import time
        started = time.monotonic()
        await asyncio.gather(*(voices._fetch('tt0903747') for _ in range(3)))
        self.assertGreaterEqual(time.monotonic() - started, 1.0)

    def test_deduplicates_studios_retains_sources_and_excludes_subtitles(self):
        payload = self.payload + [
            {'source': 'alloha', 'success': True, 'translations': [
                {'id': '7', 'name': '  STUDIO   A '}, {'id': 8, 'name': 'Субтитры'},
                {'id': 9, 'name': 'English subtitles'}, {'id': 10, 'name': ''}]},
            {'source': 'broken', 'success': False, 'translations': [{'id': 1, 'name': 'Fake'}]}]
        result = voices.parse_voices(payload, '2026-10-25T12:00:00+00:00')
        self.assertEqual([item['name'] for item in result['voices']], ['Studio A', 'Studio B'])
        self.assertEqual({p['source'] for p in result['voices'][0]['evidence']}, {'kodik', 'alloha'})
        self.assertEqual(voices.parse_voices([], 'now')['status'], 'empty')
        self.assertEqual(voices.parse_voices([{'success': False}], 'now')['status'], 'unavailable')

    async def test_tmdb_external_id_uses_tv_and_movie_namespaces(self):
        from contextlib import asynccontextmanager
        from services import tmdb

        @asynccontextmanager
        async def session():
            yield object()

        with (patch.object(tmdb, 'TMDB_API_KEY', 'test-key'), patch.object(tmdb, '_new_session', session),
              patch.object(tmdb, '_get_json', AsyncMock(return_value={'imdb_id': 'tt0903747'})) as get):
            self.assertEqual(await tmdb.external_imdb_id(1396, 'series'), 'tt0903747')
            self.assertTrue(get.call_args.args[1].endswith('/tv/1396/external_ids'))
            await tmdb.external_imdb_id(1396, 'movie')
            self.assertTrue(get.call_args.args[1].endswith('/movie/1396/external_ids'))
            get.return_value = {'imdb_id': 'wrong'}
            self.assertIsNone(await tmdb.external_imdb_id(1396, 'series'))

    async def test_chunked_response_is_bounded_without_content_length(self):
        # Exercise the streaming limit independently of a Content-Length header.
        app = web.Application()

        async def handle(request):
            response = web.StreamResponse(headers={'Content-Type': 'application/json'})
            await response.prepare(request)
            await response.write(b' ' * (voices.MAX_BYTES + 1))
            await response.write_eof()
            return response

        app.router.add_get('/players', handle)
        server = TestServer(app)
        await server.start_server()
        try:
            with patch.object(voices, 'API_URL', str(server.make_url('/players'))):
                with self.assertRaisesRegex(ValueError, 'too large'):
                    await voices._fetch('tt0903747')
        finally:
            await server.close()
