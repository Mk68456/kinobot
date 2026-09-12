"""Create bot resources before registering handlers."""

from aiogram import Bot, Dispatcher, types
from aiogram.contrib.fsm_storage.memory import MemoryStorage

bot = dp = storage = None


def initialize(token=None):
    global bot, dp, storage
    from data.config import BOT_TOKEN
    from services.access import AccessMiddleware
    from data.settings import BOT_API_BASE
    from aiogram.bot.api import TelegramAPIServer

    bot = Bot(
        token=token or BOT_TOKEN,
        parse_mode=types.ParseMode.HTML,
        server=TelegramAPIServer.from_base(BOT_API_BASE),
    )
    storage = MemoryStorage()
    dp = Dispatcher(bot, storage=storage)
    dp.middleware.setup(AccessMiddleware())
    return dp
