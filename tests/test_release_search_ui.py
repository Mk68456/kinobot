import os
import gc
from pathlib import Path
import tempfile
import time
import tkinter as tk
import unittest
from unittest.mock import Mock, patch

from tools.release_search_ui import open_search
from tests.test_release_search import candidate


class SearchUITests(unittest.TestCase):
    def setUp(self):
        gc.collect()  # Dispose previous test Tk variables on the UI thread before starting workers.
        try:
            self.root = tk.Tk()
        except tk.TclError:
            self.skipTest('Tk display unavailable')
        self.root.withdraw()
        self.addCleanup(self.root.destroy)
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.environment = patch.dict(os.environ, KINOTIME_STATE_DIR=self.temporary.name)
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.on_choose, self.on_prepare = Mock(), Mock()
        self.client = Mock()
        self.client.search.return_value = ([candidate()], [])
        self.factory = Mock(return_value=self.client)
        self.window = open_search(self.root, self.on_choose, self.on_prepare, client_factory=self.factory)
        self.window.withdraw()
        self.errors = []
        self.root.report_callback_exception = lambda *args: self.errors.append(args)
        def children(widget):
            for child in widget.winfo_children():
                yield child
                yield from children(child)
        self.widgets = list(children(self.window))
        self.buttons = {w.cget('text'): w for w in self.widgets if w.winfo_class() == 'TButton'}
        self.entries = [w for w in self.widgets if w.winfo_class() == 'TEntry']
        self.tree = next(w for w in self.widgets if w.winfo_class() == 'Treeview')
        self.set_entry(0, 'Пример')

    def set_entry(self, index, text):
        self.entries[index].delete(0, 'end')
        self.entries[index].insert(0, text)

    def settle(self):
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            self.root.update()
            if str(self.buttons['Найти варианты'].cget('state')) == 'normal':
                break
            time.sleep(.01)
        self.assertFalse(self.errors)

    def test_title_search_gets_source_without_manual_description(self):
        self.buttons['Найти варианты'].invoke()
        self.settle()
        self.assertEqual(len(self.tree.get_children()), 1)
        self.on_prepare.assert_not_called()
        self.buttons['Передать источник в очередь'].invoke()
        self.on_choose.assert_called_once_with(candidate()['source'])
        self.assertTrue((Path(self.temporary.name) / 'release-search-settings.json').exists())

    def test_automatic_mode_prepares_best_eligible_result(self):
        self.buttons['Найти и скачать'].invoke()
        self.settle()
        self.on_prepare.assert_called_once()
        self.assertEqual(self.on_prepare.call_args.args[1]['query'], 'Пример')
        self.assertEqual(self.on_prepare.call_args.args[0]['source'], candidate()['source'])

    def test_unknown_audio_blocks_automatic_preparation(self):
        row = candidate()
        row['description'].facts.pop('audio')
        self.client.search.return_value = ([row], [])
        self.buttons['Найти и скачать'].invoke()
        self.settle()
        self.assertEqual(len(self.tree.get_children()), 1)
        self.on_prepare.assert_not_called()

    def test_cancellation_does_not_prepare_results(self):
        def search(query, report, **kwargs):
            self.factory.call_args.kwargs['stop'].set()
            return [candidate()], []
        self.client.search.side_effect = search
        self.buttons['Найти и скачать'].invoke()
        self.settle()
        self.on_prepare.assert_not_called()

    def test_network_failure_is_not_shown_as_empty_search(self):
        self.client.search.side_effect = ValueError('Сайт недоступен')
        self.buttons['Найти варианты'].invoke()
        self.settle()
        labels = [str(self.root.getvar(w.cget('textvariable'))) for w in self.widgets
                  if 'textvariable' in w.keys() and w.cget('textvariable')]
        self.assertIn('Сайт недоступен', labels)
        self.on_prepare.assert_not_called()

    def test_wrong_resolution_is_hidden_and_auditable_but_cannot_be_queued(self):
        self.set_entry(6, '720')
        self.buttons['Найти варианты'].invoke()
        self.settle()
        self.assertEqual(len(self.tree.get_children()), 0)
        checkbox = next(w for w in self.widgets if w.winfo_class() == 'TCheckbutton'
                        and w.cget('text') == 'Показать исключённые')
        checkbox.invoke()
        self.root.update()
        self.assertEqual(len(self.tree.get_children()), 1)
        self.tree.selection_set('101')
        self.buttons['Передать источник в очередь'].invoke()
        self.on_choose.assert_not_called()
        labels = [str(self.root.getvar(w.cget('textvariable'))) for w in self.widgets
                  if 'textvariable' in w.keys() and w.cget('textvariable')]
        self.assertFalse(any('Лучший вариант' in value for value in labels))

    def test_size_changed_after_search_is_enforced_before_queue_transfer(self):
        self.buttons['Найти варианты'].invoke()
        self.settle()
        self.set_entry(3, '1')
        self.buttons['Передать источник в очередь'].invoke()
        self.on_choose.assert_not_called()
        self.buttons['Применить условия'].invoke()
        self.assertEqual(len(self.tree.get_children()), 0)

    def test_search_receives_filters_and_page_limit(self):
        self.buttons['Найти варианты'].invoke()
        self.settle()
        self.assertEqual(self.client.search.call_args.kwargs['max_pages'], 10)
        self.assertEqual(self.client.search.call_args.kwargs['settings']['height'], 1080)

    def test_new_series_download_does_not_require_a_card_code(self):
        from tools.release_description import parse_description
        row = candidate()
        row['title'] = 'Пример [S01] (2020) WEBRip 1080p'
        row['description'] = parse_description(row['title'] + '\n' + row['description'].text)
        self.client.search.return_value = ([row], [])
        self.set_entry(4, '1')
        self.buttons['Найти варианты'].invoke()
        self.settle()
        self.assertEqual(len(self.tree.get_children()), 1)
        self.buttons['Скачать выбранную раздачу'].invoke()
        self.on_prepare.assert_called_once()
        self.assertNotIn('movie_number', self.on_prepare.call_args.args[1])

    def test_controls_fit_minimum_size(self):
        self.window.transient('')
        self.window.geometry('980x620-10000-10000')
        self.window.deiconify()
        self.root.update()
        for button in self.buttons.values():
            self.assertLessEqual(button.winfo_x() + button.winfo_width(), button.master.winfo_width())
            self.assertGreater(button.winfo_height(), 10)
        bottom = self.buttons['Скачать выбранную раздачу'].master
        self.assertLessEqual(bottom.winfo_y() + bottom.winfo_height(), bottom.master.winfo_height())


if __name__ == '__main__':
    unittest.main()
