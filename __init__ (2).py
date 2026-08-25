from loader import database,cursor


def delete_no_active_user(user_id:int):
    cursor.execute("DELETE FROM Users WHERE id=?",(user_id,))
    database.commit()

def add_channel_(title, link, channel_id):
    cursor.execute("INSERT INTO channels_info VALUES(?,?,?)", (str(title), str(link), channel_id))
    database.commit()
def delete_channel_by_id(channel_id):
    cursor.execute("DELETE FROM channels_info WHERE id=?", (channel_id,))
    database.commit()
def delete_channel_m():
    cursor.execute("DELETE FROM channels_info WHERE title=?",('title',))
    database.commit()

def delete_channel_t(title):
    cursor.execute("DELETE FROM channels_info WHERE title=?", (title,))
    database.commit()
