from aiogram import types
from loader import dp,bot
from states.admin_states import Admin_
from aiogram.dispatcher import FSMContext
import asyncio
from database.admin.select import get_all_bot_users
from keyboards.admin.keyboard import allow_send_markup,admin_markup
from database.admin.channels_func import delete_no_active_user


@dp.message_handler(state=Admin_.send_,content_types=types.ContentTypes.ANY)
async def send_all_users_handler(message:types.Message,state:FSMContext):
    async with state.proxy() as data:
        data['markup'] = message.reply_markup
        data['mes'] = message
    await message.copy_to(message.chat.id, reply_markup=message.reply_markup)
    await message.reply(f"Сделать рассылку ?\n"
                            f"Пример поста :\n\n",reply_markup=allow_send_markup())
@dp.callback_query_handler(text='send_yes',state=Admin_.send_)
async def allow_send_handler(call:types.CallbackQuery,state:FSMContext):
    chat_id = call.message.chat.id
    await call.answer()
    await call.message.delete()
    async with state.proxy() as data:
        message = data['mes']
        reply_markup = data['markup']
        await message.copy_to(chat_id, reply_markup=reply_markup)
        await bot.send_message(chat_id, "<strong>Рассылка начинается</strong>", reply_markup=admin_markup())
        users = get_all_bot_users()
        i = 0
        for user in users:
            try:
                await asyncio.sleep(0.3)
                await message.copy_to(user[0], reply_markup=reply_markup)
                i += 1
            except:
                delete_no_active_user()
                await asyncio.sleep(0.3)
                continue
        await call.message.answer('<strong>Рассылка успешно завершена !\n\n</strong>'
                             '<strong>Статистика рассылки📊\n</strong>'
                             f'<i>Отправлено : {i}-пользователям</i>', reply_markup=admin_markup())
    await state.finish()
@dp.callback_query_handler(text='send_no',state=Admin_.send_)
async def allow_send_handler(call:types.CallbackQuery,state:FSMContext):
    await state.finish()
    await call.message.delete()
    await call.answer('Рассылка была отменена !',show_alert=True)
    await bot.send_message(call.message.chat.id, "Админ-панель",reply_markup=admin_markup())