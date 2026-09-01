import sqlite3
from loader import database, cursor


def _add_column(name, definition):
    try:
        cursor.execute(f'ALTER TABLE Users ADD COLUMN {name} {definition}')
    except sqlite3.OperationalError as e:
        if 'duplicate column name' not in str(e).lower():
            raise


def check_users_schema():
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='Users'")
    if not cursor.fetchone():
        cursor.execute('''CREATE TABLE Users (
            id INTEGER PRIMARY KEY,
            username TEXT,
            first_name TEXT,
            last_name TEXT,
            joined_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            last_active_at TEXT
        )''')
        database.commit()
        return

    _add_column('username', 'TEXT')
    _add_column('first_name', 'TEXT')
    _add_column('last_name', 'TEXT')
    _add_column('joined_at', "TEXT")
    _add_column('last_active_at', "TEXT")
    cursor.execute("UPDATE Users SET joined_at=CURRENT_TIMESTAMP WHERE joined_at IS NULL")
    database.commit()
