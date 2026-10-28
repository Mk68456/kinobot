import re
import secrets
import json
from html import escape
from aiogram import types
from loader import dp
from services.tmdb import search, details, series_structure
from services.request_voices import lookup as lookup_voices
from services.subscriptions import check_subscription
from services.welcome import send_welcome
from database import content_requests as requests
from database.search import get_movies_by_title, get_movie_from_numb
from handlers.users.find_movie import _send_movie_card


def markup(rows):
    result = types.InlineKeyboardMarkup(row_width=1)
    for text, data in rows:
        result.add(types.InlineKeyboardButton(text, callback_data=data))
    result.add(types.InlineKeyboardButton("↩️ В главное меню", callback_data="catalog:home"))
    return result


async def allowed(event, state):
    if await check_subscription(event.from_user.id):
        return True
    await state.finish()
    await send_welcome(dp.bot, event.from_user.id, subscribed=False)
    return False


async def candidates(message, state, title):
    data = await state.get_data()
    match = re.fullmatch(r"(.*?)\s*\(?((?:19|20)\d{2})\)?", title)
    from database.age_access import preferences
    results = (await search(match[1].strip() if match else title, data["request_kind"])) \
        if preferences(message.chat.id)['max_age'] == 18 else []
    if match:
        results = [r for r in results if str(r.get("year")) == match[2]]
    choices = [
        {**r, "title": r["title"] + (f" ({r['year']})" if r.get("year") else "")}
        for r in results
    ]
    choices.append({"title": title, "tmdb_id": None})
    await state.update_data(request_choices=choices)
    await state.set_state("Request:pick")
    await message.answer(
        "Выберите произведение или оставьте своё название:",
        reply_markup=markup(
            [
                (c["title"] if i < len(choices) - 1 else "Оставить: " + c["title"], "request:pick:" + str(i))
                for i, c in enumerate(choices)
            ]
        ),
    )


async def send_missing_cards(message, title, kind):
    """Show metadata before a user creates a request from an empty catalog search."""
    from database.age_access import preferences
    if preferences(message.chat.id)['max_age'] < 18:
        return False
    results = await search(title, kind)
    if not results:
        return False
    for candidate in results:
        label = candidate["title"] + (f" ({candidate['year']})" if candidate.get("year") else "")
        current = requests.find(label, kind, 0, candidate.get("tmdb_id"))
        button = "✅ Запрос уже отправлен" if current else "📩 Запросить добавление / озвучку"
        keyboard = types.InlineKeyboardMarkup().add(types.InlineKeyboardButton(
            button, callback_data=f"request:quick:{kind}:{candidate['tmdb_id']}"
        ))
        from services.instant import enabled
        if enabled(message.chat.id):
            keyboard.row(types.InlineKeyboardButton('⚡ Получить сразу',
                         callback_data=f"instant:quick:{kind}:{candidate['tmdb_id']}"))
        caption = f"<strong>{escape(label)}</strong>\n\n{escape(candidate.get('overview') or 'Описание пока не добавлено.')}"
        try:
            if candidate.get("poster_url"):
                await message.answer_photo(candidate["poster_url"], caption=caption[:1024], reply_markup=keyboard)
            else:
                await message.answer(caption, reply_markup=keyboard)
        except Exception:
            await message.answer(caption, reply_markup=keyboard)
    return True


@dp.callback_query_handler(lambda c: c.data.startswith(("request:quick:", "instant:quick:")), state="*")
async def quick_request(call, state):
    await call.answer()
    if not await allowed(call, state):
        return
    if call.data.startswith('instant:'):
        from services.instant import enabled
        from database.age_access import preferences
        if not enabled(call.from_user.id) or preferences(call.from_user.id)['max_age'] != 18:
            await call.message.answer('Быстрая выдача сейчас недоступна при ваших настройках.')
            return
    parts = call.data.split(":")
    if len(parts) != 4 or parts[2] not in ("movie", "series") or not parts[3].isdigit():
        return
    kind, tmdb_id = parts[2], int(parts[3])
    card = await details(tmdb_id, kind)
    if not card:
        await call.message.answer("Не удалось обновить данные произведения. Попробуйте ещё раз позже.")
        return
    title = card["title"] + (f" ({card['year']})" if card.get("year") else "")
    await state.finish()
    await state.update_data(request_kind=kind, request_selected={**card, "title": title},
                            request_season=0, request_episode=0, request_voice="")
    if kind == "series":
        await ask_season(call.message, state)
    else:
        await ask_voice(call.message, state)


@dp.callback_query_handler(lambda c: c.data in ("request:new", "request:search"), state="*")
async def begin(call, state):
    await call.answer()
    if not await allowed(call, state):
        return
    old = await state.get_data()
    await state.finish()
    if (
        call.data == "request:search"
        and old.get("missing_query")
        and old.get("missing_kind") in ("movie", "series")
    ):
        await state.update_data(request_kind=old["missing_kind"])
        await candidates(call.message, state, old["missing_query"])
        return
    await state.set_state("Request:type")
    await call.message.answer(
        "Что хотите увидеть в боте?",
        reply_markup=markup([("🎬 Фильм", "request:type:movie"), ("📺 Сериал", "request:type:series")]),
    )


@dp.callback_query_handler(lambda c: c.data.startswith("request:type:"), state="Request:type")
async def choose_type(call, state):
    kind = call.data.rsplit(":", 1)[-1]
    if kind not in ("movie", "series"):
        return
    await call.answer()
    await state.update_data(request_kind=kind)
    await state.set_state("Request:title")
    await call.message.answer(
        "Введите название и год, например: Интерстеллар (2014).", reply_markup=markup([])
    )


@dp.message_handler(state="Request:title", content_types=types.ContentTypes.TEXT)
async def title_input(message, state):
    if not await allowed(message, state):
        return
    title = message.text.strip()
    if not 1 <= len(title) <= 200:
        await message.answer("Название должно содержать от 1 до 200 символов.")
        return
    await candidates(message, state, title)


async def confirmation(message, state):
    data = await state.get_data()
    selected = data["request_selected"]
    kind = data["request_kind"]
    season = data.get("request_season", 0)
    matches = get_movies_by_title(selected["title"], kind, user_id=message.chat.id)
    for title, number in matches:
        if title.casefold() != selected["title"].casefold():
            continue
        target = {**data, "content_type": kind, "season": season,
                  "episode": data.get("request_episode", 0), "voice": data.get("request_voice", ""),
                  "expected_episodes": data.get("request_expected", "")}
        if not requests.target_available(target, number):
            continue
        await state.finish()
        await message.answer("Уже есть в каталоге:")
        await _send_movie_card(message.chat.id, number, get_movie_from_numb(number), user_id=message.chat.id)
        return
    existing = requests.find(selected["title"], kind, season, selected["tmdb_id"],
                             data.get("request_episode", 0), data.get("request_voice", ""))
    await state.set_state("Request:confirm")
    text = selected["title"] + requests.target_label(target_data(data))
    text += "\n" + data.get("request_verification", "")
    text += "\n" + requests.voice_evidence_label({"voice_evidence": json.dumps(
        data.get("request_voice_evidence", []), ensure_ascii=False)})
    if existing:
        text += " — " + requests.STATUSES[existing["status"]]
    actions = [("Тоже хочу посмотреть" if existing else "Отправить заявку", "request:confirm")]
    from services.instant import enabled
    from database.age_access import preferences
    if (enabled(message.chat.id) and preferences(message.chat.id)['max_age'] == 18
            and (kind == 'movie' or season and data.get('request_episode'))):
        actions.insert(0, ('⚡ Получить сразу', 'instant:start'))
    await message.answer(
        text,
        parse_mode="",
        reply_markup=markup(
            actions
        ),
    )


@dp.callback_query_handler(lambda c: c.data.startswith("request:pick:"), state="Request:pick")
async def pick(call, state):
    await call.answer()
    if not await allowed(call, state):
        return
    data = await state.get_data()
    try:
        index = int(call.data.rsplit(":", 1)[-1])
        if index < 0:
            return
        selected = data["request_choices"][index]
    except (ValueError, IndexError, KeyError):
        return
    await state.update_data(request_selected=selected, request_season=0, request_episode=0,
                            request_voice="", request_expected="", request_verification="")
    if data["request_kind"] == "series":
        await ask_season(call.message, state)
    else:
        await ask_voice(call.message, state)


@dp.message_handler(state="Request:season", content_types=types.ContentTypes.TEXT)
async def season_input(message, state):
    if not await allowed(message, state):
        return
    if not message.text.isascii() or not message.text.isdigit() or not 0 <= int(message.text) <= 999:
        await message.answer("Введите номер от 1 до 999 или 0 для всего сериала.")
        return
    await state.update_data(request_season=int(message.text), request_episode=0, request_expected="")
    if int(message.text):
        await state.set_state("Request:episode")
        await message.answer("Введите номер серии или 0 для всего сезона.", reply_markup=markup([]))
    else:
        await ask_voice(message, state)


@dp.callback_query_handler(text="request:confirm", state="Request:confirm")
async def submit(call, state):
    await call.answer()
    if not await allowed(call, state):
        return
    data = await state.get_data()
    selected = data["request_selected"]
    if not await verify_target(call.message, state):
        return
    data = await state.get_data()
    result = requests.vote(
        call.from_user.id,
        selected["title"],
        data["request_kind"],
        data.get("request_season", 0),
        selected["tmdb_id"],
        year=selected.get("year"), overview=selected.get("overview"), poster_url=selected.get("poster_url"),
        episode=data.get("request_episode", 0), voice=data.get("request_voice", ""),
        verification=data.get("request_verification", ""), expected_episodes=data.get("request_expected", ""),
        voice_evidence=data.get("request_voice_evidence", []),
    )
    await state.finish()
    text = (
        "Заявка учтена. Повторные нажатия не добавляют голосов. Сообщим, когда произведение будет добавлено."
    )
    if result["status"] == "added":
        text = "Произведение уже добавлено. Код: " + str(result["movie_number"])
    elif result["status"] == "rejected":
        text = "Ваш голос учтён. Сейчас заявка отклонена администратором."
    await call.message.answer(text, reply_markup=markup([]))


def target_data(data):
    return {"season": data.get("request_season", 0), "episode": data.get("request_episode", 0),
            "voice": data.get("request_voice", "")}


async def ask_season(message, state):
    await state.set_state("Request:season")
    await message.answer("Введите номер сезона или 0 для всего сериала (вышедшие сезоны).",
                         reply_markup=markup([]))


async def ask_voice(message, state):
    data = await state.get_data()
    token = secrets.token_hex(4)
    await state.update_data(request_voice="", request_voice_evidence=[], request_voice_choices=[],
                            request_voice_token=token)
    await state.set_state("Request:voice")
    result = await lookup_voices(data['request_selected'].get('tmdb_id'), data['request_kind'])
    # Do not replace a newer dialog when an external request finishes late.
    current = await state.get_data()
    if await state.get_state() != "Request:voice" or current.get('request_voice_token') != token:
        return
    await state.update_data(request_voice_choices=result['voices'])
    if result['voices']:
        await voice_options(message, state)
        return
    note = ("Источник озвучек сейчас недоступен. " if result['status'] == 'unavailable' else
            "Для этого произведения список озвучек не получен. ")
    await message.answer(note + "Напишите студию (до 80 символов) или выберите любую озвучку.",
                         reply_markup=markup([("Любая озвучка", "request:voice:any")]))


async def voice_options(message, state, offset=0):
    data = await state.get_data()
    choices, token = data.get('request_voice_choices', []), data['request_voice_token']
    rows = [(c['name'], f"request:voice:pick:{token}:{i}")
            for i, c in enumerate(choices) if offset <= i < offset + 10]
    if offset:
        rows.append(("⬅️ Назад", f"request:voices:{token}:{max(0, offset-10)}"))
    if offset + 10 < len(choices):
        rows.append(("Ещё озвучки ➡️", f"request:voices:{token}:{offset+10}"))
    rows.extend([("✏️ Указать другую", "request:voice:manual"), ("Любая озвучка", "request:voice:any")])
    await message.answer("Выберите озвучку или напишите название студии.\n"
                         "Эти озвучки найдены у произведения через Kinobox. "
                         "Наличие нужной серии или полного сезона в выбранной озвучке требует отдельной проверки.",
                         reply_markup=markup(rows))


@dp.callback_query_handler(lambda c: c.data.startswith("request:voices:"), state="Request:voice")
async def voice_page(call, state):
    await call.answer()
    if not await allowed(call, state):
        return
    data = await state.get_data()
    parts = call.data.split(':')
    if len(parts) != 4 or parts[2] != data.get('request_voice_token') or not parts[3].isdigit():
        return
    offset = int(parts[3])
    if offset % 10 or not 0 <= offset < len(data.get('request_voice_choices', [])):
        return
    await voice_options(call.message, state, offset)


@dp.callback_query_handler(text="request:voice:manual", state="Request:voice")
async def manual_voice(call, state):
    await call.answer()
    if not await allowed(call, state):
        return
    await call.message.answer("Напишите название студии (до 80 символов) или — для любой озвучки.",
                              reply_markup=markup([("Любая озвучка", "request:voice:any")]))


@dp.callback_query_handler(lambda c: c.data.startswith("request:voice:pick:"), state="Request:voice")
async def pick_voice(call, state):
    await call.answer()
    if not await allowed(call, state):
        return
    data = await state.get_data()
    parts = call.data.split(':')
    if len(parts) != 5 or parts[3] != data.get('request_voice_token') or not parts[4].isdigit():
        return
    index = int(parts[4])
    if not 0 <= index < len(data.get('request_voice_choices', [])):
        return
    selected = data['request_voice_choices'][index]
    await state.update_data(request_voice=selected['name'], request_voice_evidence=selected['evidence'])
    if await verify_target(call.message, state):
        await confirmation(call.message, state)


@dp.message_handler(state="Request:episode", content_types=types.ContentTypes.TEXT)
async def episode_input(message, state):
    if not await allowed(message, state):
        return
    value = message.text.strip()
    if not value.isascii() or not value.isdigit() or not 0 <= int(value) <= 9999:
        await message.answer("Введите номер серии от 1 до 9999 или 0 для всего сезона.")
        return
    await state.update_data(request_episode=int(value))
    if await verify_target(message, state):
        await ask_voice(message, state)


async def verify_target(message, state):
    from datetime import datetime, timedelta, timezone
    data = await state.get_data()
    if data['request_kind'] != 'series':
        return True
    target = target_data(data)
    structure = await series_structure(data['request_selected']['tmdb_id'])
    note = "Данные о выпуске недоступны: требуется ручная проверка администратором."
    expected = ""
    if structure is not None:
        note = {"Ended": "Сериал завершён.", "Canceled": "Сериал отменён."}.get(
            structure.get('status'), "Статус сериала: продолжается или уточняется.")
        season = target['season']
        if season and not any(s.get('season_number') == season for s in structure.get('seasons', [])):
            await message.answer(note + " Такой сезон не найден в TMDB. Введите другой номер сезона.")
            await state.set_state("Request:season")
            return False
        if not season:
            parts = []
            today = datetime.now(timezone(timedelta(hours=3))).date().isoformat()
            for summary in structure.get('seasons', []):
                sn = summary.get('season_number', 0)
                if not sn or not summary.get('air_date') or summary['air_date'] > today:
                    continue
                info = await series_structure(data['request_selected']['tmdb_id'], sn)
                if info is None:
                    note += " Состав сезонов недоступен: требуется ручная проверка."
                    parts = []
                    break
                parts.extend(f"{sn}:{e['episode_number']}" for e in info.get('episodes', [])
                             if e.get('air_date') and e['air_date'] <= today)
            expected = ','.join(parts)
            if not expected:
                note += " Нет подтверждённого списка вышедших серий: ручная проверка."
        if season:
            info = await series_structure(data['request_selected']['tmdb_id'], season)
            if info is None:
                note += " Состав сезона недоступен: требуется ручная проверка."
            else:
                episodes = info.get('episodes') or []
                if target['episode']:
                    episodes = [e for e in episodes if e.get('episode_number') == target['episode']]
                today = datetime.now(timezone(timedelta(hours=3))).date().isoformat()
                if not episodes or any(not e.get('air_date') or e['air_date'] > today for e in episodes):
                    await message.answer("Серия или полный сезон ещё не вышли, дата неизвестна либо номер не найден. "
                                         "Введите номер уже вышедшей серии или 0 для полностью вышедшего сезона.")
                    await state.set_state("Request:episode")
                    return False
                expected = ','.join(str(e['episode_number']) for e in episodes)
                note += " Запрошенные серии вышли по данным TMDB."
    await state.update_data(request_verification=note, request_expected=expected)
    return True


@dp.callback_query_handler(text="request:voice:any", state="Request:voice")
async def any_voice(call, state):
    await call.answer()
    if not await allowed(call, state):
        return
    await state.update_data(request_voice="", request_voice_evidence=[])
    if await verify_target(call.message, state):
        await confirmation(call.message, state)


@dp.message_handler(state="Request:voice", content_types=types.ContentTypes.TEXT)
async def voice_input(message, state):
    if not await allowed(message, state):
        return
    value = ' '.join(message.text.split())
    if not 1 <= len(value) <= 80:
        await message.answer("Название студии должно содержать от 1 до 80 символов.")
        return
    await state.update_data(request_voice="" if value in ('-', '—') else value, request_voice_evidence=[])
    if await verify_target(message, state):
        await confirmation(message, state)


@dp.callback_query_handler(lambda c: c.data.startswith("request:catalog:"), state="*")
async def catalog_request(call, state):
    await call.answer()
    if not await allowed(call, state):
        return
    from database.age_access import allowed as age_allowed
    try:
        number = int(call.data.rsplit(':', 1)[-1])
    except ValueError:
        return
    info = get_movie_from_numb(number)
    if not info or not age_allowed(call.from_user.id, info):
        return
    await state.finish()
    await state.update_data(request_kind=info['content_type'], request_selected={
        'title': info['movie_title'], 'tmdb_id': None}, request_season=0, request_episode=0,
        request_voice="", request_expected="", request_verification="")
    # Resolve metadata through the same user-confirmed search used by new requests.
    await candidates(call.message, state, info['movie_title'])
