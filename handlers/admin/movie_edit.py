import logging
import re
from aiogram import types
from loader import dp, bot
from aiogram.dispatcher import FSMContext
from database.movies import update_movie_title, update_movie_description, update_movie_trailer
from database.categories import get_categories_by_movie, get_subcategories_by_category, delete_category
from database.movie_queries import get_movie_title_by_numb
from database.movie_queries import get_movie_content_type
from states.admin_states import Admin_
from keyboards.admin.keyboard import (
    admin_markup,
    catbuild_finish_markup,
    edit_movie_menu_markup,
    categories_menu_markup,
    categories_delete_pick_markup,
)

logger = logging.getLogger(__name__)


# ==================== ИЗМЕНЕНИЕ ФИЛЬМА ====================


@dp.message_handler(state=Admin_.edit_movie_select)
async def edit_movie_select_handler(message: types.Message, state: FSMContext):
    if message.text == "Назад":
        await state.finish()
        await message.delete()
        await bot.send_message(
            message.chat.id, "Изменение фильма отменено", reply_markup=types.ReplyKeyboardRemove()
        )
        await bot.send_message(message.chat.id, "Админ-панель", reply_markup=admin_markup())
        return
    match = re.match(r"^\D*(\d+)\s*-\s*", (message.text or "").strip())
    numb_part = match.group(1) if match else ""
    if not numb_part.isdigit():
        await bot.send_message(message.chat.id, "Пожалуйста, выберите фильм кнопкой из списка.")
        return
    movie_title = get_movie_title_by_numb(int(numb_part))
    if movie_title is None:
        await bot.send_message(message.chat.id, "Такой фильм не найден, выберите из списка.")
        return
    movie_number = int(numb_part)
    content_type = get_movie_content_type(movie_number)
    await state.update_data(edit_movie_number=movie_number, edit_content_type=content_type)
    label = "Сериал" if content_type == "series" else "Фильм"
    await bot.send_message(
        message.chat.id,
        f"<strong>{label} «{movie_title}» (код {numb_part})</strong>\n\nЧто хотите изменить ?",
        reply_markup=types.ReplyKeyboardRemove(),
    )
    await bot.send_message(
        message.chat.id, "Выберите действие :", reply_markup=edit_movie_menu_markup(content_type)
    )
    await Admin_.edit_movie_menu.set()


@dp.callback_query_handler(lambda call: call.data == "editm_back", state=Admin_.edit_movie_menu)
async def editm_back_handler(call: types.CallbackQuery, state: FSMContext):
    await call.message.delete()
    await state.finish()
    await bot.send_message(call.message.chat.id, "Админ-панель", reply_markup=admin_markup())


@dp.callback_query_handler(lambda call: call.data == "editm_title", state=Admin_.edit_movie_menu)
async def editm_title_handler(call: types.CallbackQuery, state: FSMContext):
    await call.message.delete()
    await bot.send_message(call.message.chat.id, "<strong>Введите новое название :</strong>")
    await Admin_.edit_movie_title.set()


@dp.message_handler(state=Admin_.edit_movie_title, content_types=types.ContentTypes.TEXT)
async def edit_movie_title_handler(message: types.Message, state: FSMContext):
    data = await state.get_data()
    update_movie_title(data.get("edit_movie_number"), message.text)
    await state.finish()
    await bot.send_message(message.chat.id, "Название успешно изменено ✅", reply_markup=admin_markup())


@dp.callback_query_handler(lambda call: call.data == "editm_desc", state=Admin_.edit_movie_menu)
async def editm_desc_handler(call: types.CallbackQuery, state: FSMContext):
    await call.message.delete()
    await bot.send_message(
        call.message.chat.id,
        "<strong>Введите новое описание (или отправьте «-», чтобы удалить описание) :</strong>",
    )
    await Admin_.edit_movie_description.set()


@dp.message_handler(state=Admin_.edit_movie_description, content_types=types.ContentTypes.TEXT)
async def edit_movie_description_handler(message: types.Message, state: FSMContext):
    data = await state.get_data()
    new_description = None if message.text.strip() == "-" else message.text
    update_movie_description(data.get("edit_movie_number"), new_description)
    await state.finish()
    await bot.send_message(message.chat.id, "Описание успешно изменено ✅", reply_markup=admin_markup())


@dp.callback_query_handler(lambda call: call.data == "editm_trailer", state=Admin_.edit_movie_menu)
async def editm_trailer_handler(call: types.CallbackQuery, state: FSMContext):
    await call.message.delete()
    await bot.send_message(
        call.message.chat.id,
        "<strong>Отправьте новый видео-трейлер (или отправьте «-», чтобы удалить трейлер) :</strong>",
    )
    await Admin_.edit_movie_trailer.set()


@dp.message_handler(state=Admin_.edit_movie_trailer, content_types=types.ContentTypes.VIDEO)
async def edit_movie_trailer_video_handler(message: types.Message, state: FSMContext):
    data = await state.get_data()
    update_movie_trailer(data.get("edit_movie_number"), message.video.file_id)
    await state.finish()
    await bot.send_message(message.chat.id, "Трейлер успешно изменён ✅", reply_markup=admin_markup())


@dp.message_handler(state=Admin_.edit_movie_trailer, content_types=types.ContentTypes.TEXT)
async def edit_movie_trailer_text_handler(message: types.Message, state: FSMContext):
    data = await state.get_data()
    if message.text.strip() == "-":
        update_movie_trailer(data.get("edit_movie_number"), None)
        await state.finish()
        await bot.send_message(message.chat.id, "Трейлер удалён ✅", reply_markup=admin_markup())
    else:
        await bot.send_message(
            message.chat.id,
            "<strong>Нужно отправить видео-файл трейлера, либо «-», чтобы его удалить :</strong>",
        )


@dp.callback_query_handler(lambda call: call.data == "editm_cat", state=Admin_.edit_movie_menu)
async def editm_cat_handler(call: types.CallbackQuery, state: FSMContext):
    await call.message.edit_text(
        "<strong>Категории (озвучки/качества) :</strong>", reply_markup=categories_menu_markup()
    )
    await Admin_.edit_categories_menu.set()


@dp.callback_query_handler(lambda call: call.data == "catmenu_back", state=Admin_.edit_categories_menu)
async def catmenu_back_handler(call: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    numb = data.get("edit_movie_number")
    movie_title = get_movie_title_by_numb(numb)
    content_type = get_movie_content_type(numb)
    label = "Сериал" if content_type == "series" else "Фильм"
    await call.message.edit_text(
        f"<strong>{label} «{movie_title}» (код {numb})</strong>\n\nЧто хотите изменить ?",
        reply_markup=edit_movie_menu_markup(content_type),
    )
    await Admin_.edit_movie_menu.set()


@dp.callback_query_handler(lambda call: call.data == "catmenu_add", state=Admin_.edit_categories_menu)
async def catmenu_add_handler(call: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    numb = data.get("edit_movie_number")
    content_type = get_movie_content_type(numb)
    await state.update_data(existing_movie_number=numb, categories=[], content_type=content_type)
    await call.message.delete()
    if content_type == "series":
        prompt = (
            "<strong>Введите номер сезона (например: 1), "
            "или нажмите «Завершить», если больше добавлять не нужно :</strong>"
        )
    else:
        prompt = (
            "<strong>Введите название категории (например: Русская озвучка), "
            "или нажмите «Завершить», если больше добавлять не нужно :</strong>"
        )
    await bot.send_message(call.message.chat.id, prompt, reply_markup=catbuild_finish_markup())
    await Admin_.add_category_name.set()


@dp.callback_query_handler(lambda call: call.data == "catmenu_list", state=Admin_.edit_categories_menu)
async def catmenu_list_handler(call: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    numb = data.get("edit_movie_number")
    categories = get_categories_by_movie(numb)
    if not categories:
        await call.answer("У этого фильма пока нет категорий.", show_alert=True)
        return
    lines = []
    for category_id, name in categories:
        subs = get_subcategories_by_category(category_id)
        subs_text = ", ".join(sub[1] for sub in subs) if subs else "нет подкатегорий"
        lines.append(f"<strong>{name}</strong> : {subs_text}")
    await bot.send_message(call.message.chat.id, "<strong>Категории фильма :</strong>\n\n" + "\n".join(lines))
    await call.answer()


@dp.callback_query_handler(lambda call: call.data == "catmenu_delete", state=Admin_.edit_categories_menu)
async def catmenu_delete_handler(call: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    numb = data.get("edit_movie_number")
    categories = get_categories_by_movie(numb)
    if not categories:
        await call.answer("У этого фильма пока нет категорий.", show_alert=True)
        return
    await call.message.edit_text(
        "<strong>Выберите категорию для удаления :</strong>",
        reply_markup=categories_delete_pick_markup(categories),
    )
    await Admin_.edit_categories_delete.set()


@dp.callback_query_handler(lambda call: call.data.startswith("catdel_"), state=Admin_.edit_categories_delete)
async def catdel_handler(call: types.CallbackQuery, state: FSMContext):
    category_id = int(call.data.split("_", 1)[1])
    delete_category(category_id)
    await call.answer("Категория удалена")
    await call.message.edit_text(
        "<strong>Категории (озвучки/качества) :</strong>", reply_markup=categories_menu_markup()
    )
    await Admin_.edit_categories_menu.set()


@dp.callback_query_handler(lambda call: call.data == "catmenu_back", state=Admin_.edit_categories_delete)
async def catdel_back_handler(call: types.CallbackQuery, state: FSMContext):
    await call.message.edit_text(
        "<strong>Категории (озвучки/качества) :</strong>", reply_markup=categories_menu_markup()
    )
    await Admin_.edit_categories_menu.set()
