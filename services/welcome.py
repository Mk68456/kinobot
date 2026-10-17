import logging
from aiogram.utils.exceptions import BadRequest
from database.design import get_design, SUBSCRIPTION_NOTE
from database.channels import get_all_channels_links
from keyboards.users.keyboard import find_movie_markup
from keyboards.inline.sub_keyboard import sub_markup

logger = logging.getLogger(__name__)


async def send_welcome(bot, chat_id, subscribed=True):
    from database import age_access
    design = get_design()
    text = design["text"] + ("" if subscribed else SUBSCRIPTION_NOTE)
    notify = not age_access.preferences(chat_id)['notified']
    if notify:
        text += '\n\n👨‍👩‍👧 Добавлены возрастные настройки. По умолчанию контент 18+ и материалы без маркировки скрыты. Настройки доступны в меню или по команде /age.'
    markup = find_movie_markup() if subscribed else sub_markup(get_all_channels_links())
    if not subscribed:
        from database.contests import current
        from aiogram.types import InlineKeyboardButton
        if current():
            markup.add(InlineKeyboardButton('🎁 Конкурс', callback_data='contest:view'))
        from aiogram.types import InlineKeyboardButton
        markup.add(InlineKeyboardButton('👨‍👩‍👧 Возрастные настройки', callback_data='age:settings'))
    if design["photo"]:
        try:
            result = await bot.send_photo(
                chat_id, design["photo"], caption=text, parse_mode="", reply_markup=markup
            )
            if notify:
                age_access.mark_notified(chat_id)
            return result
        except BadRequest:
            logger.warning("Welcome photo unavailable; sending text instead.")
    result = await bot.send_message(chat_id, text, parse_mode="", reply_markup=markup)
    if notify:
        age_access.mark_notified(chat_id)
    return result
