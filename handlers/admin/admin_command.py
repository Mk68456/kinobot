from aiogram import types
from data.config import ADMIN
from loader import dp,bot
from keyboards.admin.keyboard import admin_markup

@dp.message_handler(commands=['admin'],chat_id=ADMIN)
async def admin_command_handler(message:types.Message):
    await bot.send_message(message.chat.id, f"Админ-панель",reply_markup=admin_markup())
