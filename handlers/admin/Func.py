from aiogram import types
from loader import dp,bot
from aiogram.dispatcher import FSMContext
from database.admin.add_movie import delete_movie_by_numb
from states.admin_states import Admin_
from keyboards.admin.keyboard import admin_markup,allow_movie_delete_markup
from database.admin.channels_func import delete_no_active_user
from database.admin.select import get_all_bot_users,get_movie_title_by_numb
import asyncio
import re


@dp.message_handler(state=Admin_.delete_movie)
async def delete_movie_func(message:types.Message,state:FSMContext):
    if message.text == 'Назад':
        await state.finish()
        await message.delete()
        await bot.send_message(message.chat.id, 'Удаление фильма было отменено', reply_markup=types.ReplyKeyboardRemove())
        await bot.send_message(message.chat.id, "Админ-панель\n\n"
                                                f"Статистика : {int(len(get_all_bot_users()))}",
                               reply_markup=admin_markup())
        return
    match = re.match(r'^\D*(\d+)\s*-\s*', (message.text or '').strip())
    numb_part = match.group(1) if match else ''
    if not numb_part.isdigit():
        await bot.send_message(message.chat.id, "Пожалуйста, выберите фильм кнопкой из списка.")
        return
    movie_title = get_movie_title_by_numb(int(numb_part))
    if movie_title is None:
        await bot.send_message(message.chat.id, "Такой фильм не найден, выберите из списка.")
        return
    await state.update_data(movie_number=int(numb_part))
    await bot.send_message(message.chat.id, f"Удалить фильм «{movie_title}» (код {numb_part}) ?",
                           reply_markup=allow_movie_delete_markup())


@dp.callback_query_handler(lambda call: call.data.startswith('movdel_'), state=Admin_.delete_movie)
async def confirm_delete_movie_handler(call:types.CallbackQuery,state:FSMContext):
    if call.data == 'movdel_yes':
        data = await state.get_data()
        delete_movie_by_numb(data.get('movie_number'))
        await call.message.delete()
        await state.finish()
        await bot.send_message(call.message.chat.id, "Фильм успешно удалён !", reply_markup=types.ReplyKeyboardRemove())
        await bot.send_message(call.message.chat.id, "Админ-панель", reply_markup=admin_markup())
    else:
        await call.message.delete()
        await state.finish()
        await bot.send_message(call.message.chat.id, 'Удаление фильма было отменено', reply_markup=types.ReplyKeyboardRemove())
        await bot.send_message(call.message.chat.id, "Админ-панель", reply_markup=admin_markup())


@dp.message_handler(state=Admin_.send_)
async def send_all_bot_users(message:types.Message,state:FSMContext):
    await state.finish()
    chat_id = message.chat.id
    if message.content_type == 'text':
        await bot.send_message(chat_id, "<strong>Рассылка началась !</strong>")
        users = get_all_bot_users()
        i = 0
        for user in users:
            try:
                await bot.send_message(user[0], message.text, entities=message.entities,
                                       reply_markup=message.reply_markup)
                i += 1
                await asyncio.sleep(1)
            except:
                delete_no_active_user(user[0])
                await asyncio.sleep(1)
                continue
        await message.answer('<strong>Рассылка успешно завершенна\n\n</strong>'
                             '<strong>Статистика Рассылки 📊\n</strong>'
                             f'<i>Отправлено : {i}-Пользователям</i>')
    if message.content_type == 'photo':
        await bot.send_message(chat_id, "<strong>Рассылка началась !</strong>")
        users = get_all_bot_users()
        i = 0
        for user in users:
            try:
                await bot.send_photo(user[0], photo=message.photo[0].file_id, caption=message.caption,
                                     caption_entities=message.entities, reply_markup=message.reply_markup)
                i += 1
                await asyncio.sleep(1)
            except:
                delete_no_active_user(user[0])
                await asyncio.sleep(1)
                continue
        await message.answer('<strong>Рассылка успешно завершенна\n\n</strong>'
                             '<strong>Статистика Рассылки 📊\n</strong>'
                             f'<i>Отправлено : {i}-Пользователям</i>')
    if message.content_type == 'video':
        await bot.send_message(chat_id, "<strong>Рассылка началась !</strong>")
        users = get_all_bot_users()
        i = 0
        for user in users:
            try:
                await bot.send_video(user[0], video=message.video.file_id, caption=message.caption,
                                     caption_entities=message.entities, reply_markup=message.reply_markup)
                i += 1
                await asyncio.sleep(1)
            except:
                delete_no_active_user(user[0])
                await asyncio.sleep(1)
                continue
        await message.answer('<strong>Рассылка успешно завершенна\n\n</strong>'
                             '<strong>Статистика Рассылки 📊\n</strong>'
                             f'<i>Отправлено : {i}-Пользователям</i>')
    if message.content_type == 'animation':
        await bot.send_message(chat_id, "<strong>Рассылка началась !</strong>")
        users = get_all_bot_users()
        i = 0
        for user in users:
            try:
                await bot.send_animation(user[0], animation=message.animation.file_id, caption=message.caption,
                                         caption_entities=message.entities, reply_markup=message.reply_markup)
                await asyncio.sleep(1)
                i += 1
            except:
                delete_no_active_user(user[0])
                await asyncio.sleep(1)
                continue
        await message.answer('<strong>Рассылка успешно завершенна\n\n</strong>'
                             '<strong>Статистика Рассылки 📊\n</strong>'
                             f'<i>Отправлено : {i}-Пользователям</i>')
    if message.content_type == 'voice':
        await bot.send_message(chat_id, "<strong>Рассылка началась !</strong>")
        users = get_all_bot_users()
        i = 0
        for user in users:
            try:
                await bot.send_voice(user[0], voice=message.voice.file_id, caption=message.caption,
                                     caption_entities=message.entities, reply_markup=message.reply_markup)
                i += 1
                await asyncio.sleep(1)
            except:
                delete_no_active_user(user[0])
                await asyncio.sleep(1)
                continue
        await message.answer('<strong>Рассылка успешно завершенна\n\n</strong>'
                             '<strong>Статистика Рассылки 📊\n</strong>'
                             f'<i>Отправлено : {i}-Пользователям</i>')
    if message.content_type == 'audio':
        await bot.send_message(chat_id, "<strong>Рассылка началась !</strong>")
        users = get_all_bot_users()
        i = 0
        for user in users:
            try:
                await bot.send_audio(user[0], audio=message.audio.file_id, caption=message.caption,
                                     caption_entities=message.entities, reply_markup=message.reply_markup)
                i += 1
                await asyncio.sleep(1)
            except:
                delete_no_active_user(user[0])
                await asyncio.sleep(1)
                continue
        await message.answer('<strong>Рассылка успешно завершенна\n\n</strong>'
                             '<strong>Статистика Рассылки 📊\n</strong>'
                             f'<i>Отправлено : {i}-Пользователям</i>')