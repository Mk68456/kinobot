"""Movie-specific invitations; catalog navigation never uses invitation access."""
import re
import secrets
from urllib.parse import urlencode
from database.connection import database

bot_username = None


def share_url(number, title):
    if not bot_username:
        return None
    token = share_token(number)
    return 'https://t.me/share/url?' + urlencode({'url': f'https://t.me/{bot_username}?start=share_{token}', 'text': title})


def share_token(number):
    with database.transaction():
        row = database.execute('SELECT token FROM MovieShareLinks WHERE movie_number=?', (number,)).fetchone()
        token = row[0] if row else secrets.token_urlsafe(24)
        if not row:
            database.execute('INSERT INTO MovieShareLinks VALUES (?,?)', (token, number))
    return token


def accept(user_id, payload):
    if not re.fullmatch(r'share_[A-Za-z0-9_-]{32}', payload):
        return None
    row = database.execute('SELECT s.movie_number FROM MovieShareLinks s JOIN Movies m ON m.movie_number=s.movie_number WHERE token=?', (payload[6:],)).fetchone()
    if not row:
        return None
    with database.transaction():
        database.execute('INSERT OR IGNORE INTO MovieShareAccess VALUES (?,?)', (user_id, row[0]))
    return row[0]


def permits(user_id, data):
    """Resolve each file/category through the database, never trust a supplied movie id."""
    number = None
    match = re.fullmatch(r'(dlfile|movtorrents|torrback|filecard|sharefilm|trailer)_([0-9]{1,18})', data)
    if match:
        number = int(match[2])
    category = re.fullmatch(r'movcat_([0-9]{1,18})', data)
    sub = re.fullmatch(r'movsub_([0-9]{1,18})', data)
    torrent = re.fullmatch(r'gettorrent_([0-9]{1,18})', data)
    watch = re.fullmatch(r'wp:f:([0-9]{1,18}):[01]:c:0', data)
    if watch:
        number = int(watch[1])
    query = None
    if category:
        query = ('SELECT movie_number FROM MovieCategories WHERE id=?', category[1])
    elif sub:
        query = ('SELECT c.movie_number FROM MovieSubcategories s JOIN MovieCategories c ON c.id=s.category_id WHERE s.id=?', sub[1])
    elif torrent:
        query = ('SELECT movie_number FROM MovieTorrents WHERE id=?', torrent[1])
    page = re.fullmatch(r'wpage:([0-9]{1,18}):[0-9]{1,8}', data)
    series_watch = re.fullmatch(r'wp:([se]):([0-9]{1,18}):[01]:[cpm]:[0-9]{1,8}', data)
    if page or (series_watch and series_watch[1]=='s'):
        query = ('SELECT movie_number FROM MovieCategories WHERE id=?', page[1] if page else series_watch[2])
    elif series_watch:
        query = ('SELECT c.movie_number FROM MovieSubcategories s JOIN MovieCategories c ON c.id=s.category_id WHERE s.id=?', series_watch[2])
    if query:
        row = database.execute(query[0], (query[1],)).fetchone()
        number = row[0] if row else None
    return number is not None and bool(database.execute('SELECT 1 FROM MovieShareAccess a JOIN Movies m ON m.movie_number=a.movie_number WHERE a.user_id=? AND a.movie_number=?', (user_id, number)).fetchone())
