"""Reclaim only indexed launcher artifacts, never arbitrary files or sources."""
import contextlib
import errno
from pathlib import Path
import shutil
import sqlite3
import stat
import threading
import time

try:
    from .launcher_runtime import state_path
except ImportError:
    from launcher_runtime import state_path

GIB = 1024 ** 3
_enabled, _reserve = True, 2 * GIB
_lock = threading.RLock()
_sessions = threading.local()
_held = {}
_reservations = {}


def reserved(destination):
    """Bytes promised to other active workers on this volume."""
    device = Path(destination).stat().st_dev
    owner = threading.get_ident()
    with _lock:
        return sum(size for dev, thread, size in _reservations.values() if dev == device and thread != owner)


@contextlib.contextmanager
def reservation(destination, expected, stop, report, protected=()):
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    expected = max(0, int(expected))
    token = object()
    while True:
        ensure(destination, expected, stop, report, protected)
        with _lock:
            if shutil.disk_usage(destination).free >= _reserve + expected + reserved(destination):
                _reservations[token] = (destination.stat().st_dev, threading.get_ident(), expected)
                break
    try:
        yield
    finally:
        with _lock:
            _reservations.pop(token, None)


@contextlib.contextmanager
def session():
    outer = getattr(_sessions, 'roots', None)
    if outer is not None:
        yield
        return
    _sessions.roots = set()
    try:
        yield
    finally:
        with _lock:
            for root in _sessions.roots:
                _held[root] -= 1
                if not _held[root]:
                    del _held[root]
        del _sessions.roots


def protect(root):
    roots = getattr(_sessions, 'roots', None)
    root = Path(root).absolute()
    if roots is not None and root not in roots:
        with _lock:
            roots.add(root)
            _held[root] = _held.get(root, 0) + 1


def configure(enabled=True, reserve_gib=2):
    global _enabled, _reserve
    if type(enabled) is not bool or type(reserve_gib) not in (int, float) or not 0.25 <= reserve_gib <= 100:
        raise ValueError('Резерв диска должен быть от 0.25 до 100 ГиБ.')
    _enabled, _reserve = enabled, int(reserve_gib * GIB)


def settings():
    return {'disk_cleanup': _enabled, 'disk_reserve_gib': _reserve / GIB}


@contextlib.contextmanager
def index():
    with _lock, contextlib.closing(sqlite3.connect(state_path('cache-index.sqlite', __file__), timeout=10)) as db:
        db.execute('''CREATE TABLE IF NOT EXISTS artifacts (
            path TEXT PRIMARY KEY, root TEXT NOT NULL, size INTEGER, mtime INTEGER,
            device INTEGER, inode INTEGER, kind TEXT, group_id TEXT, confirmed INTEGER DEFAULT 0,
            created REAL)''')
        with db:
            yield db


def safe_path(path, root):
    path, root = Path(path).absolute(), Path(root).absolute()
    if root.name not in ('KinoTime-Ready', 'KinoTime-Prepared') or root not in path.parents:
        return False
    # Reject symlinks, junctions and mount-point reparse redirects anywhere in the path.
    for part in (path, *path.parents):
        if part.exists():
            info = part.lstat()
            if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
                return False
    return path.resolve() == path and root.resolve() == root


def register(path, *, kind='output', alias=None):
    path = Path(path).absolute()
    root = next((p for p in path.parents if p.name in ('KinoTime-Ready', 'KinoTime-Prepared')), None)
    if root is None or kind not in ('output', 'intermediate') or not safe_path(path, root) or not path.is_file():
        return
    info = path.stat()
    with index() as db:
        old = db.execute('SELECT size,mtime,device,inode FROM artifacts WHERE path=?', (str(path),)).fetchone()
        identity = (info.st_size, info.st_mtime_ns, info.st_dev, info.st_ino)
        if old == identity:
            return
        group = str(path)
        if alias is not None:
            row = db.execute('SELECT group_id FROM artifacts WHERE path=?', (str(Path(alias).absolute()),)).fetchone()
            if row:
                group = row[0]
        db.execute('INSERT OR REPLACE INTO artifacts VALUES (?,?,?,?,?,?,?,?,0,?)',
                   (str(path), str(root), *identity, kind, group, time.time()))


def confirm(path, metadata):
    # A server receipt must match a file previously generated and indexed by this launcher.
    path = Path(path).absolute()
    root = next((p for p in path.parents if p.name in ('KinoTime-Ready', 'KinoTime-Prepared')), None)
    if root is None or not safe_path(path, root):
        return
    with index() as db:
        row = db.execute('SELECT group_id,size,mtime FROM artifacts WHERE path=?',
                         (str(Path(path).absolute()),)).fetchone()
        if row and row[1] == metadata.get('size') and str(row[2]) == str(metadata.get('mtime')):
            db.execute('UPDATE artifacts SET confirmed=1 WHERE group_id=?', (row[0],))


def reclaim(destination, required, protected=()):
    if not _enabled:
        return 0
    destination = Path(destination)
    protected = {str(Path(p).absolute()) for p in protected}
    device = destination.stat().st_dev
    removed = 0
    with index() as db:
        rows = db.execute('''SELECT path,root,size,mtime,device,inode FROM artifacts
            WHERE confirmed=1 OR kind='intermediate' ORDER BY created''').fetchall()
        for name, root, size, mtime, dev, inode in rows:
            if shutil.disk_usage(destination).free >= required:
                break
            if (dev != device or name in protected or
                    any(held == Path(name) or held in Path(name).parents for held in _held) or
                    not safe_path(name, root)):
                continue
            path = Path(name)
            try:
                info = path.stat()
                if (info.st_size, info.st_mtime_ns, info.st_dev, info.st_ino) != (size, mtime, dev, inode):
                    continue
                path.unlink()
                removed += size
                db.execute('DELETE FROM artifacts WHERE path=?', (name,))
            except FileNotFoundError:
                db.execute('DELETE FROM artifacts WHERE path=?', (name,))
            except OSError:
                # Locked Telegram files, permissions and antivirus must not stop the queue.
                continue
    return removed


def ensure(destination, expected, stop, report, protected=()):
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    last_notice = -float('inf')
    while True:
        required = _reserve + max(0, int(expected)) + reserved(destination)
        if shutil.disk_usage(destination).free >= required:
            return
        if stop.is_set():
            raise InterruptedError('Подготовка остановлена во время ожидания места на диске.')
        try:
            removed = reclaim(destination, required, protected)
        except (sqlite3.Error, OSError):
            removed = 0  # A full/locked index is never a reason to delete untracked files.
        if removed:
            report(f'Очистка кеша: удалены разрешённые копии, {removed / GIB:.2f} ГиБ файлов. Исходники сохранены.')
        free = shutil.disk_usage(destination).free
        if free >= required:
            report('Место освобождено. Продолжаю подготовку автоматически.')
            return
        if time.monotonic() - last_notice >= 30:
            report(f'Ожидание места на диске: свободно {free / GIB:.2f} ГиБ, нужно около '
                   f'{required / GIB:.2f} ГиБ с резервом. Освободите место; продолжение автоматическое.')
            last_notice = time.monotonic()
        if stop.wait(5):
            raise InterruptedError('Подготовка остановлена во время ожидания места на диске.')


def discard_intermediate(path):
    if not _enabled:
        return
    register(path, kind='intermediate')
    path = Path(path).absolute()
    with index() as db:
        row = db.execute('SELECT root,size,mtime,device,inode FROM artifacts WHERE path=? AND kind=?',
                         (str(path), 'intermediate')).fetchone()
        try:
            if row and safe_path(path, row[0]):
                info = path.stat()
                if (info.st_size, info.st_mtime_ns, info.st_dev, info.st_ino) == row[1:]:
                    path.unlink()
                    db.execute('DELETE FROM artifacts WHERE path=?', (str(path),))
        except OSError:
            pass


def release_confirmed(path):
    """Explicit streaming cleanup of this receipt's exact output copies only."""
    removed = 0
    with index() as db:
        group = db.execute('SELECT group_id FROM artifacts WHERE path=? AND confirmed=1 AND kind=?',
                           (str(Path(path).absolute()), 'output')).fetchone()
        if not group:
            return 0
        rows = db.execute('''SELECT path,root,size,mtime,device,inode FROM artifacts
            WHERE group_id=? AND confirmed=1 AND kind='output' ''', group).fetchall()
        for name, root, size, mtime, dev, inode in rows:
            candidate = Path(name)
            if not safe_path(candidate, root):
                continue
            try:
                info = candidate.stat()
                if (info.st_size, info.st_mtime_ns, info.st_dev, info.st_ino) != (size, mtime, dev, inode):
                    continue
                candidate.unlink()
                removed += size
                db.execute('DELETE FROM artifacts WHERE path=?', (name,))
            except FileNotFoundError:
                db.execute('DELETE FROM artifacts WHERE path=?', (name,))
            except OSError:
                continue
    return removed


def low(destination):
    return shutil.disk_usage(destination).free < _reserve


def copy(source, target, stop, report):
    while True:
        try:
            with reservation(Path(target).parent, Path(source).stat().st_size, stop, report, [source]):
                shutil.copy2(source, target)
            return
        except OSError as error:
            if error.errno != errno.ENOSPC and getattr(error, 'winerror', None) not in (39, 112):
                raise
            Path(target).unlink(missing_ok=True)
            report('Место закончилось при копировании. Ожидаю освобождения диска и повторю копирование.')
            if stop.wait(1):
                raise InterruptedError('Копирование остановлено.') from error
