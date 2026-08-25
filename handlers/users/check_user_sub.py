from loader import bot,dp
from aiogram import types
from database.admin.select import get_all_channels_cod
from keyboards.users.keyboard import find_movie_markup
import logging

@dp.callback_query_handler(text='check')
async def check_channals(call:types.CallbackQuery):
    chat_id = call.message.chat.id
    check = await check_user_sub(call.message)
    if check != False:
        await call.message.delete()
        await bot.send_message(chat_id, "<strong>Отлично, вы подписались на все каналы!</strong>\n\n"
                                        "Нажмите на кнопку ниже, чтобы найти фильм 👇🏻",
                               reply_markup=find_movie_markup())
    else:
        await call.answer('Вы не подписались на каналы !',show_alert=True)


async def check_user_sub(message):
    all_cods = get_all_channels_cod()
    if all_cods == False:
        return True
    else:
        for cod in all_cods:
            try:
                user_status = await bot.get_chat_member(cod[0], user_id=message.chat.id)
            except Exception as error:
                # Если Telegram не смог проверить участника (канал недоступен,
                # бот не админ, юзер не найден и т.п.) - не роняем весь хендлер,
                # а просто считаем, что подписки нет, и логируем причину.
                logging.warning(f"Не удалось проверить подписку на канал {cod[0]} "
                                f"для пользователя {message.chat.id}: {error}")
                return False
            if user_status.status == 'left':
                return False
        return True