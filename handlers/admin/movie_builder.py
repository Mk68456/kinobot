import logging
from aiogram import types
from loader import dp,bot
from aiogram.dispatcher import FSMContext
from database.admin.add_movie import add_new_movie,update_movie_title,update_movie_description,update_movie_trailer
from database.admin.categories import (add_movie_category,add_movie_subcategory,get_categories_by_movie,
                                        get_subcategories_by_category,delete_category)
from database.admin.torrents import (add_movie_torrent,get_torrents_by_movie,delete_torrent)
from database.admin.select import get_movie_title_by_numb,get_movie_content_type
from services import tmdb
from states.admin_states import Admin_
from keyboards.admin.keyboard import (admin_markup,skip_markup,file_mode_markup,catbuild_finish_markup,
                                       catbuild_finish_category_markup,edit_movie_menu_markup,
                                       categories_menu_markup,categories_delete_pick_markup,
                                       torrents_menu_markup,torrents_delete_pick_markup,torrents_finish_markup,
                                       tmdb_pick_markup)

logger = logging.getLogger(__name__)


# ==================== ВЫБОР ТИПА КОНТЕНТА (ФИЛЬМ / СЕРИАЛ) ====================

@dp.callback_query_handler(lambda call: call.data in ('addtype_movie', 'addtype_series'))
async def add_content_type_handler(call:types.CallbackQuery,state:FSMContext):
    content_type = 'series' if call.data == 'addtype_series' else 'movie'
    await state.update_data(content_type=content_type)
    await call.message.delete()
    label = 'сериала' if content_type == 'series' else 'фильма'
    await bot.send_message(call.message.chat.id, f"<strong>Отправьте название {label} :</strong>")
    await Admin_.add_new_cod.set()


# ==================== ДОБАВЛЕНИЕ ФИЛЬМА ====================

async def _ask_poster_manually(chat_id):
    await bot.send_message(chat_id,
                           "<strong>Отправьте постер (фото) для карточки фильма или нажмите Пропустить :</strong>",
                           reply_markup=skip_markup())
    await Admin_.add_movie_poster.set()


@dp.message_handler(state=Admin_.add_new_cod)
async def add_new_cod_handler(message:types.Message,state:FSMContext):
    await state.update_data(movie_title=message.text)
    data = await state.get_data()
    content_type = data.get('content_type', 'movie')

    results = await tmdb.search(message.text, content_type)
    if results:
        await state.update_data(tmdb_candidates=results)
        label = 'сериалов' if content_type == 'series' else 'фильмов'
        await bot.send_message(message.chat.id,
                               f"<strong>Нашёл похожее в TMDB среди {label} - выберите нужный вариант, "
                               f"или введите всё вручную :</strong>",
                               reply_markup=tmdb_pick_markup(results))
        await Admin_.add_tmdb_pick.set()
    else:
        # TMDB ничего не нашёл (или ключ не настроен, или TMDB недоступен) -
        # просто продолжаем как раньше, полностью вручную.
        await _ask_poster_manually(message.chat.id)


@dp.callback_query_handler(lambda call: call.data.startswith('tmdbpick_') and call.data != 'tmdbpick_manual',
                           state=Admin_.add_tmdb_pick)
async def tmdb_pick_handler(call:types.CallbackQuery,state:FSMContext):
    idx = int(call.data.split('_', 1)[1])
    data = await state.get_data()
    candidates = data.get('tmdb_candidates', [])
    content_type = data.get('content_type', 'movie')
    await call.message.delete()

    if idx >= len(candidates):
        await bot.send_message(call.message.chat.id, "<strong>Этот вариант больше недоступен, попробуйте ещё раз.</strong>")
        await _ask_poster_manually(call.message.chat.id)
        return

    chosen = candidates[idx]
    full_title = f"{chosen['title']} ({chosen['year']})" if chosen.get('year') else chosen['title']
    overview = chosen.get('overview')
    if not overview:
        overview = await tmdb.get_overview(chosen.get('tmdb_id'), content_type)

    await state.update_data(movie_title=full_title, card_description=overview)

    poster_image_id = None
    if chosen.get('poster_url'):
        try:
            caption = f"<strong>{full_title}</strong>"
            if overview:
                caption += f"\n\n{overview}"
            sent = await bot.send_photo(call.message.chat.id, photo=chosen['poster_url'], caption=caption[:1024])
            poster_image_id = sent.photo[-1].file_id
        except Exception:
            logger.exception("Не удалось загрузить постер с TMDB для «%s»", full_title)
            await bot.send_message(call.message.chat.id,
                                   f"<strong>{full_title}</strong>" + (f"\n\n{overview}" if overview else ""))
    else:
        await bot.send_message(call.message.chat.id,
                               f"<strong>{full_title}</strong>" + (f"\n\n{overview}" if overview else ""))

    if poster_image_id:
        await state.update_data(poster_image=poster_image_id)

    await bot.send_message(call.message.chat.id,
                           "<strong>Данные подтянуты с TMDB ✅</strong> "
                           "(название/постер/описание можно будет поправить позже через «✏️ Изменить»)\n\n"
                           "Отправьте видео-трейлер к фильму или нажмите Пропустить :",
                           reply_markup=skip_markup())
    await Admin_.add_movie_trailer.set()


@dp.callback_query_handler(lambda call: call.data == 'tmdbpick_manual', state=Admin_.add_tmdb_pick)
async def tmdb_pick_manual_handler(call:types.CallbackQuery,state:FSMContext):
    await call.message.delete()
    await _ask_poster_manually(call.message.chat.id)


@dp.message_handler(state=Admin_.add_movie_poster, content_types=types.ContentTypes.PHOTO)
async def add_movie_poster_handler(message:types.Message,state:FSMContext):
    await state.update_data(poster_image=message.photo[-1].file_id)
    await bot.send_message(message.chat.id,
                           "<strong>Отправьте видео-трейлер к фильму (он будет отправляться вместе с постером) "
                           "или нажмите Пропустить :</strong>",
                           reply_markup=skip_markup())
    await Admin_.add_movie_trailer.set()


@dp.message_handler(state=Admin_.add_movie_poster, content_types=types.ContentTypes.TEXT)
async def add_movie_poster_wrong_content_handler(message:types.Message,state:FSMContext):
    await bot.send_message(message.chat.id,
                           "<strong>Нужно отправить именно фото, либо нажмите Пропустить :</strong>",
                           reply_markup=skip_markup())


@dp.callback_query_handler(lambda call: call.data == 'skip_step', state=Admin_.add_movie_poster)
async def skip_poster_handler(call:types.CallbackQuery,state:FSMContext):
    await bot.delete_message(call.message.chat.id, call.message.message_id)
    await bot.send_message(call.message.chat.id,
                           "<strong>Отправьте видео-трейлер к фильму (он будет отправляться вместе с постером) "
                           "или нажмите Пропустить :</strong>",
                           reply_markup=skip_markup())
    await Admin_.add_movie_trailer.set()


@dp.message_handler(state=Admin_.add_movie_trailer, content_types=types.ContentTypes.VIDEO)
async def add_movie_trailer_handler(message:types.Message,state:FSMContext):
    await state.update_data(movie_trailer=message.video.file_id)
    await bot.send_message(message.chat.id,
                           "<strong>Отправьте описание для карточки фильма или нажмите Пропустить :</strong>",
                           reply_markup=skip_markup())
    await Admin_.add_movie_description.set()


@dp.message_handler(state=Admin_.add_movie_trailer, content_types=types.ContentTypes.TEXT)
async def add_movie_trailer_wrong_content_handler(message:types.Message,state:FSMContext):
    await bot.send_message(message.chat.id,
                           "<strong>Нужно отправить именно видео-файл трейлера, либо нажмите Пропустить :</strong>",
                           reply_markup=skip_markup())


@dp.callback_query_handler(lambda call: call.data == 'skip_step', state=Admin_.add_movie_trailer)
async def skip_trailer_handler(call:types.CallbackQuery,state:FSMContext):
    await bot.delete_message(call.message.chat.id, call.message.message_id)
    await bot.send_message(call.message.chat.id,
                           "<strong>Отправьте описание для карточки фильма или нажмите Пропустить :</strong>",
                           reply_markup=skip_markup())
    await Admin_.add_movie_description.set()


@dp.message_handler(state=Admin_.add_movie_description, content_types=types.ContentTypes.TEXT)
async def add_movie_description_handler(message:types.Message,state:FSMContext):
    await state.update_data(card_description=message.text)
    await bot.send_message(message.chat.id,
                           "<strong>Как добавить видео к фильму ?</strong>",
                           reply_markup=file_mode_markup())
    await Admin_.add_movie_file_mode.set()


@dp.callback_query_handler(lambda call: call.data == 'skip_step', state=Admin_.add_movie_description)
async def skip_description_handler(call:types.CallbackQuery,state:FSMContext):
    await bot.delete_message(call.message.chat.id, call.message.message_id)
    await bot.send_message(call.message.chat.id,
                           "<strong>Как добавить видео к фильму ?</strong>",
                           reply_markup=file_mode_markup())
    await Admin_.add_movie_file_mode.set()


@dp.callback_query_handler(lambda call: call.data == 'filemode_simple', state=Admin_.add_movie_file_mode)
async def filemode_simple_handler(call:types.CallbackQuery,state:FSMContext):
    await bot.delete_message(call.message.chat.id, call.message.message_id)
    await bot.send_message(call.message.chat.id,
                           "<strong>Отправьте файл фильма (документ или видео) или нажмите Пропустить :</strong>",
                           reply_markup=skip_markup())
    await Admin_.add_movie_file.set()


@dp.callback_query_handler(lambda call: call.data == 'filemode_categories', state=Admin_.add_movie_file_mode)
async def filemode_categories_handler(call:types.CallbackQuery,state:FSMContext):
    await bot.delete_message(call.message.chat.id, call.message.message_id)
    await state.update_data(categories=[])
    data = await state.get_data()
    if data.get('content_type') == 'series':
        prompt = "<strong>Введите номер сезона (например: 1), или нажмите «Завершить», если сезоны не нужны :</strong>"
    else:
        prompt = ("<strong>Введите название категории (например: Русская озвучка), "
                 "или нажмите «Завершить», если категории не нужны :</strong>")
    await bot.send_message(call.message.chat.id, prompt, reply_markup=catbuild_finish_markup())
    await Admin_.add_category_name.set()


@dp.message_handler(state=Admin_.add_movie_file, content_types=[types.ContentType.DOCUMENT, types.ContentType.VIDEO])
async def add_movie_file_handler(message:types.Message,state:FSMContext):
    if message.content_type == 'document':
        await state.update_data(movie_file=message.document.file_id, movie_file_type='document')
    else:
        await state.update_data(movie_file=message.video.file_id, movie_file_type='video')
    await _finalize_movie_and_categories(message.chat.id, state)


@dp.message_handler(state=Admin_.add_movie_file, content_types=types.ContentTypes.TEXT)
async def add_movie_file_wrong_content_handler(message:types.Message,state:FSMContext):
    await bot.send_message(message.chat.id,
                           "<strong>Нужно отправить файл (документ или видео), либо нажмите Пропустить :</strong>",
                           reply_markup=skip_markup())


@dp.callback_query_handler(lambda call: call.data == 'skip_step', state=Admin_.add_movie_file)
async def skip_file_handler(call:types.CallbackQuery,state:FSMContext):
    await bot.delete_message(call.message.chat.id, call.message.message_id)
    await _finalize_movie_and_categories(call.message.chat.id, state)


# ==================== КОНСТРУКТОР КАТЕГОРИЙ (озвучки/качества) ====================

@dp.message_handler(state=Admin_.add_category_name, content_types=types.ContentTypes.TEXT)
async def add_category_name_handler(message:types.Message,state:FSMContext):
    data = await state.get_data()
    if data.get('content_type') == 'series':
        season_text = message.text.strip()
        season_number = int(season_text) if season_text.isdigit() else None
        category_name = f"Сезон {season_text}"
        await state.update_data(current_category_name=category_name, current_season_number=season_number,
                                current_subcategories=[])
        await bot.send_message(message.chat.id,
                               "<strong>Введите название/номер серии (например: Серия 1) :</strong>")
    else:
        await state.update_data(current_category_name=message.text, current_season_number=None,
                                current_subcategories=[])
        await bot.send_message(message.chat.id,
                               "<strong>Введите название подкатегории/качества (например: 720p) :</strong>")
    await Admin_.add_subcategory_name.set()


@dp.callback_query_handler(lambda call: call.data == 'catbuild_finish', state=Admin_.add_category_name)
async def catbuild_finish_handler(call:types.CallbackQuery,state:FSMContext):
    await bot.delete_message(call.message.chat.id, call.message.message_id)
    await _finalize_movie_and_categories(call.message.chat.id, state)


@dp.message_handler(state=Admin_.add_subcategory_name, content_types=types.ContentTypes.TEXT)
async def add_subcategory_name_handler(message:types.Message,state:FSMContext):
    await state.update_data(current_subcategory_name=message.text)
    data = await state.get_data()
    await bot.send_message(message.chat.id,
                           f"<strong>Отправьте видео или документ для «{data.get('current_category_name')}» "
                           f"- «{message.text}» :</strong>")
    await Admin_.add_subcategory_file.set()


@dp.callback_query_handler(lambda call: call.data == 'catbuild_finish_category', state=Admin_.add_subcategory_name)
async def catbuild_finish_category_handler(call:types.CallbackQuery,state:FSMContext):
    await bot.delete_message(call.message.chat.id, call.message.message_id)
    await _close_current_category(state)
    data = await state.get_data()
    if data.get('content_type') == 'series':
        prompt = ("<strong>Введите номер следующего сезона (например: 2), "
                 "или нажмите «Завершить», если сезоны больше не нужны :</strong>")
    else:
        prompt = ("<strong>Введите название следующей категории (например: Английская озвучка), "
                 "или нажмите «Завершить», если категории больше не нужны :</strong>")
    await bot.send_message(call.message.chat.id, prompt, reply_markup=catbuild_finish_markup())
    await Admin_.add_category_name.set()


@dp.message_handler(state=Admin_.add_subcategory_file, content_types=[types.ContentType.DOCUMENT, types.ContentType.VIDEO])
async def add_subcategory_file_handler(message:types.Message,state:FSMContext):
    data = await state.get_data()
    if message.content_type == 'document':
        file_id, file_type = message.document.file_id, 'document'
    else:
        file_id, file_type = message.video.file_id, 'video'
    subcategories = data.get('current_subcategories', [])
    subcategories.append({'name': data.get('current_subcategory_name'), 'file_id': file_id, 'file_type': file_type})
    await state.update_data(current_subcategories=subcategories)
    await bot.send_message(message.chat.id,
                           "<strong>Подкатегория добавлена ✅</strong>\n\n"
                           "Введите название следующей подкатегории/качества (например: 1080p), "
                           "или нажмите «Завершить категорию» :",
                           reply_markup=catbuild_finish_category_markup())
    await Admin_.add_subcategory_name.set()


@dp.message_handler(state=Admin_.add_subcategory_file, content_types=types.ContentTypes.TEXT)
async def add_subcategory_file_wrong_content_handler(message:types.Message,state:FSMContext):
    await bot.send_message(message.chat.id,
                           "<strong>Нужно отправить видео или документ, либо нажмите «Завершить категорию» :</strong>",
                           reply_markup=catbuild_finish_category_markup())


async def _close_current_category(state:FSMContext):
    data = await state.get_data()
    categories = data.get('categories', [])
    current_name = data.get('current_category_name')
    if current_name:
        categories.append({'name': current_name, 'season_number': data.get('current_season_number'),
                           'subcategories': data.get('current_subcategories', [])})
    await state.update_data(categories=categories, current_category_name=None, current_season_number=None,
                            current_subcategories=[])


async def _finalize_movie_and_categories(chat_id:int, state:FSMContext):
    # если конструктор категорий был открыт и в нём осталась незакрытая категория - закрываем её
    data = await state.get_data()
    if data.get('current_category_name'):
        await _close_current_category(state)
        data = await state.get_data()

    categories = data.get('categories', [])
    existing_movie_number = data.get('existing_movie_number')

    if existing_movie_number:
        movie_number = existing_movie_number
        result_message = "Категории обновлены ✅"
    else:
        title = data.get('movie_title')
        poster_image = data.get('poster_image')
        card_description = data.get('card_description')
        movie_file = data.get('movie_file')
        movie_file_type = data.get('movie_file_type')
        movie_trailer = data.get('movie_trailer')
        content_type = data.get('content_type', 'movie')
        card_style = 'card' if poster_image else 'simple'
        movie_number = add_new_movie(title, poster_image=poster_image, card_description=card_description,
                                     card_style=card_style, movie_file=movie_file, movie_file_type=movie_file_type,
                                     movie_trailer=movie_trailer, content_type=content_type)
        result_message = "Успешно добавлено !"

    for category in categories:
        category_id = add_movie_category(movie_number, category['name'], category.get('season_number'))
        for sub in category.get('subcategories', []):
            add_movie_subcategory(category_id, sub['name'], sub['file_id'], sub['file_type'])

    await state.finish()
    await bot.send_message(chat_id, result_message, reply_markup=admin_markup())


# ==================== ИЗМЕНЕНИЕ ФИЛЬМА ====================

@dp.message_handler(state=Admin_.edit_movie_select)
async def edit_movie_select_handler(message:types.Message,state:FSMContext):
    if message.text == 'Назад':
        await state.finish()
        await message.delete()
        await bot.send_message(message.chat.id, 'Изменение фильма отменено', reply_markup=types.ReplyKeyboardRemove())
        await bot.send_message(message.chat.id, "Админ-панель", reply_markup=admin_markup())
        return
    numb_part = message.text.split(' - ')[0].strip()
    if not numb_part.isdigit():
        await bot.send_message(message.chat.id, "Пожалуйста, выберите фильм кнопкой из списка.")
        return
    movie_title = get_movie_title_by_numb(int(numb_part))
    if movie_title is None:
        await bot.send_message(message.chat.id, "Такой фильм не найден, выберите из списка.")
        return
    await state.update_data(edit_movie_number=int(numb_part))
    await bot.send_message(message.chat.id, f"<strong>Фильм «{movie_title}» (код {numb_part})</strong>\n\n"
                                            "Что хотите изменить ?", reply_markup=types.ReplyKeyboardRemove())
    await bot.send_message(message.chat.id, "Выберите действие :", reply_markup=edit_movie_menu_markup())
    await Admin_.edit_movie_menu.set()


@dp.callback_query_handler(lambda call: call.data == 'editm_back', state=Admin_.edit_movie_menu)
async def editm_back_handler(call:types.CallbackQuery,state:FSMContext):
    await call.message.delete()
    await state.finish()
    await bot.send_message(call.message.chat.id, "Админ-панель", reply_markup=admin_markup())


@dp.callback_query_handler(lambda call: call.data == 'editm_title', state=Admin_.edit_movie_menu)
async def editm_title_handler(call:types.CallbackQuery,state:FSMContext):
    await call.message.delete()
    await bot.send_message(call.message.chat.id, "<strong>Введите новое название фильма :</strong>")
    await Admin_.edit_movie_title.set()


@dp.message_handler(state=Admin_.edit_movie_title, content_types=types.ContentTypes.TEXT)
async def edit_movie_title_handler(message:types.Message,state:FSMContext):
    data = await state.get_data()
    update_movie_title(data.get('edit_movie_number'), message.text)
    await state.finish()
    await bot.send_message(message.chat.id, "Название успешно изменено ✅", reply_markup=admin_markup())


@dp.callback_query_handler(lambda call: call.data == 'editm_desc', state=Admin_.edit_movie_menu)
async def editm_desc_handler(call:types.CallbackQuery,state:FSMContext):
    await call.message.delete()
    await bot.send_message(call.message.chat.id,
                           "<strong>Введите новое описание фильма (или отправьте «-», чтобы удалить описание) :</strong>")
    await Admin_.edit_movie_description.set()


@dp.message_handler(state=Admin_.edit_movie_description, content_types=types.ContentTypes.TEXT)
async def edit_movie_description_handler(message:types.Message,state:FSMContext):
    data = await state.get_data()
    new_description = None if message.text.strip() == '-' else message.text
    update_movie_description(data.get('edit_movie_number'), new_description)
    await state.finish()
    await bot.send_message(message.chat.id, "Описание успешно изменено ✅", reply_markup=admin_markup())


@dp.callback_query_handler(lambda call: call.data == 'editm_trailer', state=Admin_.edit_movie_menu)
async def editm_trailer_handler(call:types.CallbackQuery,state:FSMContext):
    await call.message.delete()
    await bot.send_message(call.message.chat.id,
                           "<strong>Отправьте новый видео-трейлер фильма (или отправьте «-», "
                           "чтобы удалить трейлер) :</strong>")
    await Admin_.edit_movie_trailer.set()


@dp.message_handler(state=Admin_.edit_movie_trailer, content_types=types.ContentTypes.VIDEO)
async def edit_movie_trailer_video_handler(message:types.Message,state:FSMContext):
    data = await state.get_data()
    update_movie_trailer(data.get('edit_movie_number'), message.video.file_id)
    await state.finish()
    await bot.send_message(message.chat.id, "Трейлер успешно изменён ✅", reply_markup=admin_markup())


@dp.message_handler(state=Admin_.edit_movie_trailer, content_types=types.ContentTypes.TEXT)
async def edit_movie_trailer_text_handler(message:types.Message,state:FSMContext):
    data = await state.get_data()
    if message.text.strip() == '-':
        update_movie_trailer(data.get('edit_movie_number'), None)
        await state.finish()
        await bot.send_message(message.chat.id, "Трейлер удалён ✅", reply_markup=admin_markup())
    else:
        await bot.send_message(message.chat.id,
                               "<strong>Нужно отправить видео-файл трейлера, либо «-», чтобы его удалить :</strong>")


@dp.callback_query_handler(lambda call: call.data == 'editm_cat', state=Admin_.edit_movie_menu)
async def editm_cat_handler(call:types.CallbackQuery,state:FSMContext):
    await call.message.edit_text("<strong>Категории (озвучки/качества) фильма :</strong>",
                                 reply_markup=categories_menu_markup())
    await Admin_.edit_categories_menu.set()


@dp.callback_query_handler(lambda call: call.data == 'catmenu_back', state=Admin_.edit_categories_menu)
async def catmenu_back_handler(call:types.CallbackQuery,state:FSMContext):
    data = await state.get_data()
    numb = data.get('edit_movie_number')
    movie_title = get_movie_title_by_numb(numb)
    await call.message.edit_text(f"<strong>Фильм «{movie_title}» (код {numb})</strong>\n\nЧто хотите изменить ?",
                                 reply_markup=edit_movie_menu_markup())
    await Admin_.edit_movie_menu.set()


@dp.callback_query_handler(lambda call: call.data == 'catmenu_add', state=Admin_.edit_categories_menu)
async def catmenu_add_handler(call:types.CallbackQuery,state:FSMContext):
    data = await state.get_data()
    numb = data.get('edit_movie_number')
    content_type = get_movie_content_type(numb)
    await state.update_data(existing_movie_number=numb, categories=[], content_type=content_type)
    await call.message.delete()
    if content_type == 'series':
        prompt = ("<strong>Введите номер сезона (например: 1), "
                 "или нажмите «Завершить», если больше добавлять не нужно :</strong>")
    else:
        prompt = ("<strong>Введите название категории (например: Русская озвучка), "
                 "или нажмите «Завершить», если больше добавлять не нужно :</strong>")
    await bot.send_message(call.message.chat.id, prompt, reply_markup=catbuild_finish_markup())
    await Admin_.add_category_name.set()


@dp.callback_query_handler(lambda call: call.data == 'catmenu_list', state=Admin_.edit_categories_menu)
async def catmenu_list_handler(call:types.CallbackQuery,state:FSMContext):
    data = await state.get_data()
    numb = data.get('edit_movie_number')
    categories = get_categories_by_movie(numb)
    if not categories:
        await call.answer("У этого фильма пока нет категорий.", show_alert=True)
        return
    lines = []
    for category_id, name in categories:
        subs = get_subcategories_by_category(category_id)
        subs_text = ', '.join(sub[1] for sub in subs) if subs else 'нет подкатегорий'
        lines.append(f"<strong>{name}</strong> : {subs_text}")
    await bot.send_message(call.message.chat.id, "<strong>Категории фильма :</strong>\n\n" + '\n'.join(lines))
    await call.answer()


@dp.callback_query_handler(lambda call: call.data == 'catmenu_delete', state=Admin_.edit_categories_menu)
async def catmenu_delete_handler(call:types.CallbackQuery,state:FSMContext):
    data = await state.get_data()
    numb = data.get('edit_movie_number')
    categories = get_categories_by_movie(numb)
    if not categories:
        await call.answer("У этого фильма пока нет категорий.", show_alert=True)
        return
    await call.message.edit_text("<strong>Выберите категорию для удаления :</strong>",
                                 reply_markup=categories_delete_pick_markup(categories))
    await Admin_.edit_categories_delete.set()


@dp.callback_query_handler(lambda call: call.data.startswith('catdel_'), state=Admin_.edit_categories_delete)
async def catdel_handler(call:types.CallbackQuery,state:FSMContext):
    category_id = int(call.data.split('_', 1)[1])
    delete_category(category_id)
    await call.answer("Категория удалена")
    await call.message.edit_text("<strong>Категории (озвучки/качества) фильма :</strong>",
                                 reply_markup=categories_menu_markup())
    await Admin_.edit_categories_menu.set()


@dp.callback_query_handler(lambda call: call.data == 'catmenu_back', state=Admin_.edit_categories_delete)
async def catdel_back_handler(call:types.CallbackQuery,state:FSMContext):
    await call.message.edit_text("<strong>Категории (озвучки/качества) фильма :</strong>",
                                 reply_markup=categories_menu_markup())
    await Admin_.edit_categories_menu.set()


# ==================== TORRENT-ФАЙЛЫ ====================

@dp.callback_query_handler(lambda call: call.data == 'editm_torrents', state=Admin_.edit_movie_menu)
async def editm_torrents_handler(call:types.CallbackQuery,state:FSMContext):
    await call.message.edit_text("<strong>Torrent-файлы фильма/сериала :</strong>",
                                 reply_markup=torrents_menu_markup())
    await Admin_.edit_torrents_menu.set()


@dp.callback_query_handler(lambda call: call.data == 'trmenu_back', state=Admin_.edit_torrents_menu)
async def trmenu_back_handler(call:types.CallbackQuery,state:FSMContext):
    data = await state.get_data()
    numb = data.get('edit_movie_number')
    movie_title = get_movie_title_by_numb(numb)
    await call.message.edit_text(f"<strong>Фильм «{movie_title}» (код {numb})</strong>\n\nЧто хотите изменить ?",
                                 reply_markup=edit_movie_menu_markup())
    await Admin_.edit_movie_menu.set()


@dp.callback_query_handler(lambda call: call.data == 'trmenu_add', state=Admin_.edit_torrents_menu)
async def trmenu_add_handler(call:types.CallbackQuery,state:FSMContext):
    await call.message.delete()
    await bot.send_message(call.message.chat.id,
                           "<strong>Введите название torrent-файла (например: 1080p или Сезон 1) :</strong>")
    await Admin_.add_torrent_name.set()


@dp.message_handler(state=Admin_.add_torrent_name, content_types=types.ContentTypes.TEXT)
async def add_torrent_name_handler(message:types.Message,state:FSMContext):
    await state.update_data(current_torrent_name=message.text)
    await bot.send_message(message.chat.id, f"<strong>Отправьте torrent-файл для «{message.text}» :</strong>")
    await Admin_.add_torrent_file.set()


@dp.message_handler(state=Admin_.add_torrent_file, content_types=types.ContentTypes.DOCUMENT)
async def add_torrent_file_handler(message:types.Message,state:FSMContext):
    data = await state.get_data()
    numb = data.get('edit_movie_number')
    add_movie_torrent(numb, data.get('current_torrent_name'), message.document.file_id)
    await bot.send_message(message.chat.id,
                           "<strong>Torrent-файл добавлен ✅</strong>\n\n"
                           "Введите название следующего torrent-файла, или нажмите «Завершить» :",
                           reply_markup=torrents_finish_markup())
    await Admin_.add_torrent_name.set()


@dp.message_handler(state=Admin_.add_torrent_file, content_types=types.ContentTypes.TEXT)
async def add_torrent_file_wrong_content_handler(message:types.Message,state:FSMContext):
    await bot.send_message(message.chat.id, "<strong>Нужно отправить именно файл (документ), например .torrent :</strong>")


@dp.callback_query_handler(lambda call: call.data == 'trbuild_finish', state=Admin_.add_torrent_name)
async def trbuild_finish_handler(call:types.CallbackQuery,state:FSMContext):
    await call.message.delete()
    await bot.send_message(call.message.chat.id, "<strong>Torrent-файлы фильма/сериала :</strong>",
                           reply_markup=torrents_menu_markup())
    await Admin_.edit_torrents_menu.set()


@dp.callback_query_handler(lambda call: call.data == 'trmenu_list', state=Admin_.edit_torrents_menu)
async def trmenu_list_handler(call:types.CallbackQuery,state:FSMContext):
    data = await state.get_data()
    numb = data.get('edit_movie_number')
    torrents = get_torrents_by_movie(numb)
    if not torrents:
        await call.answer("У этого фильма/сериала пока нет torrent-файлов.", show_alert=True)
        return
    lines = [name for _, name in torrents]
    await bot.send_message(call.message.chat.id, "<strong>Torrent-файлы :</strong>\n\n" + '\n'.join(lines))
    await call.answer()


@dp.callback_query_handler(lambda call: call.data == 'trmenu_delete', state=Admin_.edit_torrents_menu)
async def trmenu_delete_handler(call:types.CallbackQuery,state:FSMContext):
    data = await state.get_data()
    numb = data.get('edit_movie_number')
    torrents = get_torrents_by_movie(numb)
    if not torrents:
        await call.answer("У этого фильма/сериала пока нет torrent-файлов.", show_alert=True)
        return
    await call.message.edit_text("<strong>Выберите torrent-файл для удаления :</strong>",
                                 reply_markup=torrents_delete_pick_markup(torrents))
    await Admin_.edit_torrents_delete.set()


@dp.callback_query_handler(lambda call: call.data.startswith('trdel_'), state=Admin_.edit_torrents_delete)
async def trdel_handler(call:types.CallbackQuery,state:FSMContext):
    torrent_id = int(call.data.split('_', 1)[1])
    delete_torrent(torrent_id)
    await call.answer("Torrent-файл удалён")
    await call.message.edit_text("<strong>Torrent-файлы фильма/сериала :</strong>",
                                 reply_markup=torrents_menu_markup())
    await Admin_.edit_torrents_menu.set()


@dp.callback_query_handler(lambda call: call.data == 'trmenu_back', state=Admin_.edit_torrents_delete)
async def trdel_back_handler(call:types.CallbackQuery,state:FSMContext):
    await call.message.edit_text("<strong>Torrent-файлы фильма/сериала :</strong>",
                                 reply_markup=torrents_menu_markup())
    await Admin_.edit_torrents_menu.set()
