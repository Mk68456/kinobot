"""Source selection and local Jackett setup; only non-secret preferences are saved."""

import json
import queue
import threading
import webbrowser

# Reuse keys entered in the desktop dialog during this process only.
_SESSION_KEYS = {}

try:
    from .launcher_runtime import state_path
    from .jackett_search import DEFAULT_URL, JackettClient, local_url, start_managed
except ImportError:
    from launcher_runtime import state_path
    from jackett_search import DEFAULT_URL, JackettClient, local_url, start_managed


def load_sources(*, session=False):
    defaults = {'rutor': True, 'url': DEFAULT_URL, 'indexers': [], 'selected': [], 'api_key': ''}
    try:
        saved = json.loads(state_path('search-sources.json', __file__).read_text('utf-8'))
        defaults.update({k: saved[k] for k in ('rutor', 'url', 'indexers', 'selected') if k in saved})
        local_url(defaults['url'])
        if (type(defaults['rutor']) is not bool or not isinstance(defaults['indexers'], list)
                or not isinstance(defaults['selected'], list)):
            raise ValueError()
        defaults['indexers'] = [{'id': str(row['id']), 'name': str(row['name'])[:120]}
                                for row in defaults['indexers'] if isinstance(row, dict) and 'id' in row and 'name' in row]
    except (OSError, ValueError, TypeError):
        defaults = {'rutor': True, 'url': DEFAULT_URL, 'indexers': [], 'selected': [], 'api_key': ''}
    if session:
        defaults['api_key'] = _SESSION_KEYS.get((str(state_path('search-sources.json', __file__)), defaults['url']), '')
    return defaults


def save_sources(options):
    public = {k: options[k] for k in ('rutor', 'url', 'indexers', 'selected')}
    state_path('search-sources.json', __file__).write_text(json.dumps(public, ensure_ascii=False, indent=2), 'utf-8')
    _SESSION_KEYS[(str(state_path('search-sources.json', __file__)), options['url'])] = options.get('api_key', '')


def selected_indexers(options):
    return [row for row in options['indexers'] if row['id'] in options['selected']]


def connection(options, stop):
    key = options.get('api_key', '')
    url = local_url(options['url'])
    if url == DEFAULT_URL and not key:
        url, key = start_managed(stop)
    return JackettClient(url, key, stop=stop)


def open_sources(parent, options, on_saved, shortcuts=lambda widget: None):
    import tkinter as tk
    from tkinter import ttk

    window = tk.Toplevel(parent)
    window.title('Источники поиска — Jackett')
    window.geometry('700x620')
    window.minsize(620, 520)
    window.transient(parent)
    box = ttk.Frame(window, padding=12)
    box.pack(fill='both', expand=True)
    def resize(event):
        for label in box.winfo_children():
            if label.winfo_class() == 'TLabel':
                label.configure(wraplength=max(300, event.width))
    box.bind('<Configure>', resize)
    ttk.Label(box, text='Оставьте галочки только у нужных источников. Поиск выполняется только в них.',
              wraplength=650).pack(anchor='w')
    ttk.Label(box, text='Для LostFilm и других трекеров сначала нажмите «Настроить трекеры», добавьте '
              'источник и войдите в свой аккаунт, затем обновите список.', wraplength=650).pack(anchor='w', pady=(4, 8))
    form = ttk.Frame(box)
    form.pack(fill='x')
    form.columnconfigure(1, weight=1)
    url = tk.StringVar(value=options['url'])
    api = tk.StringVar(value=options.get('api_key', ''))
    for row, (label, variable) in enumerate((('Адрес Jackett', url), ('API-ключ (для своего Jackett)', api))):
        ttk.Label(form, text=label).grid(row=row, column=0, sticky='w', padx=(0, 8), pady=4)
        entry = ttk.Entry(form, textvariable=variable, show='*' if row else '')
        entry.grid(row=row, column=1, sticky='ew', pady=4)
        shortcuts(entry)
    ttk.Label(box, text='Встроенный Jackett использует порт 9118. Его ключ подставляется автоматически. '
              'Ключ своего Jackett вводится заново после закрытия лаунчера.', wraplength=650).pack(anchor='w', pady=4)
    actions = ttk.Frame(box)
    actions.pack(fill='x', pady=6)
    message = tk.StringVar(value='Rutor доступен отдельно. Остальные источники берутся из настроенного Jackett.')
    ttk.Label(box, textvariable=message, wraplength=650).pack(anchor='w', pady=4)
    rutor = tk.BooleanVar(value=options['rutor'])
    ttk.Checkbutton(box, text='Rutor — встроенный поиск', variable=rutor).pack(anchor='w', pady=4)
    canvas_frame = ttk.Frame(box)
    canvas_frame.pack(fill='both', expand=True)
    canvas = tk.Canvas(canvas_frame, highlightthickness=0, height=180)
    scrollbar = ttk.Scrollbar(canvas_frame, orient='vertical', command=canvas.yview)
    canvas.configure(yscrollcommand=scrollbar.set)
    scrollbar.pack(side='right', fill='y')
    canvas.pack(side='left', fill='both', expand=True)
    checks = ttk.Frame(canvas)
    canvas.create_window((0, 0), window=checks, anchor='nw')
    checks.bind('<Configure>', lambda event: canvas.configure(scrollregion=canvas.bbox('all')))
    source_rows = list(options['indexers'])
    variables = {}
    remembered = set(options['selected'])

    def render():
        for widget in checks.winfo_children():
            widget.destroy()
        variables.clear()
        for source in source_rows:
            variable = tk.BooleanVar(value=source['id'] in remembered)
            variables[source['id']] = variable
            ttk.Checkbutton(checks, text=source['name'], variable=variable).pack(anchor='w', pady=2)
        if not source_rows:
            ttk.Label(checks, text='Список Jackett пуст. Настройте трекеры и нажмите «Обновить список».').pack(anchor='w')

    render()
    messages = queue.Queue()
    stop = threading.Event()
    state = {'running': False, 'closed': False, 'after': None}
    buttons = []

    def work(kind):
        if state['running']:
            return
        try:
            address = local_url(url.get())
            key = api.get().strip()
        except ValueError as error:
            message.set(str(error))
            return
        state['running'] = True
        for button in buttons:
            button.configure(state='disabled')
        message.set('Подключаю Jackett…')

        def worker():
            try:
                if kind == 'start':
                    address_result, key_result = start_managed(stop)
                elif address == DEFAULT_URL and not key:
                    address_result, key_result = start_managed(stop)
                else:
                    address_result, key_result = address, key
                client = JackettClient(address_result, key_result, stop=stop)
                rows = client.indexers()
                messages.put(('ready', (address_result, key_result, rows, kind)))
            except Exception as error:
                messages.put(('error', str(error)))
        threading.Thread(target=worker, daemon=True).start()

    def save():
        try:
            address = local_url(url.get())
            chosen = [source['id'] for source in source_rows if variables[source['id']].get()]
            if not rutor.get() and not chosen:
                raise ValueError('Выберите хотя бы один источник.')
            updated = {'rutor': rutor.get(), 'url': address, 'api_key': api.get().strip(),
                       'indexers': list(source_rows), 'selected': chosen}
            save_sources(updated)
            options.clear()
            options.update(updated)
            on_saved()
            window.destroy()
        except (ValueError, OSError) as error:
            message.set(str(error))

    for label, kind in (('Запустить встроенный', 'start'), ('Настроить трекеры', 'browser'), ('Обновить список', 'refresh')):
        button = ttk.Button(actions, text=label, command=lambda value=kind: work(value))
        button.pack(side='left', padx=(0, 5))
        buttons.append(button)
    bottom = ttk.Frame(box)
    bottom.pack(fill='x', pady=(8, 0))
    ttk.Button(bottom, text='Выбрать все', command=lambda: [v.set(True) for v in variables.values()]).pack(side='left')
    ttk.Button(bottom, text='Снять все', command=lambda: [v.set(False) for v in [rutor, *variables.values()]]).pack(side='left', padx=5)
    save_button = ttk.Button(bottom, text='Сохранить выбор', command=save)
    save_button.pack(side='right')
    buttons.append(save_button)

    def poll():
        if state['closed']:
            return
        try:
            while True:
                kind, payload = messages.get_nowait()
                state['running'] = False
                for button in buttons:
                    button.configure(state='normal')
                if kind == 'error':
                    message.set(payload)
                else:
                    address, key, rows, action = payload
                    remembered.update(source_id for source_id, value in variables.items() if value.get())
                    remembered.difference_update(source_id for source_id, value in variables.items() if not value.get())
                    source_rows[:] = rows
                    url.set(address)
                    api.set(key)
                    render()
                    message.set(f'Jackett подключён. Настроено трекеров: {len(rows)}. Отметьте нужные и сохраните выбор.')
                    if action == 'browser':
                        webbrowser.open(address + '/UI/Dashboard')
        except queue.Empty:
            pass
        state['after'] = window.after(100, poll)

    def cleanup(event):
        if event.widget is window:
            state['closed'] = True
            stop.set()
            if state['after'] is not None:
                window.after_cancel(state['after'])

    window.bind('<Destroy>', cleanup)
    state['after'] = window.after(100, poll)
    window.grab_set()
    return window
