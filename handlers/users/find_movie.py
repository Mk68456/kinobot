from aiogram import types
from loader import dp,bot
from aiogram.dispatcher import FSMContext
from database.users.select import get_movie_from_numb,get_movies_by_title
from database.admin.categories import get_categories_by_movie,get_subcategories_by_category,get_subcategory_by_id,get_category_by_id
from .check_user_sub import check_user_sub
from keyboards.inline.sub_keyboard import sub_markup
from keyboards.users.keyboard import find_movie_markup,get_movies_pick_markup
from states.user_states import Users_
from database.users.add_user import add_user
from database.admin.analytics import log_event


@dp.callback_query_handler(lambda call: call.data == 'find_movie')
async def find_movie_button_handler(call:types.CallbackQuery):
    chat_id = call.message.chat.id
    user_id = add_user(call)
    check = await check_user_sub(call.message)
    if check != False:
        await call.message.delete()
        await bot.send_message(chat_id,
                               "<strong>Введите название (можно несколько ключевых слов в любом порядке) "
                               "или код фильма, либо выберите фильм из списка:</strong>",
                               reply_markup=get_movies_pick_markup(0))
        await Users_.find_movie.set()
    else:
        await call.answer('Вы не подписались на каналы !',show_alert=True)


@dp.callback_query_handler(lambda call: call.data.startswith('moviepage_'), state=Users_.find_movie)
async def movie_page_handler(call:types.CallbackQuery):
    page = int(call.data.split('_')[1])
    await call.message.edit_reply_markup(reply_markup=get_movies_pick_markup(page))
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
    markup.add(types.InlineKeyboardButton(text='⬅️ Назад к озвучкам', callback_data=f'movcatback_{movie_number}'))
    return markup


async def _send_movie_card(chat_id:int, code:int, movie_info:dict):
    categories = get_categories_by_movie(code)
    if categories:
        pick_markup = _categories_markup(code, categories)
        caption_hint = "\n\nВыберите озвучку :"
    elif movie_info.get('movie_file'):
        pick_markup = _download_markup(code)
        caption_hint = ""
    else:
        pick_markup = None
        caption_hint = ""

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


@dp.callback_query_handler(lambda call: call.data.startswith('moviepick_'), state=Users_.find_movie)
async def movie_pick_handler(call:types.CallbackQuery,state:FSMContext):
    numb = call.data.split('_', 1)[1]
    chat_id = call.message.chat.id
    movie_info = get_movie_from_numb(numb)
    if movie_info is not None:
        log_event(call.from_user.id, 'movie_open', int(numb))
    await state.finish()
    await call.message.delete()
    if movie_info is None:
        await bot.send_message(chat_id, f"<strong>Фильм с таким кодом не найден : {numb} !</strong>")
    else:
        await _send_movie_card(chat_id, numb, movie_info)
    await bot.send_message(chat_id, "Искать ещё?", reply_markup=find_movie_markup())
    await call.answer()


@dp.callback_query_handler(lambda call: call.data.startswith('movcat_'))
async def movie_category_handler(call:types.CallbackQuery):
    category_id = int(call.data.split('_', 1)[1])
    subcategories = get_subcategories_by_category(category_id)
    category = get_category_by_id(category_id)
    if not subcategories or category is None:
        await call.answer('Качества для этой озвучки не найдены', show_alert=True)
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
    _, category_id, name, file_id, file_type = subcategory
    category = get_category_by_id(category_id)
    movie_number = category[1] if category else None
    await call.answer()
    log_event(call.from_user.id, 'subcategory_download', int(movie_number) if movie_number is not None else None)
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
    log_event(call.from_user.id, 'movie_download', int(numb))
    if movie_info.get('movie_file_type') == 'video':
        await bot.send_video(chat_id, video=movie_info['movie_file'])
    else:
        await bot.send_document(chat_id, document=movie_info['movie_file'])


@dp.message_handler(state=Users_.find_movie)
async def find_movie_handler(message:types.Message,state:FSMContext):
    chat_id = message.chat.id
    check = await check_user_sub(message)
    if check == False:
        await state.finish()
        await bot.send_message(chat_id, "Подпишитесь на каналы чтобы пользоваться ботом :",reply_markup=sub_markup())
        return

    query = message.text.strip()
    log_event(message.from_user.id, 'search', search_query=query)

    if query.isdigit():
        movie_info = get_movie_from_numb(query)
        await state.finish()
        if movie_info is None:
            await bot.send_message(chat_id, f"<strong>Фильм с таким кодом не найден : {query} !</strong>")
            await bot.send_message(chat_id, "Попробовать ещё раз?", reply_markup=find_movie_markup())
        else:
            log_event(message.from_user.id, 'movie_open', int(query))
            await _send_movie_card(chat_id, query, movie_info)
            await bot.send_message(chat_id, "Искать ещё?", reply_markup=find_movie_markup())
        return

    matches = get_movies_by_title(query)
    if len(matches) == 0:
        await state.finish()
        await bot.send_message(chat_id, f"<strong>Фильм с названием «{query}» не найден !</strong>")
        await bot.send_message(chat_id, "Попробовать ещё раз?", reply_markup=find_movie_markup())
    elif len(matches) == 1:
        title, numb = matches[0]
        movie_info = get_movie_from_numb(numb)
        await state.finish()
        log_event(message.from_user.id, 'movie_open', int(numb))
        await _send_movie_card(chat_id, numb, movie_info)
        await bot.send_message(chat_id, "Искать ещё?", reply_markup=find_movie_markup())
    else:
        listing = '\n'.join(f'{numb} - {title}' for title, numb in matches[:15])
        await message.answer(f"<strong>Найдено несколько фильмов, уточните название или введите код :</strong>\n\n{listing}")
