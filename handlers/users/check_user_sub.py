from aiogram import types
from loader import bot, dp
from services.subscriptions import check_subscription
from services.welcome import send_welcome


async def check_user_sub(message):
    return await check_subscription(message.from_user.id)


@dp.callback_query_handler(text="check", state="*")
async def check_channels(call: types.CallbackQuery):
    if await check_subscription(call.from_user.id):
        await call.answer("Доступ открыт")
        await send_welcome(bot, call.message.chat.id)
    else:
        await call.answer("Вы не подписались на все каналы!", show_alert=True)
