from database.connection import database, cursor
from database.categories import delete_categories_by_movie


def add_new_movie(
    title: str,
    poster_image: str = None,
    card_description: str = None,
    card_style: str = "simple",
    movie_file: str = None,
    movie_file_type: str = None,
    movie_trailer: str = None,
    content_type: str = "movie",
):
    """Добавляет новый фильм. Заполняет только те колонки карточки/файла,
    которые реально существуют в таблице Movies (на случай если миграция ещё не выполнена).
    Возвращает movie_number созданного фильма."""
    with database.transaction():
        database.execute("UPDATE MovieSequence SET value=value+1 WHERE id=1")
        movie_number = database.execute("SELECT value FROM MovieSequence WHERE id=1").fetchone()[0]
        database.execute(
            "INSERT INTO Movies (movie_title,movie_number,poster_image,card_description,card_style,movie_file,movie_file_type,movie_trailer,content_type) VALUES (?,?,?,?,?,?,?,?,?)",
            (
                title,
                movie_number,
                poster_image or "",
                card_description or "",
                card_style,
                movie_file or "",
                movie_file_type or "",
                movie_trailer or "",
                content_type,
            ),
        )
        return movie_number


def update_movie_title(numb, new_title):
    cursor.execute("UPDATE Movies SET movie_title=? WHERE movie_number=?", (new_title, numb))
    database.commit()


def update_movie_description(numb, new_description):
    cursor.execute("UPDATE Movies SET card_description=? WHERE movie_number=?", (new_description, numb))
    database.commit()


def update_movie_trailer(numb, new_trailer):
    cursor.execute("UPDATE Movies SET movie_trailer=? WHERE movie_number=?", (new_trailer or "", numb))
    database.commit()


def update_movie_content_type(numb, content_type):
    cursor.execute("UPDATE Movies SET content_type=? WHERE movie_number=?", (content_type, numb))
    database.commit()


def delete_movie_by_numb(numb):
    with database.transaction():
        delete_categories_by_movie(numb)
        database.execute("DELETE FROM MovieTorrents WHERE movie_number=?", (numb,))
        database.execute("DELETE FROM Movies WHERE movie_number=?", (numb,))
