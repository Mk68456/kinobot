"""Tk project controls; workers never read Tk variables or update widgets."""
from tkinter import TclError, messagebox, ttk

try:
    from .launcher_projects import LABELS, build_project, register_or_send
except ImportError:
    from launcher_projects import LABELS, build_project, register_or_send


def mount(parent, app, manager, background, report, stop, selection, make_client, connection, legacy_busy):
    try:
        from . import desktop_launcher as launcher
    except ImportError:
        import desktop_launcher as launcher

    ttk.Label(parent, text='Очередь проектов', style='Title.TLabel').pack(anchor='w', pady=(6, 8))
    ttk.Label(parent, text='Добавляйте сериалы во время рендера. Проекты чередуются по сериям и качествам; '
              'озвучки используют общий видеоряд. Отправка запускается отдельно после проверки карточек.',
              style='Muted.TLabel', wraplength=880).pack(anchor='w', pady=(0, 12))
    top = ttk.Frame(parent)
    top.pack(fill='x', pady=6)
    count = ttk.Combobox(top, state='readonly', values=('1', '2', '3', '4'), width=3)
    count.set(str(manager.parallelism))
    ttk.Label(top, text='Одновременных обработок:').pack(side='left')
    count.pack(side='left', padx=8)

    def change_count(_event=None):
        try:
            manager.set_parallelism(int(count.get()))
        except ValueError as error:
            count.set(str(manager.parallelism))
            report(str(error))
    count.bind('<<ComboboxSelected>>', change_count)

    def add():
        if legacy_busy():
            report('Дождитесь завершения текущей операции лаунчера.')
            return
        try:
            folder, qualities, mode, section = selection()
            series = None
            if series_number.get().strip() or season_number.get().strip():
                try:
                    series = {'movie_number': int(series_number.get()), 'season_number': int(season_number.get())}
                except ValueError:
                    raise ValueError('Укажите целые положительные номера сериала и сезона.') from None
        except ValueError as error:
            report(str(error))
            return
        def work():
            report('Проверяю исходники и фиксирую настройки нового проекта…')
            project = build_project(folder, qualities, mode, section, stop, series=series)
            if stop.is_set():
                raise InterruptedError('Добавление проекта остановлено.')
            manager.add(project)
            report('Добавлен проект «' + project['title'] + '». Подготовка запущена.')
        stop.clear()
        background(work, allow_projects=True)
    ttk.Button(top, text='Добавить выбранный источник', command=add,
               style='Primary.TButton').pack(side='left', padx=8)
    ttk.Button(top, text='Выбрать другой источник', command=lambda: connection('source')).pack(side='left')

    series_row = ttk.Frame(parent)
    series_row.pack(fill='x', pady=4)
    ttk.Label(series_row, text='Для сезона: № сериала в каталоге').pack(side='left')
    series_number = ttk.Entry(series_row, width=10)
    series_number.pack(side='left', padx=6)
    ttk.Label(series_row, text='Сезон').pack(side='left')
    season_number = ttk.Entry(series_row, width=5)
    season_number.pack(side='left', padx=6)
    ttk.Label(series_row, text='Пустые поля — обычный импорт.', style='Muted.TLabel').pack(side='left', padx=8)

    actions = ttk.Frame(parent)
    actions.pack(fill='x', pady=6)
    projects = ttk.Treeview(parent, columns=('title', 'progress', 'state', 'detail'), show='headings', height=6)
    for key, label, width in [('title', 'Проект', 190), ('progress', 'MP4', 80),
                              ('state', 'Состояние', 160), ('detail', 'Текущий этап / причина', 420)]:
        projects.heading(key, text=label)
        projects.column(key, width=width, minwidth=60)
    projects.pack(fill='x', pady=6)
    ttk.Label(parent, text='Серии, качества и озвучки выбранного проекта', style='Section.TLabel').pack(
        anchor='w', pady=(8, 4))
    detail_box = ttk.Frame(parent)
    detail_box.pack(fill='both', expand=True)
    details = ttk.Treeview(detail_box, columns=('title', 'status', 'details'), show='tree headings', height=6)
    details.column('#0', width=30, stretch=False)
    for key, label, width in [('title', 'Источник / качество / озвучка', 270),
                              ('status', 'Состояние', 150), ('details', 'Подробности', 390)]:
        details.heading(key, text=label)
        details.column(key, width=width)
    scrollbar = ttk.Scrollbar(detail_box, orient='vertical', command=details.yview)
    details.configure(yscrollcommand=scrollbar.set)
    scrollbar.pack(side='right', fill='y')
    details.pack(fill='both', expand=True)
    view = launcher.PreparationQueueView(details, LABELS)

    def selected():
        rows = projects.selection()
        if not rows:
            raise ValueError('Выберите проект.')
        return rows[0]

    def action(operation):
        try:
            operation(selected())
        except (ValueError, KeyError) as error:
            report(str(error))

    def resume(project_id):
        if legacy_busy():
            raise ValueError('Дождитесь завершения текущей операции лаунчера.')
        manager.resume(project_id)

    def cards_or_send(sending=False):
        try:
            project_id = selected()
            project = manager.get(project_id)
            if project['state'] not in ('prepared', 'cards_ready', 'send_paused', 'complete'):
                raise ValueError('Сначала завершите подготовку MP4.')
            if project['state'] == 'complete':
                raise ValueError('Все файлы проекта уже приняты ботом.')
            if sending and not project['cards']:
                raise ValueError('Сначала создайте карточки и проверьте их в WEB-панели.')
            manifests = manager.manifests(project_id)
            if sending and len(project['cards']) != len(manifests):
                raise ValueError('Созданы не все карточки. Повторите «Создать карточки».')
            client = make_client()
            # Readiness and server receipts are checked again inside the worker.
            if project.get('binding') and project['binding'] != {'server': client.url, 'instance': client.instance_id}:
                raise ValueError('Верните подключение панели и компьютера, использованное для этого проекта.')
        except (ValueError, KeyError) as error:
            report(str(error))
            return
        if sending and not messagebox.askokcancel('Отправить проект',
                'Карточки проекта оформлены в WEB-панели?\n\n'
                'Лаунчер будет управлять Telegram Desktop. Оставьте Windows разблокированной. '
                'Другие проекты продолжат рендер. Публикация подтверждается отдельно.'):
            return
        if legacy_busy():
            report('Дождитесь завершения текущей операции. Telegram отправляет один проект за раз.')
            return
        stop.clear()

        def work():
            def connect(data):
                result = launcher.TelegramDesktop(data['bot_username'], data['bot_name'])
                result.open()
                report('Проверьте чат Telegram. Отправка начнётся через 8 секунд.')
                if stop.wait(8):
                    raise InterruptedError('Отправка проекта приостановлена.')
                try:
                    result.bind()
                except Exception as error:
                    raise launcher.BeforeSendError(str(error)) from error
                return result
            register_or_send(manager, project_id, client, stop, report, connect, sending)
            report('Проект «' + project['title'] + '»: ' + ('передача завершена.' if sending else 'карточки созданы.'))
        background(work, allow_projects=True)

    ttk.Button(actions, text='Пауза проекта', command=lambda: action(manager.pause)).pack(side='left', padx=2)
    ttk.Button(actions, text='Продолжить', command=lambda: action(resume)).pack(side='left', padx=2)
    ttk.Button(actions, text='Приоритет ↔ обычный', command=lambda: action(manager.prioritize)).pack(side='left', padx=2)
    ttk.Button(actions, text='Создать карточки', command=cards_or_send).pack(side='left', padx=2)
    ttk.Button(actions, text='Отправить', command=lambda: cards_or_send(True)).pack(side='left', padx=2)
    ttk.Button(actions, text='Пауза всех', command=manager.pause).pack(side='right')
    ttk.Label(parent, text='Настройки фиксируются при добавлении. После перезапуска проекты остаются на паузе. '
              'При остановке незавершённое кодирование повторяется; готовые файлы сохраняются.',
              style='Muted.TLabel', wraplength=880).pack(anchor='w', pady=8)

    refresh_timer = {'id': None, 'closed': False}
    def refresh():
        if refresh_timer['closed']:
            return
        snapshots = manager.snapshots()
        if app.focus_get() is not count:
            count.set(str(manager.parallelism))
        existing = set(projects.get_children())
        for item in snapshots:
            values = (('★ ' if item['priority'] else '') + item['title'],
                      f"{item['ready']}/{item['total']}", LABELS[item['state']], item['message'])
            if item['id'] in existing:
                if tuple(projects.item(item['id'], 'values')) != values:
                    projects.item(item['id'], values=values)
            else:
                projects.insert('', 'end', iid=item['id'], values=values)
        if not projects.selection() and projects.get_children():
            projects.selection_set(projects.get_children()[0])
        if projects.selection():
            project = manager.get(projects.selection()[0])
            variants = []
            for task in project['tasks']:
                audio = [a['name'] for a in task['plan']['audio'] if a['enabled']] or ['Без звука']
                for voice in audio:
                    quality = task['plan']['qualities'][0]
                    progress = task.get('progress', {}).get(f'{quality} / {voice}', {})
                    state = {'done': 'Подготовлен', 'running': 'Обработка', 'pending': 'Ожидает'}[task['state']]
                    if task['state'] == 'running':
                        state = progress.get('status', state)
                    elif task['state'] == 'pending' and project['state'] in ('paused', 'pausing', 'error'):
                        state = 'Ошибка' if project['state'] == 'error' else 'Пауза'
                    variants.append({'name': f"{task['entry']['path']} / {quality} / {voice}",
                                     'source': task['entry']['path'], 'quality': quality, 'voice': voice,
                                     'status': state, 'detail': progress.get('detail', '')})
            view.render([{'file': project['folder'], 'title': project['title'], 'status': project['state'],
                          'error': project['message'], 'variants': variants,
                          'source_count': len({t['source'] for t in project['tasks']})}])
        refresh_timer['id'] = app.after(500, refresh)

    def cleanup(event):
        if event.widget is parent:
            refresh_timer['closed'] = True
            if refresh_timer['id']:
                try:
                    app.after_cancel(refresh_timer['id'])
                except TclError:
                    pass
    parent.bind('<Destroy>', cleanup, add='+')
    refresh_timer['id'] = app.after(100, refresh)
    return add
