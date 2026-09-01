from aiogram import types
from loader import dp, bot
from aiogram.dispatcher import FSMContext
from database.admin.select import get_all_bot_users
from database.admin.analytics import get_overall_stats, get_top_movies, get_users_page, get_user_stats
from keyboards.admin.keyboard import admin_return_markup, get_delete_channels, get_delete_movies, admin_markup
from keyboards.admin.analytics import analytics_menu_markup, users_page_markup, user_detail_markup
from states.admin_states import Admin_


async def _replace_admin_message(call, text, markup):
    try:
        await call.message.edit_text(text, reply_markup=markup)
    except Exception:
        await bot.send_message(call.message.chat.id, text, reply_markup=markup)


def _user_label(user):
    user_id, username, first_name, last_name, joined_at, last_active_at = user
    name = ' '.join(x for x in (first_name, last_name) if x).strip()
    if username:
        name = f'@{username}' if not name else f'{name} (@{username})'
    return name or str(user_id)


def _event_label(event_type):
    return {
        'start': 'Запуск',
        'search': 'Поиск',
        'movie_open': 'Открытие фильма',
        'movie_download': 'Скачивание фильма',
        'subcategory_download': 'Скачивание озвучки/качества',
    }.get(event_type, event_type)


@dp.callback_query_handler()
async def admin_call_handler(call: types.CallbackQuery, state: FSMContext):
    chat_id = call.message.chat.id

    if call.data == 'bot_stat':
        stat = get_overall_stats(7)
        total = len(get_all_bot_users())
        text = (
            '<strong>📊 Статистика</strong>\n\n'
            f'👥 Всего пользователей: <strong>{total}</strong>\n'
            f'🟢 Активных за 7 дней: <strong>{stat["active_users"]}</strong>\n'
            f'🔎 Поисков за 7 дней: <strong>{stat["searches"]}</strong>\n'
            f'🎬 Открытий фильмов: <strong>{stat["movie_opens"]}</strong>\n'
            f'📥 Скачиваний: <strong>{stat["downloads"] + stat["subcategory_downloads"]}</strong>'
        )
        await _replace_admin_message(call, text, analytics_menu_markup())
        await call.answer()
        return

    if call.data == 'stats_week':
        stat = get_overall_stats(7)
        text = (
            '<strong>📈 За последние 7 дней</strong>\n\n'
            f'🟢 Активных пользователей: <strong>{stat["active_users"]}</strong>\n'
            f'🔎 Поисковых запросов: <strong>{stat["searches"]}</strong>\n'
            f'🎬 Открытий фильмов: <strong>{stat["movie_opens"]}</strong>\n'
            f'📥 Простых скачиваний: <strong>{stat["downloads"]}</strong>\n'
            f'🗂 Скачиваний озвучок/качеств: <strong>{stat["subcategory_downloads"]}</strong>'
        )
        markup = analytics_menu_markup()
        await _replace_admin_message(call, text, markup)
        await call.answer()
        return

    if call.data == 'stats_top_movies':
        rows = get_top_movies(7, 10)
        if not rows:
            text = '<strong>🎬 Популярные фильмы</strong>\n\nЗа последние 7 дней данных пока нет.'
        else:
            lines = ['<strong>🎬 Популярные фильмы за 7 дней</strong>', '']
            for i, (number, title, opens, downloads) in enumerate(rows, 1):
                lines.append(f'{i}. <strong>{title}</strong> (код {number}) — открытий: {opens}, скачиваний: {downloads}')
            text = '\n'.join(lines)
        await _replace_admin_message(call, text, analytics_menu_markup())
        await call.answer()
        return

    if call.data.startswith('stats_users_'):
        page = int(call.data.rsplit('_', 1)[1])
        total, users = get_users_page(page)
        if not users:
            text = '<strong>👥 Пользователи</strong>\n\nПользователей пока нет.'
        else:
            lines = [f'<strong>👥 Пользователи</strong> — всего {total}', '']
            for user in users:
                user_id = user[0]
                active = user[5] or 'нет данных'
                lines.append(f'👤 <a href="tg://user?id={user_id}">{_user_label(user)}</a>\nID: <code>{user_id}</code> · активность: {active}')
                # Use a compact per-user button below instead of relying on inline links.
            text = '\n'.join(lines)
        # Add user buttons separately for usability.
        from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
        markup = users_page_markup(page, total)
        for user in users:
            markup.insert(InlineKeyboardButton(f'👤 {_user_label(user)[:35]}', callback_data=f'stats_user_{user[0]}'))
        await _replace_admin_message(call, text, markup)
        await call.answer()
        return

    if call.data.startswith('stats_user_'):
        user_id = int(call.data.rsplit('_', 1)[1])
        result = get_user_stats(user_id, 30)
        if not result:
            await call.answer('Пользователь не найден', show_alert=True)
            return
        user, counters, events = result
        name = _user_label(user)
        lines = [
            '<strong>👤 Пользователь</strong>',
            f'Имя: <strong>{name}</strong>',
            f'ID: <code>{user[0]}</code>',
            f'Первый запуск: {user[4] or "нет данных"}',
            f'Последняя активность: {user[5] or "нет данных"}',
            '',
            '<strong>За последние 30 дней:</strong>',
            f'🔎 Поисков: {counters.get("search", 0)}',
            f'🎬 Открытий фильмов: {counters.get("movie_open", 0)}',
            f'📥 Скачиваний: {counters.get("movie_download", 0) + counters.get("subcategory_download", 0)}',
            '',
            '<strong>Последние действия:</strong>'
        ]
        for event_type, movie_number, movie_title, search_query, created_at in events:
            detail = f' — {movie_title}' if movie_number is not None else ''
            if search_query:
                detail = f' — «{search_query}»'
            lines.append(f'{created_at} — {_event_label(event_type)}{detail}')
        await _replace_admin_message(call, '\n'.join(lines), user_detail_markup(user_id))
        await call.answer()
        return

    if call.data == 'stats_back':
        await _replace_admin_message(call, 'Админ-панель', admin_markup())
        await call.answer()
        return

    if call.data == 'add_cod':
        await bot.delete_message(chat_id, message_id=call.message.message_id)
        await bot.send_message(chat_id, '<strong>Отправьте текст :</strong>', reply_markup=admin_return_markup())
        await Admin_.add_new_cod.set()
        return

    if call.data == 'send_func':
        await bot.delete_message(chat_id, message_id=call.message.message_id)
        await bot.send_message(chat_id, '<strong>Отправьте или Перешлите текст для рассылки :</strong>', reply_markup=admin_return_markup())
        await Admin_.send_.set()
        return

    if call.data == 'add_channel':
        await bot.delete_message(chat_id, message_id=call.message.message_id)
        await bot.send_message(chat_id, '<strong>Отправьте ссылку на канал:</strong>', reply_markup=admin_return_markup())
        await Admin_.add_channel_link.set()
        return
    if call.data == 'delete_channel':
        await bot.delete_message(chat_id, message_id=call.message.message_id)
        await bot.send_message(chat_id, '<strong>Выберите канал который хотите удалить :</strong>', reply_markup=get_delete_channels())
        await Admin_.delete_channel.set()
        return
    if call.data == 'delete_movie':
        await bot.delete_message(chat_id, message_id=call.message.message_id)
        await bot.send_message(chat_id, '<strong>Выберите фильм который хотите удалить :</strong>', reply_markup=get_delete_movies())
        await Admin_.delete_movie.set()
        return
    if call.data == 'edit_movie':
        await bot.delete_message(chat_id, message_id=call.message.message_id)
        await bot.send_message(chat_id, '<strong>Выберите фильм который хотите изменить :</strong>', reply_markup=get_delete_movies())
        await Admin_.edit_movie_select.set()
        return
