from aiogram.types import InlineKeyboardButton,InlineKeyboardMarkup
from database.admin.select import get_all_channels_links

def sub_markup():
    markup = InlineKeyboardMarkup(row_width=True)
    i = 1
    all_links = get_all_channels_links()
    for link in all_links:
        markup.add(InlineKeyboardButton(text=f'{i}-Канал',url=link[0]))
        i += 1
    markup.add(InlineKeyboardButton(text='Я подписался✅',callback_data='check'))
    return markup