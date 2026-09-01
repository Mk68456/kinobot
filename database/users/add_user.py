from loader import database, cursor


def add_user(user_or_id, username=None, first_name=None, last_name=None):
    if hasattr(user_or_id, 'chat'):
        user_id = user_or_id.chat.id
        username = getattr(user_or_id.from_user, 'username', None)
        first_name = getattr(user_or_id.from_user, 'first_name', None)
        last_name = getattr(user_or_id.from_user, 'last_name', None)
    elif hasattr(user_or_id, 'from_user'):
        user_id = user_or_id.from_user.id
        username = getattr(user_or_id.from_user, 'username', None)
        first_name = getattr(user_or_id.from_user, 'first_name', None)
        last_name = getattr(user_or_id.from_user, 'last_name', None)
    else:
        user_id = int(user_or_id)

    cursor.execute('SELECT id FROM Users WHERE id=?', (user_id,))
    exists = cursor.fetchone()
    if exists is None:
        cursor.execute('''
            INSERT INTO Users (id, username, first_name, last_name, joined_at, last_active_at)
            VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
        ''', (user_id, username, first_name, last_name))
    else:
        cursor.execute('''
            UPDATE Users
            SET username=?, first_name=?, last_name=?, last_active_at=CURRENT_TIMESTAMP
            WHERE id=?
        ''', (username, first_name, last_name, user_id))
    database.commit()
    return user_id
