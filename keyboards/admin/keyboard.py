from aiogram.types import InlineKeyboardButton,InlineKeyboardMarkup,ReplyKeyboardMarkup,KeyboardButton
from database.admin.select import get_all_channels_info,get_all_channels_title

def admin_markup():
    markup = InlineKeyboardMarkup(row_width=2)
    markup.add(InlineKeyboardButton(text='Статистика',callback_data='bot_stat'),
               InlineKeyboardButton(text='Рассылка', callback_data='send_func'),
               InlineKeyboardButton(text='Добавить канал', callback_data='add_channel'),
               InlineKeyboardButton(text='Удалить канал', callback_data='delete_channel'))
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



