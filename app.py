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
    from services.instant import recover
    recover()
    from data.settings import WEB_ENABLED

    if WEB_ENABLED:
        from webpanel.server import start

        web_runner = await start(dispatcher.bot)
    from services import movie_sharing
    movie_sharing.bot_username = (await dispatcher.bot.get_me()).username
    await check_connectivity()
    await set_default_commands(dispatcher)
    broadcasts.start(dispatcher.bot)
    from services.storage_backup import backup
    backup.start(dispatcher.bot)
    from services.zona_delivery import delivery as zona_delivery
    zona_delivery.start(dispatcher.bot)
    from services.instant_agent import start as start_instant_agent
    await start_instant_agent()


async def on_shutdown(dispatcher):
    global web_runner
    from services.broadcasts import broadcasts
    from services.tmdb import close_session
    from services.storage_backup import backup

    from services.instant_agent import stop as stop_instant_agent
    await stop_instant_agent()

    from services.zona_delivery import delivery as zona_delivery
    await zona_delivery.stop()

    if web_runner is not None:
        await web_runner.cleanup()
        web_runner = None
    await broadcasts.stop()
    await backup.stop()
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

    # Reset any previously persisted Telegram update filter. The default set
    # includes messages and callback queries used by the inline keyboards.
    executor.start_polling(
        dispatcher, on_startup=on_startup, on_shutdown=on_shutdown,
        allowed_updates=[],
    )


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    main()
