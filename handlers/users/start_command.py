from aiogram import types
from loader import dp,bot
from aiogram.dispatcher.filters import CommandStart
from database.users.add_user import add_user
from keyboards.inline.sub_keyboard import sub_markup

@dp.message_handler(CommandStart())
async def start_command_handler(message:types.Message):
    add_user(message.chat.id)
    await bot.send_message(message.chat.id, "ЧТО МОЖЕТ ДЕЛАТЬ ЭТОТ БОТ?\n\n"
"Привет, это 🍿КИНОПОИСК - БОТ | Поиск🔍\n\n"
"Чтобы узнать название фильмов из ЮТУБ | ТИК ТОК. Нажмите на все ссылки снизу👇🏻 и ПОДПИШИТЕСЬ НА ВСЕ КАНАЛЫ\n\n"
                                            "(подписка на каналы занимает 5 секунд)\n\n"
"После подписки нажмите на кнопку:\n"
"✔️ Я ПОДПИСАЛСЯ ✔️ 👇🏻 и там уже узнаете название фильмов♥️",reply_markup=sub_markup())
