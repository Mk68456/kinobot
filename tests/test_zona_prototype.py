import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from tools.zona_prototype import Cancelled, Sources, discover, download, download_page, media_name, probe, request
from tools.zona_source import discover_zona, is_zona_page, sources_from_data
from tools.zona_modern import client_time, decode_response, encode_query, page_entity


VIDEO = b'\x00\x00\x00\x18ftypmp42' + bytes(range(256)) * 3000


class Handler(BaseHTTPRequestHandler):
    requests_seen = []

    def log_message(self, *args):
        pass

    def do_GET(self):
        if self.path in ('/blocked', '/blocked.mp4'):
            self.send_error(403)
            return
        if self.path == '/tvseries/test':
            return self.json_response({'serial': {'name_rus': 'Тест'}, 'episodes': {'items': [
                {'season': 1, 'episode': 2, 'mobi_link_id': 8},
                {'season': 1, 'episode': 1, 'mobi_link_id': 7}]}})
        if self.path == '/tvseries/modern':
            entity = {'slug': 'modern', 'title': 'Тест', 'kp_id': 123,
                      'seasons': [{'part': 1, 'is_banned': False}],
                      'episodes': [{'part': 1, 'is_banned': False}]}
            record = '6:' + json.dumps(['$', 'component', None, {'data': entity}]) + '\n'
            body = ('<script>self.__next_f.push(' + json.dumps([1, record]) + ')</script>').encode()
            self.send_response(200)
            self.send_header('Content-Type', 'text/html')
            self.end_headers()
            self.wfile.write(body)
            return
        if self.path.startswith('/getVideoSources?'):
            return self.player_response({'data': [{'video_source_type_id': 3,
                                                  'episode_key': 'S01E01', 'download_link_key': '456-slug'}]})
        if self.path.startswith('/getStreams/?'):
            return self.player_response([{'url': f'http://127.0.0.1:{self.server.server_port}/video.mp4',
                                          'translation': 'LostFilm', 'language': 'ru', 'resolution': '480p',
                                          'isTrailer': False, 'size': len(VIDEO), 'duration': 3200000000}])
        if self.path.startswith('/ajax/video/7?'):
            base = f'http://127.0.0.1:{self.server.server_port}'
            return self.json_response({'lqUrl': base + '/blocked.mp4', 'url': base + '/protected.mp4'})
        if self.path == '/protected.mp4' and self.headers.get('Referer') != 'https://w140.zona.plus/':
            self.send_error(403)
            return
        if self.path == '/page':
            body = b'<video><source src="/video.mp4" label="480p"></video>'
            self.send_response(200)
            self.send_header('Content-Type', 'text/html')
            self.end_headers()
            self.wfile.write(body)
            return
        offset = 0
        range_header = self.headers.get('Range')
        self.requests_seen.append((self.path, range_header))
        if range_header and self.path != '/ignore.mp4':
            offset = int(range_header.split('=')[1].split('-')[0])
            end = range_header.split('-')[1]
            last = int(end) if end else len(VIDEO) - 1
            self.send_response(206)
            self.send_header('Content-Range', f'bytes {offset}-{last}/{len(VIDEO)}')
        else:
            last = len(VIDEO) - 1
            self.send_response(200)
        body = VIDEO[offset:last + 1]
        self.send_header('Content-Type', 'video/mp4')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('ETag', '"fixture-v1"')
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass

    def json_response(self, value):
        body = json.dumps(value).encode()
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def player_response(self, value):
        body = encode_query(json.dumps(value), 31).encode()
        self.send_response(200)
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class DownloadTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f'http://127.0.0.1:{cls.server.server_port}'

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()

    def test_discovery_and_probe(self):
        found = discover(self.base + '/page')
        self.assertEqual(found[0]['label'], '480p')
        self.assertEqual(probe(found[0]['url'])['size'], len(VIDEO))

    def test_forbidden_has_actionable_error(self):
        with self.assertRaisesRegex(ValueError, '403'):
            discover(self.base + '/blocked')

    def test_resume_and_ignored_range(self):
        for source in ('/video.mp4', '/ignore.mp4'):
            with self.subTest(source=source), tempfile.TemporaryDirectory() as folder:
                stop = threading.Event()
                def progress(done, total):
                    stop.set()
                with self.assertRaises(Cancelled):
                    download(self.base + source, folder, progress=progress, cancel=stop)
                self.assertTrue(list(Path(folder).glob('*.part')))
                result = download(self.base + source, folder)
                self.assertEqual(result.read_bytes(), VIDEO)
                record = json.loads(result.with_suffix('.mp4.json').read_text())
                self.assertEqual(record['sha256'], hashlib.sha256(VIDEO).hexdigest())
                self.assertEqual(record['status'], 'ready')
                self.assertEqual(download(self.base + source, folder), result)
                self.assertFalse(list(Path(folder).glob('*.part')))
                self.assertIn((source, 'bytes=262144-'), Handler.requests_seen)

    def test_parser_deduplicates_and_filename_stays_local(self):
        parser = Sources('https://example.test/page')
        parser.feed('<source src="/a.mp4"><a href="/a.mp4">same</a>')
        self.assertEqual(len(parser.items), 1)
        self.assertNotIn('/', media_name('https://example.test/..%2Fbad.mp4'))

    def test_zona_page_to_complete_file(self):
        seen = []
        def local_request(url, headers=None, method=None):
            seen.append((url, headers))
            url = url.replace('https://w140.zona.plus', self.base)
            return request(url, headers, method)
        with tempfile.TemporaryDirectory() as folder, patch('tools.zona_prototype.request', local_request):
            result = download_page('https://w140.zona.plus/tvseries/test', folder)
            self.assertEqual(result.read_bytes(), VIDEO)
            record = json.loads(result.with_suffix('.mp4.json').read_text())
            self.assertEqual(record['metadata']['episode'], 1)
            self.assertEqual(record['metadata']['season'], 1)
            self.assertEqual(record['status'], 'ready')
            self.assertEqual(record['page_url'], 'https://w140.zona.plus/tvseries/test')
        self.assertTrue(any('/ajax/video/7?' in url for url, _ in seen))
        self.assertTrue(any(headers and headers.get('X-Requested-With') == 'XMLHttpRequest' for _, headers in seen))

    def test_zona_unavailable_source_is_not_ready(self):
        with tempfile.TemporaryDirectory() as folder, patch('tools.zona_prototype.discover', return_value=[
            {'url': self.base + '/blocked.mp4', 'label': 'Недоступное видео'}]):
            with self.assertRaisesRegex(ValueError, 'видеоисточники недоступны'):
                download_page('https://w1.zona.im/tvseries/test', folder)
            self.assertFalse(list(Path(folder).glob('*.mp4')))

    def test_zona_movie_and_no_fabricated_quality(self):
        def fake_json(url, headers=None):
            import io
            return io.BytesIO(json.dumps({'url': self.base + '/video.mp4',
                                         'lqHlsUrl': self.base + '/stream.m3u8'}).encode())
        items = sources_from_data({'movie': {'mobi_link_id': 17, 'name_rus': 'Фильм'}},
                                  'https://w140.zona.plus/movies/test', fake_json, 'https://w140.zona.plus')
        self.assertEqual(len(items), 1)
        self.assertIsNone(items[0]['metadata']['episode'])
        self.assertEqual(items[0]['metadata']['variant'], 'url')
        with self.assertRaisesRegex(ValueError, 'идентификатора'):
            sources_from_data({'movie': {}}, '', fake_json, self.base)

    def test_zona_domain_boundaries(self):
        self.assertTrue(is_zona_page('https://w1.zona.im/tvseries/test'))
        self.assertTrue(is_zona_page('https://w140.zona.plus/tvseries/test/season-2'))
        self.assertFalse(is_zona_page('https://w1.zona.im.evil.test/tvseries/test'))
        with self.assertRaises(ValueError):
            discover_zona('https://example.test/tvseries/test', request)

    def test_modern_page_to_complete_file(self):
        seen = []
        def local_request(url, headers=None, method=None):
            from urllib.parse import parse_qs, urlsplit
            seen.append(url)
            if '/getVideoSources?' in url or '/getStreams/?' in url:
                query = decode_response(parse_qs(urlsplit(url).query)['q'][0], 17)
                if '/getVideoSources?' in url:
                    self.assertIn('kinopoisk_id=123&episode_key=S01E01', query)
                else:
                    self.assertIn('extractor=filmix720p&key=456&season=1&episode=1', query)
            for origin in ('https://w1.zona.im', 'https://apiw1.mzona.net', 'https://vsw01.zonasearch.com'):
                url = url.replace(origin, self.base)
            return request(url, headers, method)
        with tempfile.TemporaryDirectory() as folder, patch('tools.zona_prototype.request', local_request):
            result = download_page('https://w1.zona.im/tvseries/modern', folder)
            self.assertEqual(result.read_bytes(), VIDEO)
            record = json.loads(result.with_suffix('.mp4.json').read_text())
            self.assertEqual(record['metadata']['translation'], 'LostFilm')
            self.assertEqual(record['metadata']['duration_seconds'], 3200)
        self.assertTrue(any('/getStreams/' in url for url in seen))

    def test_modern_parser_and_codec_reject_changed_format(self):
        self.assertEqual(decode_response(encode_query('Тест ответа', 31)), 'Тест ответа')
        with self.assertRaises(ValueError):
            decode_response('invalid-not-base64')
        with self.assertRaises(ValueError):
            page_entity('<html>other page</html>', 'modern')
        # Independently calculated using the web player's JavaScript integer/hash semantics.
        self.assertEqual(client_time(1793123660), '1793123660791.218')

    def test_short_preview_is_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaisesRegex(ValueError, 'превью'):
                download(self.base + '/video.mp4', folder, metadata={'expected_size': len(VIDEO) + 1024})
            self.assertFalse(list(Path(folder).glob('*.mp4')))

    def test_upload_size_cap_prevents_download(self):
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaisesRegex(ValueError, 'лимит'):
                download(self.base + '/video.mp4', folder, max_bytes=100)
            self.assertFalse(list(Path(folder).glob('*.part')))


if __name__ == '__main__':
    unittest.main()
