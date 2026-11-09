"""Shared scheduling and restriction state for the single provider conversation."""
import math
import time
from database.connection import database


def setup():
    database.execute('CREATE TABLE IF NOT EXISTS ProviderQueueControl (id INTEGER PRIMARY KEY, until REAL NOT NULL DEFAULT 0, manual INTEGER NOT NULL DEFAULT 0, generation INTEGER NOT NULL DEFAULT 0)')
    database.execute('INSERT OR IGNORE INTO ProviderQueueControl(id) VALUES (1)')
    database.commit()


def control():
    setup()
    row = database.execute('SELECT until,manual,generation FROM ProviderQueueControl WHERE id=1').fetchone()
    return dict(until=row[0], manual=bool(row[1]), generation=row[2])


def report(until, manual, generation):
    setup()
    if type(until) not in (int, float) or not math.isfinite(until) or until < 0 or type(manual) is not bool or type(generation) is not int:
        raise ValueError('Некорректное состояние очереди.')
    with database.transaction():
        current = control()
        database.execute('UPDATE ProviderQueueControl SET until=MAX(until,?),manual=? WHERE id=1',
                         (until, current['manual'] or (manual and generation == current['generation'])))
    return control()


def resume():
    setup()
    # Owner acknowledgement never overrides a Telegram FloodWait deadline.
    database.execute('UPDATE ProviderQueueControl SET manual=0,generation=generation+1 WHERE id=1')
    database.commit()
    return control()


def paused():
    value = control()
    return value['manual'] or value['until'] > time.time()


def exists(table):
    return bool(database.execute('SELECT 1 FROM sqlite_master WHERE name=?', (table,)).fetchone())


def waiting():
    """Normal requests FIFO; bulk work retains its lower priority."""
    entries = []
    if exists('ProviderOptions'):
        entries += [(0, stamp, key, 'options') for key, stamp in database.execute(
            "SELECT id,updated FROM ProviderOptions WHERE status='queued'").fetchall()]
    if exists('InstantJobs'):
        batch = "id IN (SELECT job_id FROM ProviderBatchItems)" if exists('ProviderBatchItems') else '0'
        entries += [(int(bulk), stamp, key, 'job') for key, stamp, bulk in database.execute(
            "SELECT id,created_at," + batch + " FROM InstantJobs WHERE status='queued'").fetchall()]
    return sorted(entries)


def turn(kind, key):
    entries = waiting()
    return not paused() and bool(entries) and entries[0][2:] == (key, kind)


def status(kind, key):
    value = control()
    if value['manual']:
        return 'Очередь на паузе: поставщик требует проверки владельцем. Запрос сохранён.'
    if value['until'] > time.time():
        minutes = max(1, math.ceil((value['until'] - time.time()) / 60))
        return f'Очередь на паузе из-за ограничения Telegram: около {minutes} мин. Запрос сохранён.'
    entries = waiting()
    for index, entry in enumerate(entries):
        if entry[2:] == (key, kind):
            return f'Место в очереди: {index + 1}. Обработка по одному запросу.'
    return 'Проверяю варианты у источника…' if kind == 'options' else 'Ищу и подготавливаю файл…'
