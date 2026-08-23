from aiogram.types import InlineKeyboardButton,InlineKeyboardMarkup,ReplyKeyboardMarkup,KeyboardButton
from database.admin.select import get_all_movies

MOVIES_PER_PAGE = 5


def find_movie_markup():
    markup = InlineKeyboardMarkup(row_width=1)
    markup.add(InlineKeyboardButton(text='🔍 Найти фильм',callback_data='find_movie'))
    return markup


def get_movies_pick_markup(page: int = 0):
    all_movies = get_all_movies()
    start = page * MOVIES_PER_PAGE
    page_movies = all_movies[start:start + MOVIES_PER_PAGE]

    markup = InlineKeyboardMarkup(row_width=1)
    for movie in page_movies:
        title, numb = movie[0], movie[1]
        label = f'{numb} - {title}'
        if len(label) > 64:
            label = label[:61] + '...'
        markup.add(InlineKeyboardButton(text=label, callback_data=f'moviepick_{numb}'))

    nav_row = []
    if page > 0:
        nav_row.append(InlineKeyboardButton(text='⬅️ Предыдущая страница', callback_data=f'moviepage_{page-1}'))
    if start + MOVIES_PER_PAGE < len(all_movies):
        nav_row.append(InlineKeyboardButton(text='Следующая страница ➡️', callback_data=f'moviepage_{page+1}'))
    if nav_row:
        markup.row(*nav_row)
    return markup if (page_movies or nav_row) else None
