"""Check queue controls in a hidden, isolated launcher without Telegram or network."""
import json
import os
from pathlib import Path
import sys
import tempfile
import tkinter as tk
from tkinter import ttk
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools import desktop_launcher


def main():
    buttons, result = [], {}
    original_button, original_tk = ttk.Button, tk.Tk

    def button(*args, **kwargs):
        widget = original_button(*args, **kwargs)
        buttons.append(widget)
        return widget

    def window(*args, **kwargs):
        app = original_tk(*args, **kwargs)
        original_loop = app.mainloop

        def loop():
            next(b for b in buttons if str(b.cget('text')).endswith('Очередь')).invoke()
            app.geometry('1040x680')
            app.update_idletasks()
            stream = next(b for b in buttons if b.cget('text') == 'Подготовка → отправка → очистка')
            stop = next(b for b in buttons if b.cget('text') == 'Стоп')
            # Sidebar 218 + page padding 56 + card padding 34.
            available = 1040 - 218 - 56 - 34
            result.update(ok=True, minimum_window_width=1040, available=available,
                          stream_row=stream.master.winfo_reqwidth(), control_row=stop.master.winfo_reqwidth(),
                          separate_rows=stream.master != stop.master)
            if not result['separate_rows'] or max(result['stream_row'], result['control_row']) > available:
                raise AssertionError(str(result))
            return original_loop()
        app.mainloop = loop
        return app

    with tempfile.TemporaryDirectory(prefix='KinoTime-layout-') as directory:
        with patch.dict(os.environ, KINOTIME_STATE_DIR=directory, KINOTIME_DOWNLOAD_DIR=directory), \
                patch.object(tk, 'Tk', side_effect=window), patch.object(ttk, 'Button', side_effect=button):
            desktop_launcher.main(smoke_test=True)
    (ROOT / 'outputs/stream-launcher-layout.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
    print(json.dumps(result))


if __name__ == '__main__':
    main()
