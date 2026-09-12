from aiogram import types
from loader import bot, dp
from services.subscriptions import check_subscription
from keyboards.users.keyboard import find_movie_markup


async def check_user_sub(message):
    return await check_subscription(message.from_user.id)


@dp.callback_query_handler(text="check", state="*")
async def check_channels(call: types.CallbackQuery):
    if await check_subscription(call.from_user.id):
        await call.answer("Доступ открыт")
        await bot.send_message(
            call.message.chat.id, "Выберите фильм или сериал:", reply_markup=find_movie_markup()
        )
    else:
        await call.answer("Вы не подписались на все каналы!", show_alert=True)
