from aiogram import types
from loader import dp,bot
from aiogram.dispatcher import FSMContext
from database.admin.add_movie import add_new_movie
from states.admin_states import Admin_
from keyboards.admin.keyboard import admin_markup
from database.admin.channels_func import delete_no_active_user
from database.admin.select import get_all_bot_users
import asyncio


@dp.message_handler(state=Admin_.add_new_cod)
async def add_new_cod_handler(message:types.Message,state:FSMContext):
    await state.finish()
    add_new_movie(message.text)
    await bot.send_message(message.chat.id, "Успешно добавлено !",reply_markup=admin_markup())

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