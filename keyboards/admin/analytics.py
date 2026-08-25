from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


def analytics_menu_markup():
    markup = InlineKeyboardMarkup(row_width=1)
    markup.add(
        InlineKeyboardButton('📊 Статистика за 7 дней', callback_data='stats_week'),
        InlineKeyboardButton('🎬 Популярные фильмы', callback_data='stats_top_movies'),
        InlineKeyboardButton('👥 Пользователи', callback_data='stats_users_0'),
        InlineKeyboardButton('⬅️ Админ-панель', callback_data='stats_back'),
    )
    return markup


def users_page_markup(page, total, per_page=8):
    markup = InlineKeyboardMarkup(row_width=3)
    buttons = []
    if page > 0:
        buttons.append(InlineKeyboardButton('◀️', callback_data=f'stats_users_{page-1}'))
    if (page + 1) * per_page < total:
        buttons.append(InlineKeyboardButton('▶️', callback_data=f'stats_users_{page+1}'))
    if buttons:
        markup.row(*buttons)
    markup.add(InlineKeyboardButton('⬅️ Статистика', callback_data='bot_stat'))
    return markup


def user_detail_markup(user_id):
    markup = InlineKeyboardMarkup(row_width=1)
    markup.add(InlineKeyboardButton('⬅️ К списку пользователей', callback_data='stats_users_0'))
    return markup
