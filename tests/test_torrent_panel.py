import tempfile
import os
import threading
from pathlib import Path
import tkinter as tk
import unittest
from unittest.mock import Mock, patch

from tools import desktop_launcher, torrent_panel
from tools.torrent_queue import TorrentQueue


class LauncherTorrentUITests(unittest.TestCase):
    def test_reprepare_button_processes_only_selected_download_without_qbittorrent(self):
        try:
            root = tk.Tk()
        except tk.TclError:
            self.skipTest('Tk display unavailable')
        root.withdraw()
        self.addCleanup(root.destroy)
        with tempfile.TemporaryDirectory() as folder:
            working = Path(folder) / 'Downloads' / 'KinoTime-Torrents'
            working.mkdir(parents=True)
            queue = TorrentQueue(working, reserve_gib=0)
            job = {'id': 'a' * 32, 'title': 'Season', 'status': 'prepared', 'selected': [0],
                   'mode': 'season', 'catalog_section': 'series', 'movie_number': 3, 'season_number': 2,
                   'error': '', 'manifest': {'root': 'old-ready'}}
            stage = queue.folder(job) / 'import'
            stage.mkdir(parents=True)
            (stage / 'Episode.mp4').write_bytes(b'video')
            job['import_folder'] = str(stage)
            queue.jobs = [job]
            queue.save()
            callback = Mock(return_value={'root': 'new-ready', 'files': [{'path': '360p/Episode.mp4'}]})
            build = Mock(return_value=callback)
            page = tk.Frame(root)
            with patch.object(torrent_panel, '__file__', str(Path(folder) / 'torrent_panel.py')), \
                 patch.dict(os.environ, KINOTIME_STATE_DIR=str(Path(folder) / 'state'),
                            KINOTIME_DOWNLOAD_DIR=str(working)), \
                 patch('tools.torrent_panel.Path.home', return_value=Path(folder)), \
                 patch.object(TorrentQueue, 'check_space'), \
                 patch('tools.torrent_queue.QBitClient.login') as login:
                def background(work):
                    work()
                    return True
                shutdown = torrent_panel.mount(page, root, background, Mock(), threading.Event(), build,
                                               Mock(), lambda: False, {'Сериалы': 'series'})
                try:
                    def descendants(widget):
                        for child in widget.winfo_children():
                            yield child
                            yield from descendants(child)
                    widgets = list(descendants(page))
                    tree = next(widget for widget in widgets if widget.winfo_class() == 'Treeview')
                    tree.selection_set(job['id'])
                    button = next(widget for widget in widgets if 'text' in widget.keys()
                                  and widget.cget('text') == 'Переподготовить выбранную')
                    button.invoke()
                    root.update()
                    build.assert_called_once()
                    callback.assert_called_once()
                    processed, path = callback.call_args.args
                    self.assertEqual(processed['movie_number'], 3)
                    self.assertEqual(processed['season_number'], 2)
                    self.assertEqual(path, stage)
                    self.assertEqual(processed['manifest']['root'], 'new-ready')
                    login.assert_not_called()
                    self.assertEqual(TorrentQueue(working, reserve_gib=0).jobs[0]['status'], 'prepared')
                    self.assertEqual((stage / 'Episode.mp4').read_bytes(), b'video')
                finally:
                    shutdown()

    def test_launcher_builds_torrent_page_and_quiet_mode_blocks_send(self):
        try:
            root = tk.Tk()
        except tk.TclError:
            self.skipTest("Tk display unavailable")
        root.withdraw()
        with tempfile.TemporaryDirectory() as folder:

            def examine():
                def descendants(widget):
                    for child in widget.winfo_children():
                        yield child
                        yield from descendants(child)

                widgets = list(descendants(root))
                labels = {widget.cget("text"): widget for widget in widgets if "text" in widget.keys()}
                self.assertIn("⇣  Торренты", labels)
                self.assertIn("Скачать и подготовить", labels)
                self.assertIn("Переподготовить выбранную", labels)
                toggle = labels["Тихая подготовка (выключите перед отправкой)"]
                root.setvar(toggle.cget("variable"), True)
                labels["2. Отправить настроенные фильмы"].invoke()
                root.update()
                self.assertIn(
                    "Тихая подготовка",
                    next(
                        root.getvar(widget.cget("textvariable"))
                        for widget in widgets
                        if "textvariable" in widget.keys()
                        and widget.cget("textvariable")
                        and "Выключите" in str(root.getvar(widget.cget("textvariable")))
                    ),
                )

            with (
                patch("tkinter.Tk", return_value=root),
                patch.object(root, "mainloop", side_effect=examine),
                patch.object(desktop_launcher, "__file__", str(Path(folder) / "desktop_launcher.py")),
                patch.object(torrent_panel, "__file__", str(Path(folder) / "torrent_panel.py")),
                patch("tools.torrent_panel.Path.home", return_value=Path(folder)),
                patch("tools.torrent_queue.QBitClient.login") as login,
                patch.object(desktop_launcher, "TelegramDesktop") as desktop,
            ):
                try:
                    desktop_launcher.main()
                    login.assert_not_called()
                    desktop.assert_not_called()
                finally:
                    # Release the panel's session lock through the normal close path.
                    root.tk.call(root.protocol("WM_DELETE_WINDOW"))


if __name__ == "__main__":
    unittest.main()
