from aiogram import types
from loader import dp,bot
from aiogram.dispatcher import FSMContext
from database.admin.select import get_all_bot_users
from keyboards.admin.keyboard import admin_return_markup,get_delete_channels,get_delete_movies,admin_markup
from states.admin_states import Admin_


@dp.callback_query_handler()
async def admin_call_handler(call:types.CallbackQuery,state:FSMContext):
    chat_id = call.message.chat.id
    if call.data == 'bot_stat':
        await bot.delete_message(chat_id, message_id=call.message.message_id)
        stat = get_all_bot_users()
        await bot.send_message(chat_id, f"<strong>Всего в боте : {len(stat)} пользователей</strong>",reply_markup=admin_markup())

    if call.data == 'add_cod':
        await bot.delete_message(chat_id, message_id=call.message.message_id)
        await bot.send_message(chat_id, "<strong>Отправьте текст :</strong>",reply_markup=admin_return_markup())
        await Admin_.add_new_cod.set()

    if call.data == 'send_func':
        await bot.delete_message(chat_id, message_id=call.message.message_id)
        await bot.send_message(chat_id, "<strong>Отправьте или Перешлите текст для рассылки :</strong>", reply_markup=admin_return_markup())
        await Admin_.send_.set()

    if call.data == 'add_channel':
        await bot.delete_message(chat_id, message_id=call.message.message_id)
        await bot.send_message(chat_id, "<strong>Отправьте ссылку на канал:</strong>",
                               reply_markup=admin_return_markup())
        await Admin_.add_channel_link.set()
    if call.data == 'delete_channel':
        await bot.delete_message(chat_id, message_id=call.message.message_id)
        await bot.send_message(chat_id, "<strong>Выберите канал который хотите удалить :</strong>",
                               reply_markup=get_delete_channels())
        await Admin_.delete_channel.set()
    if call.data == 'delete_movie':
        await bot.delete_message(chat_id, message_id=call.message.message_id)
        await bot.send_message(chat_id, "<strong>Выберите фильм который хотите удалить :</strong>",
                               reply_markup=get_delete_movies())
        await Admin_.delete_movie.set()
    if call.data == 'edit_movie':
        await bot.delete_message(chat_id, message_id=call.message.message_id)
        await bot.send_message(chat_id, "<strong>Выберите фильм который хотите изменить :</strong>",
                               reply_markup=get_delete_movies())
        await Admin_.edit_movie_select.set()
