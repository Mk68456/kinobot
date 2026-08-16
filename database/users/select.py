from loader import cursor

def get_movie_from_numb(numb : int):
    cursor.execute("SELECT movie_title FROM Movies WHERE movie_number=?",(numb, ))
    info_numb = cursor.fetchone()
    if info_numb is None:
        return 'None'
    else:
        return info_numb[0]

