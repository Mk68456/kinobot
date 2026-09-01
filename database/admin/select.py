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
def get_all_movies():
    cursor.execute("SELECT * FROM Movies")
    movies = cursor.fetchall()
    return movies
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