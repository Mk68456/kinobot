from aiogram import types
from loader import dp,bot
from aiogram.dispatcher import FSMContext
from states.admin_states import Admin_
from database.admin.select import get_all_bot_users
from database.admin.channels_func import add_channel_,delete_channel_t
from keyboards.admin.keyboard import  admin_markup,admin_return_markup,allow_channel_markup,allow_channel_delete_markup


@dp.message_handler(state=Admin_.delete_channel)
async def delete_channel_func(message:types.Message,state:FSMContext):
    if message.text == 'Назад':
        await state.finish()
        await message.delete()
        await bot.send_message(message.chat.id, 'Удаление канала было отменено', reply_markup=types.ReplyKeyboardRemove())
        await bot.send_message(message.chat.id, "Админ-панель\n\n"
                                                f"Статистика : {int(len(get_all_bot_users()))}",
                               reply_markup=admin_markup())
    else:
        async with state.proxy() as data:
            data['channel_title'] = message.text
        await bot.send_message(message.chat.id, f"Удалить канал {message.text} ?",reply_markup=allow_channel_delete_markup())
@dp.message_handler(state=Admin_.add_channel_link)
async def add_post_handler(message:types.Message,state:FSMContext):
    async with state.proxy() as data:
        data['channel_link'] = message.text
    await bot.send_message(message.chat.id, "Отправьте пост из канала ",reply_markup=admin_return_markup())
    await Admin_.add_channel.set()
@dp.message_handler(state=Admin_.add_channel,content_types=types.ContentTypes.ANY)
async def add_new_channel_post_handler(message:types.Message,state:FSMContext):
    try:
        async with state.proxy() as data:
            data['channel_title'] = message.forward_from_chat.title
            data['channel_id'] = message.forward_from_chat.id
        await bot.send_message(message.chat.id, f"Добавить канал {message.forward_from_chat.title}  ?",
                               reply_markup=allow_channel_markup())
    except:
        await bot.send_message(message.chat.id, "Отправьте пост из канала :", reply_markup=admin_return_markup())
        await Admin_.add_channel.set()


@dp.callback_query_handler(state=Admin_)
async def return_channel_handler(call:types.CallbackQuery,state:FSMContext):
    if call.data == 'admin_return':
        await state.finish()
        await call.answer('Вы вернулись в админ-панель')
        await call.message.delete()
        await bot.send_message(call.message.chat.id, "Админ-панель\n\n"
                                                f"Статистика : {int(len(get_all_bot_users()))}",
                               reply_markup=admin_markup())
    if call.data.startswith('add_'):
        if call.data[4:] == 'yes':
            await call.message.delete()
            async with state.proxy() as data:
                channel_title = data['channel_title']
                channel_id = data['channel_id']
                channel_link = data['channel_link']
            add_channel_(channel_title,channel_link,channel_id)
            await state.finish()
            await bot.send_message(call.message.chat.id, "Канал успешно добавлен !",reply_markup=admin_markup())
        else:
            await state.finish()
            await call.answer('Вы вернулись в админ-панель')
            await call.message.delete()
            await bot.send_message(call.message.chat.id, 'Админ-панель', reply_markup=admin_markup())
    if call.data.startswith('del_'):
        if call.data[4:] == 'yes':
            async with state.proxy() as data:
                channel_title_ = data['channel_title']
                delete_channel_t(channel_title_)
                await bot.send_message(call.message.chat.id, "Канал успешно удален !",
                                       reply_markup=types.ReplyKeyboardRemove())
                await bot.send_message(call.message.chat.id, 'Админ-панель', reply_markup=admin_markup())
            await call.message.delete()
            await state.finish()
        else:
            await bot.send_message(call.message.chat.id, 'Удаления канала было отменено ',
                                   reply_markup=types.ReplyKeyboardRemove())
            await state.finish()
            await call.answer('Вы вернулись в админ-панель')
            await call.message.delete()
            await bot.send_message(call.message.chat.id, 'Админ-панель', reply_markup=admin_markup())

