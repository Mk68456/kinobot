from aiogram import types
from loader import dp, bot
from aiogram.dispatcher import FSMContext
from database.movies import delete_movie_by_numb
from states.admin_states import Admin_
from keyboards.admin.keyboard import admin_markup, allow_movie_delete_markup
from database.people import get_all_bot_users
from database.movie_queries import get_movie_title_by_numb
import re


@dp.message_handler(state=Admin_.delete_movie)
async def delete_movie_func(message: types.Message, state: FSMContext):
    if message.text == "Назад":
        await state.finish()
        await message.delete()
        await bot.send_message(
            message.chat.id, "Удаление фильма было отменено", reply_markup=types.ReplyKeyboardRemove()
        )
        await bot.send_message(
            message.chat.id,
            f"Админ-панель\n\nСтатистика : {int(len(get_all_bot_users()))}",
            reply_markup=admin_markup(),
        )
        return
    match = re.match(r"^\D*(\d+)\s*-\s*", (message.text or "").strip())
    numb_part = match.group(1) if match else ""
    if not numb_part.isdigit():
        await bot.send_message(message.chat.id, "Пожалуйста, выберите фильм кнопкой из списка.")
        return
    movie_title = get_movie_title_by_numb(int(numb_part))
    if movie_title is None:
        await bot.send_message(message.chat.id, "Такой фильм не найден, выберите из списка.")
        return
    await state.update_data(movie_number=int(numb_part))
    await bot.send_message(
        message.chat.id,
        f"Удалить фильм «{movie_title}» (код {numb_part}) ?",
        reply_markup=allow_movie_delete_markup(),
    )


@dp.callback_query_handler(lambda call: call.data.startswith("movdel_"), state=Admin_.delete_movie)
async def confirm_delete_movie_handler(call: types.CallbackQuery, state: FSMContext):
    if call.data == "movdel_yes":
        data = await state.get_data()
        delete_movie_by_numb(data.get("movie_number"))
        await call.message.delete()
        await state.finish()
        await bot.send_message(
            call.message.chat.id, "Фильм успешно удалён !", reply_markup=types.ReplyKeyboardRemove()
        )
        await bot.send_message(call.message.chat.id, "Админ-панель", reply_markup=admin_markup())
    else:
        await call.message.delete()
        await state.finish()
        await bot.send_message(
            call.message.chat.id, "Удаление фильма было отменено", reply_markup=types.ReplyKeyboardRemove()
        )
        await bot.send_message(call.message.chat.id, "Админ-панель", reply_markup=admin_markup())
