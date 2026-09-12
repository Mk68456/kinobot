import logging
from aiogram.utils.exceptions import BadRequest
from database.design import get_design, SUBSCRIPTION_NOTE
from database.channels import get_all_channels_links
from keyboards.users.keyboard import find_movie_markup
from keyboards.inline.sub_keyboard import sub_markup

logger = logging.getLogger(__name__)


async def send_welcome(bot, chat_id, subscribed=True):
    design = get_design()
    text = design["text"] + ("" if subscribed else SUBSCRIPTION_NOTE)
    markup = find_movie_markup() if subscribed else sub_markup(get_all_channels_links())
    if design["photo"]:
        try:
            return await bot.send_photo(
                chat_id, design["photo"], caption=text, parse_mode="", reply_markup=markup
            )
        except BadRequest:
            logger.warning("Welcome photo unavailable; sending text instead.")
    return await bot.send_message(chat_id, text, parse_mode="", reply_markup=markup)
