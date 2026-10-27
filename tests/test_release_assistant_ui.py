import tkinter as tk
import unittest
from unittest.mock import Mock

from tools.release_assistant_ui import open_assistant


class AssistantUITests(unittest.TestCase):
    def setUp(self):
        try:
            self.root = tk.Tk()
        except tk.TclError:
            self.skipTest('Tk display unavailable')
        self.root.withdraw()
        self.addCleanup(self.root.destroy)
        self.callback = Mock()
        self.window = open_assistant(self.root, self.callback)
        self.window.withdraw()

        def children(widget):
            for child in widget.winfo_children():
                yield child
                yield from children(child)

        self.widgets = list(children(self.window))
        self.buttons = {str(w.cget('text')): w for w in self.widgets if w.winfo_class() == 'TButton'}
        self.entries = [w for w in self.widgets if w.winfo_class() == 'TEntry']
        self.text = next(w for w in self.widgets if w.winfo_class() == 'Text' and w.cget('state') == 'normal')
        self.detail = next(w for w in self.widgets if w.winfo_class() == 'Text' and w.cget('state') == 'disabled')
        self.tree = next(w for w in self.widgets if w.winfo_class() == 'Treeview')

    def add(self, source, description):
        self.entries[0].delete(0, 'end')
        self.entries[0].insert(0, source)
        self.text.delete('1.0', 'end')
        self.text.insert('1.0', description)
        self.buttons['Разобрать и добавить вариант'].invoke()
        self.root.update()

    def test_choice_uses_saved_candidate_not_edited_input(self):
        self.add('magnet:?xt=urn:btih:first', 'Название: Первый\nКачество: WEBRip 1080p')
        self.add('magnet:?xt=urn:btih:second', 'Название: Второй\nКачество: BDRip 720p')
        self.assertEqual(len(self.tree.get_children()), 2)
        self.callback.assert_not_called()
        self.entries[0].delete(0, 'end')
        self.entries[0].insert(0, 'magnet:?xt=urn:btih:edited')
        self.tree.selection_set('1')
        self.root.update()
        self.assertIn('Первый', self.detail.get('1.0', 'end'))
        self.buttons['Выбрать для очереди'].invoke()
        self.callback.assert_called_once_with('magnet:?xt=urn:btih:first')

    def test_comparison_updates_and_invalid_limits_do_not_add_candidates(self):
        self.add('', 'Название: Первый\nРазмер: 20 GiB\nПеревод: Студия А\nБез рекламы')
        self.entries[2].insert(0, '5')
        self.buttons['Сравнить по предпочтениям'].invoke()
        self.assertIn('выше лимита', str(self.tree.item('1', 'values')))
        for value in ('nan', 'inf', '-2', 'не число'):
            self.entries[2].delete(0, 'end')
            self.entries[2].insert(0, value)
            self.buttons['Разобрать и добавить вариант'].invoke()
            self.assertEqual(len(self.tree.get_children()), 1)
        self.buttons['Выбрать для очереди'].invoke()
        self.callback.assert_not_called()

    def test_controls_fit_minimum_window_size(self):
        # Map our test window offscreen so Tk performs the actual layout.
        self.window.transient('')
        self.window.geometry('980x620-10000-10000')
        self.window.deiconify()
        self.root.update()
        self.assertGreater(self.detail.winfo_height(), 40)
        self.assertGreater(self.tree.winfo_height(), 40)
        for button in self.buttons.values():
            with self.subTest(button=button.cget('text')):
                self.assertLessEqual(button.winfo_x() + button.winfo_width(), button.master.winfo_width())
        for widget in (self.detail, self.tree):
            self.assertLessEqual(widget.winfo_y() + widget.winfo_height(), widget.master.winfo_height())


if __name__ == '__main__':
    unittest.main()
