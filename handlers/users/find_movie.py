from database.channels import get_all_channels_links
import logging
from aiogram import types
from aiogram.utils.exceptions import MessageNotModified
from database import progress
from loader import dp, bot
from aiogram.dispatcher import FSMContext
from database.search import get_movie_from_numb, get_movies_by_title
from database.categories import (
    get_categories_by_movie,
    get_subcategories_by_category,
    get_subcategory_by_id,
    get_category_by_id,
)
from database.torrents import get_torrents_by_movie, get_torrent_by_id
from database.stats import log_search, log_watch
from .check_user_sub import check_user_sub
from services.subscriptions import check_subscription
from keyboards.inline.sub_keyboard import sub_markup
from keyboards.users.keyboard import find_movie_markup, get_movies_pick_markup, torrents_pick_markup
from states.user_states import Users_

logger = logging.getLogger(__name__)

_TYPE_LABELS = {"movie": "фильм", "series": "сериал"}
_TYPE_LABELS_PLURAL = {"movie": "Фильмы", "series": "Сериалы"}


def _catalog_markup(page=0, content_type=None, user_id=None):
    from database.movie_queries import get_movies_page

    rows, has_more = get_movies_page(page, content_type)
    watched = {
        numb
        for title, numb in rows
        if progress.is_watched(user_id, numb, content_type or get_movie_from_numb(numb)["content_type"])
    }
    return get_movies_pick_markup(rows, page, content_type, has_more, watched)


async def _open_catalog(chat_id: int, content_type: str, state: FSMContext):
    await state.update_data(content_type=content_type)
    label = _TYPE_LABELS_PLURAL.get(content_type, "Фильмы")
    await bot.send_message(
        chat_id,
        f"<strong>{label}</strong>\n\n"
        "Введите название (можно несколько ключевых слов в любом порядке) "
        "или код, либо выберите из списка:",
        reply_markup=_catalog_markup(0, content_type, chat_id),
    )
    await Users_.find_movie.set()


@dp.callback_query_handler(lambda call: call.data == "find_movie_movie")
async def find_movie_button_handler(call: types.CallbackQuery, state: FSMContext):
    chat_id = call.message.chat.id
    check = await check_subscription(call.from_user.id)
    if check != False:
        await call.message.delete()
        await _open_catalog(chat_id, "movie", state)
    else:
        await call.answer("Вы не подписались на каналы !", show_alert=True)


@dp.callback_query_handler(lambda call: call.data == "find_movie_series")
async def find_series_button_handler(call: types.CallbackQuery, state: FSMContext):
    chat_id = call.message.chat.id
    check = await check_subscription(call.from_user.id)
    if check != False:
        await call.message.delete()
        await _open_catalog(chat_id, "series", state)
    else:
        await call.answer("Вы не подписались на каналы !", show_alert=True)


@dp.callback_query_handler(lambda call: call.data.startswith("moviepage_"), state=Users_.find_movie)
async def movie_page_handler(call: types.CallbackQuery, state: FSMContext):
    _, ct, page = call.data.split("_", 2)
    content_type = None if ct == "all" else ct
    await call.message.edit_reply_markup(
        reply_markup=_catalog_markup(max(0, int(page)), content_type, call.from_user.id)
    )
    await call.answer()


def _download_markup(code):
    markup = types.InlineKeyboardMarkup(row_width=1)
    markup.add(types.InlineKeyboardButton(text="📥 Скачать файл", callback_data=f"dlfile_{code}"))
    return markup


def _mark_button(kind, item_id, watched, context="c", page=0):
    label = (
        "Снять отметку"
        if watched
        else {"f": "Фильм просмотрен", "s": "Сезон просмотрен", "e": "Просмотрена"}[kind]
    )
    return types.InlineKeyboardButton(
        text=label, callback_data=f"wp:{kind}:{item_id}:{int(not watched)}:{context}:{page}"
    )


def _episode_file_markup(episode_id, user_id):
    episode = get_subcategory_by_id(episode_id)
    if episode is None:
        return None
    category_id = episode[1]
    category = get_category_by_id(category_id)
    info = get_movie_from_numb(category[1]) if category else None
    markup = types.InlineKeyboardMarkup(row_width=2)
    if info and info["content_type"] == "series":
        episodes = get_subcategories_by_category(category_id)
        ids = [row[0] for row in episodes]
        index = ids.index(episode_id)
        navigation = []
        if index:
            navigation.append(
                types.InlineKeyboardButton("⬅️ Предыдущая серия", callback_data=f"movsub_{ids[index - 1]}")
            )
        if index + 1 < len(ids):
            navigation.append(
                types.InlineKeyboardButton("Следующая серия ➡️", callback_data=f"movsub_{ids[index + 1]}")
            )
        if navigation:
            markup.row(*navigation)
        watched = episode_id in progress.episode_marks(user_id, category_id)
        markup.add(
            types.InlineKeyboardButton(
                "✅ Просмотрено · снять отметку" if watched else "☑️ Отметить просмотренным",
                callback_data=f"wp:e:{episode_id}:{int(not watched)}:m:0",
            )
        )
        markup.add(
            types.InlineKeyboardButton("↩️ Назад к сериям", callback_data=f"wpage:{category_id}:{index // 8}")
        )
    else:
        markup.add(types.InlineKeyboardButton("↩️ Назад", callback_data=f"movcat_{category_id}"))
    return markup


def _categories_markup(code, categories, user_id):
    markup = types.InlineKeyboardMarkup(row_width=2)
    info = get_movie_from_numb(code)
    series = info and info["content_type"] == "series"
    counts = progress.season_progress(user_id, code) if series else {}
    for category_id, name in categories:
        watched, total = counts.get(category_id, (0, 0))
        complete = bool(total and watched == total)
        label = f"{name[:48]} {'✅' if complete else '❌'} ({watched}/{total})" if series else name
        button = types.InlineKeyboardButton(text=label, callback_data=f"movcat_{category_id}")
        if series and total:
            markup.row(button, _mark_button("s", category_id, complete))
        else:
            markup.add(button)
    return markup


def _subcategories_markup(movie_number, category_id, subcategories, user_id, page=0):
    markup = types.InlineKeyboardMarkup(row_width=2)
    info = get_movie_from_numb(movie_number)
    series = info and info["content_type"] == "series"
    marks = progress.episode_marks(user_id, category_id) if series else set()
    page = max(0, min(page, max(0, (len(subcategories) - 1) // 8)))
    if series and subcategories:
        complete = len(marks) == len(subcategories)
        markup.add(_mark_button("s", category_id, complete, "p", page))
    for sub_id, name, file_id, file_type in subcategories[page * 8 : page * 8 + 8]:
        watched = sub_id in marks
        label = f"{name[:52]} {'✅' if watched else '❌'}" if series else name
        download = types.InlineKeyboardButton(text=label, callback_data=f"movsub_{sub_id}")
        if series:
            markup.row(download, _mark_button("e", sub_id, watched, "p", page))
        else:
            markup.add(download)
    navigation = []
    if page:
        navigation.append(types.InlineKeyboardButton("⬅️", callback_data=f"wpage:{category_id}:{page - 1}"))
    if (page + 1) * 8 < len(subcategories):
        navigation.append(types.InlineKeyboardButton("➡️", callback_data=f"wpage:{category_id}:{page + 1}"))
    if navigation:
        markup.row(*navigation)
    markup.add(
        types.InlineKeyboardButton(
            text="⬅️ Назад к озвучкам/сезонам", callback_data=f"movcatback_{movie_number}"
        )
    )
    return markup


def _build_pick_markup(code, movie_info, user_id):
    categories = get_categories_by_movie(code)
    if categories:
        pick_markup = _categories_markup(code, categories, user_id)
    elif movie_info.get("movie_file"):
        pick_markup = _download_markup(code)
    else:
        pick_markup = types.InlineKeyboardMarkup(row_width=1)
    if movie_info.get("content_type") != "series":
        watched = progress.is_watched(user_id, code, "movie")
        pick_markup.add(
            types.InlineKeyboardButton(
                text=f"Фильм {'✅ просмотрен' if watched else '❌ не просмотрен'} · {'снять отметку' if watched else 'отметить'}",
                callback_data=f"wp:f:{code}:{int(not watched)}:c:0",
            )
        )
    if get_torrents_by_movie(code):
        pick_markup.add(
            types.InlineKeyboardButton(text="📁 Torrent-файлы", callback_data=f"movtorrents_{code}")
        )
    return pick_markup


@dp.callback_query_handler(lambda call: call.data.startswith("wp:"), state="*")
async def watch_progress_handler(call: types.CallbackQuery):
    parts = call.data.split(":")
    if (
        len(parts) != 6
        or parts[1] not in ("f", "s", "e")
        or parts[3] not in ("0", "1")
        or parts[4] not in ("c", "p", "m")
        or (parts[4] == "m" and parts[1] != "e")
        or not parts[2].isascii()
        or not parts[2].isdigit()
        or not parts[5].isascii()
        or not parts[5].isdigit()
        or len(parts[2]) > 18
        or len(parts[5]) > 8
    ):
        await call.answer("Кнопка недействительна.", show_alert=True)
        return
    try:
        movie_number, category_id = progress.set_watched(
            call.from_user.id, parts[1], int(parts[2]), parts[3] == "1"
        )
    except ValueError as error:
        await call.answer(str(error), show_alert=True)
        return
    if parts[4] == "m":
        markup = _episode_file_markup(int(parts[2]), call.from_user.id)
    elif parts[4] == "p" and category_id is not None:
        markup = _subcategories_markup(
            movie_number,
            category_id,
            get_subcategories_by_category(category_id),
            call.from_user.id,
            int(parts[5]),
        )
    else:
        markup = _build_pick_markup(movie_number, get_movie_from_numb(movie_number), call.from_user.id)
    try:
        await call.message.edit_reply_markup(reply_markup=markup)
    except MessageNotModified:
        pass
    await call.answer("Отмечено как просмотренное" if parts[3] == "1" else "Отметка снята")


@dp.callback_query_handler(lambda call: call.data.startswith("wpage:"), state="*")
async def episode_page_handler(call: types.CallbackQuery):
    parts = call.data.split(":")
    if len(parts) != 3 or any(not p.isascii() or not p.isdigit() or len(p) > 18 for p in parts[1:]):
        await call.answer("Кнопка недействительна.", show_alert=True)
        return
    category_id, page = map(int, parts[1:])
    category = get_category_by_id(category_id)
    if category is None:
        await call.answer("Сезон удалён.", show_alert=True)
        return
    markup = _subcategories_markup(
        category[1], category_id, get_subcategories_by_category(category_id), call.from_user.id, page
    )
    try:
        await call.message.edit_reply_markup(reply_markup=markup)
    except MessageNotModified:
        pass
    await call.answer()


async def _send_movie_card(chat_id: int, code: int, movie_info: dict, user_id: int = None):
    categories = get_categories_by_movie(code)
    content_type = movie_info.get("content_type", "movie")
    if categories:
        caption_hint = "\n\nВыберите сезон :" if content_type == "series" else "\n\nВыберите озвучку :"
    else:
        caption_hint = ""
    pick_markup = _build_pick_markup(code, movie_info, user_id or chat_id)

    sent_ok = False
    if movie_info.get("poster_image"):
        caption = f"<strong>Код : {code}</strong>\n\n<strong>{movie_info['movie_title']}</strong>"
        if movie_info.get("card_description"):
            caption += f"\n\n{movie_info['card_description']}"
        caption += caption_hint
        try:
            await bot.send_photo(
                chat_id, photo=movie_info["poster_image"], caption=caption[:1024], reply_markup=pick_markup
            )
            sent_ok = True
        except Exception:
            # Частая причина: постер был загружен через ДРУГОГО бота (например тестового),
            # и его file_id недействителен для этого бота/токена - Telegram file_id
            # привязан к конкретному боту. Не роняем всю карточку - отправляем текстом.
            logger.exception(
                "Не удалось отправить постер фильма %s (code=%s) - вероятно, "
                "невалидный file_id постера (загружен другим ботом/токеном?)",
                movie_info.get("movie_title"),
                code,
            )

    if not sent_ok:
        try:
            await bot.send_message(
                chat_id,
                f"<strong>Код : {code}</strong>\n\n"
                f"<strong>{movie_info['movie_title']}</strong>{caption_hint}",
                disable_web_page_preview=False,
                reply_markup=pick_markup,
            )
        except Exception:
            logger.exception(
                "Не удалось отправить карточку фильма %s (code=%s) даже без постера",
                movie_info.get("movie_title"),
                code,
            )
            await bot.send_message(
                chat_id, "⚠️ Не удалось показать карточку фильма. Сообщите об этом администратору."
            )
            return

    # Вместе с карточкой фильма отправляем видео-трейлер (если он загружен),
    # чтобы пользователь мог посмотреть его перед выбором озвучки/скачиванием
    if movie_info.get("movie_trailer"):
        try:
            await bot.send_video(chat_id, video=movie_info["movie_trailer"], caption="🎬 Трейлер")
        except Exception:
            logger.exception(
                "Не удалось отправить трейлер фильма %s (code=%s) - вероятно, невалидный file_id трейлера",
                movie_info.get("movie_title"),
                code,
            )

    if user_id is not None:
        log_watch(user_id, code, movie_info.get("movie_title"))


@dp.callback_query_handler(lambda call: call.data.startswith("moviepick_"), state=Users_.find_movie)
async def movie_pick_handler(call: types.CallbackQuery, state: FSMContext):
    numb = call.data.split("_", 1)[1]
    chat_id = call.message.chat.id
    await state.finish()
    await call.message.delete()
    try:
        movie_info = get_movie_from_numb(numb)
        if movie_info is None:
            await bot.send_message(chat_id, f"<strong>Фильм с таким кодом не найден : {numb} !</strong>")
        else:
            await _send_movie_card(chat_id, numb, movie_info, user_id=chat_id)
    except Exception:
        logger.exception("Ошибка при показе карточки фильма (code=%s)", numb)
        await bot.send_message(
            chat_id, "⚠️ Произошла ошибка при загрузке карточки. Попробуйте ещё раз чуть позже."
        )
    await bot.send_message(chat_id, "Искать ещё?", reply_markup=find_movie_markup())
    await call.answer()


@dp.callback_query_handler(lambda call: call.data.startswith("movcat_"))
async def movie_category_handler(call: types.CallbackQuery):
    category_id = int(call.data.split("_", 1)[1])
    subcategories = get_subcategories_by_category(category_id)
    category = get_category_by_id(category_id)
    if not subcategories or category is None:
        await call.answer("Для этого раздела пока нет файлов", show_alert=True)
        return
    movie_number = category[1]
    await call.message.edit_reply_markup(
        reply_markup=_subcategories_markup(movie_number, category_id, subcategories, call.from_user.id)
    )
    await call.answer()


@dp.callback_query_handler(lambda call: call.data.startswith("movcatback_"))
async def movie_category_back_handler(call: types.CallbackQuery):
    movie_number = call.data.split("_", 1)[1]
    movie_info = get_movie_from_numb(movie_number)
    if movie_info is None:
        await call.answer("Фильм удалён", show_alert=True)
        return
    await call.message.edit_reply_markup(
        reply_markup=_build_pick_markup(movie_number, movie_info, call.from_user.id)
    )
    await call.answer()


@dp.callback_query_handler(lambda call: call.data.startswith("movsub_"), state="*")
async def movie_subcategory_handler(call: types.CallbackQuery):
    subcategory_id = int(call.data.split("_", 1)[1])
    subcategory = get_subcategory_by_id(subcategory_id)
    chat_id = call.message.chat.id
    if subcategory is None:
        await call.answer("Файл недоступен", show_alert=True)
        return
    _, _, name, file_id, file_type = subcategory
    markup = _episode_file_markup(subcategory_id, call.from_user.id)
    await call.answer()
    if file_type == "video":
        await bot.send_video(chat_id, video=file_id, caption=name, parse_mode="", reply_markup=markup)
    else:
        await bot.send_document(chat_id, document=file_id, caption=name, parse_mode="", reply_markup=markup)


@dp.callback_query_handler(lambda call: call.data.startswith("dlfile_"))
async def download_movie_file_handler(call: types.CallbackQuery):
    numb = call.data.split("_", 1)[1]
    chat_id = call.message.chat.id
    movie_info = get_movie_from_numb(numb)
    if movie_info is None or not movie_info.get("movie_file"):
        await call.answer("Файл недоступен", show_alert=True)
        return
    await call.answer()
    if movie_info.get("movie_file_type") == "video":
        await bot.send_video(chat_id, video=movie_info["movie_file"])
    else:
        await bot.send_document(chat_id, document=movie_info["movie_file"])


@dp.callback_query_handler(lambda call: call.data.startswith("movtorrents_"))
async def movie_torrents_handler(call: types.CallbackQuery):
    code = call.data.split("_", 1)[1]
    torrents = get_torrents_by_movie(code)
    if not torrents:
        await call.answer("Torrent-файлы пока не загружены", show_alert=True)
        return
    await call.message.edit_reply_markup(reply_markup=torrents_pick_markup(code, torrents))
    await call.answer()


@dp.callback_query_handler(lambda call: call.data.startswith("torrback_"))
async def movie_torrents_back_handler(call: types.CallbackQuery):
    code = call.data.split("_", 1)[1]
    movie_info = get_movie_from_numb(code)
    if movie_info is None:
        await call.answer()
        return
    await call.message.edit_reply_markup(reply_markup=_build_pick_markup(code, movie_info, call.from_user.id))
    await call.answer()


@dp.callback_query_handler(lambda call: call.data.startswith("gettorrent_"))
async def get_torrent_handler(call: types.CallbackQuery):
    torrent_id = int(call.data.split("_", 1)[1])
    torrent = get_torrent_by_id(torrent_id)
    chat_id = call.message.chat.id
    if torrent is None:
        await call.answer("Файл недоступен", show_alert=True)
        return
    _, _, name, file_id = torrent
    await call.answer()
    await bot.send_document(chat_id, document=file_id, caption=f"📁 {name}")


@dp.message_handler(state=Users_.find_movie)
async def find_movie_handler(message: types.Message, state: FSMContext):
    chat_id = message.chat.id
    check = await check_user_sub(message)
    if check == False:
        await state.finish()
        await bot.send_message(
            chat_id,
            "Подпишитесь на каналы чтобы пользоваться ботом :",
            reply_markup=sub_markup(get_all_channels_links()),
        )
        return

    data = await state.get_data()
    content_type = data.get("content_type")

    query = message.text.strip()

    if query.isdigit():
        await state.finish()
        try:
            movie_info = get_movie_from_numb(query)
            if movie_info is None:
                await bot.send_message(chat_id, f"<strong>Не найдено с таким кодом : {query} !</strong>")
            else:
                await _send_movie_card(chat_id, query, movie_info, user_id=chat_id)
        except Exception:
            logger.exception("Ошибка при показе карточки фильма по коду (code=%s)", query)
            await bot.send_message(
                chat_id, "⚠️ Произошла ошибка при загрузке карточки. Попробуйте ещё раз чуть позже."
            )
        await bot.send_message(chat_id, "Искать ещё?", reply_markup=find_movie_markup())
        return

    matches = get_movies_by_title(query, content_type)
    log_search(chat_id, query, len(matches))
    label = _TYPE_LABELS.get(content_type, "фильм")
    if len(matches) == 0:
        await state.finish()
        await bot.send_message(chat_id, f"<strong>Не найдено с названием «{query}» !</strong>")
        await bot.send_message(chat_id, "Попробовать ещё раз?", reply_markup=find_movie_markup())
    elif len(matches) == 1:
        title, numb = matches[0]
        await state.finish()
        try:
            movie_info = get_movie_from_numb(numb)
            await _send_movie_card(chat_id, numb, movie_info, user_id=chat_id)
        except Exception:
            logger.exception("Ошибка при показе карточки фильма из поиска (code=%s)", numb)
            await bot.send_message(
                chat_id, "⚠️ Произошла ошибка при загрузке карточки. Попробуйте ещё раз чуть позже."
            )
        await bot.send_message(chat_id, "Искать ещё?", reply_markup=find_movie_markup())
    else:
        listing = "\n".join(f"{numb} - {title}" for title, numb in matches[:15])
        await message.answer(
            f"<strong>Найдено несколько результатов, уточните {label} или введите код :</strong>\n\n{listing}"
        )
