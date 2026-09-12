from aiogram import types
from loader import dp, bot
from aiogram.dispatcher.filters import CommandStart
from database.people import add_user
from services.welcome import send_welcome
from .check_user_sub import check_user_sub


@dp.message_handler(CommandStart(), state="*")
async def start_command_handler(message: types.Message, state):
    await state.finish()
    add_user(message.from_user.id, username=message.from_user.username)
    await send_welcome(bot, message.chat.id, subscribed=bool(await check_user_sub(message)))
