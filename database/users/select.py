from loader import cursor

def get_movie_from_numb(numb : int):
    cursor.execute("PRAGMA table_info(Movies)")
    columns = [row[1] for row in cursor.fetchall()]

    cursor.execute("SELECT * FROM Movies WHERE movie_number=?", (numb,))
    row = cursor.fetchone()
    if row is None:
        return None

    info = dict(zip(columns, row))
    return {'movie_title': info.get('movie_title'),
            'poster_image': info.get('poster_image') or None,
            'card_description': info.get('card_description') or None,
            'card_style': info.get('card_style') or 'simple',
            'movie_file': info.get('movie_file') or None,
            'movie_file_type': info.get('movie_file_type') or None}


def get_movies_by_title(query:str):
    """Ищет фильмы по названию (частичное совпадение, без учёта регистра).
    SQLite сам не умеет регистронезависимо сравнивать кириллицу, поэтому
    фильтруем в Python. Возвращает список (movie_title, movie_number)."""
    cursor.execute("SELECT movie_title, movie_number FROM Movies")
    all_movies = cursor.fetchall()
    query_lower = query.lower()
    return [m for m in all_movies if m[0] and query_lower in m[0].lower()]


