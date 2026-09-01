from datetime import datetime
from loader import database,cursor


def add_user(user_id:int, username:str = None):
    cursor.execute("SELECT id FROM Users WHERE id=?",(user_id,))
    user_ = cursor.fetchone()
    cursor.execute("PRAGMA table_info(Users)")
    columns = {row[1] for row in cursor.fetchall()}
    if user_ is None:
        if 'username' in columns and 'joined_at' in columns:
            cursor.execute("INSERT INTO Users (id, username, joined_at) VALUES (?,?,?)",
                           (user_id, username, datetime.utcnow().isoformat()))
        else:
            cursor.execute("INSERT INTO Users (id) VALUES(?)",(user_id,))
        database.commit()
    else:
        # Пользователь уже есть - на всякий случай обновим username, если он сменился
        if username and 'username' in columns:
            cursor.execute("UPDATE Users SET username=? WHERE id=?", (username, user_id))
            database.commit()
