from loader import database,cursor


def delete_no_active_user(user_id:int):
    cursor.execute("DELETE FROM Users WHERE id=?",(user_id,))
    database.commit()

def add_channel_link(link:str):
    cursor.execute("INSERT INTO channels_info VALUES(?,?,?)",(str('title'), str(link), str('cod'),))
    database.commit()
def add_channel_(message):
    cursor.execute("UPDATE channels_info SET title=? WHERE title=?",(message.forward_from_chat.title,str('title'),))
    cursor.execute("UPDATE channels_info SET cod=? WHERE cod=?",(message.forward_from_chat.id,str('cod'),))
    database.commit()
def delete_channel_t(title:str):
    cursor.execute("DELETE FROM channels_info WHERE title=?",(title,))
    database.commit()
def delete_channel_m():
    cursor.execute("DELETE FROM channels_info WHERE title=?",('title',))
    database.commit()
