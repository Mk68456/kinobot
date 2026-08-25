from loader import database,cursor
from .select import get_max_numb
from .categories import delete_categories_by_movie


def add_new_movie(title: str, poster_image: str = None, card_description: str = None, card_style: str = 'simple',
                  movie_file: str = None, movie_file_type: str = None, movie_trailer: str = None,
                  content_type: str = 'movie'):
    """Добавляет новый фильм. Заполняет только те колонки карточки/файла,
    которые реально существуют в таблице Movies (на случай если миграция ещё не выполнена).
    Возвращает movie_number созданного фильма."""
    max_numb = get_max_numb()
    max_numb += int(1)

    cursor.execute("PRAGMA table_info(Movies)")
    columns = {row[1] for row in cursor.fetchall()}

    fields = ['movie_title', 'movie_number']
    values = [title, max_numb]
    optional = [
        ('poster_image', poster_image or ''),
        ('card_description', card_description or ''),
        ('card_style', card_style or 'simple'),
        ('movie_file', movie_file or ''),
        ('movie_file_type', movie_file_type or ''),
        ('movie_trailer', movie_trailer or ''),
        ('content_type', content_type or 'movie'),
    ]
    for column, value in optional:
        if column in columns:
            fields.append(column)
            values.append(value)

    placeholders = ','.join(['?'] * len(fields))
    cursor.execute(f"INSERT INTO Movies ({','.join(fields)}) VALUES({placeholders})", values)
    database.commit()
    return max_numb


def update_movie_title(numb, new_title):
    cursor.execute("UPDATE Movies SET movie_title=? WHERE movie_number=?", (new_title, numb))
    database.commit()


def update_movie_description(numb, new_description):
    cursor.execute("UPDATE Movies SET card_description=? WHERE movie_number=?", (new_description, numb))
    database.commit()


def update_movie_trailer(numb, new_trailer):
    cursor.execute("UPDATE Movies SET movie_trailer=? WHERE movie_number=?", (new_trailer or '', numb))
    database.commit()


def delete_movie_by_numb(numb):
    delete_categories_by_movie(numb)
    cursor.execute("DELETE FROM Movies WHERE movie_number=?", (numb,))
    database.commit()