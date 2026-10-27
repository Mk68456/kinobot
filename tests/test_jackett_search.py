import gc
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import tempfile
import threading
import time
import tkinter as tk
import unittest
from unittest.mock import Mock, patch
import urllib.parse

from tools.jackett_search import JackettClient, local_url, merge_sources, torrent_hash, source_identity
from tools.jackett_sources_ui import load_sources, save_sources, selected_indexers
from tools.release_search import rank
from tools.release_search_ui import open_search
from tests.test_release_search import candidate


KEY = 'a123456789abcdef0123456789abcdef'
TORRENT = b'd8:announce31:https://tracker.test/secret/key4:infod6:lengthi123e4:name8:test.mkvee'


class JackettTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        env = patch.dict(os.environ, KINOTIME_STATE_DIR=self.temp.name)
        env.start()
        self.addCleanup(env.stop)
        self.calls = []
        self.broken = False
        self.redirect = False
        test = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                parsed = urllib.parse.urlsplit(self.path)
                query = urllib.parse.parse_qs(parsed.query)
                test.calls.append((parsed.path, query))
                if parsed.path.startswith('/dl/'):
                    if test.redirect:
                        self.send_response(302)
                        self.send_header('Location', 'magnet:?xt=urn:btih:' + 'b' * 40)
                        self.end_headers()
                        return
                    raw = TORRENT
                elif query.get('apikey') != [KEY]:
                    self.send_response(401)
                    self.end_headers()
                    return
                elif query.get('t') == ['indexers']:
                    raw = b'<indexers><indexer id="lostfilm" configured="true"><title>LostFilm.tv</title></indexer><indexer id="other" configured="false"><title>Hidden</title></indexer></indexers>'
                elif 'broken' in parsed.path:
                    raw = json.dumps({'Results': [], 'Indexers': [{'Status': 1, 'Error': 'private ' + KEY}]}).encode()
                else:
                    entry = {'Title': 'Example - S01E01 - Pilot - 1080p WEB-DL', 'Guid': 'release-1',
                             'Details': 'https://tracker.test/episode/1?passkey=secret', 'Size': 1024**3,
                             'Link': f'http://127.0.0.1:{self.server.server_port}/dl/lostfilm?jackett_apikey={KEY}&path=secret',
                             'Description': '', 'Seeders': 4, 'CategoryDesc': 'TV'}
                    raw = json.dumps({'Results': [entry], 'Indexers': [{'Status': 2}]}).encode()
                self.send_response(200)
                self.end_headers()
                self.wfile.write(raw)

        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.client = JackettClient(f'http://127.0.0.1:{self.server.server_port}', KEY)

    def test_configured_sources_and_selected_only_request(self):
        sources = self.client.indexers()
        self.assertEqual(sources, [{'id': 'lostfilm', 'name': 'LostFilm.tv'}])
        rows, errors = self.client.search('Example', sources)
        self.assertFalse(errors)
        self.assertEqual(rows[0]['tracker'], 'LostFilm.tv')
        self.assertEqual(self.calls[-1][0], '/api/v2.0/indexers/lostfilm/results')
        self.assertEqual(self.calls[-1][1]['Query'], ['Example'])
        self.assertNotIn(KEY, rows[0]['url'])
        self.assertNotIn('secret', rows[0]['url'])

    def test_real_private_torrent_is_materialized_without_losing_announce(self):
        rows, _ = self.client.search('Example', [{'id': 'lostfilm', 'name': 'LostFilm.tv'}])
        row = self.client.materialize(rows[0])
        path = Path(row['source'])
        self.assertEqual(path.read_bytes(), TORRENT)
        self.assertEqual(row['info_hash'], torrent_hash(TORRENT))
        self.assertNotIn(KEY, path.name)
        self.assertEqual(path.parent, Path(self.temp.name) / 'jackett-torrents')

    def test_download_can_redirect_to_magnet_but_never_another_http_host(self):
        self.redirect = True
        rows, _ = self.client.search('Example', [{'id': 'lostfilm', 'name': 'LostFilm.tv'}])
        resolved = self.client.materialize(rows[0])
        self.assertEqual(source_identity(resolved), 'b' * 40)
        self.assertEqual(self.client.download_url('http://elsewhere.test/dl/lostfilm?apikey=' + KEY), '')
        with self.assertRaises(ValueError):
            self.client.materialize({**rows[0], 'download_path': '//elsewhere.test/dl/lostfilm'})

    def test_one_tracker_failure_preserves_other_results_without_exposing_backend_error(self):
        rows, errors = self.client.search('Example', [{'id': 'broken', 'name': 'Broken'}, {'id': 'lostfilm', 'name': 'LostFilm.tv'}])
        self.assertEqual(len(rows), 1)
        self.assertEqual(len(errors), 1)
        self.assertNotIn(KEY, str(errors))

    def test_strict_unknown_advertising_and_voice_filters(self):
        rows, _ = self.client.search('Example', [{'id': 'lostfilm', 'name': 'LostFilm.tv'}])
        options = {'season': 1, 'height': 1080, 'preferred_voice': 'LostFilm', 'max_gib': 2}
        filtered = rank(rows, 'Example', avoid_ads=True, **options)[0]
        self.assertTrue(filtered['filter_issues'])
        accepted = rank(rows, 'Example', avoid_ads=False, **options)[0]
        self.assertEqual(accepted['filter_issues'], [])
        self.assertTrue(accepted['issues'])  # Audio is not invented from an indexer name.
        self.assertTrue(accepted['source_available'])
        self.assertFalse(accepted['source'])
        self.assertTrue(rank(rows, 'Example', avoid_ads=False, **{**options, 'height': 720})[0]['filter_issues'])

    def test_secret_is_not_saved_with_source_selection(self):
        prefs = {'rutor': False, 'url': self.client.url, 'api_key': KEY,
                 'indexers': [{'id': 'lostfilm', 'name': 'LostFilm.tv'}, {'id': 'rutracker', 'name': 'RuTracker'}],
                 'selected': ['lostfilm']}
        save_sources(prefs)
        restored = load_sources()
        self.assertFalse(restored['rutor'])
        self.assertEqual(selected_indexers(restored), [prefs['indexers'][0]])
        self.assertEqual(restored['api_key'], '')
        self.assertNotIn(KEY, (Path(self.temp.name) / 'search-sources.json').read_text('utf-8'))

    def test_bad_key_and_remote_connection(self):
        with self.assertRaisesRegex(ValueError, 'API'):
            JackettClient(self.client.url, 'wrong')
        for url in ('http://example.com:9117', 'http://127.0.0.1:9117/?apikey=x', 'http://user:pass@localhost:9117'):
            with self.assertRaises(ValueError):
                local_url(url)
        with self.assertRaisesRegex(ValueError, 'API'):
            JackettClient(self.client.url, 'z' * 32).indexers()

    def test_cancel_stops_before_request(self):
        self.client.stop.set()
        with self.assertRaises(InterruptedError):
            self.client.indexers()
        self.assertFalse(self.calls)

    def test_torrent_validation_hashes_original_info_bytes(self):
        info = b'd6:lengthi123e4:name8:test.mkve'
        self.assertEqual(torrent_hash(TORRENT), hashlib.sha1(info).hexdigest())
        for raw in (b'<html>login', TORRENT[:-1], b'de', b'd4:infoi1ee', b'd4:infode4:infodee'):
            with self.assertRaises(ValueError):
                torrent_hash(raw)

    def test_duplicate_hash_collapse_keeps_distinct_evidence(self):
        row = candidate()
        merged = merge_sources([row, {**row, 'id': 'other', 'tracker': 'Other'}])
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]['tracker'], 'Rutor, Other')
        different = candidate()
        different['description'].add('translation', 'New voice', 'New voice')
        self.assertEqual(len(merge_sources([row, different])), 2)


class SourceSelectionUITests(unittest.TestCase):
    def setUp(self):
        gc.collect()
        try:
            self.root = tk.Tk()
        except tk.TclError:
            self.skipTest('Tk display unavailable')
        self.root.withdraw()
        self.addCleanup(self.cleanup_gui)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        env = patch.dict(os.environ, KINOTIME_STATE_DIR=self.temp.name)
        env.start()
        self.addCleanup(env.stop)
        save_sources({'rutor': False, 'url': 'http://127.0.0.1:9117', 'api_key': '',
                      'selected': ['lostfilm'], 'indexers': [{'id': 'lostfilm', 'name': 'LostFilm.tv'},
                                                           {'id': 'rutracker', 'name': 'RuTracker'}]})
        self.fake = Mock()
        self.fake.search.return_value = ([{**candidate(), 'tracker': 'LostFilm.tv', 'provider': 'jackett'}], [])
        self.fake.stats = {'scanned': 1, 'limit_reached': False}
        self.connect = patch('tools.release_search_ui.connection', return_value=self.fake)
        self.connect.start()
        self.addCleanup(self.connect.stop)
        self.rutor = Mock()
        self.choose, self.prepare = Mock(), Mock()
        self.window = open_search(self.root, self.choose, self.prepare, client_factory=self.rutor)
        self.widgets = list(self.walk(self.window))
        self.buttons = {w.cget('text'): w for w in self.widgets if w.winfo_class() == 'TButton'}
        entry = next(w for w in self.widgets if w.winfo_class() == 'TEntry')
        entry.insert(0, 'Пример')

    def cleanup_gui(self):
        # Release destroyed Tcl objects on the UI thread before later tests start workers.
        self.root.destroy()
        self.widgets.clear()
        self.buttons.clear()
        self.window = None
        self.root = None
        gc.collect()

    def walk(self, widget):
        for child in widget.winfo_children():
            yield child
            yield from self.walk(child)

    def settle(self):
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            self.root.update()
            if str(self.buttons['Найти варианты'].cget('state')) == 'normal':
                return
            time.sleep(.01)
        self.fail('Worker did not finish')

    def test_lostfilm_only_never_calls_rutor_or_other_tracker(self):
        self.buttons['Найти варианты'].invoke()
        self.settle()
        self.rutor.assert_not_called()
        self.assertEqual(self.fake.search.call_args.args[1], [{'id': 'lostfilm', 'name': 'LostFilm.tv'}])
        tree = next(w for w in self.widgets if w.winfo_class() == 'Treeview')
        self.assertEqual(len(tree.get_children()), 1)
        self.assertIn('LostFilm.tv', tree.item(tree.get_children()[0])['values'])

    def test_private_download_resolves_before_queue_callback(self):
        row = {**candidate(), 'tracker': 'LostFilm.tv', 'provider': 'jackett', 'source': '',
               'source_available': True, 'download_path': '/dl/lostfilm?secret'}
        self.fake.search.return_value = ([row], [])
        self.fake.materialize.return_value = {**row, 'source': str(Path(self.temp.name) / 'source.torrent'),
                                              'info_hash': 'c' * 40}
        self.buttons['Найти варианты'].invoke()
        self.settle()
        self.prepare.assert_not_called()
        self.buttons['Скачать выбранную раздачу'].invoke()
        self.settle()
        self.fake.materialize.assert_called_once()
        self.prepare.assert_called_once()
        self.assertTrue(self.prepare.call_args.args[0]['source'].endswith('.torrent'))

    def test_source_dialog_really_saves_only_checked_source_and_invalidates_old_results(self):
        self.buttons['Источники…'].invoke()
        self.root.update()
        dialog = next(w for w in self.window.winfo_children() if w.winfo_class() == 'Toplevel')
        widgets = list(self.walk(dialog))
        checks = {w.cget('text'): w for w in widgets if w.winfo_class() == 'TCheckbutton'}
        checks['LostFilm.tv'].invoke()
        checks['RuTracker'].invoke()
        next(w for w in widgets if w.winfo_class() == 'TButton' and w.cget('text') == 'Сохранить выбор').invoke()
        self.root.update()
        self.assertEqual(load_sources()['selected'], ['rutracker'])
        self.buttons['Найти варианты'].invoke()
        self.settle()
        self.assertEqual(self.fake.search.call_args.args[1], [{'id': 'rutracker', 'name': 'RuTracker'}])

    def test_source_dialog_controls_fit_minimum_size(self):
        self.window.transient('')
        self.window.geometry('980x620-10000-10000')
        self.window.deiconify()
        self.root.update()
        self.buttons['Источники…'].invoke()
        self.root.update()
        dialog = next(w for w in self.window.winfo_children() if w.winfo_class() == 'Toplevel')
        dialog.transient('')
        dialog.geometry('620x520-10000-10000')
        dialog.deiconify()
        self.root.update()
        for widget in self.walk(dialog):
            if widget.winfo_class() == 'TButton':
                self.assertGreater(widget.winfo_height(), 10, widget.cget('text'))
                self.assertLessEqual(widget.winfo_x() + widget.winfo_width(), widget.master.winfo_width(), widget.cget('text'))


if __name__ == '__main__':
    unittest.main()
