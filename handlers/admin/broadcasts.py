from aiogram import types
from aiogram.dispatcher import FSMContext
from loader import dp
from states.admin_states import Admin_
from database import broadcasts as jobs
from services.broadcasts import broadcasts
from keyboards.admin.keyboard import allow_send_markup, admin_markup
from keyboards.admin.roles import buttons


@dp.message_handler(state=Admin_.send_, content_types=types.ContentTypes.ANY)
async def preview_broadcast(message: types.Message, state: FSMContext):
    await message.copy_to(message.chat.id)
    await state.update_data(source_chat_id=message.chat.id, source_message_id=message.message_id)
    await message.answer("Отправить этот пост всем активным пользователям?", reply_markup=allow_send_markup())


@dp.callback_query_handler(text="send_yes", state=Admin_.send_)
async def confirm_broadcast(call: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    if "source_message_id" not in data:
        await call.answer("Сначала отправьте пост для рассылки.", show_alert=True)
        return
    # Clear state before the first Telegram request: double clicks cannot queue the same draft twice.
    await state.finish()
    job_id = jobs.create_job(data["source_chat_id"], data["source_message_id"], call.from_user.id)
    broadcasts.notify()
    await call.answer("Рассылка добавлена в очередь")
    await call.message.answer(
        f"Рассылка #{job_id} создана.",
        reply_markup=buttons([("Статус", f"broadcast:view:{job_id}"), ("Админ-панель", "role:exit")]),
    )


@dp.callback_query_handler(text="send_no", state=Admin_.send_)
async def cancel_draft(call: types.CallbackQuery, state: FSMContext):
    await state.finish()
    await call.answer("Рассылка отменена")
    await call.message.answer("Админ-панель", reply_markup=admin_markup())


_STATUS = {
    "pending": "в очереди",
    "running": "выполняется",
    "completed": "завершена",
    "cancelled": "отменена",
    "paused": "приостановлена",
}


@dp.callback_query_handler(lambda c: c.data.startswith("broadcast:"), state="*")
async def broadcast_status(call: types.CallbackQuery):
    await call.answer()
    parts = call.data.split(":")
    if parts[1] == "list":
        rows = [
            (f"#{job_id} — {_STATUS.get(status, status)}", f"broadcast:view:{job_id}")
            for job_id, status in jobs.list_jobs()
        ]
        rows.append(("Админ-панель", "role:exit"))
        await call.message.answer("Последние рассылки:", reply_markup=buttons(rows))
        return
    job_id = int(parts[2])
    if parts[1] == "cancel":
        jobs.cancel_job(job_id)
    elif parts[1] == "retry":
        jobs.retry_job(job_id)
        broadcasts.notify()
    job = jobs.get_job(job_id)
    if not job:
        await call.message.answer("Рассылка не найдена.")
        return
    counts = jobs.job_counts(job_id)
    rows = [("Обновить", f"broadcast:view:{job_id}")]
    if job[4] in ("pending", "running", "paused"):
        rows.append(("Остановить", f"broadcast:cancel:{job_id}"))
    if job[4] == "paused" or (job[4] == "completed" and counts.get("failed")):
        rows.append(("Повторить неотправленные", f"broadcast:retry:{job_id}"))
    rows.append(("К списку", "broadcast:list"))
    await call.message.answer(
        f"Рассылка #{job_id}: {_STATUS.get(job[4], job[4])}\n"
        f"Отправлено: {counts.get('sent', 0)}\nОжидают: {counts.get('pending', 0)}\n"
        f"Ошибки: {counts.get('failed', 0)}\nНедоступны: {counts.get('inactive', 0)}",
        reply_markup=buttons(rows),
    )
