"""Manual release comparison; choosing only fills the existing queue source field."""

import math
from pathlib import Path

try:
    from .release_description import assess, parse_description
except ImportError:
    from release_description import assess, parse_description


def queue_source(source):
    source = source.strip()
    if source.startswith('magnet:?') and not any(c.isspace() for c in source):
        return source
    if source and Path(source).suffix.lower() == '.torrent' and Path(source).is_file():
        return source
    raise ValueError('Укажите magnet-ссылку либо существующий локальный .torrent-файл.')


def open_assistant(parent, on_choose, shortcuts=lambda widget: None):
    import tkinter as tk
    from tkinter import filedialog, ttk
    from tkinter.scrolledtext import ScrolledText

    window = tk.Toplevel(parent)
    window.title('Помощник выбора раздачи')
    window.geometry('1080x720')
    window.minsize(980, 620)
    window.transient(parent)
    frame = ttk.Frame(window, padding=12)
    frame.pack(fill='both', expand=True)
    ttk.Label(frame, text='Вставьте описание каждого варианта и добавьте его к сравнению. '
              'Сведения извлекаются из текста и требуют подтверждения.', wraplength=1000).pack(anchor='w')
    ttk.Label(frame, text='Работает локально. Поиск на сайте и языковая модель пока не подключены.',
              wraplength=1000).pack(anchor='w', pady=(3, 8))
    source = tk.StringVar()
    row = ttk.Frame(frame)
    row.pack(fill='x')
    ttk.Label(row, text='Magnet / .torrent:').pack(side='left')
    entry = ttk.Entry(row, textvariable=source)
    entry.pack(side='left', fill='x', expand=True, padx=6)
    shortcuts(entry)
    ttk.Button(row, text='Выбрать файл', command=lambda: source.set(
        filedialog.askopenfilename(parent=window, filetypes=[('Torrent', '*.torrent')]) or source.get()
    )).pack(side='right')
    text = ScrolledText(frame, height=8, wrap='word', undo=True)
    text.pack(fill='both', expand=True, pady=6)
    shortcuts(text)
    preferences = ttk.Frame(frame)
    preferences.pack(fill='x', pady=4)
    voice, maximum = tk.StringVar(), tk.StringVar()
    no_ads = tk.BooleanVar(value=True)
    ttk.Label(preferences, text='Желаемая озвучка:').pack(side='left')
    voice_entry = ttk.Entry(preferences, textvariable=voice, width=20)
    voice_entry.pack(side='left', padx=5)
    shortcuts(voice_entry)
    ttk.Label(preferences, text='Макс. размер всей раздачи, ГиБ:').pack(side='left')
    size_entry = ttk.Entry(preferences, textvariable=maximum, width=7)
    size_entry.pack(side='left', padx=5)
    shortcuts(size_entry)
    ttk.Checkbutton(preferences, text='Предпочитать без рекламы', variable=no_ads).pack(side='left')
    status = tk.StringVar()
    ttk.Label(frame, textvariable=status, wraplength=1000).pack(anchor='w', pady=4)
    actions = ttk.Frame(frame)
    actions.pack(fill='x')
    tree = ttk.Treeview(frame, columns=('title', 'quality', 'voice', 'size', 'issues'), show='headings', height=5)
    for key, label, width in (('title', 'Название', 175), ('quality', 'Видео', 130),
                              ('voice', 'Перевод', 180), ('size', 'Размер', 120),
                              ('issues', 'Проверка предпочтений', 340)):
        tree.heading(key, text=label)
        tree.column(key, width=width, minwidth=60)
    tree.pack(fill='both', expand=True, pady=6)
    detail = ScrolledText(frame, height=10, wrap='word', state='disabled')
    detail.pack(fill='both', expand=True)
    candidates = {}
    counter = 0

    def options():
        limit = maximum.get().strip().replace(',', '.')
        try:
            limit = float(limit) if limit else None
        except ValueError:
            raise ValueError('Лимит исходника должен быть положительным числом либо пустым.') from None
        if limit is not None and (not math.isfinite(limit) or limit <= 0):
            raise ValueError('Лимит исходника должен быть положительным числом либо пустым.')
        return voice.get(), limit, no_ads.get()

    def refresh():
        settings = options()
        for key, candidate in candidates.items():
            parsed = candidate['description']
            issues = assess(parsed, *settings)
            values = (parsed.value('title'), parsed.value('quality') + ' / ' + parsed.value('resolution'),
                      parsed.value('translation'), parsed.value('size'),
                      '; '.join(issues) or 'По предпочтениям подходит; проверьте сведения')
            if tree.exists(key):
                tree.item(key, values=values)
            else:
                tree.insert('', 'end', iid=key, values=values)

    def show(event=None):
        selected = tree.selection()
        detail.configure(state='normal')
        detail.delete('1.0', 'end')
        if selected:
            candidate = candidates[selected[0]]
            detail.insert('1.0', 'Источник выбранного варианта: ' + (candidate['source'] or 'Не указан')
                          + '\n\n' + candidate['description'].report())
        detail.configure(state='disabled')

    def add():
        nonlocal counter
        try:
            options()
            parsed = parse_description(text.get('1.0', 'end-1c'))
            counter += 1
            key = str(counter)
            candidates[key] = {'description': parsed, 'source': source.get().strip()}
            refresh()
            tree.selection_set(key)
            tree.see(key)
            show()
            status.set('Вариант добавлен. Можно вставить следующее описание. '
                       'Для другого источника измените magnet / .torrent перед добавлением.')
        except ValueError as error:
            status.set(str(error))

    def compare():
        try:
            refresh()
            status.set('Предпочтения применены ко всем вариантам. Неизвестные сведения показаны отдельно.')
        except ValueError:
            status.set('Проверьте лимит исходника: нужно положительное число либо пустое поле.')

    def remove():
        for key in tree.selection():
            candidates.pop(key, None)
            tree.delete(key)
        show()

    def choose():
        selected = tree.selection()
        if not selected:
            status.set('Выберите вариант в таблице.')
            return
        try:
            # Source belongs to the saved candidate, never to the currently edited input.
            chosen = queue_source(candidates[selected[0]]['source'])
            on_choose(chosen)
            status.set('Источник перенесён в очередь. Закройте окно и нажмите «Добавить источник». '
                       'Получение списка файлов и скачивание здесь не запускаются.')
        except ValueError as error:
            status.set(str(error) + ' Добавьте описание заново с нужным источником.')

    tree.bind('<<TreeviewSelect>>', show)
    ttk.Button(actions, text='Разобрать и добавить вариант', command=add).pack(side='left')
    ttk.Button(actions, text='Сравнить по предпочтениям', command=compare).pack(side='left', padx=5)
    ttk.Button(actions, text='Убрать вариант', command=remove).pack(side='left')
    ttk.Button(actions, text='Выбрать для очереди', command=choose).pack(side='right')
    return window
