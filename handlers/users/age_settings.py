"""Simple age settings and optional parental PIN, without identity documents."""
import hashlib
import hmac
import time

from aiogram import types
from aiogram.utils.exceptions import BadRequest
from database import age_access
from loader import bot, dp


def menu(user_id):
    info = age_access.preferences(user_id)
    text = ('👨‍👩‍👧 Возрастные настройки\n\n' +
            ('Контент 18+ включён.' if info['max_age'] == 18 else f"Получение материалов: до {info['max_age']}+ включительно.") +
            '\nКарточки видны всем. В безопасном режиме получение материалов выше выбранного возраста и без маркировки заблокировано.')
    rows = []
    if info['parental']:
        text += '\n\n🔒 Родительский режим включён. Для изменения требуется PIN.'
        rows = [('Изменить допустимый возраст', 'age:parent'), ('Отключить родительский режим', 'age:off')]
    else:
        rows = [('Оставить безопасный режим · до 16+', 'age:safe') if info['max_age'] == 18 else
                ('Показывать контент 18+', 'age:adult'), ('Настроить родительский режим', 'age:parent')]
    rows.append(('↩️ Главное меню', 'catalog:home'))
    return text, keyboard(rows)


def keyboard(rows):
    markup = types.InlineKeyboardMarkup(row_width=1)
    for label, callback in rows:
        markup.add(types.InlineKeyboardButton(label, callback_data=callback))
    return markup


async def show(chat_id, user_id):
    text, markup = menu(user_id)
    await bot.send_message(chat_id, text, parse_mode='', reply_markup=markup)


@dp.message_handler(commands=['age'], state='*')
async def age_command(message, state):
    await state.finish()
    await show(message.chat.id, message.from_user.id)


@dp.callback_query_handler(lambda c: (c.data or '').startswith('age:'), state='*')
async def age_callback(call, state):
    action = call.data[4:]
    user_id = call.from_user.id
    await call.answer()
    if action.startswith('limit:'):
        data = await state.get_data()
        if await state.get_state() != 'Age:limit' or data.get('expires', 0) < time.time():
            await state.finish()
            await show(call.message.chat.id, user_id)
            return
        if action[6:] not in ('0', '6', '12', '16'):
            return
        await state.update_data(limit=int(action[6:]), mode='change' if age_access.preferences(user_id)['parental'] else 'create')
        await state.set_state('Age:pin')
        await bot.send_message(call.message.chat.id,
            'Введите текущий родительский PIN (6 цифр).' if age_access.preferences(user_id)['parental'] else
            'Придумайте PIN из 6 цифр. Он потребуется для изменения ограничения. '
            'Не сообщайте его ребёнку. Если забыть PIN, самостоятельно снять блокировку не получится.',
            parse_mode='', reply_markup=keyboard([('Отмена', 'age:settings')]))
        return
    await state.finish()
    if action == 'adult':
        if age_access.preferences(user_id)['parental']:
            await show(call.message.chat.id, user_id)
            return
        await bot.send_message(call.message.chat.id,
            'Включить произведения 18+?\n\nПодтвердите, что вам исполнилось 18 лет. '
            'Это самостоятельное подтверждение возраста, без документов.', parse_mode='',
            reply_markup=keyboard([('Мне исполнилось 18 лет', 'age:confirm'),
                                   ('Оставить безопасный режим', 'age:settings')]))
        return
    if action in ('confirm', 'safe'):
        try:
            (age_access.confirm_adult if action == 'confirm' else age_access.safe_mode)(user_id)
        except ValueError as error:
            await bot.send_message(call.message.chat.id, str(error), parse_mode='')
    elif action == 'parent':
        await state.set_state('Age:limit')
        await state.update_data(expires=time.time() + 300)
        await bot.send_message(call.message.chat.id, 'Выберите максимальную возрастную маркировку:',
            reply_markup=keyboard([(str(limit) + '+', f'age:limit:{limit}') for limit in (0, 6, 12, 16)] +
                                   [('Отмена', 'age:settings')]))
        return
    elif action == 'off' and age_access.preferences(user_id)['parental']:
        await state.set_state('Age:pin')
        await state.update_data(mode='change', limit=None, expires=time.time() + 300)
        await bot.send_message(call.message.chat.id, 'Введите родительский PIN для отключения режима. '
            'После отключения останется безопасный режим до 16+.', parse_mode='',
            reply_markup=keyboard([('Отмена', 'age:settings')]))
        return
    await show(call.message.chat.id, user_id)


@dp.message_handler(state='Age:pin', content_types=types.ContentTypes.TEXT)
async def pin_input(message, state):
    pin = message.text or ''
    try:
        await message.delete()
    except BadRequest:
        pass
    data = await state.get_data()
    if data.get('expires', 0) < time.time():
        await state.finish()
        await message.answer('Время настройки истекло. Откройте возрастные настройки заново.')
        return
    if data.get('mode') == 'change':
        ok, text = age_access.change_parental(message.from_user.id, pin, data.get('limit'))
        await message.answer(text, parse_mode='')
        if ok:
            await state.finish()
            await show(message.chat.id, message.from_user.id)
        return
    if len(pin) != 6 or not pin.isascii() or not pin.isdigit():
        await message.answer('PIN должен содержать ровно 6 цифр.')
        return
    if data.get('mode') == 'create':
        await state.update_data(mode='confirm', pending_hash=hashlib.sha256(pin.encode('ascii')).hexdigest())
        await message.answer('Повторите PIN из 6 цифр.', reply_markup=keyboard([('Отмена', 'age:settings')]))
        return
    if data.get('mode') != 'confirm' or not hmac.compare_digest(
            hashlib.sha256(pin.encode('ascii')).hexdigest(), data.get('pending_hash', '')):
        await message.answer('PIN не совпадает. Повторите второй ввод или отмените настройку.')
        return
    try:
        age_access.enable_parental(message.from_user.id, data['limit'], pin)
    except ValueError as error:
        await message.answer(str(error), parse_mode='')
    await state.finish()
    await show(message.chat.id, message.from_user.id)
