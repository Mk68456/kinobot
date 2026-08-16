from aiogram import types
from loader import dp,bot
from database.users.select import get_movie_from_numb
from .check_user_sub import check_user_sub
from keyboards.inline.sub_keyboard import sub_markup

@dp.message_handler(lambda m:m.text.isdigit())
async def find_movie_handler(message:types.Message):
    chat_id = message.chat.id
    check = await check_user_sub(message)
    if check != False:
        movie_info = get_movie_from_numb(message.text)
        if movie_info is None:
            await message.answer(f"<strong>Фильм с таким кодом не найден : {message.text} !</strong>")
        else:
            await message.reply(f"<strong>Код : {message.text}</strong>\n\n"
                                f"<strong>{movie_info}</strong>",disable_web_page_preview=False)
    else:
        await bot.send_message(chat_id, "Подпишитесь на каналы чтобы пользоваться ботом :",reply_markup=sub_markup())
