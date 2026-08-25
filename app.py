from aiogram.types import InlineKeyboardButton,InlineKeyboardMarkup,ReplyKeyboardMarkup,KeyboardButton
from database.admin.select import get_all_channels_info,get_all_channels_title,get_all_movies

def admin_markup():
    markup = InlineKeyboardMarkup(row_width=2)
    markup.add(InlineKeyboardButton(text='Статистика',callback_data='bot_stat'),
               InlineKeyboardButton(text='Рассылка', callback_data='send_func'),
               InlineKeyboardButton(text='Добавить канал', callback_data='add_channel'),
               InlineKeyboardButton(text='Удалить канал', callback_data='delete_channel'),
               InlineKeyboardButton(text='Удалить фильм', callback_data='delete_movie'),
               InlineKeyboardButton(text='✏️ Изменить фильм', callback_data='edit_movie'))
    markup.insert(InlineKeyboardButton(text='Добавить код', callback_data='add_cod'))
    return markup


def admin_return_markup():
    markup = InlineKeyboardMarkup(row_width=True)
    markup.add(InlineKeyboardButton(text='Назад',callback_data='admin_return'))
    return markup


def get_delete_channels():
    all_info = get_all_channels_title()
    markup = ReplyKeyboardMarkup(resize_keyboard=True)
    for info in all_info:
        markup.add(KeyboardButton(text=f'{info[0]}'))
    markup.add(KeyboardButton('Назад'))
    return markup
def allow_send_markup():
    markup = InlineKeyboardMarkup(row_width=1)
    markup.add(InlineKeyboardButton(text='Да',callback_data='send_yes'),
               InlineKeyboardButton(text='Нет',callback_data='send_no'))
    return markup


def allow_channel_markup():
    markup = InlineKeyboardMarkup(row_width=1)
    markup.add(InlineKeyboardButton(text='Да',callback_data='add_yes'),
               InlineKeyboardButton(text='Нет',callback_data='add_no'))
    return markup

def allow_channel_delete_markup():
    markup = InlineKeyboardMarkup(row_width=1)
    markup.add(InlineKeyboardButton(text='Да',callback_data='del_yes'),
               InlineKeyboardButton(text='Нет',callback_data='del_no'))
    return markup


def content_type_choice_markup():
    markup = InlineKeyboardMarkup(row_width=2)
    markup.add(InlineKeyboardButton(text='🎬 Фильм', callback_data='addtype_movie'),
               InlineKeyboardButton(text='📺 Сериал', callback_data='addtype_series'))
    return markup


def stats_menu_markup():
    markup = InlineKeyboardMarkup(row_width=1)
    markup.add(InlineKeyboardButton(text='📋 Список пользователей', callback_data='stat_users'),
               InlineKeyboardButton(text='🎬 Топ просмотров (7 дней)', callback_data='stat_top_watched'),
               InlineKeyboardButton(text='🔍 Топ запросов (7 дней)', callback_data='stat_top_searches'),
               InlineKeyboardButton(text='👤 История пользователя', callback_data='stat_user_lookup'),
               InlineKeyboardButton(text='⬅️ Назад', callback_data='stat_back'))
    return markup


def stats_back_markup():
    markup = InlineKeyboardMarkup(row_width=1)
    markup.add(InlineKeyboardButton(text='⬅️ Назад', callback_data='stat_menu_back'))
    return markup


def users_page_markup(page: int, has_more: bool):
    markup = InlineKeyboardMarkup(row_width=2)
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(text='⬅️ Назад', callback_data=f'statusers_{page-1}'))
    if has_more:
        nav.append(InlineKeyboardButton(text='Вперёд ➡️', callback_data=f'statusers_{page+1}'))
    if nav:
        markup.row(*nav)
    markup.add(InlineKeyboardButton(text='⬅️ В меню статистики', callback_data='stat_menu_back'))
    return markup


def skip_markup():
    markup = InlineKeyboardMarkup(row_width=1)
    markup.add(InlineKeyboardButton(text='Пропустить', callback_data='skip_step'))
    return markup


def get_delete_movies():
    all_movies = get_all_movies()
    markup = ReplyKeyboardMarkup(resize_keyboard=True)
    for movie in all_movies:
        title, numb = movie[0], movie[1]
        label = f'{numb} - {title}'
        if len(label) > 64:
            label = label[:61] + '...'
        markup.add(KeyboardButton(text=label))
    markup.add(KeyboardButton('Назад'))
    return markup


def allow_movie_delete_markup():
    markup = InlineKeyboardMarkup(row_width=1)
    markup.add(InlineKeyboardButton(text='Да',callback_data='movdel_yes'),
               InlineKeyboardButton(text='Нет',callback_data='movdel_no'))
    return markup


def file_mode_markup():
    markup = InlineKeyboardMarkup(row_width=1)
    markup.add(InlineKeyboardButton(text='🎬 Один файл (простой фильм)', callback_data='filemode_simple'),
               InlineKeyboardButton(text='🗂 Несколько категорий (озвучки/качества)', callback_data='filemode_categories'))
    return markup


def catbuild_finish_markup():
    markup = InlineKeyboardMarkup(row_width=1)
    markup.add(InlineKeyboardButton(text='Завершить', callback_data='catbuild_finish'))
    return markup


def catbuild_finish_category_markup():
    markup = InlineKeyboardMarkup(row_width=1)
    markup.add(InlineKeyboardButton(text='Завершить категорию', callback_data='catbuild_finish_category'))
    return markup


def edit_movie_menu_markup():
    markup = InlineKeyboardMarkup(row_width=1)
    markup.add(InlineKeyboardButton(text='✏️ Изменить название', callback_data='editm_title'),
               InlineKeyboardButton(text='📝 Изменить описание', callback_data='editm_desc'),
               InlineKeyboardButton(text='🎬 Изменить трейлер', callback_data='editm_trailer'),
               InlineKeyboardButton(text='🗂 Категории (озвучки/качества)', callback_data='editm_cat'),
               InlineKeyboardButton(text='📁 Torrent-файлы', callback_data='editm_torrents'),
               InlineKeyboardButton(text='⬅️ Назад', callback_data='editm_back'))
    return markup


def categories_menu_markup():
    markup = InlineKeyboardMarkup(row_width=1)
    markup.add(InlineKeyboardButton(text='➕ Добавить категорию', callback_data='catmenu_add'),
               InlineKeyboardButton(text='📋 Показать категории', callback_data='catmenu_list'),
               InlineKeyboardButton(text='🗑 Удалить категорию', callback_data='catmenu_delete'),
               InlineKeyboardButton(text='⬅️ Назад', callback_data='catmenu_back'))
    return markup


def categories_delete_pick_markup(categories):
    markup = InlineKeyboardMarkup(row_width=1)
    for category_id, name in categories:
        markup.add(InlineKeyboardButton(text=name, callback_data=f'catdel_{category_id}'))
    markup.add(InlineKeyboardButton(text='⬅️ Назад', callback_data='catmenu_back'))
    return markup


def torrents_menu_markup():
    markup = InlineKeyboardMarkup(row_width=1)
    markup.add(InlineKeyboardButton(text='➕ Добавить torrent-файл', callback_data='trmenu_add'),
               InlineKeyboardButton(text='📋 Показать torrent-файлы', callback_data='trmenu_list'),
               InlineKeyboardButton(text='🗑 Удалить torrent-файл', callback_data='trmenu_delete'),
               InlineKeyboardButton(text='⬅️ Назад', callback_data='trmenu_back'))
    return markup


def torrents_delete_pick_markup(torrents):
    markup = InlineKeyboardMarkup(row_width=1)
    for torrent_id, name in torrents:
        markup.add(InlineKeyboardButton(text=name, callback_data=f'trdel_{torrent_id}'))
    markup.add(InlineKeyboardButton(text='⬅️ Назад', callback_data='trmenu_back'))
    return markup


def torrents_finish_markup():
    markup = InlineKeyboardMarkup(row_width=1)
    markup.add(InlineKeyboardButton(text='✅ Завершить', callback_data='trbuild_finish'))
    return markup



