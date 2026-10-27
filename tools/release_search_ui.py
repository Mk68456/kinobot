"""Title search UI; worker threads communicate through a polled message queue."""

import json
from collections import Counter
import math
import os
import queue
import threading

try:
    from .launcher_runtime import state_path
    from .release_assistant_ui import open_assistant
    from .release_search import DEFAULT_SITE, RutorClient, rank, parse_seasons, automatic_candidates
    from .jackett_search import merge_sources
    from .jackett_sources_ui import load_sources, open_sources, selected_indexers, connection
except ImportError:
    from launcher_runtime import state_path
    from release_assistant_ui import open_assistant
    from release_search import DEFAULT_SITE, RutorClient, rank, parse_seasons, automatic_candidates
    from jackett_search import merge_sources
    from jackett_sources_ui import load_sources, open_sources, selected_indexers, connection


def open_search(parent, on_choose, on_prepare, shortcuts=lambda widget: None, client_factory=RutorClient):
    import tkinter as tk
    from tkinter import ttk
    from tkinter.scrolledtext import ScrolledText

    window = tk.Toplevel(parent)
    window.title('Поиск и скачивание исходников')
    window.geometry('1100x740')
    window.minsize(980, 620)
    window.transient(parent)
    frame = ttk.Frame(window, padding=12)
    frame.pack(fill='both', expand=True)
    ttk.Label(frame, text='Название → поиск → скачивание → очередь проектов → обычная подготовка',
              wraplength=1000).pack(anchor='w')
    ttk.Label(frame, text='Заполненные условия обязательны. Исключённые варианты скрыты, пока вы не включите их показ. '
              'Карточки создаются позже, после подготовки. Код существующей карточки не нужен.',
              wraplength=1000).pack(anchor='w', pady=(3, 8))
    defaults = {'query': '', 'year': '', 'season': '0', 'height': '1080', 'voice': '', 'maximum': '30',
                'site': os.environ.get('KINOTIME_RUTOR_URL', DEFAULT_SITE), 'max_pages': '10'}
    path = state_path('release-search-settings.json', __file__)
    if path.exists():
        try:
            saved = json.loads(path.read_text(encoding='utf-8'))
            defaults.update({key: str(saved[key])[:160] for key in defaults if key in saved and key != 'query'})
            if defaults['site'].rstrip('/') in ('https://rutor.info', 'https://rutor.is') and not os.environ.get('KINOTIME_RUTOR_URL'):
                defaults['site'] = DEFAULT_SITE
        except (OSError, ValueError, TypeError):
            pass
    variables = {key: tk.StringVar(value=value) for key, value in defaults.items()}
    sources = load_sources()
    form = ttk.Frame(frame)
    form.pack(fill='x')
    form.columnconfigure(1, weight=1)
    form.columnconfigure(3, weight=1)
    labels = [('query', 'Название'), ('year', 'Год (необязательно)'), ('voice', 'Озвучка (пусто — любая)'),
              ('maximum', 'Макс. размер раздачи, ГиБ'), ('season', 'Сезоны: 0; 1-3; 1,3; все'),
              ('site', 'Адрес Rutor'), ('height', 'Разрешение, p')]
    inputs = {}
    for index, (key, label) in enumerate(labels):
        row, col = divmod(index, 2)
        ttk.Label(form, text=label).grid(row=row, column=col * 2, sticky='w', padx=(0, 8), pady=3)
        widget = ttk.Entry(form, textvariable=variables[key])
        widget.grid(row=row, column=col * 2 + 1, sticky='ew', padx=(0, 10), pady=3)
        shortcuts(widget)
        inputs[key] = widget
    no_ads = tk.BooleanVar(value=True)
    show_excluded = tk.BooleanVar(value=False)
    filters = ttk.Frame(frame)
    filters.pack(fill='x')
    ads_control = ttk.Checkbutton(filters, text='Только с явно указанным отсутствием рекламы', variable=no_ads,
                                  command=lambda: apply_filters())
    ads_control.pack(side='left')
    ttk.Label(filters, text='Страниц Rutor:').pack(side='left', padx=(12, 5))
    page_limit = ttk.Spinbox(filters, from_=1, to=20, width=4, textvariable=variables['max_pages'])
    page_limit.pack(side='left')
    ttk.Checkbutton(filters, text='Показать исключённые', variable=show_excluded,
                    command=lambda: apply_filters()).pack(side='right')
    actions = ttk.Frame(frame)
    actions.pack(fill='x', pady=6)
    source_summary = tk.StringVar()
    source_row = ttk.Frame(frame)
    source_row.pack(fill='x')
    ttk.Label(source_row, textvariable=source_summary, wraplength=780).pack(side='left')
    status = tk.StringVar(value='Введите название. Описания и magnet-ссылки помощник получит сам.')
    ttk.Label(frame, textvariable=status, wraplength=1000).pack(anchor='w', pady=4)
    listing_frame = ttk.Frame(frame)
    listing_frame.pack(fill='both', expand=True)
    tree = ttk.Treeview(listing_frame, columns=('title', 'tracker', 'quality', 'size', 'seeds', 'issues'), show='headings', height=6)
    for key, label, width in (('title', 'Раздача', 320), ('tracker', 'Трекер', 130), ('quality', 'Видео', 125), ('size', 'Размер', 110),
                              ('seeds', 'Раздают', 65), ('issues', 'Результат проверки', 400)):
        tree.heading(key, text=label)
        tree.column(key, width=width, minwidth=55, stretch=True)
    tree.grid(row=0, column=0, sticky='nsew')
    listing_frame.columnconfigure(0, weight=1)
    listing_frame.rowconfigure(0, weight=1)
    horizontal = ttk.Scrollbar(listing_frame, orient='horizontal', command=tree.xview)
    horizontal.grid(row=1, column=0, sticky='ew')
    vertical = ttk.Scrollbar(listing_frame, orient='vertical', command=tree.yview)
    vertical.grid(row=0, column=1, sticky='ns')
    tree.configure(xscrollcommand=horizontal.set, yscrollcommand=vertical.set)
    detail = ScrolledText(frame, height=6, wrap='word', state='disabled')
    detail.pack(fill='both', expand=True, pady=6)
    bottom = ttk.Frame(frame)
    bottom.pack(fill='x')
    messages = queue.Queue()
    stop = threading.Event()
    state = {'running': False, 'closed': False, 'rows': {}, 'all_rows': [], 'stats': {},
             'errors': [], 'settings': None, 'poll_id': None, 'jackett': None, 'sources_window': None}
    search_buttons = []

    def settings():
        values = {key: variable.get().strip() for key, variable in variables.items()}
        if not 3 <= len(values['query']) <= 120:
            raise ValueError('Введите название длиной от 3 до 120 символов.')
        if values['year'] and (not values['year'].isdigit() or not 1888 <= int(values['year']) <= 2100):
            raise ValueError('Проверьте год выпуска.')
        try:
            limit = float(values['maximum'].replace(',', '.')) if values['maximum'] else None
            season = parse_seasons(values['season'])
            height = int(values['height']) if values['height'] else None
            pages = int(values['max_pages'])
        except ValueError:
            raise ValueError('Размер, сезон и разрешение должны быть числами.') from None
        if (limit is not None and (not math.isfinite(limit) or limit <= 0)) or (
            height is not None and height not in (360, 480, 576, 720, 1080, 1440, 2160)) or not 1 <= pages <= 20:
            raise ValueError('Проверьте положительный размер, сезон 0–99 и стандартное разрешение.')
        # Validate the site before starting a worker, without making a request.
        if sources['rutor']:
            RutorClient(values['site'])
        if not sources['rutor'] and not selected_indexers(sources):
            raise ValueError('Выберите хотя бы один источник поиска.')
        options = {'year': values['year'], 'season': season, 'height': height,
                   'preferred_voice': values['voice'], 'max_gib': limit, 'avoid_ads': no_ads.get()}
        return values, options

    def busy(value):
        state['running'] = value
        for button in search_buttons:
            button.configure(state='disabled' if value else 'normal')
        for widget in inputs.values():
            widget.configure(state='disabled' if value else 'normal')
        page_limit.configure(state='disabled' if value else 'normal')
        ads_control.configure(state='disabled' if value else 'normal')

    def show(event=None):
        selection = tree.selection()
        detail.configure(state='normal')
        detail.delete('1.0', 'end')
        if selection:
            candidate = state['rows'][selection[0]]
            detail.insert('1.0', 'Трекер: ' + candidate.get('tracker', 'Rutor') + '\nСтраница: ' + candidate['url'] + '\n\n'
                          + 'Обязательные условия: ' + ('; '.join(candidate['filter_issues']) or 'Выполнены')
                          + '\nАвтовыбор для скачивания: ' + ('; '.join(candidate['issues']) or 'Подходит по заданным правилам')
                          + '\n\n' + candidate['description'].report())
        elif state['all_rows'] or state['errors']:
            reasons = Counter(issue for row in state['all_rows'] for issue in row.get('filter_issues', []))
            detail.insert('1.0', 'Нет выбранного подходящего варианта.\n'
                          + '\n'.join(f'{reason}: {count}' for reason, count in reasons.most_common(8))
                          + ('\nОшибки чтения:\n' + '\n'.join(state['errors']) if state['errors'] else ''))
        if selection and state['errors']:
            detail.insert('end', '\n\nОшибки источников:\n' + '\n'.join(state['errors']))
        detail.configure(state='disabled')

    def render_rows():
        selected = tree.selection()
        tree.delete(*tree.get_children())
        all_rows = state['all_rows']
        accepted = [row for row in all_rows if not row['filter_issues']]
        eligible = [row for row in accepted if not row['issues']]
        visible = all_rows if show_excluded.get() else accepted
        state['rows'] = {row['id']: row for row in visible}
        for candidate in visible:
            parsed = candidate['description']
            note = ('Исключён: ' + '; '.join(candidate['filter_issues'])) if candidate['filter_issues'] else (
                'Нужна проверка: ' + '; '.join(candidate['issues']) if candidate['issues'] else 'Подходит для скачивания')
            tree.insert('', 'end', iid=candidate['id'], values=(candidate['title'], candidate.get('tracker', 'Rutor'), parsed.value('resolution'),
                        parsed.value('size'), candidate['seeds'] if candidate['seeds'] is not None else '?', note))
        if selected and selected[0] in state['rows']:
            tree.selection_set(selected[0])
        elif eligible:
            tree.selection_set(eligible[0]['id'])
        elif accepted:
            tree.selection_set(accepted[0]['id'])
        stats = state['stats']
        status.set(f'Проверено раздач: {stats.get("scanned", len(all_rows))}; '
                   f'подходят по условиям: {len(accepted)}; исключено: {len(all_rows) - len(accepted)}; '
                   f'ошибок чтения: {len(state["errors"])}. '
                   + ('Достигнут лимит страниц поиска. ' if stats.get('limit_reached') else '')
                   + ('Выделен подходящий вариант.' if eligible else 'Готового варианта для автовыбора нет.'))
        show()

    def apply_filters():
        if state['running'] or not state['all_rows']:
            return
        try:
            values, options = settings()
            previous = state['settings']
            if previous is not None and any(values[key] != previous[0][key] for key in
                                            ('query', 'year', 'season', 'height', 'max_pages')):
                # Earlier title prefiltering may have skipped details now needed by new criteria.
                start(False)
                return
            state['settings'] = values, options
            state['all_rows'] = rank(state['all_rows'], values['query'], **options)
            render_rows()
        except ValueError as error:
            status.set(str(error))

    def transfer(candidate, values, options, prepare=True):
        choices = candidate if isinstance(candidate, list) else [{'candidate': candidate, 'season': options['season']}]
        if not any(choice['candidate'].get('download_path') for choice in choices):
            if prepare:
                on_prepare(candidate, {**options, 'query': values['query']})
            else:
                on_choose(candidate['source'])
            status.set('Источник передан в торрент-очередь. После скачивания исходники появятся в очереди проектов.')
            return
        client = state['jackett']
        if client is None:
            raise ValueError('Повторите поиск для подключения Jackett.')
        busy(True)
        stop.clear()
        status.set('Получаю .torrent через Jackett…')

        def worker():
            try:
                result = []
                for choice in choices:
                    row = choice['candidate']
                    result.append({**choice, 'candidate': client.materialize(row) if row.get('download_path') else row})
                resolved = result if isinstance(candidate, list) else result[0]['candidate']
                messages.put(('resolved', (resolved, values, options, prepare)))
            except Exception as error:
                messages.put(('error', str(error)))
        threading.Thread(target=worker, daemon=True).start()

    def start(automatic=False):
        if state['running']:
            return
        try:
            values, options = settings()
            path.write_text(json.dumps({k: v for k, v in values.items() if k != 'query'}, ensure_ascii=False), encoding='utf-8')
            state['settings'] = values, options
            stop.clear()
            state['rows'], state['all_rows'], state['stats'], state['errors'] = {}, [], {}, []
            tree.delete(*tree.get_children())
            show()
            busy(True)
            status.set('Подключаюсь к сайту…')
            source_options = {**sources, 'indexers': list(sources['indexers']), 'selected': list(sources['selected'])}

            def worker():
                try:
                    candidates, errors, stats = [], [], {'scanned': 0, 'limit_reached': False}
                    report = lambda message: messages.put(('progress', message))
                    if source_options['rutor']:
                        try:
                            client = client_factory(values['site'], stop=stop)
                            found, problems = client.search(values['query'], report,
                                                           settings=options, max_pages=int(values['max_pages']))
                            candidates.extend(found)
                            errors.extend(problems)
                            current_stats = getattr(client, 'stats', None)
                            if isinstance(current_stats, dict):
                                stats.update(current_stats)
                        except ValueError as error:
                            if not selected_indexers(source_options):
                                raise
                            errors.append('Rutor: ' + str(error))
                    configured = selected_indexers(source_options)
                    jackett = None
                    if configured:
                        try:
                            jackett = connection(source_options, stop)
                            found, problems = jackett.search(values['query'], configured, report)
                            candidates.extend(found)
                            errors.extend(problems)
                            stats['scanned'] += jackett.stats['scanned']
                            stats['limit_reached'] = stats['limit_reached'] or jackett.stats['limit_reached']
                        except ValueError as error:
                            errors.append('Jackett: ' + str(error))
                    rows = rank(merge_sources(candidates), values['query'], **options)
                    messages.put(('results', (rows, errors, automatic, values, options,
                                              stats, jackett)))
                except Exception as error:
                    messages.put(('error', str(error)))

            threading.Thread(target=worker, daemon=True).start()
        except (ValueError, OSError) as error:
            status.set(str(error))

    def poll():
        if state['closed']:
            return
        try:
            while True:
                kind, payload = messages.get_nowait()
                if kind == 'progress':
                    status.set(payload)
                elif kind == 'error':
                    busy(False)
                    status.set(payload)
                elif kind == 'resolved':
                    busy(False)
                    if stop.is_set():
                        status.set('Получение источника остановлено. Скачивание не запущено.')
                        continue
                    candidate, values, options, prepare = payload
                    try:
                        if prepare:
                            on_prepare(candidate, {**options, 'query': values['query']})
                        else:
                            on_choose(candidate['source'])
                        status.set('Источник передан в торрент-очередь.')
                    except (ValueError, OSError) as error:
                        status.set(str(error))
                elif kind == 'results':
                    busy(False)
                    if stop.is_set():
                        status.set('Поиск остановлен. Загрузка не запущена.')
                        continue
                    rows, errors, automatic, values, options, stats, jackett = payload
                    state['jackett'] = jackett
                    state['errors'] = errors
                    state['all_rows'], state['stats'] = rows, stats
                    render_rows()
                    if automatic:
                        try:
                            chosen = automatic_candidates(rows, options['season'])
                            if len(chosen) == 1:
                                transfer(chosen[0][0], values, {**options, 'season': chosen[0][1]})
                            else:
                                transfer([{'candidate': row, 'season': seasons} for row, seasons in chosen], values, options)
                        except (ValueError, OSError) as error:
                            status.set(status.get() + ' Скачивание не запущено: ' + str(error))
        except queue.Empty:
            pass
        state['poll_id'] = window.after(100, poll)

    def choose(prepare=False):
        if state['running']:
            status.set('Дождитесь поиска или остановите его.')
            return
        selected = tree.selection()
        if not selected:
            status.set('Выберите раздачу в таблице.')
            return
        try:
            candidate = state['rows'][selected[0]]
            values, options = settings()
            checked = rank([candidate], values['query'], **options)[0]
            if checked['filter_issues']:
                raise ValueError('Обязательные условия не выполнены: ' + '; '.join(checked['filter_issues']))
            if not candidate['source'] and not candidate.get('download_path'):
                raise ValueError('Ссылка скачивания не найдена на странице раздачи.')
            transfer(checked, values, options, prepare)
        except (ValueError, OSError) as error:
            status.set(str(error))

    def cleanup(event=None):
        if event is not None and event.widget is not window:
            return
        state['closed'] = True
        stop.set()
        if state['poll_id'] is not None:
            try:
                window.after_cancel(state['poll_id'])
            except tk.TclError:
                pass
            state['poll_id'] = None

    def close():
        cleanup()
        window.destroy()

    def sources_changed():
        names = (['Rutor'] if sources['rutor'] else []) + [row['name'] for row in selected_indexers(sources)]
        summary = ', '.join(names) or 'не выбраны'
        if len(summary) > 120:
            summary = summary[:117] + '…'
        source_summary.set('Источники: ' + summary)
        state['rows'], state['all_rows'], state['errors'] = {}, [], []
        tree.delete(*tree.get_children())
        show()
        status.set('Выбор источников сохранён. Запустите поиск.')

    def configure_sources():
        current = state['sources_window']
        if current is not None and current.winfo_exists():
            current.lift()
            return
        state['sources_window'] = open_sources(window, sources, sources_changed, shortcuts)

    for label, command in (('Найти варианты', lambda: start(False)),
                           ('Найти и скачать', lambda: start(True))):
        button = ttk.Button(actions, text=label, command=command)
        button.pack(side='left', padx=(0, 6))
        search_buttons.append(button)
    ttk.Button(actions, text='Остановить поиск', command=stop.set).pack(side='left')
    sources_button = ttk.Button(source_row, text='Источники…', command=configure_sources)
    sources_button.pack(side='right')
    search_buttons.append(sources_button)
    ttk.Button(actions, text='Вставить описание вручную', command=lambda: open_assistant(window, on_choose, shortcuts)).pack(side='right')
    ttk.Button(bottom, text='Передать источник в очередь', command=lambda: choose(False)).pack(side='left')
    ttk.Button(bottom, text='Применить условия', command=apply_filters).pack(side='left', padx=6)
    ttk.Button(bottom, text='Скачать выбранную раздачу', command=lambda: choose(True)).pack(side='right')
    tree.bind('<<TreeviewSelect>>', show)
    window.bind('<Destroy>', cleanup)
    window.protocol('WM_DELETE_WINDOW', close)
    state['poll_id'] = window.after(100, poll)
    sources_changed()
    return window
