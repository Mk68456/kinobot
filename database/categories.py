import sqlite3
import re
from database.connection import database, cursor


def create_categories_tables():
    """Создаёт таблицы категорий (озвучки/сезоны) и подкатегорий (качества/серии) фильмов, если их ещё нет."""
    cursor.execute("""CREATE TABLE IF NOT EXISTS MovieCategories (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        movie_number INTEGER,
        name TEXT
    )""")
    cursor.execute("""CREATE TABLE IF NOT EXISTS MovieSubcategories (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        category_id INTEGER,
        name TEXT,
        file_id TEXT,
        file_type TEXT
    )""")
    # Номер сезона - используется только для сериалов (у фильмов остаётся NULL).
    # Категория для сериала - это, по сути, сезон.
    try:
        cursor.execute("""ALTER TABLE MovieCategories ADD COLUMN season_number INTEGER""")
    except sqlite3.OperationalError as e:
        if "duplicate column name" not in str(e).lower():
            raise
    # Для старых баз, где сериалы были заведены до появления content_type,
    # восстанавливаем тип по существующим категориям вида «Сезон 1».
    try:
        cursor.execute("PRAGMA table_info(Movies)")
        movie_columns = {row[1] for row in cursor.fetchall()}
        if "content_type" in movie_columns:
            cursor.execute("SELECT id, movie_number, name, season_number FROM MovieCategories")
            for category_id, movie_number, name, season_number in cursor.fetchall():
                if season_number is None and name:
                    match = re.match(r"^\s*(?:сезон\s*)?(\d+)\s*(?:сезон)?\s*$", str(name), re.IGNORECASE)
                    if match:
                        season_number = int(match.group(1))
                        cursor.execute(
                            "UPDATE MovieCategories SET season_number=? WHERE id=?",
                            (season_number, category_id),
                        )
                        cursor.execute(
                            "UPDATE Movies SET content_type='series' WHERE movie_number=?", (movie_number,)
                        )
    except sqlite3.OperationalError:
        # Таблица Movies может отсутствовать на этапе первичного создания БД.
        pass
    database.commit()


def add_movie_category(movie_number, name, season_number=None):
    cursor.execute(
        "INSERT INTO MovieCategories (movie_number, name, season_number) VALUES (?,?,?)",
        (movie_number, name, season_number),
    )
    database.commit()
    return cursor.lastrowid


def add_movie_subcategory(category_id, name, file_id, file_type):
    cursor.execute(
        "INSERT INTO MovieSubcategories (category_id, name, file_id, file_type) VALUES (?,?,?,?)",
        (category_id, name, file_id, file_type),
    )
    database.commit()
    return cursor.lastrowid


def get_categories_by_movie(movie_number):
    cursor.execute(
        "SELECT id, name FROM MovieCategories WHERE movie_number=? ORDER BY season_number IS NULL, season_number, id",
        (movie_number,),
    )
    return cursor.fetchall()


def episode_order(row):
    """Recognize episode labels, without treating a year in a title as an episode."""
    name = str(row[1] or '').strip()
    match = re.match(r'^(?:серия|эпизод|episode|ep)[\s._:#-]*(\d+)(?!\d)', name, re.IGNORECASE)
    if not match:
        match = re.match(r'^S\d+[ ._-]*E(\d+)(?!\d)', name, re.IGNORECASE)
    if not match:
        match = re.match(r'^(\d+)(?:\s*[-.:)]|\s+(?:серия|эпизод)\b)', name, re.IGNORECASE)
    return (0, int(match[1]), row[0]) if match else (1, 0, row[0])


def get_subcategories_by_category(category_id):
    cursor.execute(
        "SELECT id, name, file_id, file_type FROM MovieSubcategories WHERE category_id=? ORDER BY id",
        (category_id,),
    )
    rows = cursor.fetchall()
    kind = database.execute("""SELECT COALESCE(m.content_type,'movie') FROM MovieCategories c
        JOIN Movies m ON m.movie_number=c.movie_number WHERE c.id=?""", (category_id,)).fetchone()
    return sorted(rows, key=episode_order) if kind and kind[0] == 'series' else rows


def get_category_by_id(category_id):
    cursor.execute("SELECT id, movie_number, name FROM MovieCategories WHERE id=?", (category_id,))
    return cursor.fetchone()


def get_subcategory_by_id(subcategory_id):
    cursor.execute(
        "SELECT id, category_id, name, file_id, file_type FROM MovieSubcategories WHERE id=?",
        (subcategory_id,),
    )
    return cursor.fetchone()


def delete_category(category_id):
    cursor.execute("DELETE FROM MovieSubcategories WHERE category_id=?", (category_id,))
    cursor.execute("DELETE FROM MovieCategories WHERE id=?", (category_id,))
    database.commit()


def delete_categories_by_movie(movie_number):
    cursor.execute("SELECT id FROM MovieCategories WHERE movie_number=?", (movie_number,))
    ids = [row[0] for row in cursor.fetchall()]
    for category_id in ids:
        cursor.execute("DELETE FROM MovieSubcategories WHERE category_id=?", (category_id,))
    cursor.execute("DELETE FROM MovieCategories WHERE movie_number=?", (movie_number,))
    database.commit()


def get_seasons_by_movie(movie_number):
    """Возвращает сезоны сериала: (id, name, season_number)."""
    cursor.execute(
        "SELECT id, name, season_number FROM MovieCategories WHERE movie_number=? "
        "ORDER BY season_number IS NULL, season_number, id",
        (movie_number,),
    )
    return cursor.fetchall()


def get_category_by_id_full(category_id):
    cursor.execute(
        "SELECT id, movie_number, name, season_number FROM MovieCategories WHERE id=?", (category_id,)
    )
    return cursor.fetchone()


def update_category(category_id, name, season_number=None):
    cursor.execute(
        "UPDATE MovieCategories SET name=?, season_number=? WHERE id=?", (name, season_number, category_id)
    )
    database.commit()


def update_subcategory_name(subcategory_id, name):
    cursor.execute("UPDATE MovieSubcategories SET name=? WHERE id=?", (name, subcategory_id))
    database.commit()


def update_subcategory_file(subcategory_id, file_id, file_type):
    cursor.execute(
        "UPDATE MovieSubcategories SET file_id=?, file_type=? WHERE id=?",
        (file_id, file_type, subcategory_id),
    )
    database.commit()


def delete_subcategory(subcategory_id):
    cursor.execute("DELETE FROM MovieSubcategories WHERE id=?", (subcategory_id,))
    database.commit()
