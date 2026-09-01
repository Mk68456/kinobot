from aiogram import types
from loader import dp,bot
from aiogram.dispatcher.filters import CommandStart
from database.users.add_user import add_user
from keyboards.inline.sub_keyboard import sub_markup
from keyboards.users.keyboard import find_movie_markup
from .check_user_sub import check_user_sub

@dp.message_handler(CommandStart())
async def start_command_handler(message:types.Message):
    add_user(message.chat.id, username=message.from_user.username)
    check = await check_user_sub(message)
    if check != False:
        await bot.send_message(message.chat.id, "Нажмите на кнопку ниже, чтобы найти фильм 👇🏻",
                               reply_markup=find_movie_markup())
    else:
        await bot.send_message(message.chat.id, "ЧТО МОЖЕТ ДЕЛАТЬ ЭТОТ БОТ?\n\n"
"Привет, это 🍿KinoTime - Бот для скачивания и просмотра фильмов на твоём телефоне\n\n"
"Чтобы скачать или посмотреть фильм из Youtube или ТИК ТОК, нажмите на все ссылки снизу👇🏻 и ПОДПИШИТЕСЬ НА ВСЕ КАНАЛЫ\n\n"
                                            "(подписка на каналы занимает 5 секунд)\n\n"
"После подписки нажмите на кнопку:\n"
"✔️ Я ПОДПИСАЛСЯ ✔️ 👇🏻 и там уже узнаете название фильмов♥️",reply_markup=sub_markup())
