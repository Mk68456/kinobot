from aiogram import executor
from loader import dp
from utils.set_bot_commands import set_default_commands
from database.admin.migrate_movies import check_schema
from database.admin.categories import create_categories_tables
import handlers




async def on_startup(dispatcher):
    check_schema()
    create_categories_tables()
    await set_default_commands(dispatcher)


if __name__ == '__main__':
    import logging
    logging.basicConfig(level=logging.INFO)
    executor.start_polling(dp, on_startup=on_startup)
