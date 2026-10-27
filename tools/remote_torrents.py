"""Bounded remote search. Tracker URLs and credentials never leave the computer."""
import copy
import json
import os
import re
import threading
import uuid
from urllib.parse import parse_qs, urlsplit

try:
    from .release_search import DEFAULT_SITE, RutorClient, rank, parse_seasons
    from .jackett_search import merge_sources
    from .jackett_sources_ui import load_sources, selected_indexers, connection
    from .launcher_runtime import state_path
    from .torrent_queue import VIDEO_EXTENSIONS
except ImportError:
    from release_search import DEFAULT_SITE, RutorClient, rank, parse_seasons
    from jackett_search import merge_sources
    from jackett_sources_ui import load_sources, selected_indexers, connection
    from launcher_runtime import state_path
    from torrent_queue import VIDEO_EXTENSIONS


def public_error(error):
    text = re.sub(r'https?://\S+', '[адрес источника]', str(error))
    return re.sub(r'(?i)(api[_-]?key|password|passkey|token)[=: ]+[^\s,;]+', r'\1=***', text)[:500]


def validate_magnet(value):
    if not isinstance(value, str) or len(value) > 8000 or any(ord(c) < 32 for c in value):
        raise ValueError('Вставьте корректную magnet-ссылку.')
    parsed = urlsplit(value)
    if parsed.scheme != 'magnet' or not any(re.fullmatch(r'urn:btih:([0-9a-fA-F]{40}|[a-zA-Z2-7]{32})', v)
                                           for v in parse_qs(parsed.query).get('xt', [])):
        raise ValueError('Вставьте magnet-ссылку с хешем раздачи BTIH.')
    return value


class Search:
    def __init__(self):
        self.lock = threading.RLock()
        self.stop = threading.Event()
        self.state = {'state': 'idle', 'message': '', 'results': []}
        self.rows = {}
        self.jackett = None

    def snapshot(self):
        with self.lock:
            return copy.deepcopy(self.state)

    def progress(self, message):
        with self.lock:
            self.state['message'] = public_error(message)

    def start(self, payload):
        query = payload.get('query', '').strip()
        if not 3 <= len(query) <= 120:
            raise ValueError('Введите название от 3 до 120 символов.')
        season = parse_seasons(payload.get('seasons', '0'))
        with self.lock:
            if self.state['state'] == 'searching':
                raise ValueError('Поиск уже выполняется.')
            self.stop.clear()
            self.rows = {}
            self.state = {'state': 'searching', 'message': 'Ищу раздачи…', 'query': query, 'results': []}
        threading.Thread(target=self.run, args=(query, season), daemon=True).start()

    def run(self, query, season):
        try:
            options = load_sources(session=True)
            candidates, errors, jackett = [], [], None
            site = os.environ.get('KINOTIME_RUTOR_URL', DEFAULT_SITE)
            try:
                saved = json.loads(state_path('release-search-settings.json', __file__).read_text('utf-8'))
                site = saved.get('site') or site
            except (OSError, ValueError):
                pass
            if options['rutor']:
                try:
                    found, problems = RutorClient(site, stop=self.stop).search(query, self.progress, limit=80, max_pages=3)
                    candidates.extend(found)
                    errors.extend(problems)
                except Exception as error:
                    errors.append(public_error(error))
            indexers = selected_indexers(options)
            if indexers and not self.stop.is_set():
                try:
                    jackett = connection(options, self.stop)
                    found, problems = jackett.search(query, indexers, self.progress)
                    candidates.extend(found)
                    errors.extend(problems)
                except Exception as error:
                    errors.append(public_error(error))
            if not options['rutor'] and not indexers:
                raise ValueError('Выберите источники в лаунчере: Торренты → Помощник → Источники поиска.')
            rows = rank(merge_sources(candidates), query, season=season, avoid_ads=False, height=None, max_gib=None)
            with self.lock:
                self.jackett = jackett
                self.rows = {uuid.uuid4().hex: row for row in rows[:80]}
                self.state.update(state='cancelled' if self.stop.is_set() else 'done',
                    message=('Поиск остановлен.' if self.stop.is_set() else f'Найдено: {len(rows)}. Показано до 80 раздач.')
                            + (' ' + ' · '.join(public_error(e) for e in errors[:3]) if errors else ''),
                    results=[{'id': key, 'title': row['title'][:300], 'tracker': row.get('tracker', 'Rutor')[:100],
                              'size': row['description'].value('size')[:100],
                              'quality': row['description'].value('resolution')[:100],
                              'voice': row['description'].value('translation')[:300],
                              'year': row['description'].value('year')[:50],
                              'seeds': row.get('seeds'),
                              'issues': [str(i)[:200] for i in row.get('issues', [])[:5]]}
                             for key, row in self.rows.items()] if not self.stop.is_set() else [])
        except Exception as error:
            with self.lock:
                self.state.update(state='error', message=public_error(error), results=[])

    def resolve(self, result_id):
        with self.lock:
            if self.state['state'] != 'done' or result_id not in self.rows:
                raise ValueError('Результат поиска устарел. Повторите поиск и выберите раздачу.')
            row, client = copy.deepcopy(self.rows[result_id]), self.jackett
        if row.get('download_path'):
            if client is None:
                raise ValueError('Повторите поиск для подключения Jackett.')
            row = client.materialize(row)
        return row


def jobs_snapshot(queue):
    if queue is None:
        return []
    return [{**{key: job.get(key) for key in ('id', 'title', 'status', 'progress', 'project_id')},
             'error': public_error(job.get('error', '')), 'selected': list(job.get('selected', [])),
             'seasons': job.get('project_seasons', 0),
             'selection_locked': bool(job.get('import_folder')),
             'files': [{'index': f['index'], 'name': f['name'], 'size': f['size']}
                       for f in list(job.get('files', [])) if any(str(f['name']).lower().endswith(ext) for ext in VIDEO_EXTENSIONS)]}
            for job in list(queue.jobs)]
