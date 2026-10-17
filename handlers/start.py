"""Register /start before conversational handlers so it always exits a dialogue."""

import logging
import re

from aiogram import types
from aiogram.dispatcher.filters import CommandStart
from loader import dp, bot
from database.people import add_user
from services.welcome import send_welcome
from services.subscriptions import check_subscription

logger = logging.getLogger(__name__)


async def check_user_sub(message):
    return await check_subscription(message.from_user.id)


@dp.message_handler(CommandStart(), state="*")
async def start_command_handler(message: types.Message, state):
    user_id = message.from_user.id
    stage = "reset_state"
    logger.info("START received user=%s", user_id)
    try:
        await state.finish()
        stage = "activate_user"
        payload = message.get_args() or ''
        from database.contests import accept_start
        from database.connection import database
        with database.transaction():
            is_new = add_user(user_id, username=message.from_user.username)
            contest_id = accept_start(user_id, payload, is_new)
        if contest_id is not None:
            stage = 'send_contest'
            from services.contests import send_page
            await send_page(bot, message.chat.id, user_id, contest_id)
            return
        from services import movie_sharing
        if payload.startswith('share_'):
            number = movie_sharing.accept(user_id, payload)
            if number is None:
                await message.answer('Ссылка на фильм недействительна или фильм удалён.')
            else:
                await open_linked_movie(message.chat.id, user_id, number)
            return
        stage = "check_subscription"
        logger.info("START checking subscription user=%s", user_id)
        subscribed = bool(await check_user_sub(message))
        match = re.fullmatch(r'movie_([1-9][0-9]{0,14})', message.get_args() or '')
        if match:
            number = int(match[1])
            if subscribed:
                await open_linked_movie(message.chat.id, user_id, number)
                return
            await state.update_data(pending_movie=number)
        stage = "send_welcome"
        logger.info("START sending welcome user=%s subscribed=%s", user_id, subscribed)
        await send_welcome(bot, message.chat.id, subscribed=subscribed)
    except Exception:
        logger.exception("START failed user=%s stage=%s", user_id, stage)
        raise
    logger.info("START completed user=%s", user_id)


async def open_linked_movie(chat_id, user_id, number):
    from database.search import get_movie_from_numb
    from handlers.users.find_movie import _send_movie_card
    info = get_movie_from_numb(number)
    if info is None:
        await bot.send_message(chat_id, 'Этот фильм пока недоступен.')
    else:
        await _send_movie_card(chat_id, number, info, user_id=user_id)
