"""Tk controls for the local ingestion queue; networking stays off the UI thread."""

import json
import os
from pathlib import Path

try:
    from .launcher_runtime import state_path
    from .torrent_queue import GIB, QBitClient, TorrentQueue, VIDEO_EXTENSIONS, queue_lock
    from .release_search_ui import open_search
    from .release_search import auto_file_ids, required_issues, parse_seasons
    from .jackett_search import source_identity
except ImportError:
    from launcher_runtime import state_path
    from torrent_queue import GIB, QBitClient, TorrentQueue, VIDEO_EXTENSIONS, queue_lock
    from release_search_ui import open_search
    from release_search import auto_file_ids, required_issues, parse_seasons
    from jackett_search import source_identity


STATUS = {
    "metadata": "Получение списка",
    "choose_files": "Выберите файлы",
    "queued": "В очереди",
    "downloading": "Скачивание",
    "downloaded": "Скачано",
    "preparing": "Подготовка",
    "prepared": "Готово к отправке",
    'project_queued': 'В очереди проектов',
    "paused": "Пауза",
    "error": "Ошибка",
    "cancelled": "Отменено",
}


def mount(
    page,
    app,
    background,
    report,
    stop,
    build_preparer,
    open_job,
    quiet,
    sections,
    shortcuts=lambda entry: None,
    ingest_background=None,
    remote=None,
):
    import tkinter as tk
    from tkinter import filedialog, ttk

    defaults = {
        "url": "http://127.0.0.1:8080",
        "username": "admin",
        "password": "",
        "api_key": "",
        "auth_mode": "password",
        "root": os.environ.get('KINOTIME_DOWNLOAD_DIR', str(Path.home() / "Downloads" / "KinoTime-Torrents")),
        "quota": "100",
        "reserve": "20",
        "speed": "5",
        "source": "",
    }
    config_path = state_path('torrent-settings.json', __file__)
    if config_path.exists():
        try:
            saved = json.loads(config_path.read_text(encoding="utf-8"))
            defaults.update({k: saved[k] for k in defaults if k not in ("source", "password", "api_key") and k in saved})
        except (ValueError, OSError):
            pass
    variables = {key: tk.StringVar(value=value) for key, value in defaults.items()}
    queue_box = {"queue": None, "root": None, "guard": None}
    operation = {"running": False}
    active_client = {"client": None}
    try:
        from . import remote_torrents
    except ImportError:
        import remote_torrents
    remote_search = remote_torrents.Search()
    remote_feedback = {'message': '', 'error': False}
    ttk.Label(page, text="Скачивание → подготовка → ручная отправка", style="Section.TLabel").pack(
        anchor="w", pady=(8, 4)
    )
    ttk.Label(
        page,
        text="Нужен запущенный qBittorrent 5.x с локальным Web UI. "
        "Пароль и API-ключ хранятся только до закрытия лаунчера.",
        style="Muted.TLabel",
        wraplength=850,
    ).pack(anchor="w")
    auth_row = ttk.Frame(page)
    auth_row.pack(fill='x', pady=(6, 0))
    ttk.Label(auth_row, text='Способ входа:').pack(side='left', padx=(0, 10))
    entries = {}

    def auth_fields():
        by_key = variables['auth_mode'].get() == 'api_key'
        for key in ('username', 'password', 'api_key'):
            if key in entries:
                active = by_key if key == 'api_key' else not by_key
                entries[key].configure(state='normal' if active else 'disabled')

    for label, value in (('Логин и пароль', 'password'), ('API-ключ (qBittorrent 5.2+)', 'api_key')):
        ttk.Radiobutton(auth_row, text=label, variable=variables['auth_mode'], value=value,
                        command=auth_fields).pack(side='left', padx=(0, 12))
    form = ttk.Frame(page)
    form.pack(fill="x", pady=8)
    form.columnconfigure(1, weight=1)
    form.columnconfigure(3, weight=1)
    for row, pairs in enumerate(
        [
            [("url", "Web UI"), ("username", "Логин")],
            [("root", "Рабочая папка"), ("password", "Пароль")],
            [("quota", "Лимит папки, ГиБ"), ("reserve", "Оставлять свободно, ГиБ")],
            [("speed", "Скачивание, МиБ/с"), ("api_key", "API-ключ")],
        ]
    ):
        for column, (key, label) in enumerate(pairs):
            ttk.Label(form, text=label).grid(row=row, column=column * 2, sticky="w", padx=(0, 8), pady=3)
            entry = ttk.Entry(form, textvariable=variables[key], show="*" if key in ('password', 'api_key') else "")
            entry.grid(row=row, column=column * 2 + 1, sticky="ew", padx=(0, 12), pady=3)
            shortcuts(entry)
            entries[key] = entry
    auth_fields()
    source_row = ttk.Frame(page)
    source_row.pack(fill="x", pady=6)
    source_entry = ttk.Entry(source_row, textvariable=variables["source"])
    source_entry.pack(side="left", fill="x", expand=True)
    shortcuts(source_entry)
    ttk.Button(
        source_row,
        text="Выбрать .torrent",
        command=lambda: variables["source"].set(
            filedialog.askopenfilename(filetypes=[("Torrent", "*.torrent")]) or variables["source"].get()
        ),
    ).pack(side="left", padx=5)

    assistant = {"window": None}

    def describe_source():
        current = assistant['window']
        if current is not None and current.winfo_exists():
            current.lift()
            return
        assistant['window'] = open_search(app, variables['source'].set, prepare_release, shortcuts)

    ttk.Button(page, text="Помощник выбора раздачи", command=describe_source).pack(anchor='w', pady=3)

    tree = ttk.Treeview(page, columns=("title", "state", "detail"), show="headings", height=8)
    for key, title, width in [
        ("title", "Раздача", 290),
        ("state", "Состояние", 155),
        ("detail", "Прогресс / причина", 350),
    ]:
        tree.heading(key, text=title)
        tree.column(key, width=width)
    actions = ttk.Frame(page)
    actions.pack(fill="x", pady=6)
    tree.pack(fill="both", expand=True, pady=5)

    def refresh():
        if not app.winfo_exists():
            return
        selected = tree.selection()
        tree.delete(*tree.get_children())
        queue = queue_box["queue"]
        if queue:
            for job in queue.jobs:
                detail = job.get("error") or (
                    str(job.get("progress", 0)) + "%" if job["status"] == "downloading" else ""
                )
                tree.insert(
                    "",
                    "end",
                    iid=job["id"],
                    values=(job["title"], STATUS.get(job["status"], job["status"]), detail),
                )
            for key in selected:
                if tree.exists(key):
                    tree.selection_add(key)

    def changed():
        try:
            app.after(0, refresh)
        except (RuntimeError, tk.TclError):
            pass

    def snapshot():
        options = {key: variable.get() if key == 'password' else variable.get().strip()
                   for key, variable in variables.items()}
        quota, reserve, speed = (float(options[key]) for key in ("quota", "reserve", "speed"))
        if quota <= 0 or reserve < 0 or speed <= 0:
            raise ValueError("Проверьте лимит папки, резерв и скорость скачивания.")
        if not options["root"]:
            raise ValueError("Укажите рабочую папку.")
        root = str(Path(options["root"]).expanduser().resolve())
        if operation["running"]:
            raise ValueError("Дождитесь окончания текущей операции.")
        if queue_box["root"] != root:
            Path(root).mkdir(parents=True, exist_ok=True)
            guard = queue_lock(root, "torrent-controller.lock")
            guard.__enter__()
            try:
                queue = TorrentQueue(root, quota, reserve)
            except Exception:
                guard.__exit__(None, None, None)
                raise
            if queue_box["guard"]:
                queue_box["guard"].__exit__(None, None, None)
            queue_box.update(queue=queue, root=root, guard=guard)
        else:
            queue_box["queue"].quota = quota * GIB
            queue_box["queue"].reserve = reserve * GIB
        config_path.write_text(
            json.dumps(
                {k: v for k, v in options.items() if k not in ("password", "api_key", "source")},
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        return options, queue_box["queue"]

    def launch(fn, *, ingest=False):
        if operation["running"]:
            report("Дождитесь окончания текущей операции.")
            return False
        operation["running"] = True

        def worker():
            try:
                fn()
            except Exception as error:
                report(str(error))
            finally:
                operation["running"] = False
                changed()

        if not (ingest_background if ingest and ingest_background is not None else background)(worker):
            operation["running"] = False
            report("Лаунчер занят. Дождитесь завершения или нажмите «Стоп».")
            return False
        return True

    def client(options):
        if options['auth_mode'] == 'api_key':
            if not options['api_key']:
                raise ValueError('Скопируйте полный API-ключ из настроек Web UI qBittorrent и вставьте в поле «API-ключ».')
            value = QBitClient(options['url'], api_key=options['api_key'])
        else:
            value = QBitClient(options["url"], options["username"], options["password"])
        value.login()
        active_client["client"] = value
        return value

    def selected_job():
        selected = tree.selection()
        if not selected or not queue_box["queue"]:
            raise ValueError("Выберите раздачу в списке.")
        return next(job for job in queue_box["queue"].jobs if job["id"] == selected[0])

    def connect():
        try:
            options, _ = snapshot()
            launch(lambda: report("Подключено: qBittorrent " + client(options).request("app/version")))
            refresh()
        except Exception as error:
            report(str(error))

    def add():
        try:
            options, queue = snapshot()
            if not options["source"]:
                raise ValueError("Выберите .torrent или вставьте magnet-ссылку.")
            stop.clear()

            def work():
                with queue_lock(queue.root):
                    queue.add(client(options), options["source"], stop, report)
                report("Список получен. Выберите раздачу и нажмите «Выбрать файлы».")

            launch(work)
        except Exception as error:
            report(str(error))

    def prepare_release(candidate, selection):
        settings = {key: selection[key] for key in
                    ('year', 'season', 'preferred_voice', 'max_gib', 'avoid_ads', 'height')}
        choices = candidate if isinstance(candidate, list) else [{'candidate': candidate, 'season': selection['season']}]
        for choice in choices:
            issues = required_issues(choice['candidate'], selection['query'], **{**settings, 'season': choice['season']})
            if issues:
                raise ValueError('Условия не выполнены: ' + '; '.join(issues))
        options, queue = snapshot()
        choices = [choice for choice in choices if not any(source_identity(choice['candidate']) and job.get('release_hash') == source_identity(choice['candidate'])
                   and job['status'] != 'cancelled' for job in queue.jobs)]
        if not choices:
            raise ValueError('Эта раздача уже добавлена в очередь. Продолжите существующее задание.')
        prepare_callback = build_preparer()
        stop.clear()

        def work():
            connection = client(options)
            job_ids = set()
            for choice in choices:
                candidate = choice['candidate']
                seasons = parse_seasons(choice['season'])
                with queue_lock(queue.root):
                    job = queue.add(connection, candidate['source'], stop, report)
                    job.update(release_hash=source_identity(candidate), release_page=candidate['url'],
                               project_seasons=seasons, mode='project',
                               catalog_title=selection['query'], catalog_section='series' if seasons else 'movies')
                    queue.save()
                    try:
                        ids = auto_file_ids(job['files'], season=seasons)
                        size = sum(file['size'] for file in job['files'] if file['index'] in ids)
                        if selection['max_gib'] is not None and size > selection['max_gib'] * GIB:
                            raise ValueError('Фактический размер видео превышает выбранный лимит.')
                        queue.select(job, ids, mode='project', section='series' if seasons else 'movies')
                        job_ids.add(job['id'])
                    except ValueError as error:
                        job.update(status='choose_files', error=str(error))
                        queue.save()
                        raise
            changed()
            queue.run(connection, stop, report, changed, prepare_callback, float(options['speed']), job_ids=job_ids)

        if not launch(work, ingest=True):
            raise ValueError('Лаунчер занят. Дождитесь завершения текущей операции.')

    def choose_files():
        try:
            options, queue = snapshot()
            job = selected_job()
            if job.get("import_folder"):
                raise ValueError("Этот выбор уже скачан. Откройте его для отправки.")

            def work():
                connection = client(options)
                with queue_lock(queue.root):
                    if not job.get("hash"):
                        raise ValueError("Раздача не добавлена. Попробуйте добавить .torrent заново.")
                    queue.verify(connection, job)
                    connection.stop(job["hash"])
                    files = connection.files(job["hash"])
                    if not files:
                        raise ValueError("Нет списка файлов. Попробуйте .torrent вместо magnet.")
                    connection.priorities(job["hash"], [f["index"] for f in files], 0)
                    job["files"] = files
                    queue.save()
                app.after(0, show_files, job)

            launch(work)
        except Exception as error:
            report(str(error))

    def show_files(job):
        dialog = tk.Toplevel(app)
        dialog.title("Выбор файлов — " + job["title"])
        dialog.geometry("900x600")
        listing = ttk.Treeview(dialog, columns=("name", "size"), show="headings", selectmode="extended")
        listing.heading("name", text="Видео (Ctrl/Shift — несколько файлов)")
        listing.heading("size", text="ГиБ")
        listing.column("name", width=700)
        listing.column("size", width=90)
        listing.pack(fill="both", expand=True, padx=12, pady=10)
        for file in job["files"]:
            if Path(file["name"]).suffix.lower() in VIDEO_EXTENSIONS:
                listing.insert(
                    "", "end", iid=str(file["index"]), values=(file["name"], f"{file['size'] / GIB:.2f}")
                )
        for key in job["selected"]:
            if listing.exists(str(key)):
                listing.selection_add(str(key))
        kind = tk.StringVar(value=job["mode"])
        section = tk.StringVar(
            value=next((name for name, key in sections.items() if key == job["catalog_section"]), "Фильмы")
        )
        ttk.Radiobutton(
            dialog, text="Отдельные фильмы: каждый файл — отдельная карточка", variable=kind, value="batch"
        ).pack(anchor="w", padx=12)
        ttk.Radiobutton(
            dialog,
            text="Один фильм: выбранные файлы — варианты одной карточки",
            variable=kind,
            value="single",
        ).pack(anchor="w", padx=12)
        ttk.Radiobutton(
            dialog, text="Сезон существующего сериала: выбранные файлы — серии", variable=kind, value="season"
        ).pack(anchor="w", padx=12)
        ttk.Radiobutton(dialog, text='Новый проект: скачать и передать в общую очередь без кода карточки',
                        variable=kind, value='project').pack(anchor='w', padx=12)
        ttk.Combobox(dialog, textvariable=section, values=list(sections), state="readonly").pack(
            anchor="w", padx=12, pady=8
        )
        series_fields = ttk.Frame(dialog)
        series_fields.pack(fill="x", padx=12)
        movie_number = tk.StringVar(value=str(job.get("movie_number") or ""))
        season_number = tk.StringVar(value=str(job.get("season_number") or "1"))
        ttk.Label(series_fields, text="Для сезона: код сериала").pack(side="left")
        ttk.Entry(series_fields, textvariable=movie_number, width=12).pack(side="left", padx=6)
        ttk.Label(series_fields, text="Номер сезона").pack(side="left")
        ttk.Entry(series_fields, textvariable=season_number, width=8).pack(side="left", padx=6)

        def save():
            try:
                if operation["running"]:
                    raise ValueError("Очередь работает. Сначала приостановите её.")
                queue = queue_box["queue"]
                with queue_lock(queue.root):
                    queue.select(
                        job,
                        [int(key) for key in listing.selection()],
                        mode=kind.get(),
                        section=sections[section.get()],
                        movie_number=int(movie_number.get()) if kind.get() == "season" else None,
                        season_number=int(season_number.get()) if kind.get() == "season" else None,
                    )
                dialog.destroy()
                refresh()
                report("Выбор сохранён. Запустите «Скачать и подготовить».")
            except Exception as error:
                report(str(error))

        ttk.Button(
            dialog, text="Выбрать все видео", command=lambda: listing.selection_set(listing.get_children())
        ).pack(side="left", padx=12, pady=10)
        ttk.Button(dialog, text="Сохранить выбор", style="Primary.TButton", command=save).pack(
            side="right", padx=12, pady=10
        )

    def run():
        try:
            options, queue = snapshot()
            if not any(
                job.get("selected") and job["status"] not in ("prepared", "cancelled", 'project_queued') for job in queue.jobs
            ):
                raise ValueError("Нет заданий. Сначала выберите видеофайлы раздачи.")
            prepare_callback = build_preparer()
            stop.clear()
            launch(
                lambda: queue.run(
                    client(options), stop, report, changed, prepare_callback, float(options["speed"])
                )
            )
            if operation["running"] and quiet():
                app.iconify()
        except Exception as error:
            report(str(error))

    def open_for_send():
        try:
            if operation["running"]:
                raise ValueError("Дождитесь окончания операции.")
            job = selected_job()
            if job["status"] not in ('prepared', 'project_queued'):
                raise ValueError("Сначала скачайте и подготовьте этот проект.")
            open_job(job)
        except Exception as error:
            report(str(error))

    def reprepare_selected():
        try:
            if operation['running']:
                raise ValueError('Очередь работает. Сначала приостановите её.')
            _options, queue = snapshot()
            job = selected_job()
            prepare_callback = build_preparer()
            stop.clear()

            def work():
                with queue_lock(queue.root):
                    queue.reprepare(job)
                    try:
                        queue.prepare(job, stop, prepare_callback, changed)
                    except Exception as error:
                        job.update(status='paused' if stop.is_set() else 'error', error=str(error))
                        queue.save()
                        changed()
                        raise
                report('Задача переподготовлена с выбранными качествами. Проверьте новую карточку в WEB перед отправкой.')

            launch(work)
            if operation['running'] and quiet():
                app.iconify()
        except Exception as error:
            report(str(error))

    def cancel():
        try:
            options, queue = snapshot()
            job = selected_job()
            if job.get('project_id'):
                raise ValueError('Исходники уже в очереди проектов. Приостановите проект в разделе «Проекты».')

            def work():
                with queue_lock(queue.root):
                    connection = client(options)
                    queue.verify(connection, job)
                    connection.stop(job["hash"])
                    job.update(status="cancelled", error="Файлы сохранены на диске.")
                    queue.save()

            launch(work)
        except Exception as error:
            report(str(error))

    ttk.Button(actions, text="Подключить / обновить", command=connect).pack(side="left", padx=2)
    ttk.Button(actions, text="Добавить источник", command=add).pack(side="left", padx=2)
    ttk.Button(actions, text="Выбрать файлы", command=choose_files).pack(side="left", padx=2)
    ttk.Button(actions, text="Переподготовить выбранную", command=reprepare_selected).pack(side="left", padx=2)
    bottom = ttk.Frame(page)
    bottom.pack(fill="x", pady=5)
    ttk.Button(bottom, text="Скачать и подготовить", style="Primary.TButton", command=run).pack(
        side="left", padx=2
    )
    ttk.Button(bottom, text="Пауза", command=stop.set).pack(side="left", padx=2)
    ttk.Button(bottom, text="Отменить выбранную", command=cancel).pack(side="left", padx=2)
    ttk.Button(bottom, text="Открыть для отправки", command=open_for_send).pack(side="right", padx=2)
    ttk.Label(
        page,
        text="Подготовка использует качества из раздела «Источник». Отправка в Telegram не запускается автоматически. "
        "Исходники и готовые копии не удаляются автоматически.",
        style="Muted.TLabel",
        wraplength=850,
    ).pack(anchor="w", pady=5)
    # Restore the local journal without contacting qBittorrent or starting jobs.
    try:
        snapshot()
        refresh()
    except Exception as error:
        report(str(error))

    def shutdown():
        # Closing Tk must also stop only our active download in qBittorrent.
        import threading
        remote_search.stop.set()

        connection, queue = active_client["client"], queue_box["queue"]
        if connection and queue:
            hashes = [
                job.get("hash")
                for job in queue.jobs
                if job["status"] in ("downloading", "metadata") and job.get("hash")
            ]

            def halt():
                for torrent_hash in hashes:
                    try:
                        connection.stop(torrent_hash)
                    except Exception:
                        pass

            threading.Thread(target=halt, daemon=False).start()
        if queue_box["guard"]:
            queue_box["guard"].__exit__(None, None, None)
            queue_box["guard"] = None

    def remote_snapshot():
        return {'search': remote_search.snapshot(), 'jobs': remote_torrents.jobs_snapshot(queue_box['queue']),
                'busy': operation['running'], 'folder': queue_box['root'], **remote_feedback}

    def remote_command(action, payload):
        if action == 'torrent_search':
            remote_search.start(payload)
            return 'Поиск запущен. Результаты появятся в WEB.'
        if action == 'torrent_search_stop':
            remote_search.stop.set()
            return 'Остановка поиска запрошена.'
        if action == 'torrent_pause':
            if operation['running']:
                stop.set()
            return 'Пауза скачивания запрошена.'
        if action not in ('torrent_add', 'torrent_start', 'torrent_files'):
            raise ValueError('Неизвестная команда торрента.')
        options, queue = snapshot()
        prepare_callback = None
        if action in ('torrent_start', 'torrent_files'):
            job = next((j for j in queue.jobs if j['id'] == payload.get('job_id')), None)
            if job is None or job['status'] in ('prepared', 'project_queued', 'cancelled'):
                raise ValueError('Выберите незавершённое скачивание.')
            if action == 'torrent_start':
                qualities = payload.get('qualities')
                if not isinstance(qualities, list) or not qualities or any(q not in ('source','1080p','720p','480p','360p') for q in qualities):
                    raise ValueError('Выберите качества для подготовки проекта.')
                prepare_callback = build_preparer(qualities)
            elif job.get('import_folder'):
                raise ValueError('Файлы уже скачаны. Продолжите с прежним выбором.')
        elif not payload.get('result_id'):
            remote_torrents.validate_magnet(payload.get('magnet'))
        seasons = parse_seasons(payload.get('seasons', '0')) if action == 'torrent_add' else None
        stop.clear()
        remote_feedback.update(message='Операция запущена…', error=False)

        def work():
            try:
                connection = client(options)
                if action == 'torrent_add':
                    candidate = remote_search.resolve(payload['result_id']) if payload.get('result_id') else {'source': payload['magnet']}
                    identity = source_identity(candidate)
                    if identity and any(j.get('release_hash') == identity and j['status'] != 'cancelled' for j in queue.jobs):
                        raise ValueError('Раздача уже в очереди. Выберите существующее скачивание.')
                    with queue_lock(queue.root):
                        added = queue.add(connection, candidate['source'], stop, report)
                        added.update(release_hash=identity, project_seasons=seasons, mode='project',
                                     catalog_section='series' if seasons else 'movies')
                        try:
                            added['selected'] = auto_file_ids(added['files'], season=seasons)
                        except ValueError:
                            added['selected'] = []
                        queue.save()
                    remote_feedback.update(message='Список файлов получен. Проверьте выбор и нажмите «Скачать и подготовить».', error=False)
                elif action == 'torrent_files':
                    with queue_lock(queue.root):
                        queue.verify(connection, job)
                        connection.stop(job['hash'])
                        files = connection.files(job['hash'])
                        if not files:
                            raise ValueError('Список файлов ещё не получен. Повторите позже.')
                        connection.priorities(job['hash'], [f['index'] for f in files], 0)
                        job.update(files=files, status='choose_files', error='')
                        queue.save()
                    remote_feedback.update(message='Список файлов обновлён. Проверьте выбор.', error=False)
                else:
                    if not job.get('import_folder'):
                        with queue_lock(queue.root):
                            queue.select(job, payload.get('file_ids', []), mode='project', section=job['catalog_section'])
                    elif payload.get('file_ids') != job.get('selected'):
                        raise ValueError('Файлы уже скачаны. Продолжите с прежним выбором.')
                    queue.run(connection, stop, report, changed, prepare_callback, float(options['speed']), job_ids={job['id']})
                    remote_feedback.update(message=remote_torrents.public_error(job.get('error') or STATUS.get(job['status'], job['status'])), error=job['status']=='error')
            except Exception as error:
                remote_feedback.update(message=remote_torrents.public_error(error), error=True)
                raise ValueError(remote_torrents.public_error(error)) from None
        if not launch(work, ingest=True):
            remote_feedback.update(message='Лаунчер занят другой операцией. Повторите позже.', error=True)
            raise ValueError(remote_feedback['message'])
        return 'Операция запущена. Следите за состоянием скачивания в WEB.'

    if remote is not None:
        remote.update(snapshot=remote_snapshot, command=remote_command)
    return shutdown
