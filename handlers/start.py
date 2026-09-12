"""Register /start before conversational handlers so it always exits a dialogue."""

from aiogram import types
from aiogram.dispatcher.filters import CommandStart
from loader import dp, bot
from database.people import add_user
from services.welcome import send_welcome
from services.subscriptions import check_subscription


async def check_user_sub(message):
    return await check_subscription(message.from_user.id)


@dp.message_handler(CommandStart(), state="*")
async def start_command_handler(message: types.Message, state):
    await state.finish()
    add_user(message.from_user.id, username=message.from_user.username)
    await send_welcome(bot, message.chat.id, subscribed=bool(await check_user_sub(message)))
