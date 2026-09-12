import logging
from aiogram import types
from loader import dp, bot
from aiogram.dispatcher import FSMContext
from states.admin_states import Admin_
from keyboards.admin.keyboard import admin_markup, catbuild_finish_markup, catbuild_finish_category_markup

logger = logging.getLogger(__name__)


# ==================== КОНСТРУКТОР КАТЕГОРИЙ (озвучки/качества) ====================


@dp.message_handler(state=Admin_.add_category_name, content_types=types.ContentTypes.TEXT)
async def add_category_name_handler(message: types.Message, state: FSMContext):
    data = await state.get_data()
    if data.get("content_type") == "series":
        season_text = message.text.strip()
        season_number = int(season_text) if season_text.isdigit() else None
        category_name = f"Сезон {season_text}"
        await state.update_data(
            current_category_name=category_name, current_season_number=season_number, current_subcategories=[]
        )
        await bot.send_message(
            message.chat.id, "<strong>Введите название/номер серии (например: Серия 1) :</strong>"
        )
    else:
        await state.update_data(
            current_category_name=message.text, current_season_number=None, current_subcategories=[]
        )
        await bot.send_message(
            message.chat.id, "<strong>Введите название подкатегории/качества (например: 720p) :</strong>"
        )
    await Admin_.add_subcategory_name.set()


@dp.callback_query_handler(lambda call: call.data == "catbuild_finish", state=Admin_.add_category_name)
async def catbuild_finish_handler(call: types.CallbackQuery, state: FSMContext):
    await bot.delete_message(call.message.chat.id, call.message.message_id)
    await _finalize_movie_and_categories(call.message.chat.id, state)


@dp.message_handler(state=Admin_.add_subcategory_name, content_types=types.ContentTypes.TEXT)
async def add_subcategory_name_handler(message: types.Message, state: FSMContext):
    await state.update_data(current_subcategory_name=message.text)
    data = await state.get_data()
    await bot.send_message(
        message.chat.id,
        f"<strong>Отправьте видео или документ для «{data.get('current_category_name')}» "
        f"- «{message.text}» :</strong>",
    )
    await Admin_.add_subcategory_file.set()


@dp.callback_query_handler(
    lambda call: call.data == "catbuild_finish_category", state=Admin_.add_subcategory_name
)
async def catbuild_finish_category_handler(call: types.CallbackQuery, state: FSMContext):
    await bot.delete_message(call.message.chat.id, call.message.message_id)
    await _close_current_category(state)
    data = await state.get_data()
    if data.get("content_type") == "series":
        prompt = (
            "<strong>Введите номер следующего сезона (например: 2), "
            "или нажмите «Завершить», если сезоны больше не нужны :</strong>"
        )
    else:
        prompt = (
            "<strong>Введите название следующей категории (например: Английская озвучка), "
            "или нажмите «Завершить», если категории больше не нужны :</strong>"
        )
    await bot.send_message(call.message.chat.id, prompt, reply_markup=catbuild_finish_markup())
    await Admin_.add_category_name.set()


@dp.message_handler(
    state=Admin_.add_subcategory_file, content_types=[types.ContentType.DOCUMENT, types.ContentType.VIDEO]
)
async def add_subcategory_file_handler(message: types.Message, state: FSMContext):
    data = await state.get_data()
    if message.content_type == "document":
        file_id, file_type = message.document.file_id, "document"
    else:
        file_id, file_type = message.video.file_id, "video"
    subcategories = data.get("current_subcategories", [])
    subcategories.append(
        {"name": data.get("current_subcategory_name"), "file_id": file_id, "file_type": file_type}
    )
    await state.update_data(current_subcategories=subcategories)
    await bot.send_message(
        message.chat.id,
        "<strong>Подкатегория добавлена ✅</strong>\n\n"
        "Введите название следующей подкатегории/качества (например: 1080p), "
        "или нажмите «Завершить категорию» :",
        reply_markup=catbuild_finish_category_markup(),
    )
    await Admin_.add_subcategory_name.set()


@dp.message_handler(state=Admin_.add_subcategory_file, content_types=types.ContentTypes.TEXT)
async def add_subcategory_file_wrong_content_handler(message: types.Message, state: FSMContext):
    await bot.send_message(
        message.chat.id,
        "<strong>Нужно отправить видео или документ, либо нажмите «Завершить категорию» :</strong>",
        reply_markup=catbuild_finish_category_markup(),
    )


async def _close_current_category(state: FSMContext):
    data = await state.get_data()
    categories = data.get("categories", [])
    current_name = data.get("current_category_name")
    if current_name:
        categories.append(
            {
                "name": current_name,
                "season_number": data.get("current_season_number"),
                "subcategories": data.get("current_subcategories", []),
            }
        )
    await state.update_data(
        categories=categories,
        current_category_name=None,
        current_season_number=None,
        current_subcategories=[],
    )


async def _finalize_movie_and_categories(chat_id: int, state: FSMContext):
    # если конструктор категорий был открыт и в нём осталась незакрытая категория - закрываем её
    data = await state.get_data()
    if data.get("current_category_name"):
        await _close_current_category(state)
        data = await state.get_data()

    from services.catalog import save_movie_draft

    movie_number, result_message = save_movie_draft(data)

    await state.finish()
    await bot.send_message(chat_id, result_message, reply_markup=admin_markup())
