from aiogram import types
from loader import dp,bot
from aiogram.dispatcher import FSMContext
from database.admin.select import get_all_bot_users
from keyboards.admin.keyboard import (admin_return_markup,get_delete_channels,get_delete_movies,admin_markup,
                                       content_type_choice_markup,stats_menu_markup)
from states.admin_states import Admin_

_KNOWN_ADMIN_CALLBACKS = (
    'bot_stat', 'add_cod', 'send_func', 'add_channel', 'delete_channel', 'delete_movie', 'edit_movie',
)


# ВАЖНО: этот хендлер не должен ловить ВСЕ callback-запросы бота, иначе он перехватит
# и "проглотит" клики других разделов (Пропустить, выбор озвучки/сезона, подтверждения
# удаления и т.д.), которые регистрируются позже. Поэтому фильтруем строго по своим call.data.
@dp.callback_query_handler(lambda call: call.data in _KNOWN_ADMIN_CALLBACKS)
async def admin_call_handler(call:types.CallbackQuery,state:FSMContext):
    chat_id = call.message.chat.id
    if call.data == 'bot_stat':
        await bot.delete_message(chat_id, message_id=call.message.message_id)
        stat = get_all_bot_users()
        await bot.send_message(chat_id, f"<strong>📊 Статистика</strong>\n\n"
                                        f"Всего пользователей : {len(stat)}",
                               reply_markup=stats_menu_markup())

    if call.data == 'add_cod':
        await bot.delete_message(chat_id, message_id=call.message.message_id)
        await bot.send_message(chat_id, "<strong>Что вы хотите добавить ?</strong>",
                               reply_markup=content_type_choice_markup())

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
        await bot.send_message(chat_id, "<strong>Выберите фильм/сериал который хотите удалить :</strong>",
                               reply_markup=get_delete_movies())
        await Admin_.delete_movie.set()
    if call.data == 'edit_movie':
        await bot.delete_message(chat_id, message_id=call.message.message_id)
        await bot.send_message(chat_id, "<strong>Выберите фильм/сериал который хотите изменить :</strong>",
                               reply_markup=get_delete_movies())
        await Admin_.edit_movie_select.set()


@dp.callback_query_handler(lambda call: call.data == 'stat_back')
async def stat_back_handler(call:types.CallbackQuery):
    await call.message.delete()
    await bot.send_message(call.message.chat.id, "Админ-панель", reply_markup=admin_markup())
