"""Persistent, fair local rendering projects. Credentials never enter the journal."""
import copy
import json
import os
import sqlite3
import threading
import time
import uuid
from contextlib import closing
from pathlib import Path


LABELS = {
    'queued': 'В очереди', 'running': 'Подготовка', 'pausing': 'Останавливается',
    'paused': 'Пауза', 'error': 'Ошибка', 'prepared': 'MP4 готовы',
    'registering': 'Создание карточек', 'cards_ready': 'Карточки созданы',
    'sending': 'Отправка', 'send_paused': 'Отправка приостановлена', 'complete': 'Принято ботом',
}


def build_project(folder, qualities, mode='single', section='movies', stop=None, series=None, *,
                  new_series=False, title=None, fallback_season=None, project_id=None):
    """Freeze source identities, audio names and encoding settings before scheduling."""
    try:
        from . import desktop_launcher as launcher
    except ImportError:
        import desktop_launcher as launcher

    stop = stop or threading.Event()
    if mode not in ('single', 'batch'):
        raise ValueError('Неизвестный режим проекта.')
    if new_series and (mode != 'single' or series):
        raise ValueError('Новый сериал должен быть одним проектом без кода существующей карточки.')
    if not qualities or any(q not in launcher.QUALITY_NAMES for q in qualities):
        raise ValueError('Выберите качества проекта.')
    if series:
        if mode != 'single' or any(type(series.get(k)) is not int or not 0 < series[k] < 2**53
                                   for k in ('movie_number', 'season_number')):
            raise ValueError('Для сезона выберите один проект, номер существующего сериала и номер сезона.')
        section = series.get('catalog_section', 'series')
    chosen = Path(folder).resolve()
    if mode == 'batch':
        sources = [chosen / item['file'] for item in launcher.batch_plan(chosen)]
    else:
        sources = [chosen]
    project_id = project_id or uuid.uuid4().hex
    output = chosen.parent / 'KinoTime-Ready' / ('project-' + project_id)
    tasks = []
    for group_index, source in enumerate(sources):
        original, plans = launcher.plan_folder_qualities(source, qualities, stop)
        root = Path(original['root'])
        for source_index, plan in enumerate(plans):
            identity = None
            if new_series:
                try:
                    from .release_search import episode_identity
                except ImportError:
                    from release_search import episode_identity
                identity = episode_identity(plan['entry']['path'], fallback_season)
                if identity is None:
                    raise ValueError('Не удалось определить сезон и серию файла: ' + plan['entry']['path'])
            for quality in plan['qualities']:
                tasks.append({
                    'id': uuid.uuid4().hex, 'group': str(group_index), 'title': (title or original['title'])[:200],
                    'source': str(root / plan['entry']['path']), 'entry': plan['entry'],
                    'source_index': source_index, 'plan': {**plan, 'qualities': [quality]},
                    'state': 'pending', 'files': [],
                    'count': max(1, sum(a['enabled'] for a in plan['audio'])),
                    **({'season_number': identity[0], 'episode_number': identity[1]} if identity else {}),
                })
    return {'id': project_id, 'folder': str(chosen), 'title': (title or (chosen.stem if chosen.is_file() else chosen.name))[:200],
            'mode': mode, 'section': section, 'output': str(output), 'tasks': tasks,
            'state': 'queued', 'message': '', 'priority': 0, 'turn': 0, 'cards': {},
            'created': time.time(), 'series': series, 'new_series': bool(new_series)}


def handoff_download(manager, job, folder, qualities, stop):
    """Durable idempotent handoff: a crash before the torrent journal save cannot duplicate a project."""
    try:
        from .release_search import season_set
    except ImportError:
        from release_search import season_set
    project_id = uuid.uuid5(uuid.NAMESPACE_URL, 'kinotime-download:' + str(Path(folder).resolve()) + ':' + job['id']).hex
    try:
        manager.get(project_id)
        return project_id
    except KeyError:
        pass
    seasons = job.get('project_seasons', 0)
    requested = season_set(seasons)
    project = build_project(folder, qualities, 'single', job['catalog_section'], stop,
                            new_series=bool(seasons), title=job.get('catalog_title') or job['title'],
                            fallback_season=next(iter(requested)) if len(requested) == 1 else None,
                            project_id=project_id)
    if stop.is_set():
        raise InterruptedError('Передача в очередь проектов остановлена.')
    manager.add(project)
    return project_id


def register_or_send(manager, project_id, client, stop, report, connect=None, sending=False):
    try:
        from .desktop_launcher import keep_awake
    except ImportError:
        from desktop_launcher import keep_awake
    with keep_awake():
        return _register_or_send(manager, project_id, client, stop, report, connect, sending)


def _register_or_send(manager, project_id, client, stop, report, connect=None, sending=False):
    """Use durable asset IDs to resume sends even after confirmed MP4s were reclaimed."""
    try:
        from . import desktop_launcher as launcher
    except ImportError:
        import desktop_launcher as launcher
    project = manager.get(project_id)
    if project['state'] not in ('prepared', 'cards_ready', 'send_paused'):
        raise ValueError('Сначала завершите подготовку проекта.')
    manifests = manager.manifests(project_id)
    if sending and (set(project['cards']) != set(manifests) or
                    set(project.get('card_data', {})) != set(manifests)):
        raise ValueError('Сначала создайте все карточки проекта.')
    manager.bind(project_id, client.url, client.instance_id)
    phase = 'sending' if sending else 'registering'
    manager.phase(project_id, phase)
    safe = launcher.RetryingClient(client, stop, report)
    try:
        session = safe.request('session')
        manager.verify_account(project_id, session.get('admin_id'), session['bot_username'])
        desktop = None
        for group, manifest in manifests.items():
            if stop.is_set():
                raise InterruptedError('Операция проекта приостановлена.')
            if sending:
                data = copy.deepcopy(project['card_data'][group])
                draft_id = project['cards'][group]
                state = safe.request('import/' + draft_id)
                if state['draft_status'] == 'published':
                    continue
                expected = {i['asset_id'] for i in data['draft']['body']['items']}
                if expected != {f['id'] for f in state['files']}:
                    raise ValueError('Состав карточки изменён в WEB. Проверьте файлы перед отправкой.')
                data.update(bot_username=session['bot_username'], bot_name=session['bot_name'])
                launcher.resume_rejected_before_send(safe, data, report)
                desktop = launcher.send_with_recovery(safe, data, manifest, desktop, stop,
                                                     report, connect, launcher.transfer)
                if stop.is_set():
                    raise InterruptedError('Отправка проекта приостановлена.')
                confirmed = safe.request('import/' + draft_id)
                if not confirmed['files'] or any(f['status'] != 'ready' for f in confirmed['files']):
                    raise ValueError('Бот подтвердил не все файлы. Готовые копии сохранены.')
            else:
                if group in project['cards']:
                    # A response already journalled is enough; do not hash reclaimed files again.
                    continue
                data = safe.request('import', manifest)
                record = {'draft': {'id': data['draft']['id'], 'body': {'items': [
                    {k: item[k] for k in ('asset_id', 'relative_path', 'name') if k in item}
                    for item in data['draft']['body']['items']]}}}
                manager.phase(project_id, phase, card=(group, data['draft']['id']), card_data=record)
        manager.phase(project_id, 'complete' if sending else 'cards_ready',
                      'Принято ботом. Проверьте публикацию в WEB.' if sending else
                      'Оформите карточки в WEB, затем нажмите «Отправить».')
    except Exception as error:
        manager.phase(project_id, 'send_paused' if sending else 'prepared', str(error))
        raise


def render_task(project, task, stop, report):
    try:
        from . import desktop_launcher as launcher, disk_space
    except ImportError:
        import desktop_launcher as launcher
        import disk_space

    source = Path(task['source'])
    info = source.stat()
    if info.st_size != task['entry']['size'] or str(info.st_mtime_ns) != task['entry']['mtime']:
        raise ValueError('Исходник изменился: ' + str(source))
    output = Path(project['output'])
    with disk_space.session(), launcher.keep_awake():
        disk_space.protect(output)
        manifest = launcher.prepare_media(source, report, stop, qualities=task['plan']['qualities'],
                                         cache_root=output / 'cache', media_plan=task['plan'])
        card_root = output / 'cards' / task['group']
        files = []
        for entry in manifest['files']:
            if stop.is_set():
                raise InterruptedError('Проект приостановлен. Готовые файлы сохранены.')
            generated = Path(manifest['root']) / entry['path']
            relative = Path(entry['quality']) / f"{task['source_index']:04d}-{source.stem[:80].rstrip(' .')}" / generated.name
            target = card_root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            if not target.exists():
                partial = target.with_name(target.stem + '.' + uuid.uuid4().hex + '.partial.mp4')
                try:
                    try:
                        os.link(generated, partial)
                    except OSError:
                        disk_space.copy(generated, partial, stop, report)
                    partial.replace(target)
                finally:
                    partial.unlink(missing_ok=True)
            stat = target.stat()
            original = generated.stat()
            if (stat.st_size, stat.st_mtime_ns) != (original.st_size, original.st_mtime_ns):
                raise ValueError('Готовая копия изменилась: ' + str(target))
            disk_space.register(target, alias=generated)
            files.append({**entry, 'path': relative.as_posix(),
                          'name': f"Серия {task['episode_number']}" if project.get('new_series') else source.stem,
                          **({'season_number': task['season_number']} if project.get('new_series') else {}),
                          'voice': entry.get('voice', '')[:80],
                          'size': stat.st_size, 'mtime': str(stat.st_mtime_ns)})
        info = source.stat()
        if info.st_size != task['entry']['size'] or str(info.st_mtime_ns) != task['entry']['mtime']:
            raise ValueError('Исходник изменился во время обработки: ' + str(source))
        return files


class ProjectManager:
    """One quality/episode per turn; at most one worker owns any given project."""

    def __init__(self, path, runner=render_task):
        self.path, self.runner = Path(path), runner
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._condition = threading.Condition(threading.RLock())
        self._workers = {}
        self._closed = False
        self._dispatcher = None
        self._projects = {}
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute('CREATE TABLE IF NOT EXISTS projects (id TEXT PRIMARY KEY, body TEXT NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value INTEGER NOT NULL)')
            row = db.execute("SELECT value FROM settings WHERE key='parallelism'").fetchone()
            self.parallelism = max(1, min(4, row[0])) if row else 2
            for project_id, body in db.execute('SELECT id,body FROM projects'):
                project = json.loads(body)
                for task in project['tasks']:
                    if task['state'] == 'running':
                        task['state'] = 'pending'
                if project['state'] in ('queued', 'running', 'pausing', 'registering'):
                    project.update(state='paused', message='Восстановлен после закрытия. Нажмите «Продолжить».')
                elif project['state'] == 'sending':
                    project.update(state='send_paused', message='Проверьте подтверждения перед продолжением отправки.')
                self._projects[project_id] = project
        self._turn = max((p['turn'] for p in self._projects.values()), default=0)
        for project in self._projects.values():
            self._save(project)

    def _save(self, project):
        with closing(sqlite3.connect(self.path, timeout=10)) as db, db:
            db.execute('INSERT OR REPLACE INTO projects VALUES (?,?)',
                       (project['id'], json.dumps(project, ensure_ascii=False)))

    def add(self, project):
        with self._condition:
            if self._closed:
                raise ValueError('Лаунчер закрывается.')
            sources = {t['source'].casefold() for t in project['tasks']}
            for old in self._projects.values():
                if old['state'] != 'complete' and sources.intersection(t['source'].casefold() for t in old['tasks']):
                    raise ValueError('Эти исходники уже есть в проекте «' + old['title'] + '». Продолжите его.')
            if not project['tasks']:
                raise ValueError('В проекте нет задач.')
            self._save(project)
            self._projects[project['id']] = copy.deepcopy(project)
            self._start()
            return project['id']

    def _start(self):
        if self._dispatcher is None:
            self._dispatcher = threading.Thread(target=self._dispatch, name='project-scheduler', daemon=True)
            self._dispatcher.start()
        self._condition.notify_all()

    @property
    def busy(self):
        with self._condition:
            return bool(self._workers) or any(p['state'] in ('queued', 'running') for p in self._projects.values())

    def snapshots(self, details=False):
        with self._condition:
            result = []
            for project in self._projects.values():
                if details:
                    result.append(copy.deepcopy(project))
                else:
                    result.append({**{k: project[k] for k in ('id', 'title', 'folder', 'state', 'message', 'priority')},
                                   'ready': sum(t['count'] for t in project['tasks'] if t['state'] == 'done'),
                                   'total': sum(t['count'] for t in project['tasks'])})
            return result

    def get(self, project_id):
        with self._condition:
            return copy.deepcopy(self._projects[project_id])

    def set_parallelism(self, value):
        if type(value) is not int or not 1 <= value <= 4:
            raise ValueError('Число одновременных обработок: от 1 до 4.')
        with self._condition:
            if self.busy:
                raise ValueError('Приостановите проекты перед изменением числа обработок.')
            with closing(sqlite3.connect(self.path)) as db, db:
                db.execute("INSERT OR REPLACE INTO settings VALUES ('parallelism',?)", (value,))
            self.parallelism = value

    def pause(self, project_id=None):
        with self._condition:
            for project in self._projects.values():
                if project_id is not None and project['id'] != project_id:
                    continue
                if project['state'] in ('running', 'queued', 'pausing'):
                    worker = self._workers.get(project['id'])
                    project['state'] = 'pausing' if worker else 'paused'
                    if worker:
                        worker[0].set()
                    self._save(project)
            self._condition.notify_all()

    def resume(self, project_id):
        with self._condition:
            if self._closed:
                raise ValueError('Лаунчер закрывается.')
            project = self._projects[project_id]
            if project_id in self._workers or project['state'] in ('registering', 'sending', 'complete'):
                raise ValueError('Дождитесь завершения текущей операции проекта.')
            project.update(state='queued' if any(t['state'] != 'done' for t in project['tasks']) else
                           ('cards_ready' if project['cards'] else 'prepared'), message='')
            self._save(project)
            self._start()

    def prioritize(self, project_id):
        with self._condition:
            project = self._projects[project_id]
            project['priority'] = 0 if project['priority'] else 1
            self._save(project)
            self._condition.notify_all()

    def phase(self, project_id, state, message='', card=None, card_data=None):
        with self._condition:
            project = self._projects[project_id]
            if project_id in self._workers or any(t['state'] != 'done' for t in project['tasks']):
                raise ValueError('Сначала завершите подготовку проекта.')
            project.update(state=state, message=str(message)[:2000])
            if card:
                project['cards'][card[0]] = card[1]
                if card_data:
                    project.setdefault('card_data', {})[card[0]] = copy.deepcopy(card_data)
            self._save(project)

    def bind(self, project_id, server, instance):
        with self._condition:
            project = self._projects[project_id]
            binding = {'server': server, 'instance': instance}
            if project.get('binding', binding) != binding:
                raise ValueError('Проект привязан к другой панели или компьютеру. Верните прежнее подключение.')
            project['binding'] = binding
            self._save(project)

    def verify_account(self, project_id, admin_id, bot_username):
        with self._condition:
            project = self._projects[project_id]
            account = {'admin_id': admin_id, 'bot_username': bot_username}
            if project.get('account', account) != account:
                raise ValueError('Код подключения относится к другому администратору или боту.')
            project['account'] = account
            self._save(project)

    def manifests(self, project_id):
        project = self.get(project_id)
        if any(t['state'] != 'done' for t in project['tasks']):
            raise ValueError('Сначала завершите подготовку проекта.')
        groups = {}
        for task in project['tasks']:
            manifest = groups.setdefault(task['group'], {
                'version': 1, 'root': str(Path(project['output']) / 'cards' / task['group']),
                'title': task['title'], 'catalog_section': project['section'], 'files': []})
            if project.get('series'):
                manifest.update(content_type='series', movie_number=project['series']['movie_number'],
                                season_number=project['series']['season_number'])
            elif project.get('new_series'):
                manifest.update(content_type='series', season_number=None, new_series=True)
            manifest['files'].extend(copy.deepcopy(task['files']))
        return groups

    def _dispatch(self):
        with self._condition:
            while not self._closed:
                eligible = [p for p in self._projects.values() if p['state'] == 'queued'
                            and p['id'] not in self._workers]
                if len(self._workers) >= self.parallelism or not eligible:
                    self._condition.wait(.25)
                    continue
                project = min(eligible, key=lambda p: (-p['priority'], p['turn'], p['created']))
                task = next((t for t in project['tasks'] if t['state'] != 'done'), None)
                if task is None:
                    project['state'] = 'prepared'
                    self._save(project)
                    continue
                self._turn += 1
                project.update(state='running', turn=self._turn, message='')
                task['state'] = 'running'
                task['progress'] = {}
                try:
                    self._save(project)
                except (OSError, sqlite3.Error) as error:
                    project.update(state='error', message='Не удалось сохранить очередь: ' + str(error))
                    task['state'] = 'pending'
                    continue
                stop = threading.Event()
                thread = threading.Thread(target=self._work, args=(project['id'], task['id'], stop), daemon=True)
                self._workers[project['id']] = (stop, thread)
                thread.start()

    def _work(self, project_id, task_id, stop):
        try:
            from . import video_hardware
        except ImportError:
            import video_hardware

        project = self.get(project_id)
        task = next(t for t in project['tasks'] if t['id'] == task_id)
        def report(text):
            with self._condition:
                self._projects[project_id]['message'] = str(text)[:2000]
        def variant(name, status, detail):
            with self._condition:
                current = next(t for t in self._projects[project_id]['tasks'] if t['id'] == task_id)
                current.setdefault('progress', {})[name] = {'status': status, 'detail': detail}
            report(f"{Path(task['source']).name} · {name}: {status} {detail}")
        report.variant = variant
        files, failure = None, None
        try:
            with video_hardware.worker_budget(self.parallelism):
                files = self.runner(project, task, stop, report)
            if len(files) != task['count']:
                raise ValueError('Обработка вернула не все выбранные озвучки.')
        except Exception as error:
            failure = error
        finally:
            with self._condition:
                project = self._projects[project_id]
                task = next(t for t in project['tasks'] if t['id'] == task_id)
                if failure is None:
                    task.update(state='done', files=files)
                    done = all(t['state'] == 'done' for t in project['tasks'])
                    project['state'] = 'prepared' if done else ('paused' if stop.is_set() else 'queued')
                    project['message'] = 'MP4 готовы. Создайте карточки, затем проверьте их в WEB.' if done else ''
                else:
                    task['state'] = 'pending'
                    project.update(state='paused' if stop.is_set() or isinstance(failure, InterruptedError) else 'error',
                                   message=str(failure)[:2000])
                try:
                    self._save(project)
                except (OSError, sqlite3.Error) as error:
                    project.update(state='error', message='Не удалось сохранить очередь: ' + str(error))
                finally:
                    self._workers.pop(project_id, None)
                    self._condition.notify_all()

    def close(self):
        with self._condition:
            self._closed = True
            self.pause()
            self._condition.notify_all()

    def wait_closed(self, timeout=10):
        deadline = time.monotonic() + timeout
        with self._condition:
            while self._workers and time.monotonic() < deadline:
                self._condition.wait(max(.01, deadline - time.monotonic()))
            return not self._workers
