from aiogram import types
from loader import dp, bot
from keyboards.admin.keyboard import admin_markup


@dp.message_handler(commands=["admin"], state="*")
async def admin_command_handler(message: types.Message, state):
    await state.finish()
    await bot.send_message(message.chat.id, "Админ-панель", reply_markup=admin_markup())
