from html import escape
from aiogram import types
from loader import dp
from database import progress
from database.movie_queries import get_movie_title_by_numb
from keyboards.admin.roles import buttons

PAGE_SIZE = 10


def _navigation(prefix, page, has_more):
    rows = []
    if page:
        rows.append(("⬅️ Предыдущая", f"{prefix}:{page - 1}"))
    if has_more:
        rows.append(("Следующая ➡️", f"{prefix}:{page + 1}"))
    return rows


async def show_series_stats(message, page=0, user_id=None):
    rows = progress.series_stats(page, PAGE_SIZE, user_id)
    # Counts are current unique user/episode marks, not downloads or card openings.
    lines = [
        f"<b>Просмотренные серии пользователя {user_id}</b>"
        if user_id
        else "<b>Отметки просмотра сериалов</b>"
    ]
    links = []
    for code, title, total, watched, users in rows[:PAGE_SIZE]:
        safe_title = escape(str(title)[:120])
        if user_id is None:
            lines.append(
                f"• {safe_title} (код {code}): {watched} отметок серий от {users} пользователей; в каталоге {total} серий."
            )
        else:
            lines.append(f"• {safe_title} (код {code}): {watched} из {total} серий.")
            links.append(
                (f"{str(title)[:40]} · {watched}/{total}", f"watchstats:episodes:{user_id}:{code}:0")
            )
    if not rows:
        lines.append("Сериалов пока нет.")
    prefix = f"watchstats:user:{user_id}" if user_id else "watchstats:all"
    links.extend(_navigation(prefix, page, len(rows) > PAGE_SIZE))
    if user_id:
        films, episodes = progress.totals(user_id)
        lines.insert(1, f"Всего отмечено: {episodes} серий, {films} фильмов.")
        links.append(("Просмотренные фильмы", f"watchstats:films:{user_id}:0"))
    else:
        lines.insert(
            1,
            "Считаются личные отметки. Повторное нажатие не увеличивает счётчик; снятие отметки уменьшает его.",
        )
    links.append(("К статистике", "stat_menu_back"))
    await message.answer("\n\n".join(lines), reply_markup=buttons(links))


@dp.callback_query_handler(lambda call: call.data.startswith("watchstats:"), state="*")
async def watch_stats_handler(call: types.CallbackQuery):
    parts = call.data.split(":")
    expected = {"all": 3, "user": 4, "episodes": 5, "films": 4}
    if len(parts) != expected.get(parts[1], 0) or any(
        not value.isascii() or not value.isdigit() or len(value) > 18 for value in parts[2:]
    ):
        await call.answer("Кнопка недействительна.", show_alert=True)
        return
    await call.answer()
    page = int(parts[-1])
    if page > 1000000:
        return
    if parts[1] == "all":
        await show_series_stats(call.message, page)
        return
    user_id = int(parts[2])
    if parts[1] == "user":
        await show_series_stats(call.message, page, user_id)
        return
    if parts[1] == "films":
        rows = progress.user_films(user_id, page, PAGE_SIZE)
        lines = [f"<b>Просмотренные фильмы пользователя {user_id}</b>"]
        for code, title, date in rows[:PAGE_SIZE]:
            lines.append(f"✅ {escape(str(title)[:160])} (код {code}) — {date[:10]}")
        prefix = f"watchstats:films:{user_id}"
    else:
        code = int(parts[3])
        rows = progress.user_episodes(user_id, code, page, PAGE_SIZE)
        title = get_movie_title_by_numb(code)
        lines = [f"<b>Пользователь {user_id} · {escape(str(title or 'Сериал удалён')[:150])}</b>"]
        for season, episode, date in rows[:PAGE_SIZE]:
            lines.append(f"✅ {escape(str(season)[:90])} → {escape(str(episode)[:90])} — {date[:10]}")
        prefix = f"watchstats:episodes:{user_id}:{code}"
    if not rows:
        lines.append("Нет отмеченных просмотров.")
    links = _navigation(prefix, page, len(rows) > PAGE_SIZE)
    links.append(("К просмотрам пользователя", f"watchstats:user:{user_id}:0"))
    await call.message.answer("\n\n".join(lines), reply_markup=buttons(links))
