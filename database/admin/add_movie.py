from loader import database,cursor
from .select import get_max_numb

def add_new_movie(title : str):
    max_numb = get_max_numb()
    max_numb += int(1)
    cursor.execute("INSERT INTO Movies VALUES(?,?)",(title,max_numb,))
    database.commit()

