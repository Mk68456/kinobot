import sqlite3
from html import escape
from aiogram import types
from aiogram.dispatcher import FSMContext
from aiogram.dispatcher.filters.state import State, StatesGroup
from loader import dp
from database import roles
from database.connection import database
from keyboards.admin.roles import buttons, roles_page, role_card
from keyboards.admin.keyboard import admin_markup


class RoleStates(StatesGroup):
    name = State()
    user_id = State()
    assign = State()


async def show_list(message, page=0, user_id=None):
    page = max(0, page)
    rows = roles.list_roles(page * 10, 11)
    title = "Роли пользователей"
    if user_id:
        current = roles.user_role(user_id)
        title = (
            f"Пользователь <code>{user_id}</code>\n"
            f"Текущая роль: {escape(current[1]) if current else 'не назначена'}\nВыберите роль:"
        )
    await message.answer(title, reply_markup=roles_page(rows[:10], page, len(rows) > 10, user_id))


async def show_role(message, role_id):
    role = roles.get_role(role_id)
    if not role:
        await message.answer("Роль уже удалена.")
        return
    await message.answer(
        f"<b>{escape(role[1])}</b>\nПроверка подписки: {'выключена' if role[2] else 'включена'}\n"
        f"Назначена пользователям: {roles.assigned_count(role_id)}",
        reply_markup=role_card(role_id, role[2]),
    )


@dp.callback_query_handler(lambda c: c.data.startswith("role:"), state="*")
async def role_callback(call: types.CallbackQuery, state: FSMContext):
    await call.answer()
    parts = call.data.split(":")
    action = parts[1]
    if action == "exit":
        await state.finish()
        await call.message.answer("Админ-панель", reply_markup=admin_markup())
    elif action in ("home", "page"):
        await state.finish()
        await show_list(call.message, int(parts[2]) if action == "page" else 0)
    elif action == "create":
        await state.finish()
        await RoleStates.name.set()
        await state.update_data(role_id=None)
        await call.message.answer(
            "Введите название новой роли (до 48 символов).\nПроверка подписки у новой роли включена.",
            reply_markup=buttons([("Отмена", "role:home")]),
        )
    elif action == "rename":
        role_id = int(parts[2])
        if not roles.get_role(role_id):
            await call.message.answer("Роль уже удалена.")
            return
        await state.finish()
        await RoleStates.name.set()
        await state.update_data(role_id=role_id)
        await call.message.answer(
            "Введите новое название роли:", reply_markup=buttons([("Отмена", "role:home")])
        )
    elif action == "view":
        await state.finish()
        await show_role(call.message, int(parts[2]))
    elif action == "bypass":
        await state.finish()
        roles.set_bypass(int(parts[2]), parts[3] == "1")
        await show_role(call.message, int(parts[2]))
    elif action == "delete":
        role_id = int(parts[2])
        role = roles.get_role(role_id)
        if not role:
            await call.message.answer("Роль уже удалена.")
            return
        await call.message.answer(
            f"Удалить роль «{escape(role[1])}»?\nУ {roles.assigned_count(role_id)} пользователей "
            "назначение будет снято, проверка подписки станет обязательной.",
            reply_markup=buttons(
                [("Удалить", f"role:confirmdelete:{role_id}"), ("Отмена", f"role:view:{role_id}")]
            ),
        )
    elif action == "confirmdelete":
        await state.finish()
        roles.delete_role(int(parts[2]))
        await show_list(call.message)
    elif action == "user":
        await state.finish()
        await RoleStates.user_id.set()
        await call.message.answer(
            "Введите Telegram ID пользователя (из списка пользователей в статистике):",
            reply_markup=buttons([("Отмена", "role:home")]),
        )
    elif action in ("assign", "pickpage"):
        data = await state.get_data()
        user_id = data.get("role_user_id")
        if await state.get_state() != RoleStates.assign.state or not user_id:
            await call.message.answer(
                "Сначала выберите пользователя.",
                reply_markup=buttons([("Выбрать пользователя", "role:user")]),
            )
            return
        if action == "pickpage":
            await show_list(call.message, int(parts[2]), user_id)
            return
        try:
            roles.assign_role(user_id, int(parts[2]) or None)
        except ValueError as error:
            await call.message.answer(str(error))
            return
        await state.finish()
        await call.message.answer(
            "Роль пользователя обновлена. Настройки применяются сразу.",
            reply_markup=buttons([("Другой пользователь", "role:user"), ("К списку ролей", "role:home")]),
        )


@dp.message_handler(state=RoleStates.name, content_types=types.ContentTypes.ANY)
async def role_name(message: types.Message, state: FSMContext):
    data = await state.get_data()
    try:
        role_id = roles.save_role(message.text or "", data.get("role_id"))
    except (ValueError, sqlite3.IntegrityError) as error:
        text = str(error) if isinstance(error, ValueError) else "Роль с таким названием уже существует."
        await message.answer(text)
        return
    await state.finish()
    await show_role(message, role_id)


@dp.message_handler(state=RoleStates.user_id, content_types=types.ContentTypes.ANY)
async def role_user(message: types.Message, state: FSMContext):
    text = (message.text or "").strip()
    if not text.isascii() or not text.isdigit() or not 0 < int(text) < 2**63:
        await message.answer("Нужен положительный числовой Telegram ID.")
        return
    user_id = int(text)
    if not database.execute("SELECT 1 FROM Users WHERE id=?", (user_id,)).fetchone():
        await message.answer("Пользователь не найден. Сначала он должен отправить боту /start.")
        return
    await RoleStates.assign.set()
    await state.update_data(role_user_id=user_id)
    await show_list(message, user_id=user_id)
