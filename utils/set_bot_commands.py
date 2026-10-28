from aiogram import types


async def set_default_commands(dp):
    await dp.bot.set_my_commands(
        [
            types.BotCommand("start", "Запустить бота"),
            types.BotCommand("contest", "Конкурс и приглашения"),
            types.BotCommand("instant_status", "Мои запросы на быструю выдачу"),
        ]
    )
