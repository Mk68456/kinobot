import sqlite3
from loader import database, cursor


def check_users_schema():
    """Добавляет к таблице Users колонки username и joined_at, если их ещё нет."""
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='Users'")
    if not cursor.fetchone():
        return

    cursor.execute("PRAGMA table_info(Users)")
    columns = {row[1] for row in cursor.fetchall()}

    if 'username' not in columns:
        try:
            cursor.execute("ALTER TABLE Users ADD COLUMN username TEXT")
        except sqlite3.OperationalError as e:
            if "duplicate column name" not in str(e).lower():
                raise

    if 'joined_at' not in columns:
        try:
            cursor.execute("ALTER TABLE Users ADD COLUMN joined_at TEXT")
        except sqlite3.OperationalError as e:
            if "duplicate column name" not in str(e).lower():
                raise

    database.commit()
