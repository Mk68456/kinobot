from aiogram import types
from aiogram.dispatcher import FSMContext
from aiogram.dispatcher.filters.state import State, StatesGroup
from data import settings
from loader import dp
from webpanel.auth import create_login
from webpanel.repository import receive_telegram
from keyboards.admin.roles import buttons


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
