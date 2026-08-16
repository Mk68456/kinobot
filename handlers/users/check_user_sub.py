from loader import bot,dp
from aiogram import types
from database.admin.select import get_all_channels_cod

@dp.callback_query_handler(text='check')
async def check_channals(call:types.CallbackQuery):
    chat_id = call.message.chat.id
    check = await check_user_sub(call.message)
    if check != False:
        await call.message.delete()
        await bot.send_message(chat_id, f"<strong>Введите код для получения названия фильма :</strong>")
    else:
        await call.answer('Вы не подписались на каналы !',show_alert=True)


async def check_user_sub(message):
    all_cods = get_all_channels_cod()
    if all_cods == False:
        return True
    else:
        for cod in all_cods:
            user_status = await bot.get_chat_member(cod[0],user_id=message.chat.id)
            if user_status.status == 'left':
                return False
        return True