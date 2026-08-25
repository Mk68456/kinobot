import logging
import sqlite3
from datetime import datetime, timedelta
from loader import database, cursor

logger = logging.getLogger(__name__)


def create_stats_tables():
    """Создаёт таблицы для логирования поисков и просмотров фильмов/сериалов."""
    cursor.execute('''CREATE TABLE IF NOT EXISTS SearchEvents (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        query TEXT,
        found_count INTEGER,
        created_at TEXT
    )''')
    cursor.execute('''CREATE TABLE IF NOT EXISTS WatchEvents (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        movie_number INTEGER,
        movie_title TEXT,
        created_at TEXT
    )''')
    database.commit()


def _safe_execute(sql, params):
    """Выполняет INSERT для логов статистики так, чтобы это НИКОГДА не могло уронить бота.
    Если нужной таблицы вдруг нет (например, миграции при старте не отработали на этом
    хостинге) - создаём таблицы на лету и повторяем один раз. Любая другая ошибка просто
    логируется и проглатывается: статистика - вспомогательная функция, а не критичная."""
    try:
        cursor.execute(sql, params)
        database.commit()
    except sqlite3.OperationalError as e:
        if 'no such table' in str(e).lower():
            try:
                create_stats_tables()
                cursor.execute(sql, params)
                database.commit()
            except Exception:
                logger.exception("Не удалось записать событие статистики даже после создания таблиц")
        else:
            logger.exception("Ошибка записи статистики")
    except Exception:
        logger.exception("Ошибка записи статистики")


def log_search(user_id: int, query: str, found_count: int):
    _safe_execute("INSERT INTO SearchEvents (user_id, query, found_count, created_at) VALUES (?,?,?,?)",
                 (user_id, query, found_count, datetime.utcnow().isoformat()))


def log_watch(user_id: int, movie_number, movie_title: str):
    _safe_execute("INSERT INTO WatchEvents (user_id, movie_number, movie_title, created_at) VALUES (?,?,?,?)",
                 (user_id, movie_number, movie_title, datetime.utcnow().isoformat()))


def _since(days: int):
    return (datetime.utcnow() - timedelta(days=days)).isoformat()


def get_searches_count(days: int = 7):
    cursor.execute("SELECT COUNT(*) FROM SearchEvents WHERE created_at>=?", (_since(days),))
    return cursor.fetchone()[0]


def get_watches_count(days: int = 7):
    cursor.execute("SELECT COUNT(*) FROM WatchEvents WHERE created_at>=?", (_since(days),))
    return cursor.fetchone()[0]


def get_new_users_count(days: int = 7):
    cursor.execute("PRAGMA table_info(Users)")
    columns = {row[1] for row in cursor.fetchall()}
    if 'joined_at' not in columns:
        return 0
    cursor.execute("SELECT COUNT(*) FROM Users WHERE joined_at>=?", (_since(days),))
    return cursor.fetchone()[0]


def get_top_watched_movies(days: int = 7, limit: int = 10):
    cursor.execute('''SELECT movie_title, movie_number, COUNT(*) as cnt FROM WatchEvents
                       WHERE created_at>=? GROUP BY movie_number ORDER BY cnt DESC LIMIT ?''',
                   (_since(days), limit))
    return cursor.fetchall()


def get_top_searches(days: int = 7, limit: int = 15):
    cursor.execute('''SELECT query, COUNT(*) as cnt FROM SearchEvents
                       WHERE created_at>=? GROUP BY LOWER(query) ORDER BY cnt DESC LIMIT ?''',
                   (_since(days), limit))
    return cursor.fetchall()


def get_users_list(limit: int = 30, offset: int = 0):
    cursor.execute("PRAGMA table_info(Users)")
    columns = {row[1] for row in cursor.fetchall()}
    if 'username' in columns and 'joined_at' in columns:
        cursor.execute("SELECT id, username, joined_at FROM Users ORDER BY rowid DESC LIMIT ? OFFSET ?",
                       (limit, offset))
    else:
        cursor.execute("SELECT id, NULL, NULL FROM Users ORDER BY rowid DESC LIMIT ? OFFSET ?", (limit, offset))
    return cursor.fetchall()


def get_users_total_count():
    cursor.execute("SELECT COUNT(*) FROM Users")
    return cursor.fetchone()[0]


def get_user_watch_history(user_id: int, limit: int = 20):
    cursor.execute('''SELECT movie_title, movie_number, created_at FROM WatchEvents
                       WHERE user_id=? ORDER BY created_at DESC LIMIT ?''', (user_id, limit))
    return cursor.fetchall()


def get_user_search_history(user_id: int, limit: int = 20):
    cursor.execute('''SELECT query, created_at FROM SearchEvents
                       WHERE user_id=? ORDER BY created_at DESC LIMIT ?''', (user_id, limit))
    return cursor.fetchall()
