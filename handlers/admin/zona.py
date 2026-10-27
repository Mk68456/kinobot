"""Administrator-only trial of page -> chosen video -> private Telegram delivery."""

import asyncio
import secrets
import time

from aiogram import types
from loader import dp
from database import zona_jobs as jobs
from services.zona_delivery import delivery, normalize_page, size_error, upload_limit, STATUSES
from tools.zona_prototype import discover

looking_up = set()


def job_markup(job):
    keyboard = types.InlineKeyboardMarkup()
    if job['status'] == 'sent' and job['file_id']:
        keyboard.add(types.InlineKeyboardButton('Получить ещё раз', callback_data='zona:get:' + job['id']))
    if job['status'] in {'error', 'interrupted'}:
        label = 'Проверил чат — повторить' if job['status'] == 'interrupted' else 'Повторить'
        keyboard.add(types.InlineKeyboardButton(label, callback_data='zona:retry:' + job['id']))
    return keyboard


async def show_choices(message, state, page=0):
    data = await state.get_data()
    sources = data['zona_sources']
    keyboard = types.InlineKeyboardMarkup(row_width=1)
    for index in range(page * 8, min((page + 1) * 8, len(sources))):
        keyboard.add(types.InlineKeyboardButton(sources[index]['label'][:100],
                                               callback_data=f"zona:pick:{data['zona_nonce']}:{index}"))
    if page:
        keyboard.add(types.InlineKeyboardButton('← Назад', callback_data=f"zona:page:{data['zona_nonce']}:{page - 1}"))
    if (page + 1) * 8 < len(sources):
        keyboard.add(types.InlineKeyboardButton('Далее →', callback_data=f"zona:page:{data['zona_nonce']}:{page + 1}"))
    await message.answer('Выберите вариант. Для сериала пока доступна первая серия выбранного сезона.',
                         reply_markup=keyboard)


async def inspect_page(message, state, value):
    try:
        page = normalize_page(value)
    except ValueError as exc:
        await message.answer(str(exc), parse_mode='')
        return
    if message.from_user.id in looking_up:
        await message.answer('Предыдущая страница ещё проверяется. Подождите немного.')
        return
    nonce = secrets.token_hex(4)
    looking_up.add(message.from_user.id)
    await state.set_state('Zona:loading')
    await state.update_data(zona_nonce=nonce)
    try:
        await message.answer('Проверяю страницу и доступные варианты…')
        sources = await asyncio.to_thread(discover, page)
        # Another command may have closed this dialog while network I/O was running.
        if (await state.get_data()).get('zona_nonce') != nonce or await state.get_state() != 'Zona:loading':
            return
        sources = [{key: source[key] for key in ('source_id', 'label', 'metadata')}
                   for source in sources[:40] if source.get('source_id')]
        if not sources:
            raise ValueError('Нет поддерживаемых вариантов видео.')
        await state.set_state('Zona:pick')
        await state.update_data(zona_page=page, zona_sources=sources, zona_created=time.time())
        await show_choices(message, state)
    except Exception:
        if (await state.get_data()).get('zona_nonce') == nonce:
            await state.finish()
            await message.answer('Не удалось получить варианты со страницы. Источник мог измениться '
                                 'или временно недоступен. Попробуйте /zona позже.')
    finally:
        looking_up.discard(message.from_user.id)


@dp.message_handler(commands=['zona'], state='*')
async def zona_command(message, state):
    if message.from_user.id in looking_up:
        await message.answer('Предыдущая страница ещё проверяется. Подождите немного.')
        return
    await state.finish()
    value = message.get_args().strip()
    if value:
        await inspect_page(message, state, value)
    else:
        await state.set_state('Zona:url')
        await message.answer('Пришлите ссылку на страницу фильма или сериала Zona. '
                             'Я предложу озвучку и качество, затем загружу видео в этот чат.')


@dp.message_handler(lambda message: not (message.text or '').startswith('/'),
                    state='Zona:url', content_types=types.ContentTypes.TEXT)
async def zona_url(message, state):
    await inspect_page(message, state, message.text)


@dp.callback_query_handler(lambda call: call.data.startswith(('zona:pick:', 'zona:page:')), state='*')
async def zona_choice(call, state):
    parts = call.data.split(':')
    data = await state.get_data()
    try:
        valid = (len(parts) == 4 and parts[2] == data.get('zona_nonce')
                 and time.time() - data.get('zona_created', 0) <= 900)
        index = int(parts[-1])
        if not valid or index < 0:
            raise ValueError()
        if parts[1] == 'page':
            if index * 8 >= len(data['zona_sources']):
                raise ValueError()
            await call.answer()
            await show_choices(call.message, state, index)
            return
        source = data['zona_sources'][index]
    except (ValueError, IndexError, KeyError):
        await call.answer('Список устарел. Откройте страницу заново через /zona.', show_alert=True)
        return
    expected = int(source.get('metadata', {}).get('expected_size') or 0)
    limit = upload_limit()
    if expected > limit:
        await call.answer('Размер превышает лимит подключения.', show_alert=True)
        await call.message.answer(size_error(expected, limit), parse_mode='')
        return
    if delivery.task is None:
        await call.answer('Обработчик загрузок ещё не запущен. Перезапустите бота.', show_alert=True)
        return
    job = jobs.enqueue(call.from_user.id, data['zona_page'], source)
    delivery.notify()
    await call.answer()
    # Keep the nonce for idempotent double clicks; a new /zona replaces it.
    await call.message.answer(f"{STATUSES[job['status']]}: {job['label']}\nСостояние: /zona_status",
                              parse_mode='', reply_markup=job_markup(job))


@dp.message_handler(commands=['zona_status'], state='*')
async def zona_status(message):
    records = jobs.recent(message.from_user.id)
    if not records:
        await message.answer('Загрузок пока нет. Начните с /zona.')
    for job in records:
        text = STATUSES[job['status']] + ': ' + job['label']
        if job['error']:
            text += '\n' + job['error']
        await message.answer(text, parse_mode='', reply_markup=job_markup(job))


@dp.callback_query_handler(lambda call: call.data.startswith(('zona:get:', 'zona:retry:')), state='*')
async def zona_job_action(call):
    _, action, job_id = call.data.split(':', 2)
    job = jobs.get(job_id, call.from_user.id)
    if job is None:
        await call.answer('Загрузка не найдена.', show_alert=True)
        return
    if action == 'retry':
        if delivery.task is None:
            await call.answer('Обработчик загрузок ещё не запущен.', show_alert=True)
            return
        try:
            job = jobs.retry(job_id, call.from_user.id)
        except ValueError as exc:
            await call.answer(str(exc), show_alert=True)
            return
        delivery.notify()
        await call.answer('Загрузка поставлена в очередь.')
    elif job['status'] == 'sent' and job['file_id']:
        await call.answer()
        from services.storage_backup import send_catalog_file
        try:
            await send_catalog_file(dp.bot, call.from_user.id, job['file_id'], 'video', caption=job['label'])
        except Exception:
            await call.message.answer('Повторная выдача не удалась. Проверьте доступ к Telegram.')
    else:
        await call.answer('Видео пока не готово.', show_alert=True)
