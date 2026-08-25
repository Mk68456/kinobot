from aiogram import types
from loader import dp,bot
from aiogram.dispatcher import FSMContext
from database.users.select import get_movie_from_numb,get_movies_by_title
from database.admin.categories import get_categories_by_movie,get_subcategories_by_category,get_subcategory_by_id,get_category_by_id
from database.admin.torrents import get_torrents_by_movie,get_torrent_by_id
from database.admin.stats import log_search,log_watch
from .check_user_sub import check_user_sub
from keyboards.inline.sub_keyboard import sub_markup
from keyboards.users.keyboard import find_movie_markup,get_movies_pick_markup,torrents_pick_markup
from states.user_states import Users_

_TYPE_LABELS = {'movie': 'фильм', 'series': 'сериал'}
_TYPE_LABELS_PLURAL = {'movie': 'Фильмы', 'series': 'Сериалы'}


async def _open_catalog(chat_id:int, content_type:str, state:FSMContext):
    await state.update_data(content_type=content_type)
    label = _TYPE_LABELS_PLURAL.get(content_type, 'Фильмы')
    await bot.send_message(chat_id,
                           f"<strong>{label}</strong>\n\n"
                           "Введите название (можно несколько ключевых слов в любом порядке) "
                           "или код, либо выберите из списка:",
                           reply_markup=get_movies_pick_markup(0, content_type))
    await Users_.find_movie.set()


@dp.callback_query_handler(lambda call: call.data == 'find_movie_movie')
async def find_movie_button_handler(call:types.CallbackQuery, state:FSMContext):
    chat_id = call.message.chat.id
    check = await check_user_sub(call.message)
    if check != False:
        await call.message.delete()
        await _open_catalog(chat_id, 'movie', state)
    else:
        await call.answer('Вы не подписались на каналы !',show_alert=True)


@dp.callback_query_handler(lambda call: call.data == 'find_movie_series')
async def find_series_button_handler(call:types.CallbackQuery, state:FSMContext):
    chat_id = call.message.chat.id
    check = await check_user_sub(call.message)
    if check != False:
        await call.message.delete()
        await _open_catalog(chat_id, 'series', state)
    else:
        await call.answer('Вы не подписались на каналы !',show_alert=True)


@dp.callback_query_handler(lambda call: call.data.startswith('moviepage_'), state=Users_.find_movie)
async def movie_page_handler(call:types.CallbackQuery, state:FSMContext):
    _, ct, page = call.data.split('_', 2)
    content_type = None if ct == 'all' else ct
    await call.message.edit_reply_markup(reply_markup=get_movies_pick_markup(int(page), content_type))
    await call.answer()


def _download_markup(code):
    markup = types.InlineKeyboardMarkup(row_width=1)
    markup.add(types.InlineKeyboardButton(text='📥 Скачать файл', callback_data=f'dlfile_{code}'))
    return markup


def _categories_markup(code, categories):
    markup = types.InlineKeyboardMarkup(row_width=1)
    for category_id, name in categories:
        markup.add(types.InlineKeyboardButton(text=name, callback_data=f'movcat_{category_id}'))
    return markup


def _subcategories_markup(movie_number, category_id, subcategories):
    markup = types.InlineKeyboardMarkup(row_width=1)
    for sub_id, name, file_id, file_type in subcategories:
        markup.add(types.InlineKeyboardButton(text=name, callback_data=f'movsub_{sub_id}'))
    markup.add(types.InlineKeyboardButton(text='⬅️ Назад к озвучкам/сезонам', callback_data=f'movcatback_{movie_number}'))
    return markup


def _build_pick_markup(code, movie_info):
    categories = get_categories_by_movie(code)
    if categories:
        pick_markup = _categories_markup(code, categories)
    elif movie_info.get('movie_file'):
        pick_markup = _download_markup(code)
    else:
        pick_markup = None

    torrents = get_torrents_by_movie(code)
    if torrents:
        if pick_markup is None:
            pick_markup = types.InlineKeyboardMarkup(row_width=1)
        pick_markup.add(types.InlineKeyboardButton(text='📁 Torrent-файлы', callback_data=f'movtorrents_{code}'))
    return pick_markup


async def _send_movie_card(chat_id:int, code:int, movie_info:dict, user_id:int = None):
    categories = get_categories_by_movie(code)
    content_type = movie_info.get('content_type', 'movie')
    if categories:
        caption_hint = "\n\nВыберите сезон :" if content_type == 'series' else "\n\nВыберите озвучку :"
    else:
        caption_hint = ""
    pick_markup = _build_pick_markup(code, movie_info)

    if movie_info.get('poster_image'):
        caption = f"<strong>Код : {code}</strong>\n\n<strong>{movie_info['movie_title']}</strong>"
        if movie_info.get('card_description'):
            caption += f"\n\n{movie_info['card_description']}"
        caption += caption_hint
        await bot.send_photo(chat_id, photo=movie_info['poster_image'], caption=caption[:1024],
                             reply_markup=pick_markup)
    else:
        await bot.send_message(chat_id, f"<strong>Код : {code}</strong>\n\n"
                                        f"<strong>{movie_info['movie_title']}</strong>{caption_hint}",
                               disable_web_page_preview=False, reply_markup=pick_markup)

    # Вместе с карточкой фильма отправляем видео-трейлер (если он загружен),
    # чтобы пользователь мог посмотреть его перед выбором озвучки/скачиванием
    if movie_info.get('movie_trailer'):
        await bot.send_video(chat_id, video=movie_info['movie_trailer'], caption='🎬 Трейлер')

    if user_id is not None:
        log_watch(user_id, code, movie_info.get('movie_title'))


@dp.callback_query_handler(lambda call: call.data.startswith('moviepick_'), state=Users_.find_movie)
async def movie_pick_handler(call:types.CallbackQuery,state:FSMContext):
    numb = call.data.split('_', 1)[1]
    chat_id = call.message.chat.id
    movie_info = get_movie_from_numb(numb)
    await state.finish()
    await call.message.delete()
    if movie_info is None:
        await bot.send_message(chat_id, f"<strong>Фильм с таким кодом не найден : {numb} !</strong>")
    else:
        await _send_movie_card(chat_id, numb, movie_info, user_id=chat_id)
    await bot.send_message(chat_id, "Искать ещё?", reply_markup=find_movie_markup())
    await call.answer()


@dp.callback_query_handler(lambda call: call.data.startswith('movcat_'))
async def movie_category_handler(call:types.CallbackQuery):
    category_id = int(call.data.split('_', 1)[1])
    subcategories = get_subcategories_by_category(category_id)
    category = get_category_by_id(category_id)
    if not subcategories or category is None:
        await call.answer('Для этого раздела пока нет файлов', show_alert=True)
        return
    movie_number = category[1]
    await call.message.edit_reply_markup(reply_markup=_subcategories_markup(movie_number, category_id, subcategories))
    await call.answer()


@dp.callback_query_handler(lambda call: call.data.startswith('movcatback_'))
async def movie_category_back_handler(call:types.CallbackQuery):
    movie_number = call.data.split('_', 1)[1]
    categories = get_categories_by_movie(movie_number)
    await call.message.edit_reply_markup(reply_markup=_categories_markup(movie_number, categories))
    await call.answer()


@dp.callback_query_handler(lambda call: call.data.startswith('movsub_'))
async def movie_subcategory_handler(call:types.CallbackQuery):
    subcategory_id = int(call.data.split('_', 1)[1])
    subcategory = get_subcategory_by_id(subcategory_id)
    chat_id = call.message.chat.id
    if subcategory is None:
        await call.answer('Файл недоступен', show_alert=True)
        return
    _, _, name, file_id, file_type = subcategory
    await call.answer()
    if file_type == 'video':
        await bot.send_video(chat_id, video=file_id, caption=name)
    else:
        await bot.send_document(chat_id, document=file_id, caption=name)


@dp.callback_query_handler(lambda call: call.data.startswith('dlfile_'))
async def download_movie_file_handler(call:types.CallbackQuery):
    numb = call.data.split('_', 1)[1]
    chat_id = call.message.chat.id
    movie_info = get_movie_from_numb(numb)
    if movie_info is None or not movie_info.get('movie_file'):
        await call.answer('Файл недоступен', show_alert=True)
        return
    await call.answer()
    if movie_info.get('movie_file_type') == 'video':
        await bot.send_video(chat_id, video=movie_info['movie_file'])
    else:
        await bot.send_document(chat_id, document=movie_info['movie_file'])


@dp.callback_query_handler(lambda call: call.data.startswith('movtorrents_'))
async def movie_torrents_handler(call:types.CallbackQuery):
    code = call.data.split('_', 1)[1]
    torrents = get_torrents_by_movie(code)
    if not torrents:
        await call.answer('Torrent-файлы пока не загружены', show_alert=True)
        return
    await call.message.edit_reply_markup(reply_markup=torrents_pick_markup(code, torrents))
    await call.answer()


@dp.callback_query_handler(lambda call: call.data.startswith('torrback_'))
async def movie_torrents_back_handler(call:types.CallbackQuery):
    code = call.data.split('_', 1)[1]
    movie_info = get_movie_from_numb(code)
    if movie_info is None:
        await call.answer()
        return
    await call.message.edit_reply_markup(reply_markup=_build_pick_markup(code, movie_info))
    await call.answer()


@dp.callback_query_handler(lambda call: call.data.startswith('gettorrent_'))
async def get_torrent_handler(call:types.CallbackQuery):
    torrent_id = int(call.data.split('_', 1)[1])
    torrent = get_torrent_by_id(torrent_id)
    chat_id = call.message.chat.id
    if torrent is None:
        await call.answer('Файл недоступен', show_alert=True)
        return
    _, _, name, file_id = torrent
    await call.answer()
    await bot.send_document(chat_id, document=file_id, caption=f'📁 {name}')


@dp.message_handler(state=Users_.find_movie)
async def find_movie_handler(message:types.Message,state:FSMContext):
    chat_id = message.chat.id
    check = await check_user_sub(message)
    if check == False:
        await state.finish()
        await bot.send_message(chat_id, "Подпишитесь на каналы чтобы пользоваться ботом :",reply_markup=sub_markup())
        return

    data = await state.get_data()
    content_type = data.get('content_type')

    query = message.text.strip()

    if query.isdigit():
        movie_info = get_movie_from_numb(query)
        await state.finish()
        if movie_info is None:
            await bot.send_message(chat_id, f"<strong>Не найдено с таким кодом : {query} !</strong>")
            await bot.send_message(chat_id, "Попробовать ещё раз?", reply_markup=find_movie_markup())
        else:
            await _send_movie_card(chat_id, query, movie_info, user_id=chat_id)
            await bot.send_message(chat_id, "Искать ещё?", reply_markup=find_movie_markup())
        return

    matches = get_movies_by_title(query, content_type)
    log_search(chat_id, query, len(matches))
    label = _TYPE_LABELS.get(content_type, 'фильм')
    if len(matches) == 0:
        await state.finish()
        await bot.send_message(chat_id, f"<strong>Не найдено с названием «{query}» !</strong>")
        await bot.send_message(chat_id, "Попробовать ещё раз?", reply_markup=find_movie_markup())
    elif len(matches) == 1:
        title, numb = matches[0]
        movie_info = get_movie_from_numb(numb)
        await state.finish()
        await _send_movie_card(chat_id, numb, movie_info, user_id=chat_id)
        await bot.send_message(chat_id, "Искать ещё?", reply_markup=find_movie_markup())
    else:
        listing = '\n'.join(f'{numb} - {title}' for title, numb in matches[:15])
        await message.answer(f"<strong>Найдено несколько результатов, уточните {label} или введите код :</strong>\n\n{listing}")
