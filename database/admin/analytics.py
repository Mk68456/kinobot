from loader import database, cursor


def create_analytics_tables():
    """Create analytics tables and indexes used by the admin statistics."""
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS UserEvents (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            event_type TEXT NOT NULL,
            movie_number INTEGER,
            search_query TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_user_events_created_at ON UserEvents(created_at)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_user_events_user_id ON UserEvents(user_id)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_user_events_type ON UserEvents(event_type)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_user_events_movie ON UserEvents(movie_number)')
    database.commit()


def log_event(user_id, event_type, movie_number=None, search_query=None):
    if not user_id:
        return
    cursor.execute(
        '''INSERT INTO UserEvents (user_id, event_type, movie_number, search_query)
           VALUES (?, ?, ?, ?)''',
        (int(user_id), event_type, movie_number, search_query)
    )
    # Commit each event so statistics survive a bot restart immediately.
    database.commit()


def get_overall_stats(days=7):
    params = (f'-{int(days)} days',)
    def count(event_type=None):
        if event_type:
            cursor.execute(
                "SELECT COUNT(*) FROM UserEvents WHERE created_at >= datetime('now', ?) AND event_type=?",
                params + (event_type,)
            )
        else:
            cursor.execute("SELECT COUNT(*) FROM UserEvents WHERE created_at >= datetime('now', ?)", params)
        return cursor.fetchone()[0]

    cursor.execute(
        "SELECT COUNT(DISTINCT user_id) FROM UserEvents WHERE created_at >= datetime('now', ?)", params
    )
    active_users = cursor.fetchone()[0]
    return {
        'active_users': active_users,
        'searches': count('search'),
        'movie_opens': count('movie_open'),
        'downloads': count('movie_download'),
        'subcategory_downloads': count('subcategory_download'),
    }


def get_top_movies(days=7, limit=10):
    cursor.execute('''
        SELECT e.movie_number, COALESCE(m.movie_title, 'Удалённый фильм'),
               SUM(CASE WHEN e.event_type='movie_open' THEN 1 ELSE 0 END) AS opens,
               SUM(CASE WHEN e.event_type IN ('movie_download', 'subcategory_download') THEN 1 ELSE 0 END) AS downloads
        FROM UserEvents e
        LEFT JOIN Movies m ON m.movie_number=e.movie_number
        WHERE e.created_at >= datetime('now', ?)
          AND e.movie_number IS NOT NULL
        GROUP BY e.movie_number
        ORDER BY opens DESC, downloads DESC
        LIMIT ?
    ''', (f'-{int(days)} days', int(limit)))
    return cursor.fetchall()


def get_users_page(page=0, per_page=8):
    offset = max(0, int(page)) * per_page
    cursor.execute('SELECT COUNT(*) FROM Users')
    total = cursor.fetchone()[0]
    cursor.execute('''
        SELECT id, username, first_name, last_name, joined_at, last_active_at
        FROM Users
        ORDER BY COALESCE(last_active_at, joined_at) DESC, id DESC
        LIMIT ? OFFSET ?
    ''', (per_page, offset))
    return total, cursor.fetchall()


def get_user_stats(user_id, days=30):
    cursor.execute('''
        SELECT id, username, first_name, last_name, joined_at, last_active_at
        FROM Users WHERE id=?
    ''', (int(user_id),))
    user = cursor.fetchone()
    if not user:
        return None

    cursor.execute('''
        SELECT event_type, COUNT(*)
        FROM UserEvents
        WHERE user_id=? AND created_at >= datetime('now', ?)
        GROUP BY event_type
    ''', (int(user_id), f'-{int(days)} days'))
    counters = dict(cursor.fetchall())

    cursor.execute('''
        SELECT e.event_type, e.movie_number, COALESCE(m.movie_title, 'Удалённый фильм'),
               e.search_query, e.created_at
        FROM UserEvents e
        LEFT JOIN Movies m ON m.movie_number=e.movie_number
        WHERE e.user_id=?
        ORDER BY e.id DESC
        LIMIT 30
    ''', (int(user_id),))
    events = cursor.fetchall()
    return user, counters, events
