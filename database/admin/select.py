from loader import cursor


def get_max_numb():
    cursor.execute("SELECT MAX(movie_number) FROM Movies")
    max_numb = cursor.fetchone()[0]
    if max_numb is None:
        return 1
    else:
        return max_numb
def get_all_bot_users():
    cursor.execute("SELECT id FROM Users")
    users = cursor.fetchall()
    return users
def get_all_movies(content_type=None):
    """Если content_type не задан - возвращает всё (для админ-панели).
    Если задан ('movie' или 'series') - только фильмы/сериалы этого типа (для пользователей)."""
    cursor.execute("PRAGMA table_info(Movies)")
    columns = {row[1] for row in cursor.fetchall()}
    if content_type and 'content_type' in columns:
        cursor.execute("SELECT * FROM Movies WHERE COALESCE(content_type,'movie')=?", (content_type,))
    else:
        cursor.execute("SELECT * FROM Movies")
    movies = cursor.fetchall()
    return movies


def get_movie_content_type(numb):
    cursor.execute("PRAGMA table_info(Movies)")
    columns = {row[1] for row in cursor.fetchall()}
    if 'content_type' not in columns:
        return 'movie'
    cursor.execute("SELECT content_type FROM Movies WHERE movie_number=?", (numb,))
    row = cursor.fetchone()
    return (row[0] if row and row[0] else 'movie')
def get_movie_title_by_numb(numb):
    cursor.execute("SELECT movie_title FROM Movies WHERE movie_number=?", (numb,))
    row = cursor.fetchone()
    return row[0] if row else None


def get_all_channels_title():
    cursor.execute("SELECT title FROM channels_info")
    channels_title = cursor.fetchall()
    return channels_title
def get_all_channels_cod():
    cursor.execute("SELECT cod FROM channels_info")
    channels_cod = cursor.fetchall()
    if channels_cod is None:
        return False
    else:
        return channels_cod
def get_all_channels_links():
    cursor.execute("SELECT link FROM channels_info")
    channels_link = cursor.fetchall()
    return channels_link
def get_all_channels_info():
    cursor.execute("SELECT * FROM channels_info")
    channels_info = cursor.fetchall()
    return channels_info