"""Read-only remote folder navigation and honest render progress snapshots."""
import copy
import ctypes
from datetime import datetime, timezone
import os
from pathlib import Path
import re
import threading
import uuid

PAGE_SIZE = 100


def existing_folder(value, allowed=(), enabled=True):
    if not isinstance(value, str) or not value.strip() or len(value) > 4000:
        raise ValueError('Укажите полный путь к папке на выбранном компьютере.')
    path = Path(value)
    if not path.is_absolute():
        raise ValueError('Нужен полный путь к папке, например D:\\Сериалы.')
    path = path.resolve(strict=True)
    if not path.is_dir():
        raise ValueError('Папка не найдена на выбранном компьютере.')
    if not enabled and path not in {Path(p).resolve() for p in allowed}:
        raise ValueError('Выбор любых папок через веб выключен в настройках лаунчера.')
    return str(path)


def drive_roots():
    if os.name != 'nt':
        return [{'name': '/', 'path': '/'}]
    mask = ctypes.windll.kernel32.GetLogicalDrives()
    # Avoid probing drives: removable and network drives can block on I/O.
    return [{'name': f'{chr(65+i)}:', 'path': f'{chr(65+i)}:\\'}
            for i in range(26) if mask & (1 << i)]


def browse_folder(folder='', offset=0, *, enabled=True):
    if not enabled:
        raise ValueError('Выбор любых папок через веб выключен в настройках лаунчера.')
    if type(offset) is not int or not 0 <= offset <= 100000:
        raise ValueError('Некорректная страница папок.')
    if folder == '':
        return {'folder': '', 'parent': None, 'entries': drive_roots(),
                'offset': 0, 'next_offset': None}
    root = Path(existing_folder(folder))
    children = []
    with os.scandir(root) as entries:
        for entry in entries:
            try:
                if entry.is_dir():
                    children.append({'name': entry.name, 'path': str(root / entry.name)})
            except OSError:
                continue
    children.sort(key=lambda entry: (entry['name'].casefold(), entry['name']))
    end = offset + PAGE_SIZE
    return {'folder': str(root), 'parent': str(root.parent) if root.parent != root else '',
            'entries': children[offset:end], 'offset': offset,
            'next_offset': end if len(children) > end else None}


class JobState:
    """Command acknowledgement means started; completion comes from the worker."""
    def __init__(self):
        self._lock = threading.RLock()
        self._job = {}
        self._items = []

    def start(self, kind, folder, mode):
        with self._lock:
            self._items = []
            self._job = {'id': uuid.uuid4().hex, 'kind': kind, 'folder': str(folder),
                         'mode': mode, 'state': 'running', 'message': 'Запускаю задачу…',
                         'started_at': datetime.now(timezone.utc).isoformat(),
                         'finished_at': None, 'ready': 0, 'total': 0, 'current': '', 'percent': None}

    def report(self, text):
        with self._lock:
            if self._job.get('state') == 'running':
                self._job['message'] = str(text)[:2000]

    def queue(self, items):
        with self._lock:
            if self._job.get('state') != 'running':
                return
            self._items = copy.deepcopy(items)
            variants = [v for item in items for v in item.get('variants', [])]
            self._job['total'] = len(variants)
            ready_states = ('Принят',) if self._job['kind'] == 'send' else ('Подготовлен', 'Принят')
            self._job['ready'] = sum(v.get('status') in ready_states for v in variants)
            current = next((v for v in variants if re.search(r'\d+%', v.get('status', ''))), None)
            if current:
                self._job['current'] = str(current.get('name', ''))[:1000]
                self._job['percent'] = int(re.search(r'(\d+)%', current['status'])[1])
            else:
                self._job.update(current='', percent=None)

    def finish(self, error=None, stopped=False):
        with self._lock:
            if not self._job:
                return
            allowed = ('complete', 'published') if self._job['kind'] == 'send' else ('prepared', 'complete', 'published')
            failed = next((item for item in self._items if item.get('status') not in allowed), None)
            if error or failed or stopped:
                paused = stopped or isinstance(error, InterruptedError)
                reason = str(error or (failed or {}).get('error') or
                             ('Остановка запрошена.' if stopped else 'Не все файлы подготовлены. Проверьте очередь.'))
                self._job.update(state='paused' if paused else 'failed', message=reason[:2000])
            else:
                self._job['state'] = 'completed'
                if self._job['kind'] == 'prepare':
                    self._job['message'] = 'MP4 подготовлены. Оформите карточки в веб-панели; отправка запускается отдельно.'
                else:
                    self._job['message'] = 'Передача завершена. Проверьте карточки в веб-панели.'
            self._job.update(finished_at=datetime.now(timezone.utc).isoformat(), current='', percent=None)

    def snapshot(self):
        with self._lock:
            return copy.deepcopy(self._job)
