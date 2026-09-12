import logging
from aiogram import types
from loader import dp, bot
from aiogram.dispatcher import FSMContext
from database.categories import (
    add_movie_category,
    add_movie_subcategory,
    get_subcategories_by_category,
    delete_category,
    get_seasons_by_movie,
    get_category_by_id_full,
    update_category,
    update_subcategory_name,
    update_subcategory_file,
    delete_subcategory,
    get_subcategory_by_id,
)
from database.movie_queries import get_movie_title_by_numb
from database.movie_queries import get_movie_content_type
from states.admin_states import Admin_
from keyboards.admin.keyboard import (
    edit_movie_menu_markup,
    seasons_menu_markup,
    season_edit_markup,
    episode_edit_markup,
)

logger = logging.getLogger(__name__)


# ==================== РЕДАКТИРОВАНИЕ СЕЗОНОВ И СЕРИЙ ====================


async def _show_seasons_menu(call, state, delete_message=False):
    data = await state.get_data()
    numb = data.get("edit_movie_number")
    seasons = get_seasons_by_movie(numb)
    if delete_message:
        try:
            await call.message.delete()
        except Exception:
            pass
    text = "<strong>📺 Сезоны и серии</strong>\n\n"
    text += "Выберите сезон для редактирования:" if seasons else "Сезонов пока нет. Добавьте первый сезон:"
    await bot.send_message(call.message.chat.id, text, reply_markup=seasons_menu_markup(seasons))
    await Admin_.edit_seasons_menu.set()


@dp.callback_query_handler(lambda call: call.data == "editm_seasons", state=Admin_.edit_movie_menu)
async def editm_seasons_handler(call: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    numb = data.get("edit_movie_number")
    if get_movie_content_type(numb) != "series":
        await call.answer("Этот контент не является сериалом.", show_alert=True)
        return
    await call.message.delete()
    await _show_seasons_menu(call, state)


@dp.callback_query_handler(lambda call: call.data == "seasons_back", state=Admin_.edit_seasons_menu)
async def seasons_back_handler(call: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    numb = data.get("edit_movie_number")
    title = get_movie_title_by_numb(numb)
    await call.message.edit_text(
        f"<strong>Сериал «{title}» (код {numb})</strong>\n\nЧто хотите изменить ?",
        reply_markup=edit_movie_menu_markup("series"),
    )
    await Admin_.edit_movie_menu.set()


@dp.callback_query_handler(lambda call: call.data == "seasons_back_menu", state=Admin_.edit_season)
async def seasons_back_menu_handler(call: types.CallbackQuery, state: FSMContext):
    await call.message.delete()
    await _show_seasons_menu(call, state)


@dp.callback_query_handler(lambda call: call.data.startswith("seasonedit_"), state=Admin_.edit_seasons_menu)
async def seasonedit_handler(call: types.CallbackQuery, state: FSMContext):
    season_id = int(call.data.split("_", 1)[1])
    season = get_category_by_id_full(season_id)
    if not season:
        await call.answer("Сезон не найден.", show_alert=True)
        return
    _, movie_number, name, season_number = season
    subs = get_subcategories_by_category(season_id)
    await state.update_data(edit_season_id=season_id)
    label = name or (f"Сезон {season_number}" if season_number is not None else "Сезон")
    lines = [f"<strong>📺 {label}</strong>", "", f"Серий: <strong>{len(subs)}</strong>"]
    if subs:
        lines.append("")
        lines.append("Выберите серию для редактирования:")
    else:
        lines.append("")
        lines.append("Серий пока нет.")
    await call.message.edit_text("\n".join(lines), reply_markup=season_edit_markup(season_id, subs))
    await Admin_.edit_season.set()
    await call.answer()


@dp.callback_query_handler(lambda call: call.data == "season_add", state=Admin_.edit_seasons_menu)
async def season_add_handler(call: types.CallbackQuery, state: FSMContext):
    await call.message.delete()
    await bot.send_message(
        call.message.chat.id, "<strong>Введите номер сезона</strong> (например, 1) или название сезона:"
    )
    await Admin_.add_season_name.set()


@dp.message_handler(state=Admin_.add_season_name, content_types=types.ContentTypes.TEXT)
async def add_season_name_handler(message: types.Message, state: FSMContext):
    data = await state.get_data()
    value = message.text.strip()
    if not value:
        await bot.send_message(message.chat.id, "Название сезона не может быть пустым.")
        return
    season_number = int(value) if value.isdigit() else None
    name = f"Сезон {value}" if value.isdigit() else value
    season_id = add_movie_category(data.get("edit_movie_number"), name, season_number)
    await state.update_data(edit_season_id=season_id)
    await bot.send_message(
        message.chat.id,
        f"<strong>{name}</strong> создан ✅\n\nТеперь можно добавлять серии:",
        reply_markup=season_edit_markup(season_id, []),
    )
    await Admin_.edit_season.set()


@dp.callback_query_handler(lambda call: call.data.startswith("seasonrename_"), state=Admin_.edit_season)
async def seasonrename_handler(call: types.CallbackQuery, state: FSMContext):
    season_id = int(call.data.split("_", 1)[1])
    season = get_category_by_id_full(season_id)
    if not season:
        await call.answer("Сезон не найден.", show_alert=True)
        return
    await state.update_data(edit_season_id=season_id)
    await call.message.delete()
    await bot.send_message(
        call.message.chat.id,
        "<strong>Введите новое название/номер сезона</strong> (например: 2 или «Спецвыпуски»):",
    )
    await Admin_.edit_season_name.set()


@dp.message_handler(state=Admin_.edit_season_name, content_types=types.ContentTypes.TEXT)
async def edit_season_name_handler(message: types.Message, state: FSMContext):
    data = await state.get_data()
    season_id = data.get("edit_season_id")
    value = message.text.strip()
    if not value:
        await bot.send_message(message.chat.id, "Название сезона не может быть пустым.")
        return
    season_number = int(value) if value.isdigit() else None
    name = f"Сезон {value}" if value.isdigit() else value
    update_category(season_id, name, season_number)
    subs = get_subcategories_by_category(season_id)
    await bot.send_message(
        message.chat.id,
        f"<strong>{name}</strong> изменён ✅",
        reply_markup=season_edit_markup(season_id, subs),
    )
    await Admin_.edit_season.set()


@dp.callback_query_handler(lambda call: call.data.startswith("seasondelete_"), state=Admin_.edit_season)
async def seasondelete_handler(call: types.CallbackQuery, state: FSMContext):
    season_id = int(call.data.split("_", 1)[1])
    season = get_category_by_id_full(season_id)
    if not season:
        await call.answer("Сезон уже удалён.", show_alert=True)
        return
    delete_category(season_id)
    await call.answer("Сезон удалён ✅")
    await call.message.delete()
    await _show_seasons_menu(call, state)


@dp.callback_query_handler(lambda call: call.data.startswith("episodeedit_"), state=Admin_.edit_season)
async def episodeedit_handler(call: types.CallbackQuery, state: FSMContext):
    sub_id = int(call.data.split("_", 1)[1])
    sub = get_subcategory_by_id(sub_id)
    if not sub:
        await call.answer("Серия не найдена.", show_alert=True)
        return
    _, season_id, name, file_id, file_type = sub
    await state.update_data(edit_episode_id=sub_id, edit_season_id=season_id)
    await call.message.edit_text(
        f"<strong>🎬 Серия: {name}</strong>\n\nФайл: {file_type or 'не указан'}",
        reply_markup=episode_edit_markup(sub_id, season_id),
    )
    await call.answer()


@dp.callback_query_handler(lambda call: call.data.startswith("episode_add_"), state=Admin_.edit_season)
async def episode_add_handler(call: types.CallbackQuery, state: FSMContext):
    season_id = int(call.data.split("_", 2)[2])
    if not get_category_by_id_full(season_id):
        await call.answer("Сезон не найден.", show_alert=True)
        return
    await state.update_data(edit_season_id=season_id)
    await call.message.delete()
    await bot.send_message(
        call.message.chat.id, "<strong>Введите название/номер новой серии</strong> (например: Серия 1):"
    )
    await Admin_.add_episode_name.set()


@dp.message_handler(state=Admin_.add_episode_name, content_types=types.ContentTypes.TEXT)
async def add_episode_name_handler(message: types.Message, state: FSMContext):
    value = message.text.strip()
    if not value:
        await bot.send_message(message.chat.id, "Название серии не может быть пустым.")
        return
    await state.update_data(current_episode_name=value)
    await bot.send_message(message.chat.id, f"<strong>Отправьте видео или документ для «{value}» :</strong>")
    await Admin_.edit_episode_file.set()
    # flag distinguishes creation from replacement
    await state.update_data(edit_episode_id=None)


@dp.callback_query_handler(lambda call: call.data.startswith("episodename_"), state=Admin_.edit_season)
async def episodename_handler(call: types.CallbackQuery, state: FSMContext):
    sub_id = int(call.data.split("_", 1)[1])
    sub = get_subcategory_by_id(sub_id)
    if not sub:
        await call.answer("Серия не найдена.", show_alert=True)
        return
    await state.update_data(edit_episode_id=sub_id, edit_season_id=sub[1])
    await call.message.delete()
    await bot.send_message(call.message.chat.id, "<strong>Введите новое название серии:</strong>")
    await Admin_.edit_episode_name.set()


@dp.message_handler(state=Admin_.edit_episode_name, content_types=types.ContentTypes.TEXT)
async def edit_episode_name_handler(message: types.Message, state: FSMContext):
    data = await state.get_data()
    sub_id = data.get("edit_episode_id")
    if not message.text.strip():
        await bot.send_message(message.chat.id, "Название серии не может быть пустым.")
        return
    update_subcategory_name(sub_id, message.text.strip())
    sub = get_subcategory_by_id(sub_id)
    subs = get_subcategories_by_category(sub[1]) if sub else []
    await bot.send_message(
        message.chat.id, "Название серии изменено ✅", reply_markup=season_edit_markup(sub[1], subs)
    )
    await Admin_.edit_season.set()


@dp.callback_query_handler(lambda call: call.data.startswith("episodefile_"), state=Admin_.edit_season)
async def episodefile_handler(call: types.CallbackQuery, state: FSMContext):
    sub_id = int(call.data.split("_", 1)[1])
    sub = get_subcategory_by_id(sub_id)
    if not sub:
        await call.answer("Серия не найдена.", show_alert=True)
        return
    await state.update_data(edit_episode_id=sub_id, edit_season_id=sub[1])
    await call.message.delete()
    await bot.send_message(
        call.message.chat.id, "<strong>Отправьте новый файл серии</strong> (видео или документ):"
    )
    await Admin_.edit_episode_file.set()


@dp.message_handler(
    state=Admin_.edit_episode_file, content_types=[types.ContentType.DOCUMENT, types.ContentType.VIDEO]
)
async def edit_episode_file_handler(message: types.Message, state: FSMContext):
    data = await state.get_data()
    season_id = data.get("edit_season_id")
    sub_id = data.get("edit_episode_id")
    if message.content_type == "document":
        file_id, file_type = message.document.file_id, "document"
    else:
        file_id, file_type = message.video.file_id, "video"
    if sub_id:
        update_subcategory_file(sub_id, file_id, file_type)
        text = "Файл серии заменён ✅"
    else:
        add_movie_subcategory(season_id, data.get("current_episode_name"), file_id, file_type)
        text = "Серия добавлена ✅"
    subs = get_subcategories_by_category(season_id)
    await state.update_data(edit_episode_id=None, current_episode_name=None)
    await bot.send_message(message.chat.id, text, reply_markup=season_edit_markup(season_id, subs))
    await Admin_.edit_season.set()


@dp.message_handler(state=Admin_.edit_episode_file, content_types=types.ContentTypes.TEXT)
async def edit_episode_file_wrong_handler(message: types.Message, state: FSMContext):
    await bot.send_message(message.chat.id, "<strong>Нужно отправить видео или документ.</strong>")


@dp.callback_query_handler(lambda call: call.data.startswith("episodedelete_"), state=Admin_.edit_season)
async def episodedelete_handler(call: types.CallbackQuery, state: FSMContext):
    sub_id = int(call.data.split("_", 1)[1])
    sub = get_subcategory_by_id(sub_id)
    if not sub:
        await call.answer("Серия уже удалена.", show_alert=True)
        return
    season_id = sub[1]
    delete_subcategory(sub_id)
    subs = get_subcategories_by_category(season_id)
    await call.answer("Серия удалена ✅")
    await call.message.edit_text(
        f"<strong>Сезон</strong>\n\nСерий: <strong>{len(subs)}</strong>",
        reply_markup=season_edit_markup(season_id, subs),
    )


@dp.callback_query_handler(lambda call: call.data.startswith("seasonedit_"), state=Admin_.edit_season)
async def seasonedit_from_episode_handler(call: types.CallbackQuery, state: FSMContext):
    # Возврат из карточки серии к сезону.
    season_id = int(call.data.split("_", 1)[1])
    season = get_category_by_id_full(season_id)
    if not season:
        await call.answer("Сезон не найден.", show_alert=True)
        return
    _, _, name, season_number = season
    subs = get_subcategories_by_category(season_id)
    await state.update_data(edit_season_id=season_id)
    label = name or (f"Сезон {season_number}" if season_number is not None else "Сезон")
    await call.message.edit_text(
        f"<strong>📺 {label}</strong>\n\nСерий: <strong>{len(subs)}</strong>",
        reply_markup=season_edit_markup(season_id, subs),
    )
    await call.answer()
