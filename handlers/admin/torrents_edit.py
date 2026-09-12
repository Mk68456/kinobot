import logging
from aiogram import types
from loader import dp, bot
from aiogram.dispatcher import FSMContext
from database.torrents import add_movie_torrent, get_torrents_by_movie, delete_torrent
from database.movie_queries import get_movie_title_by_numb
from database.movie_queries import get_movie_content_type
from states.admin_states import Admin_
from keyboards.admin.keyboard import (
    edit_movie_menu_markup,
    torrents_menu_markup,
    torrents_delete_pick_markup,
    torrents_finish_markup,
)

logger = logging.getLogger(__name__)


# ==================== TORRENT-ФАЙЛЫ ====================


@dp.callback_query_handler(lambda call: call.data == "editm_torrents", state=Admin_.edit_movie_menu)
async def editm_torrents_handler(call: types.CallbackQuery, state: FSMContext):
    await call.message.edit_text(
        "<strong>Torrent-файлы фильма/сериала :</strong>", reply_markup=torrents_menu_markup()
    )
    await Admin_.edit_torrents_menu.set()


@dp.callback_query_handler(lambda call: call.data == "trmenu_back", state=Admin_.edit_torrents_menu)
async def trmenu_back_handler(call: types.CallbackQuery, state: FSMContext):
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


@dp.callback_query_handler(lambda call: call.data == "trmenu_add", state=Admin_.edit_torrents_menu)
async def trmenu_add_handler(call: types.CallbackQuery, state: FSMContext):
    await call.message.delete()
    await bot.send_message(
        call.message.chat.id,
        "<strong>Введите название torrent-файла (например: 1080p или Сезон 1) :</strong>",
    )
    await Admin_.add_torrent_name.set()


@dp.message_handler(state=Admin_.add_torrent_name, content_types=types.ContentTypes.TEXT)
async def add_torrent_name_handler(message: types.Message, state: FSMContext):
    await state.update_data(current_torrent_name=message.text)
    await bot.send_message(message.chat.id, f"<strong>Отправьте torrent-файл для «{message.text}» :</strong>")
    await Admin_.add_torrent_file.set()


@dp.message_handler(state=Admin_.add_torrent_file, content_types=types.ContentTypes.DOCUMENT)
async def add_torrent_file_handler(message: types.Message, state: FSMContext):
    data = await state.get_data()
    numb = data.get("edit_movie_number")
    add_movie_torrent(numb, data.get("current_torrent_name"), message.document.file_id)
    await bot.send_message(
        message.chat.id,
        "<strong>Torrent-файл добавлен ✅</strong>\n\n"
        "Введите название следующего torrent-файла, или нажмите «Завершить» :",
        reply_markup=torrents_finish_markup(),
    )
    await Admin_.add_torrent_name.set()


@dp.message_handler(state=Admin_.add_torrent_file, content_types=types.ContentTypes.TEXT)
async def add_torrent_file_wrong_content_handler(message: types.Message, state: FSMContext):
    await bot.send_message(
        message.chat.id, "<strong>Нужно отправить именно файл (документ), например .torrent :</strong>"
    )


@dp.callback_query_handler(lambda call: call.data == "trbuild_finish", state=Admin_.add_torrent_name)
async def trbuild_finish_handler(call: types.CallbackQuery, state: FSMContext):
    await call.message.delete()
    await bot.send_message(
        call.message.chat.id,
        "<strong>Torrent-файлы фильма/сериала :</strong>",
        reply_markup=torrents_menu_markup(),
    )
    await Admin_.edit_torrents_menu.set()


@dp.callback_query_handler(lambda call: call.data == "trmenu_list", state=Admin_.edit_torrents_menu)
async def trmenu_list_handler(call: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    numb = data.get("edit_movie_number")
    torrents = get_torrents_by_movie(numb)
    if not torrents:
        await call.answer("У этого фильма/сериала пока нет torrent-файлов.", show_alert=True)
        return
    lines = [name for _, name in torrents]
    await bot.send_message(call.message.chat.id, "<strong>Torrent-файлы :</strong>\n\n" + "\n".join(lines))
    await call.answer()


@dp.callback_query_handler(lambda call: call.data == "trmenu_delete", state=Admin_.edit_torrents_menu)
async def trmenu_delete_handler(call: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    numb = data.get("edit_movie_number")
    torrents = get_torrents_by_movie(numb)
    if not torrents:
        await call.answer("У этого фильма/сериала пока нет torrent-файлов.", show_alert=True)
        return
    await call.message.edit_text(
        "<strong>Выберите torrent-файл для удаления :</strong>",
        reply_markup=torrents_delete_pick_markup(torrents),
    )
    await Admin_.edit_torrents_delete.set()


@dp.callback_query_handler(lambda call: call.data.startswith("trdel_"), state=Admin_.edit_torrents_delete)
async def trdel_handler(call: types.CallbackQuery, state: FSMContext):
    torrent_id = int(call.data.split("_", 1)[1])
    delete_torrent(torrent_id)
    await call.answer("Torrent-файл удалён")
    await call.message.edit_text(
        "<strong>Torrent-файлы фильма/сериала :</strong>", reply_markup=torrents_menu_markup()
    )
    await Admin_.edit_torrents_menu.set()


@dp.callback_query_handler(lambda call: call.data == "trmenu_back", state=Admin_.edit_torrents_delete)
async def trdel_back_handler(call: types.CallbackQuery, state: FSMContext):
    await call.message.edit_text(
        "<strong>Torrent-файлы фильма/сериала :</strong>", reply_markup=torrents_menu_markup()
    )
    await Admin_.edit_torrents_menu.set()
