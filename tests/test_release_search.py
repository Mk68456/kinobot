import http.server
from email.message import Message
import threading
import unittest
from unittest.mock import Mock

from tools.release_search import (RutorClient, auto_file_ids, episode_count, evaluate, identity_matches,
                                  magnet_hash, rank, release_details, search_rows, required_issues)


MAGNET = 'magnet:?xt=urn:btih:' + 'a' * 40
SEARCH = '''<html><div id="index"><table>
<tr><th>Название</th></tr>
<tr><td><a href="/torrent/101/test">Пример / Example (2020) WEBRip 1080p</a></td></tr>
<tr><td><a href="/torrent/101/test">Повтор</a></td></tr>
<tr><td><a href="https://evil.example/torrent/102/test">Чужая страница</a></td></tr>
</table></div></html>'''
DETAIL = '''<html><h1>Пример / Example (2020) WEBRip 1080p</h1>
<a href="''' + MAGNET + '''">Скачать</a>
<div id="description"><b>Название:</b> Пример<br>
<b>Оригинальное название:</b> Example<br><b>Год выпуска:</b> 2020<br>
<b>Качество:</b> WEBRip 1080p<br><b>Перевод:</b> Студия А<br>
<b>Звук:</b> Русский AAC<br><b>Реклама:</b> нет<br>
<b>Видео:</b> AVC 1920x1080<br><b>Формат:</b> Matroska<br></div>
<table id="details"><tr><td>Размер</td><td>2.00 GB (2147483648 Bytes)</td></tr>
<tr><td>Раздают</td><td>15</td></tr><tr><td>Категория</td><td>Зарубежные фильмы</td></tr></table>
<div id="comments">Качество: CAMRip 720p. Перевод: Другая студия</div></html>'''


def candidate():
    return release_details(DETAIL, {'id': '101', 'title': 'Пример', 'url': 'https://rutor.info/torrent/101/test'})


class SearchTests(unittest.TestCase):
    def test_parse_search_deduplicates_and_excludes_navigation(self):
        rows = search_rows(SEARCH, 'https://rutor.info')
        self.assertEqual([row['id'] for row in rows], ['101', '102'])
        self.assertEqual(rows[0]['url'], 'https://rutor.info/torrent/101/test')
        with self.assertRaisesRegex(ValueError, 'распознать'):
            search_rows('<html>Проверка браузера</html>', 'https://rutor.info')
        self.assertEqual(search_rows('<h1>Ничего не найдено</h1>', 'https://rutor.info'), [])

    def test_details_ignore_comments_and_use_exact_whole_release_size(self):
        row = candidate()
        self.assertEqual(row['seeds'], 15)
        self.assertEqual(row['source'], MAGNET)
        self.assertEqual(row['description'].value('translation'), 'Студия А')
        self.assertEqual(row['description'].value('quality'), 'WEBRIP')
        self.assertEqual(row['description'].size_bytes, 2 * 1024**3)
        self.assertEqual(evaluate(row, 'Пример', year='2020', preferred_voice='Студия А'), [])

    def test_current_rutor_table_layout_without_description_id(self):
        original = candidate()
        body = DETAIL.split('<div id="description">')[1].split('</div>')[0]
        html = ('<h1>Пример / Example (2020) WEBRip 1080p</h1><a href="' + MAGNET + '">M</a>'
                '<table id="details"><tr><td></td><td>' + body + '</td></tr>'
                '<tr><td class="header">Размер</td><td>2 GiB</td></tr>'
                '<tr><td class="header">Раздают</td><td>5</td></tr>'
                '<tr><td colspan="2"><div id="index"><table><tr><td>Размер</td>'
                '<td>100 GB</td></tr></table></div></td></tr></table>')
        row = release_details(html, original)
        self.assertEqual(row['description'].value('title'), 'Пример')
        self.assertEqual(row['description'].size_bytes, 2 * 1024**3)
        self.assertEqual(row['seeds'], 5)
        self.assertEqual(len(row['description'].facts['size']), 1)

    def test_firefox_extension_header_is_only_sent_to_its_mirror(self):
        for base, expected in (('https://cat.dark-proxy.ru', 'rutor'), ('https://rutor.info', None)):
            client = RutorClient(base)
            response = Mock()
            response.headers = Message()
            response.headers['Content-Type'] = 'text/html; charset=utf-8'
            response.read.return_value = b'<html></html>'
            client.opener = Mock()
            client.opener.open.return_value.__enter__ = Mock(return_value=response)
            client.opener.open.return_value.__exit__ = Mock(return_value=False)
            client.get(base + '/')
            request = client.opener.open.call_args.args[0]
            self.assertEqual(request.get_header('X-proxy-access'), expected)

    def test_auto_choice_rejects_remake_unknowns_and_series(self):
        row = candidate()
        self.assertIn('Нужный год не подтверждён', evaluate(row, 'Пример', year='2021'))
        self.assertFalse(identity_matches('Суперпример', row))
        self.assertTrue(identity_matches('Example', row))
        row['seeds'] = 0
        self.assertIn('Нет подтверждённых раздающих', evaluate(row, 'Пример'))
        row['category'] = 'Зарубежные сериалы'
        self.assertIn('Это сериал: укажите нужный сезон', evaluate(row, 'Пример'))

    def test_ranking_prefers_confirmed_candidate_over_unconfirmed_with_more_seeds(self):
        bad, good = candidate(), candidate()
        bad.update(id='102', seeds=1000)
        bad['description'].facts.pop('translation')
        rows = rank([bad, good], 'Пример', preferred_voice='Студия А')
        self.assertEqual(rows[0]['id'], '101')

    def test_remakes_require_year_before_automatic_choice(self):
        first, second = candidate(), candidate()
        second.update(id='102', title='Пример / Example (2021) WEBRip 1080p')
        rows = rank([first, second], 'Пример')
        self.assertTrue(all(any('разные годы' in issue for issue in row['issues']) for row in rows))

    def test_network_search_is_bounded_cancellable_and_reports_partial_failures(self):
        calls = []
        def transport(url):
            calls.append(url)
            return SEARCH if '/search/' in url else DETAIL
        client = RutorClient(transport=transport)
        rows, errors = client.search('Пример')
        self.assertEqual(len(rows), 1)
        self.assertEqual(len(errors), 1)
        self.assertEqual(len(calls), 2)  # Off-site links are rejected before transport.
        self.assertIn('%D0%9F', calls[0])
        client.stop.set()
        with self.assertRaises(InterruptedError):
            client.search('Пример')

    def test_local_http_end_to_end_search_and_detail(self):
        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                content = SEARCH if self.path.startswith('/search/') else DETAIL
                self.send_response(200)
                self.send_header('Content-Type', 'text/html; charset=utf-8')
                self.end_headers()
                self.wfile.write(content.encode('utf-8'))
            def log_message(self, *args):
                pass
        server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            client = RutorClient(f'http://127.0.0.1:{server.server_port}')
            rows, errors = client.search('Пример')
            self.assertEqual(len(rows), 1)
            self.assertEqual(rank(rows, 'Пример')[0]['issues'], [])
            self.assertEqual(len(errors), 1)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(2)

    def test_redirect_to_another_site_is_rejected(self):
        from tools.release_search import _Redirects
        redirects = _Redirects({'rutor.info'})
        with self.assertRaises(ValueError):
            redirects.redirect_request(None, None, 302, '', {}, 'http://127.0.0.1/private')
        for base in ('file:///tmp', 'https://user:pass@rutor.info', 'https://rutor.info/path'):
            with self.assertRaises(ValueError):
                RutorClient(base)

    def test_series_count_requires_complete_range_and_checks_file_count(self):
        from tools.release_description import parse_description
        full = parse_description('Релиз: Example [02x01-14 из 14] WEBRip 1080p')
        partial = parse_description('Релиз: Example [02x01-04 из 14] WEBRip 1080p')
        self.assertEqual(full.value('season'), '2')
        self.assertEqual(episode_count(full), 14)
        self.assertIsNone(episode_count(partial))
        files = [{'index': n, 'name': f'Example.S02E{n+1:02}.mkv'} for n in range(2)]
        self.assertEqual(auto_file_ids(files, season=2, expected_count=2), [0, 1])
        with self.assertRaises(ValueError):
            auto_file_ids(files, season=2, expected_count=14)

    def test_ambiguous_files_require_manual_selection(self):
        def files(*names):
            return [{'index': n, 'name': name} for n, name in enumerate(names)]
        self.assertEqual(auto_file_ids(files('Movie.mkv', 'sample/sample.mp4', 'poster.jpg')), [0])
        for names, season in ((('Movie.mkv', 'Other.mkv'), 0), (('Episode.mkv',), 1),
                              (('S01E01.mkv', 'S01E01.mp4'), 1),
                              (('S01E01E02.mkv',), 1), (('S02E01.mkv',), 1)):
            with self.subTest(names=names), self.assertRaises(ValueError):
                auto_file_ids(files(*names), season=season)

    def test_magnet_validation(self):
        self.assertEqual(magnet_hash(MAGNET), 'a' * 40)
        self.assertFalse(magnet_hash('magnet:?xt=urn:btih:garbage'))
        self.assertFalse(magnet_hash(MAGNET + '\n'))

    def test_each_filled_condition_is_a_hard_filter(self):
        row = candidate()
        self.assertEqual(required_issues(row, 'Пример'), [])
        for options, reason in (({'year': '2021'}, 'год'), ({'height': 720}, 'разрешение'),
                                ({'max_gib': 1}, 'лимита'), ({'preferred_voice': 'Студия Б'}, 'озвучка'),
                                ({'season': 1}, 'сезон')):
            with self.subTest(options=options):
                self.assertTrue(any(reason in issue for issue in required_issues(row, 'Пример', **options)))
        row['description'].facts.pop('advertising')
        self.assertTrue(required_issues(row, 'Пример'))
        self.assertEqual(required_issues(row, 'Пример', avoid_ads=False), [])

    def test_blank_size_and_height_disable_only_those_filters(self):
        row = candidate()
        row['description'].facts.pop('resolution')
        row['description'].facts.pop('size')
        row['description'].size_bytes = None
        self.assertTrue(required_issues(row, 'Пример'))
        self.assertEqual(required_issues(row, 'Пример', height=None, max_gib=None), [])

    def test_paginated_search_reads_more_than_eight_and_deduplicates(self):
        def page(ids, next_link=''):
            return '<div id="index"><table>' + ''.join(
                f'<tr><td><a href="/torrent/{n}/test">Пример (2020) WEBRip 1080p</a></td></tr>'
                for n in ids) + '</table></div>' + next_link
        def transport(url):
            if '/search/0/' in url:
                return page(range(100, 110), '<a href="/search/1/0/000/0/Пример">Далее</a>')
            if '/search/1/' in url:
                return page(range(109, 115))
            return DETAIL
        stop = Mock()
        stop.is_set.return_value = stop.wait.return_value = False
        client = RutorClient(transport=transport, stop=stop)
        rows, errors = client.search('Пример', settings={'season': 0, 'height': 1080})
        self.assertEqual(len(rows), 15)
        self.assertEqual(errors, [])
        self.assertEqual(client.stats['pages'], 2)
        self.assertEqual(client.stats['descriptions'], 15)
        self.assertFalse(client.stats['limit_reached'])
        client.search('Пример', max_pages=1)
        self.assertEqual(client.stats['pages'], 1)
        self.assertTrue(client.stats['limit_reached'])

    def test_prefilter_skips_other_seasons_but_continues_to_later_pages(self):
        calls = []
        def transport(url):
            calls.append(url)
            if '/search/0/' in url:
                return ('<div id="index"><table><tr><td><a href="/torrent/100/test">'
                        'Пример [S05] (2026) WEBRip 1080p</a></td></tr></table></div>'
                        '<a href="/search/1/0/000/0/Пример">Далее</a>')
            if '/search/1/' in url:
                return ('<div id="index"><table><tr><td><a href="/torrent/101/test">'
                        'Пример [S01] (2020) WEBRip 1080p</a></td></tr></table></div>')
            return DETAIL.replace('Пример / Example (2020)', 'Пример / Example [S01] (2020)')
        stop = Mock()
        stop.is_set.return_value = stop.wait.return_value = False
        client = RutorClient(transport=transport, stop=stop)
        rows, _ = client.search('Пример', settings={'season': 1, 'height': 1080})
        self.assertEqual(client.stats['prefiltered'], 1)
        self.assertEqual(client.stats['descriptions'], 1)
        self.assertFalse(any('/torrent/100/' in url for url in calls))
        ranked = rank(rows, 'Пример', season=1)
        self.assertEqual([row['id'] for row in ranked if not row['filter_issues']], ['101'])
        self.assertFalse(any('разные годы' in issue for row in ranked if not row['filter_issues'] for issue in row['issues']))


if __name__ == '__main__':
    unittest.main()
