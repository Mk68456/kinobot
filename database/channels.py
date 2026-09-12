from database.connection import database, cursor


def add_channel_(title, link, channel_id):
    cursor.execute(
        "INSERT INTO channels_info(title,link,cod) VALUES(?,?,?)", (str(title), str(link), channel_id)
    )
    database.commit()


def delete_channel_by_id(channel_id):
    cursor.execute("DELETE FROM channels_info WHERE cod=?", (channel_id,))
    database.commit()


def delete_channel_m():
    cursor.execute("DELETE FROM channels_info WHERE title=?", ("title",))
    database.commit()


def delete_channel_t(title):
    cursor.execute("DELETE FROM channels_info WHERE title=?", (title,))
    database.commit()


def get_all_channels_title():
    cursor.execute("SELECT title FROM channels_info")
    channels_title = cursor.fetchall()
    return channels_title


def get_all_channels_cod():
    cursor.execute("SELECT cod FROM channels_info")
    channels_cod = cursor.fetchall()
    if channels_cod is None:
        return False
    else:
        return channels_cod


def get_all_channels_links():
    cursor.execute("SELECT link FROM channels_info")
    channels_link = cursor.fetchall()
    return channels_link


def get_all_channels_info():
    cursor.execute("SELECT * FROM channels_info")
    channels_info = cursor.fetchall()
    return channels_info
