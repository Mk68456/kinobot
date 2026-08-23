from loader import database, cursor


def create_categories_tables():
    """Создаёт таблицы категорий (озвучки) и подкатегорий (качества) фильмов, если их ещё нет."""
    cursor.execute('''CREATE TABLE IF NOT EXISTS MovieCategories (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        movie_number INTEGER,
        name TEXT
    )''')
    cursor.execute('''CREATE TABLE IF NOT EXISTS MovieSubcategories (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        category_id INTEGER,
        name TEXT,
        file_id TEXT,
        file_type TEXT
    )''')
    database.commit()


def add_movie_category(movie_number, name):
    cursor.execute("INSERT INTO MovieCategories (movie_number, name) VALUES (?,?)", (movie_number, name))
    database.commit()
    return cursor.lastrowid


def add_movie_subcategory(category_id, name, file_id, file_type):
    cursor.execute("INSERT INTO MovieSubcategories (category_id, name, file_id, file_type) VALUES (?,?,?,?)",
                   (category_id, name, file_id, file_type))
    database.commit()
    return cursor.lastrowid


def get_categories_by_movie(movie_number):
    cursor.execute("SELECT id, name FROM MovieCategories WHERE movie_number=?", (movie_number,))
    return cursor.fetchall()


def get_subcategories_by_category(category_id):
    cursor.execute("SELECT id, name, file_id, file_type FROM MovieSubcategories WHERE category_id=?", (category_id,))
    return cursor.fetchall()


def get_category_by_id(category_id):
    cursor.execute("SELECT id, movie_number, name FROM MovieCategories WHERE id=?", (category_id,))
    return cursor.fetchone()


def get_subcategory_by_id(subcategory_id):
    cursor.execute("SELECT id, category_id, name, file_id, file_type FROM MovieSubcategories WHERE id=?",
                   (subcategory_id,))
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
