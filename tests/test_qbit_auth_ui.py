import json
import os
from pathlib import Path
import tempfile
import threading
import tkinter as tk
import unittest
from unittest.mock import Mock, patch

from tools import torrent_panel


class QBitAuthUITests(unittest.TestCase):
    def test_mode_switch_sends_key_and_does_not_persist_credentials(self):
        try:
            root = tk.Tk()
        except tk.TclError:
            self.skipTest('Tk display unavailable')
        root.withdraw()
        self.addCleanup(root.destroy)
        with tempfile.TemporaryDirectory() as folder:
            state = Path(folder) / 'state'
            with patch.dict(os.environ, KINOTIME_STATE_DIR=str(state), KINOTIME_DOWNLOAD_DIR=str(Path(folder) / 'downloads')), \
                 patch.object(torrent_panel, 'QBitClient') as constructor:
                constructor.return_value.request.return_value = 'v5.2.4'
                report = Mock()
                def background(fn):
                    fn()
                    return True
                page = tk.Frame(root)
                shutdown = torrent_panel.mount(page, root, background, report, threading.Event(),
                                               Mock(), Mock(), lambda: False, {'Фильмы': 'movies'})
                try:
                    def descendants(widget):
                        for child in widget.winfo_children():
                            yield child
                            yield from descendants(child)
                    widgets = list(descendants(page))
                    by_key = next(w for w in widgets if w.winfo_class() == 'TRadiobutton'
                                  and w.cget('value') == 'api_key')
                    by_password = next(w for w in widgets if w.winfo_class() == 'TRadiobutton'
                                       and w.cget('value') == 'password')
                    connect = next(w for w in widgets if w.winfo_class() == 'TButton'
                                   and w.cget('text') == 'Подключить / обновить')
                    secret_entries = [w for w in widgets if w.winfo_class() == 'TEntry' and w.cget('show') == '*']
                    password, key_entry = secret_entries
                    self.assertEqual(str(key_entry.cget('state')), 'disabled')
                    by_key.invoke()
                    self.assertEqual(str(password.cget('state')), 'disabled')
                    key = 'qbt_' + 'A' * 28
                    key_entry.insert(0, key)
                    connect.invoke()
                    root.update()
                    constructor.assert_called_with('http://127.0.0.1:8080', api_key=key)
                    text = (state / 'torrent-settings.json').read_text(encoding='utf-8')
                    saved = json.loads(text)
                    self.assertEqual(saved['auth_mode'], 'api_key')
                    for secret in ('api_key', 'password', 'source'):
                        self.assertNotIn(secret, saved)
                    self.assertNotIn(key, text)
                    by_password.invoke()
                    password.insert(0, ' password ')
                    connect.invoke()
                    root.update()
                    constructor.assert_called_with('http://127.0.0.1:8080', 'admin', ' password ')
                    self.assertNotIn(' password ', (state / 'torrent-settings.json').read_text(encoding='utf-8'))
                finally:
                    shutdown()


if __name__ == '__main__':
    unittest.main()
