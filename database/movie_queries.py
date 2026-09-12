from database.connection import cursor


def get_max_numb():
    cursor.execute("SELECT MAX(movie_number) FROM Movies")
    max_numb = cursor.fetchone()[0]
    if max_numb is None:
        return 1
    else:
        return max_numb


def get_all_movies(content_type=None):
    sql = "SELECT movie_title,movie_number,COALESCE(content_type,'movie') FROM Movies"
    params = ()
    if content_type:
        sql += " WHERE COALESCE(content_type,'movie')=?"
        params = (content_type,)
    return cursor.execute(sql + " ORDER BY movie_number", params).fetchall()


def get_movies_page(page=0, content_type=None, per_page=5):
    sql = "SELECT movie_title,movie_number FROM Movies"
    params = []
    if content_type:
        sql += " WHERE COALESCE(content_type,'movie')=?"
        params.append(content_type)
    sql += " ORDER BY movie_number LIMIT ? OFFSET ?"
    params.extend([per_page + 1, max(0, page) * per_page])
    rows = cursor.execute(sql, params).fetchall()
    return rows[:per_page], len(rows) > per_page


def get_movie_content_type(numb):
    cursor.execute("PRAGMA table_info(Movies)")
    columns = {row[1] for row in cursor.fetchall()}
    if "content_type" not in columns:
        return "movie"
    cursor.execute("SELECT content_type FROM Movies WHERE movie_number=?", (numb,))
    row = cursor.fetchone()
    return row[0] if row and row[0] else "movie"


def get_movie_title_by_numb(numb):
    cursor.execute("SELECT movie_title FROM Movies WHERE movie_number=?", (numb,))
    row = cursor.fetchone()
    return row[0] if row else None
