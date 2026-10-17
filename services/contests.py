"""Contest messages and shareable announcements; publication remains manual."""

import re
from urllib.parse import urlencode

from aiogram import types
from aiogram.utils.exceptions import BadRequest

from database import contests


async def username(bot):
    from services import movie_sharing
    name = movie_sharing.bot_username
    if not name:
        name = (await bot.get_me()).username
        movie_sharing.bot_username = name
    if not isinstance(name, str) or not re.fullmatch(r'[A-Za-z][A-Za-z0-9_]{4,31}', name):
        raise ValueError('Не удалось получить имя бота. Повторите позже.')
    return name


def link(name, payload):
    return f'https://t.me/{name}?start={payload}'


def rules(item):
    body = item['body']
    return (f"Для участия пригласи хотя бы одного нового пользователя по своей ссылке. "
            f"Приглашение засчитывается, когда друг запустит бота и откроет карточку в каталоге. "
            "Для доступа к каталогу действуют обязательные подписки бота.\n"
            f"1 подтверждённый друг = 1 билет. Максимум {body['ticket_cap']} билетов на участника.\n"
            "Повторный запуск и разблокировка бота не считаются новым приглашением. "
            "Самоприглашения и накрутка исключаются после проверки.\n"
            "Подписка на канал новостей не требуется. Победитель выбирается случайно среди билетов; "
            "результат будет доступен здесь. Приз передаёт организатор вручную.")


def announcement(item, name):
    return (f"🎁 {item['body']['title']}\n\nПриз: {item['body']['gift_name']}\n"
            f"{item['body']['gift_url']}\n\n"
            "🍿 KinoTime — фильмы и сериалы в Telegram.\n"
            f"{item['body']['description']}\n\n{rules(item)}\n\n"
            f"Окончание: {contests.ends_label(item)}\n"
            f"Участвовать: {link(name, 'campaign_' + str(item['id']))}")


def result_text(item, name):
    result = item['draw_result']
    if not result:
        return ''
    return (f"🎉 Итоги: {item['body']['title']}\n\n"
            f"Приз: {item['body']['gift_name']}\nПобедитель: участник №{result['winner_member_id']}\n"
            f"Выбран билет №{result['ticket']} из {result['total_tickets']}.\n"
            f"Участников с билетами: {result['eligible']}.\n\n"
            f"Проверить свой результат: {link(name, 'contest_' + str(item['id']))}")


async def send_page(bot, chat_id, user_id, contest_id=None):
    item = contests.get(contest_id) if contest_id else contests.current()
    if not item or item['status'] == 'draft':
        await bot.send_message(chat_id, 'Сейчас открытого конкурса нет.', parse_mode='')
        return
    personal = contests.personal(item['id'], user_id)
    body = item['body']
    name = await username(bot)
    markup = types.InlineKeyboardMarkup(row_width=1)
    text = f"🎁 {body['title']}\n\nПриз: {body['gift_name']}\n"
    if body['description']:
        text += '\n' + body['description'] + '\n'
    text += f"\nОкончание: {contests.ends_label(item)}\n\n"
    if contests.is_open(item):
        text += (f"Подтверждено друзей: {personal['confirmed']}\n"
                 f"Ожидают открытия карточки: {personal['pending']}\n"
                 f"Твои билеты: {personal['tickets']} / {body['ticket_cap']}\n"
                 + ('✅ Ты участвуешь!' if personal['tickets'] else 'Пригласи первого друга, чтобы участвовать.'))
        if personal['member']:
            referral = link(name, f"ref_{item['id']}_{personal['member']['code']}")
            text += '\n\nТвоя ссылка:\n' + referral
            share = '🍿 Фильмы и сериалы в KinoTime! Заходи и участвуй в розыгрыше: ' + body['gift_name']
            markup.add(types.InlineKeyboardButton('📤 Пригласить друга', url='https://t.me/share/url?' + urlencode({'url': referral, 'text': share})))
        text += '\n\n' + rules(item)
    elif item['status'] == 'finished':
        text += f"Розыгрыш завершён. Твои билеты: {personal['tickets']}.\n\n" + result_text(item, name)
        if personal['is_winner']:
            text += '\n\n🎉 Ты победитель!'
            if item['delivered_at']:
                text += '\nОрганизатор отметил приз как переданный.'
            elif personal['claimed']:
                text += '\nЗапрос на получение приза зарегистрирован. Организатор увидит его в панели.'
            else:
                markup.add(types.InlineKeyboardButton('🎁 Запросить получение приза', callback_data=f"contest:claim:{item['id']}"))
            if not item['delivered_at']:
                text += f"\nДля передачи приза напиши организатору: участник №{personal['member']['id']}."
                markup.add(types.InlineKeyboardButton('✉️ Связаться с организатором', url=body['organizer_url']))
    elif item['status'] == 'cancelled':
        text += 'Конкурс отменён организатором.'
    else:
        text += 'Приём приглашений завершён. Ожидаем итоги здесь и в канале новостей.'
    if body['gift_url']:
        markup.add(types.InlineKeyboardButton('🎁 Посмотреть приз', url=body['gift_url']))
    markup.add(types.InlineKeyboardButton('🍿 Открыть карточки каталога', callback_data=f"contest:catalog:{item['id']}:0"))
    markup.add(types.InlineKeyboardButton('🔄 Обновить результат', callback_data=f"contest:view:{item['id']}"))
    if body['channel_url']:
        markup.add(types.InlineKeyboardButton('📣 Новости розыгрыша · необязательно', url=body['channel_url']))
    markup.add(types.InlineKeyboardButton('↩️ Главное меню', callback_data='catalog:home'))
    if body['photo_url'] and len(text) <= 1024:
        try:
            await bot.send_photo(chat_id, body['photo_url'], caption=text, parse_mode='', reply_markup=markup)
            return
        except BadRequest:
            pass
    elif body['photo_url']:
        try:
            await bot.send_photo(chat_id, body['photo_url'], caption='🎁 ' + body['gift_name'], parse_mode='')
        except BadRequest:
            pass
    await bot.send_message(chat_id, text, parse_mode='', reply_markup=markup, disable_web_page_preview=True)
