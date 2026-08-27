from aiogram import executor
from loader import dp
from utils.set_bot_commands import set_default_commands
from database.admin.migrate_movies import check_schema
from database.admin.migrate_users import check_users_schema
from database.admin.categories import create_categories_tables
from database.admin.stats import create_stats_tables
from database.admin.torrents import create_torrents_table
from services.tmdb import check_connectivity
import handlers




async def on_startup(dispatcher):
    check_schema()
    check_users_schema()
    create_categories_tables()
    create_stats_tables()
    create_torrents_table()
    await check_connectivity()
    await set_default_commands(dispatcher)


if __name__ == '__main__':
    import logging
    logging.basicConfig(level=logging.INFO)
    executor.start_polling(dp, on_startup=on_startup)
