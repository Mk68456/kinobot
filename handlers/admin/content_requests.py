from aiogram import types
from loader import dp, bot
from database import content_requests as requests
from database.search import get_movie_from_numb


def admin_markup(rows):
    result = types.InlineKeyboardMarkup(row_width=1)
    for text, data in rows:
        result.add(types.InlineKeyboardButton(text, callback_data=data))
    result.add(types.InlineKeyboardButton("↩️ В админку", callback_data="admin_return"))
    return result


@dp.callback_query_handler(lambda c: c.data.startswith("requests:list:"), state="*")
async def list_requests(call, state):
    await call.answer()
    await state.finish()
    try:
        offset = max(0, int(call.data.rsplit(":", 1)[-1]))
    except ValueError:
        return
    rows = requests.page(offset)
    buttons = [
        (
            f"{count} 👤 · {'📺' if kind == 'series' else '🎬'} {title}"
            + requests.target_label(requests.get(id))
            + " · "
            + requests.STATUSES[status],
            f"requests:show:{id}",
        )
        for id, title, kind, season, status, count in rows[:10]
    ]
    if offset:
        buttons.append(("⬅️ Назад", f"requests:list:{max(0, offset - 10)}"))
    if len(rows) > 10:
        buttons.append(("Далее ➡️", f"requests:list:{offset + 10}"))
    await call.message.answer(
        "Заявки пользователей · по статусу и числу желающих" if rows else "Заявок пока нет.",
        reply_markup=admin_markup(buttons),
    )


@dp.callback_query_handler(lambda c: c.data.startswith("requests:show:"), state="*")
async def show_request(call, state):
    await call.answer()
    await state.finish()
    try:
        request = requests.get(int(call.data.rsplit(":", 1)[-1]))
    except ValueError:
        return
    if not request:
        return
    id = request["id"]
    rows = (
        [
            ("В работу", f"requests:status:{id}:planned"),
            ("Отклонить", f"requests:status:{id}:rejected"),
            ("Вернуть в новые", f"requests:status:{id}:new"),
            ("Добавлен — указать код и уведомить", f"requests:complete:{id}"),
        ]
        if request["status"] != "added"
        else []
    )
    rows.append(("К заявкам", "requests:list:0"))
    await call.message.answer(
        request["title"]
        + (" · сериал" if request["content_type"] == "series" else " · фильм")
        + requests.target_label(request)
        + "\n"
        + requests.STATUSES[request["status"]] + "\n" + request.get("verification", "")
        + "\n" + requests.voice_evidence_label(request),
        parse_mode="",
        reply_markup=admin_markup(rows),
    )


@dp.callback_query_handler(lambda c: c.data.startswith("requests:status:"), state="*")
async def status_request(call, state):
    parts = call.data.split(":")
    try:
        requests.set_status(int(parts[2]), parts[3])
    except (ValueError, IndexError):
        await call.answer("Неверный статус.", show_alert=True)
        return
    await call.answer("Статус сохранён")


@dp.callback_query_handler(lambda c: c.data.startswith("requests:complete:"), state="*")
async def begin_completion(call, state):
    await call.answer()
    try:
        request = requests.get(int(call.data.rsplit(":", 1)[-1]))
    except ValueError:
        return
    if not request or request["status"] == "added":
        return
    await state.finish()
    await state.update_data(request_complete_id=request["id"])
    await state.set_state("AdminRequest:code")
    await call.message.answer(
        "Введите код добавленного фильма или сериала. Затем проверьте карточку перед уведомлением.",
        reply_markup=admin_markup([]),
    )


@dp.message_handler(state="AdminRequest:code", content_types=types.ContentTypes.TEXT)
async def completion_code(message, state):
    if not message.text.isascii() or not message.text.isdigit() or len(message.text) > 15:
        await message.answer("Введите числовой код из каталога.")
        return
    number = int(message.text)
    info = get_movie_from_numb(number)
    data = await state.get_data()
    request = requests.get(data["request_complete_id"])
    if not info or not request or info["content_type"] != request["content_type"]:
        await message.answer("Код не найден или тип произведения не совпадает.")
        return
    if not requests.target_available(request, number, manual=True):
        await message.answer("Запрошенные серии или озвучка ещё не найдены в каталоге.")
        return
    await state.update_data(request_movie_number=number)
    await state.set_state("AdminRequest:confirm")
    await message.answer(
        f"Заявка: {request['title']}{requests.target_label(request)}\n{request.get('verification', '')}\nДобавлено: {info['movie_title']} · код {number}\nУведомить поддержавших?",
        parse_mode="",
        reply_markup=admin_markup([("Подтвердить и уведомить", "requests:notify")]),
    )


@dp.callback_query_handler(text="requests:notify", state="AdminRequest:confirm")
async def notify_request(call, state):
    await call.answer()
    data = await state.get_data()
    request = requests.get(data["request_complete_id"])
    if request["notification_job"]:
        await state.finish()
        await call.message.answer("Уведомление уже поставлено в очередь.")
        return
    number = data["request_movie_number"]
    if not requests.target_available(request, number, manual=True):
        await call.message.answer("Запрошенные серии или озвучка ещё не найдены в каталоге.")
        return
    username = (await bot.get_me()).username
    keyboard = types.InlineKeyboardMarkup().add(
        types.InlineKeyboardButton("Открыть в боте", url=f"https://t.me/{username}?start=movie_{number}")
    )
    source = await bot.send_message(
        call.message.chat.id,
        "По вашей заявке добавлено: "
        + request["title"]
        + requests.target_label(request),
        parse_mode="",
        reply_markup=keyboard,
    )
    try:
        job = requests.complete(request["id"], number, call.from_user.id, source.chat.id, source.message_id)
    except ValueError as error:
        await call.message.answer(str(error))
        return
    from services.broadcasts import broadcasts

    broadcasts.notify()
    await state.finish()
    await call.message.answer(
        f"Добавление подтверждено. Уведомления в очереди №{job}; статус доступен в заданиях рассылки.",
        reply_markup=admin_markup([]),
    )
