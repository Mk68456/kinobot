from loader import database, cursor


def create_torrents_table():
    cursor.execute('''CREATE TABLE IF NOT EXISTS MovieTorrents (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        movie_number INTEGER,
        name TEXT,
        file_id TEXT
    )''')
    database.commit()


def add_movie_torrent(movie_number, name, file_id):
    cursor.execute("INSERT INTO MovieTorrents (movie_number, name, file_id) VALUES (?,?,?)",
                   (movie_number, name, file_id))
    database.commit()
    return cursor.lastrowid


def get_torrents_by_movie(movie_number):
    cursor.execute("SELECT id, name FROM MovieTorrents WHERE movie_number=?", (movie_number,))
    return cursor.fetchall()


def get_torrent_by_id(torrent_id):
    cursor.execute("SELECT id, movie_number, name, file_id FROM MovieTorrents WHERE id=?", (torrent_id,))
    return cursor.fetchone()


def delete_torrent(torrent_id):
    cursor.execute("DELETE FROM MovieTorrents WHERE id=?", (torrent_id,))
    database.commit()
