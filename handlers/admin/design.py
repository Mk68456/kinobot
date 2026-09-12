from aiogram import types
from aiogram.dispatcher.filters.state import State, StatesGroup
from loader import dp, bot
from database.design import set_design, reset_text
from services.welcome import send_welcome
from keyboards.admin.roles import buttons
from keyboards.admin.keyboard import admin_markup


class DesignEdit(StatesGroup):
    photo = State()
    text = State()


def menu():
    return buttons(
        [
            ("🖼 Изменить фото", "design:photo"),
            ("✏️ Изменить текст", "design:text"),
            ("👀 Предпросмотр", "design:preview"),
            ("Убрать фото", "design:remove"),
            ("Вернуть стандартный текст", "design:reset"),
            ("Карточка до /start", "design:about"),
            ("⬅️ В админку", "design:exit"),
        ]
    )


@dp.callback_query_handler(text_startswith="design:", state="*")
async def design_action(call: types.CallbackQuery, state):
    action = call.data.split(":")[1]
    await call.answer()
    await state.finish()
    if action in ("photo", "text"):
        await (DesignEdit.photo if action == "photo" else DesignEdit.text).set()
        prompt = (
            "Отправьте новую обложку как фото (не документ). Лучше горизонтальное изображение 16:9."
            if action == "photo"
            else "Отправьте новый текст приветствия, до 800 символов. Обычный текст и эмодзи, без HTML."
        )
        await call.message.answer(prompt, reply_markup=buttons([("Отмена", "design:home")]))
        return
    if action in ("remove", "reset"):
        await call.message.answer(
            "Подтвердить изменение?",
            reply_markup=buttons([("Да", f"design:confirm_{action}"), ("Отмена", "design:home")]),
        )
        return
    if action == "confirm_remove":
        set_design("welcome_photo", "")
    elif action == "confirm_reset":
        reset_text()
    elif action == "preview":
        await send_welcome(bot, call.message.chat.id)
    elif action == "about":
        await call.message.answer(
            "Карточка «Что может делать этот бот?» до /start настраивается владельцем в @BotFather: "
            "/mybots → выберите бота → Edit Bot → Edit Description Picture (фото) и Edit Description (текст).\n\n"
            "Здесь меняется приветствие, которое бот отправляет после /start.",
            parse_mode="",
        )
    elif action == "exit":
        await call.message.answer("Админ-панель", reply_markup=admin_markup())
        return
    await call.message.answer(
        "🎨 Дизайн приветствия /start\nФото и текст сохраняются в базе и применяются к новым сообщениям.",
        reply_markup=menu(),
    )


@dp.message_handler(state=DesignEdit.photo, content_types=["photo"])
async def receive_design_photo(message: types.Message, state):
    set_design("welcome_photo", message.photo[-1].file_id)
    await state.finish()
    await send_welcome(bot, message.chat.id)
    await message.answer("Обложка сохранена ✅", reply_markup=menu())


@dp.message_handler(state=DesignEdit.text, content_types=["text"])
async def receive_design_text(message: types.Message, state):
    try:
        set_design("welcome_text", message.text.strip())
    except ValueError as error:
        await message.answer(str(error), reply_markup=buttons([("Отмена", "design:home")]))
        return
    await state.finish()
    await send_welcome(bot, message.chat.id)
    await message.answer("Текст сохранён ✅", reply_markup=menu())


@dp.message_handler(state=[DesignEdit.photo, DesignEdit.text], content_types=types.ContentTypes.ANY)
async def wrong_design_input(message: types.Message):
    await message.answer(
        "Отправьте фото для обложки или обычный текст для приветствия — в зависимости от выбранного действия.",
        reply_markup=buttons([("Отмена", "design:home")]),
    )
