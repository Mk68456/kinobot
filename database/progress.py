"""Personal completion marks. Opening or downloading a file never marks it watched."""

from database.connection import database


def episode_marks(user_id, category_id):
    return {
        row[0]
        for row in database.execute(
            """SELECT p.episode_id FROM EpisodeProgress p
        JOIN MovieSubcategories s ON s.id=p.episode_id WHERE p.user_id=? AND s.category_id=?""",
            (user_id, category_id),
        )
    }


def season_progress(user_id, movie_number):
    return {
        cid: (watched, total)
        for cid, watched, total in database.execute(
            """
        SELECT c.id,COUNT(p.episode_id),COUNT(s.id) FROM MovieCategories c
        LEFT JOIN MovieSubcategories s ON s.category_id=c.id
        LEFT JOIN EpisodeProgress p ON p.episode_id=s.id AND p.user_id=?
        WHERE c.movie_number=? GROUP BY c.id""",
            (user_id, movie_number),
        )
    }


def is_watched(user_id, movie_number, content_type):
    if content_type == "series":
        seasons = season_progress(user_id, movie_number)
        total = sum(t for w, t in seasons.values())
        return bool(total and sum(w for w, t in seasons.values()) == total)
    return bool(
        database.execute(
            "SELECT 1 FROM FilmProgress WHERE user_id=? AND movie_number=?", (user_id, movie_number)
        ).fetchone()
    )


def set_watched(user_id, kind, item_id, enabled):
    """Set the desired state, making duplicate clicks idempotent. Return movie/category IDs."""
    with database.transaction():
        if kind == "f":
            movie = database.execute(
                "SELECT movie_number FROM Movies WHERE movie_number=? AND COALESCE(content_type,'movie')='movie'",
                (item_id,),
            ).fetchone()
            if not movie:
                raise ValueError("Фильм больше недоступен.")
            if enabled:
                database.execute(
                    "INSERT OR IGNORE INTO FilmProgress(user_id,movie_number) VALUES (?,?)",
                    (user_id, item_id),
                )
            else:
                database.execute(
                    "DELETE FROM FilmProgress WHERE user_id=? AND movie_number=?", (user_id, item_id)
                )
            return item_id, None
        if kind == "e":
            row = database.execute(
                """SELECT c.movie_number,c.id FROM MovieSubcategories s
                JOIN MovieCategories c ON c.id=s.category_id JOIN Movies m ON m.movie_number=c.movie_number
                WHERE s.id=? AND m.content_type='series' """,
                (item_id,),
            ).fetchone()
        elif kind == "s":
            row = database.execute(
                """SELECT c.movie_number,c.id FROM MovieCategories c
                JOIN Movies m ON m.movie_number=c.movie_number WHERE c.id=? AND m.content_type='series' """,
                (item_id,),
            ).fetchone()
        else:
            raise ValueError("Неизвестный тип отметки.")
        if not row:
            raise ValueError("Серия или сезон больше недоступны.")
        episodes = (
            [item_id]
            if kind == "e"
            else [
                r[0]
                for r in database.execute("SELECT id FROM MovieSubcategories WHERE category_id=?", (item_id,))
            ]
        )
        if not episodes:
            raise ValueError("В этом сезоне пока нет серий.")
        for episode_id in episodes:
            if enabled:
                database.execute(
                    "INSERT OR IGNORE INTO EpisodeProgress(user_id,episode_id) VALUES (?,?)",
                    (user_id, episode_id),
                )
            else:
                database.execute(
                    "DELETE FROM EpisodeProgress WHERE user_id=? AND episode_id=?", (user_id, episode_id)
                )
        return row


def totals(user_id=None):
    where = " WHERE p.user_id=?" if user_id is not None else ""
    params = (user_id,) if user_id is not None else ()
    episodes = database.execute(
        """SELECT COUNT(*) FROM EpisodeProgress p
        JOIN MovieSubcategories s ON s.id=p.episode_id JOIN MovieCategories c ON c.id=s.category_id
        JOIN Movies m ON m.movie_number=c.movie_number AND m.content_type='series' """
        + where,
        params,
    ).fetchone()[0]
    films = database.execute(
        """SELECT COUNT(*) FROM FilmProgress p JOIN Movies m ON m.movie_number=p.movie_number
        AND COALESCE(m.content_type,'movie')='movie' """
        + where,
        params,
    ).fetchone()[0]
    return films, episodes


def series_stats(page=0, limit=10, user_id=None):
    params = []
    user_filter = ""
    if user_id is not None:
        user_filter = " AND p.user_id=?"
        params.append(user_id)
    params.extend([limit + 1, max(0, page) * limit])
    return database.execute(
        """SELECT m.movie_number,m.movie_title,COUNT(DISTINCT s.id),
        COUNT(p.episode_id),COUNT(DISTINCT p.user_id) FROM Movies m
        LEFT JOIN MovieCategories c ON c.movie_number=m.movie_number
        LEFT JOIN MovieSubcategories s ON s.category_id=c.id
        LEFT JOIN EpisodeProgress p ON p.episode_id=s.id """
        + user_filter
        + """
        WHERE m.content_type='series' GROUP BY m.movie_number ORDER BY m.movie_number LIMIT ? OFFSET ?""",
        params,
    ).fetchall()


def user_episodes(user_id, movie_number, page=0, limit=10):
    return database.execute(
        """SELECT c.name,s.name,p.watched_at FROM EpisodeProgress p
        JOIN MovieSubcategories s ON s.id=p.episode_id JOIN MovieCategories c ON c.id=s.category_id
        JOIN Movies m ON m.movie_number=c.movie_number
        WHERE p.user_id=? AND c.movie_number=? AND m.content_type='series'
        ORDER BY c.season_number IS NULL,c.season_number,c.id,s.id LIMIT ? OFFSET ?""",
        (user_id, movie_number, limit + 1, max(0, page) * limit),
    ).fetchall()


def user_films(user_id, page=0, limit=10):
    return database.execute(
        """SELECT m.movie_number,m.movie_title,p.watched_at FROM FilmProgress p
        JOIN Movies m ON m.movie_number=p.movie_number WHERE p.user_id=? AND COALESCE(m.content_type,'movie')='movie'
        ORDER BY p.watched_at DESC,m.movie_number LIMIT ? OFFSET ?""",
        (user_id, limit + 1, max(0, page) * limit),
    ).fetchall()
