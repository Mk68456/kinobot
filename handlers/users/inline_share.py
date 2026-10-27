"""Only invitation tokens are accepted; inline mode is not a public catalog search."""
import re
from html import unescape
from aiogram import types
from aiogram.utils.exceptions import BadRequest
from loader import dp, bot
from database.connection import database
from database.search import get_movie_from_numb
from services import movie_sharing
from services.ratings import line


def plain(value):
    return unescape(re.sub(r'<[^>]*>', '', value or ''))


def results(number, info, token, username):
    title=plain(info['movie_title'])[:200]
    text='🎬 '+title
    ratings=line(info)
    if ratings:text+='\n'+ratings
    description=plain(info.get('card_description'))
    if description:text+='\n\n'+description[:400]+('…' if len(description)>400 else '')
    text+='\n\n🍿 Кажется, нашёл, что посмотреть. Делюсь с тобой!'
    markup=types.InlineKeyboardMarkup().add(types.InlineKeyboardButton('📺 Открыть сериал' if info.get('content_type')=='series' else '🎬 Открыть фильм',url=f'https://t.me/{username}?start=share_{token}'))
    fallback=types.InlineQueryResultArticle(id=f'text-{number}',title=title,description='Отправить описание и кнопку открытия',input_message_content=types.InputTextMessageContent(text,parse_mode=''),reply_markup=markup)
    poster=info.get('poster_image')
    if not poster:return [fallback]
    common=dict(id=f'photo-{number}',title=title,caption=text,parse_mode='',reply_markup=markup)
    if poster.startswith(('https://','http://')):
        photo=types.InlineQueryResultPhoto(photo_url=poster,thumb_url=poster,**common)
    else:
        photo=types.InlineQueryResultCachedPhoto(photo_file_id=poster,**common)
    return [photo,fallback]


@dp.inline_handler(state='*')
async def inline_share(query: types.InlineQuery):
    match=re.fullmatch(r'share_([A-Za-z0-9_-]{32})',query.query.strip())
    if not match:
        await query.answer([],cache_time=0,is_personal=True)
        return
    row=database.execute('SELECT movie_number FROM MovieShareLinks WHERE token=?',(match[1],)).fetchone()
    info=get_movie_from_numb(row[0]) if row else None
    if not info:
        await query.answer([],cache_time=0,is_personal=True)
        return
    if not movie_sharing.bot_username:
        movie_sharing.bot_username=(await bot.get_me()).username
    choices=results(row[0],info,match[1],movie_sharing.bot_username)
    try:
        await query.answer(choices,cache_time=0,is_personal=True)
    except BadRequest:
        if len(choices)==1:raise
        await query.answer(choices[-1:],cache_time=0,is_personal=True)
