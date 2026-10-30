import re
import secrets
import time

from aiogram import types
from loader import dp
from database import instant_jobs as jobs, content_requests as requests
from database.age_access import preferences
from database.connection import database
from services import instant
from services.subscriptions import check_subscription
from tools.yougen_bridge import VOICES, MOVIE_VOICES, SERIES_VOICES

QUALITIES = ['360p', '480p', '720p', '1080p']


@dp.message_handler(commands=['instant_mode'], state='*')
async def mode_command(message):
    from services.access import is_admin
    from database import instant_settings
    if not is_admin(message.from_user.id) or message.chat.type != 'private':
        return
    argument = message.get_args().strip().lower()
    if argument:
        if argument not in {'pilot', 'public', 'off', 'env'}:
            await message.answer('Режимы: /instant_mode pilot — только администраторы; '
                                 '/instant_mode public — все допущенные пользователи; '
                                 '/instant_mode off — выключить; /instant_mode env — настройки окружения.')
            return
        instant_settings.set_mode(argument, message.from_user.id)
    active, public = instant_settings.effective()
    description = 'выключена' if not active else 'для всех допущенных пользователей' if public else 'только для администраторов'
    worker = 'на связи' if jobs.online() else 'пока не на связи'
    await message.answer(f'Моментальная выдача: {description}.\n'
                         f'Режим: {instant_settings.mode()}. Обработчик: {worker}.\n'
                         'Для пользователей: /instant_mode public\n'
                         'Пробное включение: /instant_mode pilot\nВыключение: /instant_mode off')


def voice_choices(data):
    return sorted(MOVIE_VOICES if data['request_kind'] == 'movie' else SERIES_VOICES)


async def allowed(user_id):
    return instant.enabled(user_id) and preferences(user_id)['max_age'] == 18 and await check_subscription(user_id)


async def options(message, state):
    data = await state.get_data()
    nonce = data['instant_nonce']
    keyboard = types.InlineKeyboardMarkup(row_width=2)
    if not data.get('instant_voice'):
        for i, voice in enumerate(voice_choices(data)):
            keyboard.insert(types.InlineKeyboardButton(voice, callback_data=f'instant:voice:{nonce}:{i}'))
        text = 'Выберите озвучку. Если её нет у источника, запрос остановится без подмены варианта.'
    else:
        for i, quality in enumerate(QUALITIES):
            keyboard.insert(types.InlineKeyboardButton(quality, callback_data=f'instant:quality:{nonce}:{i}'))
        text = 'Выберите качество. Подготовка может занять несколько минут; новая карточка каталога останется на проверке.'
    keyboard.add(types.InlineKeyboardButton('Назад к заявке', callback_data='instant:back'))
    await message.answer(text, reply_markup=keyboard)


@dp.callback_query_handler(text='instant:start', state='Request:confirm')
async def begin(call, state):
    await call.answer()
    if not await allowed(call.from_user.id):
        await call.message.answer('Быстрая выдача сейчас недоступна. Проверьте подписку и возрастные настройки либо отправьте обычную заявку.')
        return
    data = await state.get_data()
    if data['request_kind'] == 'series' and not (data.get('request_season') and data.get('request_episode')):
        await call.message.answer('Для быстрой выдачи укажите одну серию. Весь сезон можно запросить обычной заявкой.')
        return
    await state.update_data(instant_nonce=secrets.token_hex(4), instant_created=time.time(), instant_voice='')
    await state.set_state('Instant:options')
    await options(call.message, state)


@dp.callback_query_handler(text='instant:back', state='Instant:options')
async def back(call, state):
    await call.answer()
    from handlers.users.content_requests import confirmation
    await confirmation(call.message, state)


@dp.callback_query_handler(lambda c: c.data.startswith(('instant:voice:', 'instant:quality:')), state='Instant:options')
async def selection(call, state):
    await call.answer()
    if not await allowed(call.from_user.id):
        return
    data = await state.get_data()
    parts = call.data.split(':')
    if (len(parts) != 4 or parts[2] != data.get('instant_nonce')
            or time.time() - data.get('instant_created', 0) > 900 or not parts[3].isdigit()):
        await call.message.answer('Выбор устарел. Откройте заявку заново.')
        return
    index = int(parts[3])
    if parts[1] == 'voice':
        voices = voice_choices(data)
        if index >= len(voices):
            return
        await state.update_data(instant_voice=voices[index])
        await options(call.message, state)
        return
    if index >= len(QUALITIES) or data.get('instant_voice') not in VOICES:
        return
    selected = data['request_selected']
    year_suffix = re.search(r'\(((?:19|20)\d{2})\)\s*$', selected['title'])
    title = re.sub(r'\s*\((?:19|20)\d{2}\)\s*$', '', selected['title']).strip()
    payload = {'query': title, 'title': title, 'content_type': data['request_kind'],
               'year': str(selected.get('year') or (year_suffix[1] if year_suffix else '')),
               'voice': data['instant_voice'],
               'quality': QUALITIES[index], 'season': data.get('request_season', 0),
               'episode': data.get('request_episode', 0),
               'metadata': {'description': str(selected.get('overview') or '')[:2500],
                            'poster_url': str(selected.get('poster_url') or '')[:1000]}}
    try:
        # Use the same request/vote identity; a private file does not publish the catalogue.
        with database.transaction():
            request = requests.vote(call.from_user.id, selected['title'], payload['content_type'],
                                    payload['season'], selected.get('tmdb_id'), year=payload['year'],
                                    overview=selected.get('overview'), poster_url=selected.get('poster_url'),
                                    episode=payload['episode'], voice=payload['voice'])
            job = jobs.create(call.from_user.id, payload, request['id'])
    except ValueError as error:
        await call.message.answer(str(error), parse_mode='')
        return
    await state.finish()
    await call.message.answer(instant.label(job) + '\nСтатус: /instant_status', parse_mode='', reply_markup=instant.markup(job))


@dp.message_handler(commands=['instant_status'], state='*')
async def status_command(message):
    records = jobs.recent(message.from_user.id)
    if not records:
        await message.answer('Быстрых запросов пока нет. Начните с поиска или заявки на произведение.')
    for job in records:
        await message.answer(instant.label(job), parse_mode='', reply_markup=instant.markup(job))


@dp.callback_query_handler(lambda c: c.data.startswith(('instant:get:', 'instant:status:')), state='*')
async def status_callback(call):
    parts = call.data.split(':')
    if len(parts) not in (3, 4) or parts[1] == 'get' and (len(parts) != 4 or not parts[3].isdigit()):
        await call.answer('Кнопка устарела.', show_alert=True)
        return
    job = jobs.get(parts[2], call.from_user.id)
    if not job:
        await call.answer('Задание недоступно.', show_alert=True)
        return
    await call.answer()
    if parts[1] == 'get':
        if not await instant.deliver(call.bot, job['id'], call.from_user.id, repeat=True,
                                     expected_generation=int(parts[3])):
            await call.message.answer('Файл ещё не готов или доступ ограничен. Проверьте возрастные настройки и подписку.')
        job = jobs.get(job['id'], call.from_user.id)
    await call.message.answer(instant.label(job), parse_mode='', reply_markup=instant.markup(job))
