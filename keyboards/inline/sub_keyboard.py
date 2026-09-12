from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


def sub_markup(all_links):
    markup = InlineKeyboardMarkup(row_width=True)
    i = 1
    for link in all_links:
        markup.add(InlineKeyboardButton(text=f"{i}-Канал", url=link[0]))
        i += 1
    markup.add(InlineKeyboardButton(text="Я подписался✅", callback_data="check"))
    return markup
