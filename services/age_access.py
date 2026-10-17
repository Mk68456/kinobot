"""Resolve old and new catalog buttons to their actual database owners."""
import re

from aiogram import types
from database import age_access
from database.connection import database
from database.search import get_movie_from_numb


def settings_markup():
    return types.InlineKeyboardMarkup().add(types.InlineKeyboardButton(
        '👨‍👩‍👧 Возрастные настройки', callback_data='age:settings'))


async def deny(bot, chat_id):
    await bot.send_message(chat_id,
        'Это произведение недоступно при текущих возрастных настройках. '
        'Материалы без маркировки также скрыты в безопасном режиме.',
        parse_mode='', reply_markup=settings_markup())


def callback_movies(data):
    numbers = []
    direct = re.fullmatch(r'(?:moviepick|dlfile|filecard|sharefilm|trailer|movtorrents|torrback|movcatback)_([0-9]{1,18})', data)
    season_card = re.fullmatch(r'epseasons:([0-9]{1,18})', data)
    if direct or season_card:
        return [int((direct or season_card)[1])]
    query, ids = None, []
    category = re.fullmatch(r'movcat_([0-9]{1,18})|wpage:([0-9]{1,18}):[0-9]{1,8}', data)
    episode = re.fullmatch(r'movsub_([0-9]{1,18})|ep(?:quality|voices):([0-9]{1,18}):[0-9]{1,18}', data)
    next_episode = re.fullmatch(r'epnext:([0-9]{1,18}):[0-9]{1,18}:([0-9]{1,18})', data)
    watched = re.fullmatch(r'wp:([fse]):([0-9]{1,18}):[01]:[cpm]:[0-9]{1,18}', data)
    torrent = re.fullmatch(r'gettorrent_([0-9]{1,18})', data)
    if watched and watched[1] == 'f':
        return [int(watched[2])]
    if category or watched and watched[1] == 's':
        query = 'SELECT movie_number FROM MovieCategories WHERE id=?'
        ids = [next(value for value in category.groups() if value is not None) if category else watched[2]]
    elif episode or next_episode or watched and watched[1] == 'e':
        query = '''SELECT c.movie_number FROM MovieSubcategories e
            JOIN MovieCategories c ON c.id=e.category_id WHERE e.id=?'''
        ids = ([value for value in episode.groups() if value is not None] if episode else
               list(next_episode.groups()) if next_episode else [watched[2]])
    elif torrent:
        query, ids = 'SELECT movie_number FROM MovieTorrents WHERE id=?', [torrent[1]]
    for identifier in ids:
        row = database.execute(query, (identifier,)).fetchone()
        if row:
            numbers.append(row[0])
    return numbers


def callback_allowed(user_id, data):
    return all(age_access.allowed(user_id, get_movie_from_numb(number)) for number in callback_movies(data))
