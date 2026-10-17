import re

from aiogram import types

from database import contests
from database.connection import database
from database.people import add_user
from database.channels import get_all_channels_links
from keyboards.inline.sub_keyboard import sub_markup
from loader import bot, dp
from services import subscriptions
from services.contests import send_page


@dp.message_handler(commands=['contest', 'ref'], state='*')
async def contest_command(message: types.Message, state):
    await state.finish()
    add_user(message.from_user.id, message.from_user.username)
    await send_page(bot, message.chat.id, message.from_user.id)


@dp.callback_query_handler(lambda call: (call.data or '').startswith('contest:'), state='*')
async def contest_callback(call: types.CallbackQuery, state):
    user_id = call.from_user.id
    add_user(user_id, call.from_user.username)
    match = re.fullmatch(r'contest:(view|claim|catalog|card)(?::([1-9][0-9]{0,8}))?(?::([0-9]{1,8}))?', call.data or '')
    if not match:
        await call.answer('Кнопка недействительна.', show_alert=True)
        return
    action, raw_id, raw_value = match.groups()
    try:
        item = contests.get(int(raw_id)) if raw_id else contests.current()
    except ValueError:
        await call.answer('Конкурс не найден.', show_alert=True)
        return
    if not item or item['status'] == 'draft':
        await call.answer('Сейчас конкурса нет.', show_alert=True)
        return
    contest_id = item['id']
    if action in ('catalog', 'card') and not await subscriptions.check_subscription(user_id):
        # Reuse the requested action for the subscription check so the user can
        # continue to the same page/card without losing referral attribution.
        markup = sub_markup(get_all_channels_links(), check_callback=call.data)
        markup.add(types.InlineKeyboardButton('↩️ К конкурсу', callback_data=f'contest:view:{contest_id}'))
        await call.answer('Для доступа подпишитесь на все обязательные каналы.', show_alert=True)
        await bot.send_message(
            call.message.chat.id,
            'Чтобы открыть каталог и смотреть фильмы, подпишитесь на обязательные каналы ниже.\n'
            'После подписки нажмите «Я подписался✅».',
            parse_mode='', reply_markup=markup,
        )
        return
    if action == 'claim':
        try:
            contests.claim(contest_id, user_id)
        except ValueError as error:
            await call.answer(str(error), show_alert=True)
            return
    await call.answer()
    await state.finish()
    if action in ('view', 'claim'):
        await send_page(bot, call.message.chat.id, user_id, contest_id)
    elif action == 'catalog' and raw_value is not None:
        page = int(raw_value)
        from database.age_access import sql_filter
        age_clause, values = sql_filter(user_id, 'm.age_rating')
        rows = list(database.execute(f'''
            SELECT m.movie_title,m.movie_number FROM Movies m JOIN CatalogSections s
            ON s.section_key=m.catalog_section WHERE s.enabled=1 AND {age_clause}
            ORDER BY m.movie_number LIMIT 9 OFFSET ?''', (*values, page * 8)))
        markup = types.InlineKeyboardMarkup(row_width=1)
        for title, number in rows[:8]:
            markup.add(types.InlineKeyboardButton(title[:60], callback_data=f'contest:card:{contest_id}:{number}'))
        navigation = []
        if page:
            navigation.append(types.InlineKeyboardButton('⬅️', callback_data=f'contest:catalog:{contest_id}:{page - 1}'))
        if len(rows) > 8:
            navigation.append(types.InlineKeyboardButton('➡️', callback_data=f'contest:catalog:{contest_id}:{page + 1}'))
        if navigation:
            markup.row(*navigation)
        markup.add(types.InlineKeyboardButton('↩️ К конкурсу', callback_data=f'contest:view:{contest_id}'))
        await bot.send_message(call.message.chat.id, '🍿 Выбери фильм или сериал и открой его карточку.' if rows else 'Каталог пока пуст.', parse_mode='', reply_markup=markup)
    elif action == 'card' and raw_value is not None:
        from database.search import get_movie_from_numb
        from database.catalog_sections import get_section
        from handlers.users.find_movie import _send_movie_card
        number = int(raw_value)
        info = get_movie_from_numb(number)
        if not info or not get_section(info['catalog_section'])['enabled']:
            await bot.send_message(call.message.chat.id, 'Эта карточка сейчас недоступна.', parse_mode='')
            return
        await _send_movie_card(call.message.chat.id, number, info, user_id=user_id,
                              extra_buttons=[types.InlineKeyboardButton('🎁 Вернуться к конкурсу', callback_data=f'contest:view:{contest_id}')])
