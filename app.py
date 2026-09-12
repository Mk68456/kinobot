import logging
from aiogram import executor
from data.settings import DATABASE_PATH, BACKUP_DIR
from database.connection import database
from database.migrations import migrate

web_runner = None


async def on_startup(dispatcher):
    global web_runner
    from utils.set_bot_commands import set_default_commands
    from services.tmdb import check_connectivity
    from services.broadcasts import broadcasts

    database.open(DATABASE_PATH)
    migrate(BACKUP_DIR)
    from data.settings import WEB_ENABLED

    if WEB_ENABLED:
        from webpanel.server import start

        web_runner = await start(dispatcher.bot)
    await check_connectivity()
    await set_default_commands(dispatcher)
    broadcasts.start(dispatcher.bot)


async def on_shutdown(dispatcher):
    global web_runner
    from services.broadcasts import broadcasts
    from services.tmdb import close_session

    if web_runner is not None:
        await web_runner.cleanup()
        web_runner = None
    await broadcasts.stop()
    await close_session()
    await dispatcher.storage.close()
    await dispatcher.storage.wait_closed()
    database.close()


def main():
    import os

    if os.environ.get("STORAGE_DIAGNOSTICS") == "1":
        from tools.storage_diagnostics import run

        run()
        return

    from loader import initialize

    dispatcher = initialize()
    import handlers  # noqa: F401 -- Register handlers after initialization.

    executor.start_polling(dispatcher, on_startup=on_startup, on_shutdown=on_shutdown)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    main()
