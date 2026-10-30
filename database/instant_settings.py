"""Persistent administrator override; absent override preserves environment settings."""

from database.connection import database


def mode():
    if not database.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='InstantSettings'").fetchone():
        return 'env'
    row = database.execute('SELECT mode FROM InstantSettings WHERE id=1').fetchone()
    return row[0] if row else 'env'


def set_mode(value, admin_id):
    if value not in {'env', 'pilot', 'public', 'off'}:
        raise ValueError('Неизвестный режим.')
    with database.transaction():
        database.execute('CREATE TABLE IF NOT EXISTS InstantSettings ('
                         'id INTEGER PRIMARY KEY CHECK(id=1), mode TEXT NOT NULL, admin_id INTEGER NOT NULL)')
        database.execute('INSERT INTO InstantSettings(id,mode,admin_id) VALUES (1,?,?) '
                         'ON CONFLICT(id) DO UPDATE SET mode=excluded.mode,admin_id=excluded.admin_id',
                         (value, admin_id))


def effective():
    from data import settings
    value = mode()
    if value == 'pilot':
        return True, False
    if value == 'public':
        return True, True
    if value == 'off':
        return False, False
    return settings.INSTANT_ENABLED, settings.INSTANT_PUBLIC
