from loader import database,cursor


def add_user(user_id:int):
    cursor.execute("SELECT id FROM Users WHERE id=?",(user_id,))
    user_ = cursor.fetchone()
    if user_ is None:
        cursor.execute("INSERT INTO Users VALUES(?)",(user_id,))
        database.commit()
    else:
        pass