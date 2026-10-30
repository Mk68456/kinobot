"""Private administrator controls for provider imports and publication policy."""
import re
from aiogram import types
from loader import dp
from services.access import is_admin
from database import provider_batches as batches
from services import provider_catalog


def keyboard(batch):
    result = types.InlineKeyboardMarkup(row_width=1)
    actions = {'prepared': [('Загрузить всё доступное', 'start')],
               'queued': [('Приостановить', 'pause')],
               'discovering': [('Приостановить', 'pause')],
               'running': [('Приостановить', 'pause')],
               'paused': [('Продолжить', 'resume')]}.get(batch['status'], [])
    if batch['status'] == 'review' and not batch['counts']:
        actions.append(('Повторить обход', 'retry'))
    for label, action in actions:
        result.add(types.InlineKeyboardButton(label, callback_data=f"bulk:{action}:{batch['id']}"))
    result.add(types.InlineKeyboardButton('Обновить прогресс', callback_data=f"bulk:status:{batch['id']}"))
    if batch['status'] not in ('complete', 'cancelled'):
        result.add(types.InlineKeyboardButton('Остановить загрузку', callback_data=f"bulk:cancel:{batch['id']}"))
    return result


async def show(message, batch):
    labels = {'prepared': 'Готово к запуску', 'queued': 'Ждёт обработчика', 'discovering': 'Обход меню источника',
              'running': 'Передача файлов', 'paused': 'Приостановлено', 'cancelled': 'Остановлено',
              'complete': 'Завершено', 'review': 'Нужна проверка'}
    counts = batch['counts']
    await message.answer(f"{batch['payload']['title']} ({batch['payload']['year']})\n"
                         f"{labels[batch['status']]}\n"
                         f"В плане: {sum(counts.values())}; готово: {counts.get('sent', 0)}; "
                         f"на проверке: {counts.get('review', 0)}.\n"
                         f"Уже были в каталоге/очереди: {batch['skipped']}.\n"
                         f"Опубликовано: {batch['publication'].get('published', 0)}; "
                         f"черновиков: {batch['publication'].get('draft', 0)}.\n"
                         + (f"Причина остановки: {batch['error']}\n" if batch['error'] else '') +
                         'Остановка не удаляет уже полученные файлы. Текущая передача может завершиться.',
                         parse_mode='', reply_markup=keyboard(batch))


@dp.message_handler(commands=['bulk_import', 'bulk_status', 'provider_publish', 'provider_link'], state='*')
async def command(message):
    if not is_admin(message.from_user.id) or message.chat.type != 'private':
        return
    name = message.text.split()[0].split('@')[0]
    args = message.get_args().strip()
    try:
        if name == '/provider_link':
            match = re.fullmatch(r'([1-9][0-9]*)\s+(movie|series)\s+(.+?)\s+\(((?:19|20)\d{2})\)', args)
            if not match:
                raise ValueError('Пример: /provider_link 1017 series Гангстерленд (2025)')
            provider_catalog.link(int(match[1]), {'content_type': match[2], 'title': match[3], 'year': match[4]})
            await message.answer('Привязка сохранена. Новые варианты будут добавляться в эту карточку.')
        elif name == '/bulk_import':
            match = re.fullmatch(r'(movie|series)\s+(.+?)\s+\(((?:19|20)\d{2})\)', args)
            if not match:
                raise ValueError('Пример: /bulk_import series Ричер (2022) или /bulk_import movie Джек Ричер (2012)')
            payload = {'content_type': match[1], 'title': match[2].strip(), 'query': match[2].strip(), 'year': match[3]}
            from services.tmdb import search
            found = [c for c in await search(payload['title'], payload['content_type'])
                     if str(c.get('year')) == payload['year'] and c.get('title', '').casefold() == payload['title'].casefold()]
            if len(found) == 1:
                payload['metadata'] = {'description': str(found[0].get('overview') or '')[:2500],
                                       'poster_url': str(found[0].get('poster_url') or '')[:1000]}
            await show(message, batches.create(message.from_user.id, payload))
        elif name == '/bulk_status':
            records = batches.recent(message.from_user.id)
            if not records:
                await message.answer('Массовых загрузок пока нет. Начните с /bulk_import.')
            for batch in records:
                await show(message, batch)
        else:
            if args == 'review':
                provider_catalog.set_policy(False)
            elif args.startswith('auto'):
                parts = args.split()
                if len(parts) != 2:
                    raise ValueError('Укажите маркировку новых карточек: /provider_publish auto 18+ '
                                     '(также 0+, 6+, 12+, 16+). Без неё автопубликация не включится.')
                provider_catalog.set_policy(True, parts[1])
            elif args:
                raise ValueError('Режимы: /provider_publish review или /provider_publish auto 18+')
            automatic, age = provider_catalog.policy()
            await message.answer('Новые файлы: ' + (f'автопубликация; маркировка новых карточек {age}.' if automatic else
                                                   'черновики на проверку.') +
                                 '\nДля произведений с другой маркировкой используйте ручную проверку.')
    except ValueError as error:
        await message.answer(str(error), parse_mode='')


@dp.callback_query_handler(lambda call: call.data.startswith('bulk:'), state='*')
async def action(call):
    if not is_admin(call.from_user.id) or call.message.chat.type != 'private':
        await call.answer('Доступ только администратору.', show_alert=True)
        return
    await call.answer()
    parts = call.data.split(':')
    try:
        if len(parts) == 4 and parts[1] == 'card' and parts[2] in ('movie', 'series') and parts[3].isdigit():
            from services.tmdb import details
            card = await details(int(parts[3]), parts[2])
            if not card or not card.get('year'):
                raise ValueError('Не удалось подтвердить название и год карточки.')
            payload = {'title': card['title'], 'query': card['title'], 'year': str(card['year']), 'content_type': parts[2]}
            payload['metadata'] = {'description': str(card.get('overview') or '')[:2500],
                                   'poster_url': str(card.get('poster_url') or '')[:1000]}
            batch = batches.create(call.from_user.id, payload)
            batch = batches.control(batch['id'], call.from_user.id, 'start')
        elif len(parts) == 3:
            batch = (batches.get(parts[2], call.from_user.id) if parts[1] == 'status' else
                     batches.control(parts[2], call.from_user.id, parts[1]))
            if not batch:
                raise ValueError('Массовая загрузка недоступна.')
        else:
            raise ValueError('Кнопка устарела.')
        await show(call.message, batch)
    except ValueError as error:
        await call.message.answer(str(error), parse_mode='')
