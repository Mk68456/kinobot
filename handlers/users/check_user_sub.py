from aiogram import types
from loader import bot, dp
from services.subscriptions import check_subscription
from services.welcome import send_welcome
from aiogram.dispatcher import FSMContext


async def check_user_sub(message):
    return await check_subscription(message.from_user.id)


@dp.callback_query_handler(text="check", state="*")
async def check_channels(call: types.CallbackQuery, state: FSMContext):
    if await check_subscription(call.from_user.id):
        await call.answer("Доступ открыт")
        data = await state.get_data()
        if data.get('pending_movie'):
            from handlers.start import open_linked_movie
            await open_linked_movie(call.message.chat.id, call.from_user.id, data['pending_movie'])
            await state.update_data(pending_movie=None)
            return
        await send_welcome(bot, call.message.chat.id)
    else:
        await call.answer("Вы не подписались на все каналы!", show_alert=True)
