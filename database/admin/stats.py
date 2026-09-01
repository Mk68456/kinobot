from datetime import datetime, timedelta
from loader import database, cursor


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


def log_search(user_id: int, query: str, found_count: int):
    cursor.execute("INSERT INTO SearchEvents (user_id, query, found_count, created_at) VALUES (?,?,?,?)",
                   (user_id, query, found_count, datetime.utcnow().isoformat()))
    database.commit()


def log_watch(user_id: int, movie_number, movie_title: str):
    cursor.execute("INSERT INTO WatchEvents (user_id, movie_number, movie_title, created_at) VALUES (?,?,?,?)",
                   (user_id, movie_number, movie_title, datetime.utcnow().isoformat()))
    database.commit()


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
