from aiogram import types
from loader import dp,bot
from aiogram.dispatcher import FSMContext
from database.admin.select import get_all_bot_users
from database.admin.stats import (get_users_list,get_users_total_count,get_top_watched_movies,
                                   get_top_searches,get_user_watch_history,get_user_search_history,
                                   get_new_users_count,get_watches_count,get_searches_count)
from keyboards.admin.keyboard import stats_menu_markup,stats_back_markup,users_page_markup
from states.admin_states import Admin_

USERS_PER_PAGE = 15


async def _render_stats_menu(chat_id:int):
    total = get_users_total_count() or len(get_all_bot_users())
    new_week = get_new_users_count(7)
    watched_week = get_watches_count(7)
    searched_week = get_searches_count(7)
    text = ("<strong>📊 Статистика</strong>\n\n"
           f"Всего пользователей : {total}\n"
           f"Новых за неделю : {new_week}\n"
           f"Просмотров карточек за неделю : {watched_week}\n"
           f"Поисковых запросов за неделю : {searched_week}")
    await bot.send_message(chat_id, text, reply_markup=stats_menu_markup())


@dp.callback_query_handler(lambda call: call.data == 'stat_menu_back')
async def stat_menu_back_handler(call:types.CallbackQuery,state:FSMContext):
    await state.finish()
    await call.message.delete()
    await _render_stats_menu(call.message.chat.id)


@dp.callback_query_handler(lambda call: call.data == 'stat_users')
async def stat_users_handler(call:types.CallbackQuery):
    await call.message.delete()
    await _render_users_page(call.message.chat.id, 0)


@dp.callback_query_handler(lambda call: call.data.startswith('statusers_'))
async def stat_users_page_handler(call:types.CallbackQuery):
    page = int(call.data.split('_', 1)[1])
    await call.message.delete()
    await _render_users_page(call.message.chat.id, page)


async def _render_users_page(chat_id:int, page:int):
    users = get_users_list(limit=USERS_PER_PAGE, offset=page * USERS_PER_PAGE)
    if not users and page == 0:
        await bot.send_message(chat_id, "Пока нет ни одного пользователя.", reply_markup=stats_back_markup())
        return
    lines = []
    for user_id, username, joined_at in users:
        uname = f"@{username}" if username else "без username"
        joined = joined_at.split('T')[0] if joined_at else "—"
        lines.append(f"<code>{user_id}</code> — {uname} (с {joined})")
    has_more = len(users) == USERS_PER_PAGE
    await bot.send_message(chat_id, f"<strong>Пользователи (стр. {page+1}) :</strong>\n\n" + '\n'.join(lines),
                           reply_markup=users_page_markup(page, has_more))


@dp.callback_query_handler(lambda call: call.data == 'stat_top_watched')
async def stat_top_watched_handler(call:types.CallbackQuery):
    await call.message.delete()
    top = get_top_watched_movies(7, 10)
    if not top:
        text = "За последние 7 дней ещё не было ни одного просмотра."
    else:
        lines = [f"{i+1}. {title} (код {numb}) — {cnt}" for i, (title, numb, cnt) in enumerate(top)]
        text = "<strong>🎬 Топ просмотров за 7 дней :</strong>\n\n" + '\n'.join(lines)
    await bot.send_message(call.message.chat.id, text, reply_markup=stats_back_markup())


@dp.callback_query_handler(lambda call: call.data == 'stat_top_searches')
async def stat_top_searches_handler(call:types.CallbackQuery):
    await call.message.delete()
    top = get_top_searches(7, 15)
    if not top:
        text = "За последние 7 дней ещё не было поисковых запросов."
    else:
        lines = [f"{i+1}. «{query}» — {cnt}" for i, (query, cnt) in enumerate(top)]
        text = "<strong>🔍 Топ запросов за 7 дней :</strong>\n\n" + '\n'.join(lines)
    await bot.send_message(call.message.chat.id, text, reply_markup=stats_back_markup())


@dp.callback_query_handler(lambda call: call.data == 'stat_user_lookup')
async def stat_user_lookup_handler(call:types.CallbackQuery,state:FSMContext):
    await call.message.delete()
    await bot.send_message(call.message.chat.id,
                           "<strong>Отправьте id пользователя (можно скопировать из списка пользователей) :</strong>",
                           reply_markup=stats_back_markup())
    await Admin_.stats_user_lookup.set()


@dp.message_handler(state=Admin_.stats_user_lookup, content_types=types.ContentTypes.TEXT)
async def stat_user_lookup_input_handler(message:types.Message,state:FSMContext):
    if not message.text.strip().isdigit():
        await bot.send_message(message.chat.id, "Нужно отправить числовой id пользователя.")
        return
    user_id = int(message.text.strip())
    watches = get_user_watch_history(user_id, 15)
    searches = get_user_search_history(user_id, 15)
    await state.finish()

    lines = [f"<strong>История пользователя {user_id}</strong>\n"]
    lines.append("<strong>Просмотренные фильмы/сериалы :</strong>")
    if watches:
        for title, numb, created_at in watches:
            lines.append(f"• {title} (код {numb}) — {created_at.split('T')[0]}")
    else:
        lines.append("нет данных")
    lines.append("\n<strong>Поисковые запросы :</strong>")
    if searches:
        for query, created_at in searches:
            lines.append(f"• «{query}» — {created_at.split('T')[0]}")
    else:
        lines.append("нет данных")

    await bot.send_message(message.chat.id, '\n'.join(lines), reply_markup=stats_back_markup())
