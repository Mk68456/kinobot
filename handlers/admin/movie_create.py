import logging
from aiogram import types
from loader import dp, bot
from aiogram.dispatcher import FSMContext
from services import tmdb
from states.admin_states import Admin_
from keyboards.admin.keyboard import skip_markup, file_mode_markup, catbuild_finish_markup, tmdb_pick_markup

logger = logging.getLogger(__name__)


from .movie_categories import _finalize_movie_and_categories

# ==================== ВЫБОР ТИПА КОНТЕНТА (ФИЛЬМ / СЕРИАЛ) ====================


@dp.callback_query_handler(lambda call: call.data in ("addtype_movie", "addtype_series"))
async def add_content_type_handler(call: types.CallbackQuery, state: FSMContext):
    content_type = "series" if call.data == "addtype_series" else "movie"
    await state.update_data(content_type=content_type)
    await call.message.delete()
    label = "сериала" if content_type == "series" else "фильма"
    await bot.send_message(call.message.chat.id, f"<strong>Отправьте название {label} :</strong>")
    await Admin_.add_new_cod.set()


# ==================== ДОБАВЛЕНИЕ ФИЛЬМА ====================


async def _ask_poster_manually(chat_id):
    await bot.send_message(
        chat_id,
        "<strong>Отправьте постер (фото) для карточки фильма или нажмите Пропустить :</strong>",
        reply_markup=skip_markup(),
    )
    await Admin_.add_movie_poster.set()


@dp.message_handler(state=Admin_.add_new_cod)
async def add_new_cod_handler(message: types.Message, state: FSMContext):
    await state.update_data(movie_title=message.text)
    data = await state.get_data()
    content_type = data.get("content_type", "movie")

    results = await tmdb.search(message.text, content_type)
    if results:
        await state.update_data(tmdb_candidates=results)
        label = "сериалов" if content_type == "series" else "фильмов"
        await bot.send_message(
            message.chat.id,
            f"<strong>Нашёл похожее в TMDB среди {label} - выберите нужный вариант, "
            f"или введите всё вручную :</strong>",
            reply_markup=tmdb_pick_markup(results),
        )
        await Admin_.add_tmdb_pick.set()
    else:
        # TMDB ничего не нашёл (или ключ не настроен, или TMDB недоступен) -
        # просто продолжаем как раньше, полностью вручную.
        await _ask_poster_manually(message.chat.id)


@dp.callback_query_handler(
    lambda call: call.data.startswith("tmdbpick_") and call.data != "tmdbpick_manual",
    state=Admin_.add_tmdb_pick,
)
async def tmdb_pick_handler(call: types.CallbackQuery, state: FSMContext):
    idx = int(call.data.split("_", 1)[1])
    data = await state.get_data()
    candidates = data.get("tmdb_candidates", [])
    content_type = data.get("content_type", "movie")
    await call.message.delete()

    if idx >= len(candidates):
        await bot.send_message(
            call.message.chat.id, "<strong>Этот вариант больше недоступен, попробуйте ещё раз.</strong>"
        )
        await _ask_poster_manually(call.message.chat.id)
        return

    chosen = candidates[idx]
    full_title = f"{chosen['title']} ({chosen['year']})" if chosen.get("year") else chosen["title"]
    overview = chosen.get("overview")
    if not overview:
        overview = await tmdb.get_overview(chosen.get("tmdb_id"), content_type)

    await state.update_data(movie_title=full_title, card_description=overview)

    poster_image_id = None
    if chosen.get("poster_url"):
        try:
            caption = f"<strong>{full_title}</strong>"
            if overview:
                caption += f"\n\n{overview}"
            sent = await bot.send_photo(
                call.message.chat.id, photo=chosen["poster_url"], caption=caption[:1024]
            )
            poster_image_id = sent.photo[-1].file_id
        except Exception:
            logger.exception("Не удалось загрузить постер с TMDB для «%s»", full_title)
            await bot.send_message(
                call.message.chat.id,
                f"<strong>{full_title}</strong>" + (f"\n\n{overview}" if overview else ""),
            )
    else:
        await bot.send_message(
            call.message.chat.id, f"<strong>{full_title}</strong>" + (f"\n\n{overview}" if overview else "")
        )

    if poster_image_id:
        await state.update_data(poster_image=poster_image_id)

    await bot.send_message(
        call.message.chat.id,
        "<strong>Данные подтянуты с TMDB ✅</strong> "
        "(название/постер/описание можно будет поправить позже через «✏️ Изменить»)\n\n"
        "Отправьте видео-трейлер к фильму или нажмите Пропустить :",
        reply_markup=skip_markup(),
    )
    await Admin_.add_movie_trailer.set()


@dp.callback_query_handler(lambda call: call.data == "tmdbpick_manual", state=Admin_.add_tmdb_pick)
async def tmdb_pick_manual_handler(call: types.CallbackQuery, state: FSMContext):
    await call.message.delete()
    await _ask_poster_manually(call.message.chat.id)


@dp.message_handler(state=Admin_.add_movie_poster, content_types=types.ContentTypes.PHOTO)
async def add_movie_poster_handler(message: types.Message, state: FSMContext):
    await state.update_data(poster_image=message.photo[-1].file_id)
    await bot.send_message(
        message.chat.id,
        "<strong>Отправьте видео-трейлер к фильму (он будет отправляться вместе с постером) "
        "или нажмите Пропустить :</strong>",
        reply_markup=skip_markup(),
    )
    await Admin_.add_movie_trailer.set()


@dp.message_handler(state=Admin_.add_movie_poster, content_types=types.ContentTypes.TEXT)
async def add_movie_poster_wrong_content_handler(message: types.Message, state: FSMContext):
    await bot.send_message(
        message.chat.id,
        "<strong>Нужно отправить именно фото, либо нажмите Пропустить :</strong>",
        reply_markup=skip_markup(),
    )


@dp.callback_query_handler(lambda call: call.data == "skip_step", state=Admin_.add_movie_poster)
async def skip_poster_handler(call: types.CallbackQuery, state: FSMContext):
    await bot.delete_message(call.message.chat.id, call.message.message_id)
    await bot.send_message(
        call.message.chat.id,
        "<strong>Отправьте видео-трейлер к фильму (он будет отправляться вместе с постером) "
        "или нажмите Пропустить :</strong>",
        reply_markup=skip_markup(),
    )
    await Admin_.add_movie_trailer.set()


@dp.message_handler(state=Admin_.add_movie_trailer, content_types=types.ContentTypes.VIDEO)
async def add_movie_trailer_handler(message: types.Message, state: FSMContext):
    await state.update_data(movie_trailer=message.video.file_id)
    await bot.send_message(
        message.chat.id,
        "<strong>Отправьте описание для карточки фильма или нажмите Пропустить :</strong>",
        reply_markup=skip_markup(),
    )
    await Admin_.add_movie_description.set()


@dp.message_handler(state=Admin_.add_movie_trailer, content_types=types.ContentTypes.TEXT)
async def add_movie_trailer_wrong_content_handler(message: types.Message, state: FSMContext):
    await bot.send_message(
        message.chat.id,
        "<strong>Нужно отправить именно видео-файл трейлера, либо нажмите Пропустить :</strong>",
        reply_markup=skip_markup(),
    )


@dp.callback_query_handler(lambda call: call.data == "skip_step", state=Admin_.add_movie_trailer)
async def skip_trailer_handler(call: types.CallbackQuery, state: FSMContext):
    await bot.delete_message(call.message.chat.id, call.message.message_id)
    await bot.send_message(
        call.message.chat.id,
        "<strong>Отправьте описание для карточки фильма или нажмите Пропустить :</strong>",
        reply_markup=skip_markup(),
    )
    await Admin_.add_movie_description.set()


@dp.message_handler(state=Admin_.add_movie_description, content_types=types.ContentTypes.TEXT)
async def add_movie_description_handler(message: types.Message, state: FSMContext):
    await state.update_data(card_description=message.text)
    await bot.send_message(
        message.chat.id, "<strong>Как добавить видео к фильму ?</strong>", reply_markup=file_mode_markup()
    )
    await Admin_.add_movie_file_mode.set()


@dp.callback_query_handler(lambda call: call.data == "skip_step", state=Admin_.add_movie_description)
async def skip_description_handler(call: types.CallbackQuery, state: FSMContext):
    await bot.delete_message(call.message.chat.id, call.message.message_id)
    await bot.send_message(
        call.message.chat.id,
        "<strong>Как добавить видео к фильму ?</strong>",
        reply_markup=file_mode_markup(),
    )
    await Admin_.add_movie_file_mode.set()


@dp.callback_query_handler(lambda call: call.data == "filemode_simple", state=Admin_.add_movie_file_mode)
async def filemode_simple_handler(call: types.CallbackQuery, state: FSMContext):
    await bot.delete_message(call.message.chat.id, call.message.message_id)
    await bot.send_message(
        call.message.chat.id,
        "<strong>Отправьте файл фильма (документ или видео) или нажмите Пропустить :</strong>",
        reply_markup=skip_markup(),
    )
    await Admin_.add_movie_file.set()


@dp.callback_query_handler(lambda call: call.data == "filemode_categories", state=Admin_.add_movie_file_mode)
async def filemode_categories_handler(call: types.CallbackQuery, state: FSMContext):
    await bot.delete_message(call.message.chat.id, call.message.message_id)
    await state.update_data(categories=[])
    data = await state.get_data()
    if data.get("content_type") == "series":
        prompt = "<strong>Введите номер сезона (например: 1), или нажмите «Завершить», если сезоны не нужны :</strong>"
    else:
        prompt = (
            "<strong>Введите название категории (например: Русская озвучка), "
            "или нажмите «Завершить», если категории не нужны :</strong>"
        )
    await bot.send_message(call.message.chat.id, prompt, reply_markup=catbuild_finish_markup())
    await Admin_.add_category_name.set()


@dp.message_handler(
    state=Admin_.add_movie_file, content_types=[types.ContentType.DOCUMENT, types.ContentType.VIDEO]
)
async def add_movie_file_handler(message: types.Message, state: FSMContext):
    if message.content_type == "document":
        await state.update_data(movie_file=message.document.file_id, movie_file_type="document")
    else:
        await state.update_data(movie_file=message.video.file_id, movie_file_type="video")
    await _finalize_movie_and_categories(message.chat.id, state)


@dp.message_handler(state=Admin_.add_movie_file, content_types=types.ContentTypes.TEXT)
async def add_movie_file_wrong_content_handler(message: types.Message, state: FSMContext):
    await bot.send_message(
        message.chat.id,
        "<strong>Нужно отправить файл (документ или видео), либо нажмите Пропустить :</strong>",
        reply_markup=skip_markup(),
    )


@dp.callback_query_handler(lambda call: call.data == "skip_step", state=Admin_.add_movie_file)
async def skip_file_handler(call: types.CallbackQuery, state: FSMContext):
    await bot.delete_message(call.message.chat.id, call.message.message_id)
    await _finalize_movie_and_categories(call.message.chat.id, state)
