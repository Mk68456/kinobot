from aiogram import types
from aiogram.dispatcher import FSMContext
from aiogram.dispatcher.filters.state import State, StatesGroup
from data import settings
from loader import dp
from webpanel.auth import create_login
from webpanel.repository import receive_telegram
from keyboards.admin.roles import buttons
from webpanel import imports
from services.access import is_admin
from services.storage_backup import backup, configure


@dp.message_handler(commands=['desktop'], state='*')
async def desktop_pair(message: types.Message):
    if not settings.WEB_ENABLED:
        await message.answer('Сначала включите панель для связи с лаунчером.')
        return
    token = imports.pair(message.from_user.id)
    await message.answer(
        f'Подключение лаунчера Telegram Desktop\nАдрес: {settings.WEB_PUBLIC_URL}\n'
        f'Код: <code>{token}</code>\nДействует 48 часов. Не передавайте код другим людям. '
        'Он разрешает только импорт и просмотр готовности, но не публикацию.')


@dp.message_handler(lambda message: (message.caption or '').startswith('KTFILE:'),
                    content_types=['video', 'document'], state='*')
async def receive_desktop_file(message: types.Message):
    try:
        asset_id = imports.receive(message.from_user.id, message)
    except ValueError as error:
        await message.answer(str(error))
        return
    await message.answer(f'Файл принят: <code>{asset_id}</code>. Публикация — после одобрения в /web.')
    backup.notify()


class StorageConnect(StatesGroup):
    waiting_forward = State()


@dp.message_handler(commands=['storage'], state='*')
async def storage_connect(message: types.Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    await state.finish()
    await StorageConnect.waiting_forward.set()
    await message.answer(
        'Перешлите сюда любое сообщение из закрытого канала KinoTime Storage. '
        'Бот уже должен быть администратором этого канала с правом публикации.\n\n'
        'Отмена: /cancel')


@dp.message_handler(commands=['cancel'], state=StorageConnect.waiting_forward)
async def storage_cancel(message: types.Message, state: FSMContext):
    await state.finish()
    await message.answer('Подключение Storage-канала отменено.')


@dp.message_handler(state=StorageConnect.waiting_forward, content_types=types.ContentTypes.ANY)
async def storage_forward(message: types.Message, state: FSMContext):
    channel = message.forward_from_chat
    if not channel or channel.type != 'channel':
        await message.answer('Нужно переслать сообщение именно из закрытого канала. Отмена: /cancel')
        return
    me = await message.bot.get_me()
    member = await message.bot.get_chat_member(channel.id, me.id)
    if member.status not in ('administrator', 'creator') or not getattr(member, 'can_post_messages', False):
        await message.answer('Бот не может публиковать в этом канале. Добавьте его администратором и повторите пересылку.')
        return
    configure(message.from_user.id, channel.id, channel.title or 'KinoTime Storage')
    await state.finish()
    await message.answer(
        f'Хранилище подключено: <b>{channel.title}</b>. Новые файлы будут резервироваться автоматически. '
        'Состояние и перенос старого каталога доступны в разделе «Резервирование» WEB-панели.')


class WebInbox(StatesGroup):
    collecting = State()


@dp.message_handler(commands=["web"], state="*")
async def web_login(message: types.Message):
    if not settings.WEB_ENABLED:
        await message.answer("Веб-панель пока не включена. Инструкция запуска: docs/WEB_SETUP.md.")
        return
    token = create_login(message.from_user.id)
    # Fragment never reaches HTTP access logs. Browser exchanges it once for an HttpOnly cookie.
    url = f"{settings.WEB_PUBLIC_URL}/#login={token}"
    await message.answer(
        "Откройте веб-панель. Ссылка одноразовая и действует 10 минут. Не пересылайте её.",
        reply_markup=types.InlineKeyboardMarkup().add(
            types.InlineKeyboardButton("Открыть веб-панель", url=url)
        ),
    )


@dp.message_handler(commands=["webfiles"], state="*")
async def web_inbox(message: types.Message, state: FSMContext):
    await state.finish()
    await WebInbox.collecting.set()
    await message.answer(
        "Отправляйте или пересылайте видео и документы. Они появятся в разделе "
        "«Из Telegram» веб-панели. Можно отправить несколько файлов подряд. "
        "Для завершения — /done.",
        reply_markup=buttons([("Завершить приём", "webinbox:done")]),
    )


@dp.message_handler(commands=["done"], state=WebInbox.collecting)
async def inbox_done(message: types.Message, state: FSMContext):
    await state.finish()
    await message.answer("Приём завершён. Откройте /web, чтобы привязать файлы к каталогу.")


@dp.callback_query_handler(text="webinbox:done", state="*")
async def inbox_done_button(call: types.CallbackQuery, state: FSMContext):
    await state.finish()
    await call.answer("Приём завершён")
    await call.message.answer("Откройте /web, чтобы привязать файлы к каталогу.")


@dp.message_handler(state=WebInbox.collecting, content_types=["video", "document"])
async def receive_web_file(message: types.Message):
    receive_telegram(message.from_user.id, message)
    await message.answer("Файл добавлен в «Из Telegram». Отправьте следующий или /done.")


@dp.message_handler(state=WebInbox.collecting, content_types=types.ContentTypes.ANY)
async def wrong_web_file(message: types.Message):
    await message.answer("Нужно видео или документ. Названия можно изменить в веб-панели. Завершить: /done.")
