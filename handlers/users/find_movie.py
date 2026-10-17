from database.channels import get_all_channels_links
import logging
from contextlib import asynccontextmanager
from aiogram import types
from aiogram.utils.exceptions import MessageNotModified
from aiogram.utils.exceptions import MessageCantBeDeleted, MessageToDeleteNotFound
from services.welcome import send_welcome
from services.episode_cards import selection_text
from database import progress
from database.episode_variants import choices as episode_choices, quality_groups
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
_episode_transfers = set()


async def _remove_navigation_message(message):
    # A delivered file remains usable from its original message and buttons.
    if message.video or message.document:
        return
    try:
        await message.delete()
    except (MessageCantBeDeleted, MessageToDeleteNotFound):
        pass


async def _catalog_call_allowed(call):
    from services.age_access import callback_allowed, deny
    if callback_allowed(call.from_user.id, call.data or ''):
        return True
    await deny(bot, call.message.chat.id)
    return False


@dp.callback_query_handler(text="catalog:home", state="*")
async def catalog_home_handler(call: types.CallbackQuery, state: FSMContext):
    await state.finish()
    await call.answer()
    await send_welcome(
        bot, call.message.chat.id, subscribed=bool(await check_subscription(call.from_user.id))
    )
    await _remove_navigation_message(call.message)


_TYPE_LABELS = {"movie": "фильм", "series": "сериал"}
_TYPE_LABELS_PLURAL = {"movie": "Фильмы", "series": "Сериалы"}


def _catalog_markup(page=0, content_type=None, user_id=None, catalog_section=None):
    from database.movie_queries import get_movies_page

    rows, has_more = get_movies_page(page, content_type, catalog_section=catalog_section, user_id=user_id)
    watched = {
        numb
        for title, numb in rows
        if progress.is_watched(user_id, numb, content_type or get_movie_from_numb(numb)["content_type"])
    }
    return get_movies_pick_markup(rows, page, content_type, has_more, watched, catalog_section)


async def _open_catalog(chat_id: int, content_type: str, state: FSMContext, catalog_section=None):
    from database.catalog_sections import get_section
    section = get_section(catalog_section) if catalog_section else None
    await state.update_data(content_type=content_type, catalog_section=catalog_section)
    label = section['title'] if section else _TYPE_LABELS_PLURAL.get(content_type, "🔎 Общий поиск")
    await bot.send_message(
        chat_id,
        f"<strong>{label}</strong>\n\n"
        "Введите название (можно несколько ключевых слов в любом порядке) "
        "или код, либо выберите из списка:",
        reply_markup=_catalog_markup(0, content_type, chat_id, catalog_section),
    )
    await Users_.find_movie.set()


@dp.callback_query_handler(text="catalog:all", state="*")
async def general_search_handler(call: types.CallbackQuery, state: FSMContext):
    if not await check_subscription(call.from_user.id):
        await call.answer("Для доступа подпишитесь на все каналы.", show_alert=True)
        return
    await call.answer()
    await state.finish()
    await _open_catalog(call.message.chat.id, None, state)
    await _remove_navigation_message(call.message)


@dp.callback_query_handler(lambda call: call.data == "find_movie_movie")
async def find_movie_button_handler(call: types.CallbackQuery, state: FSMContext):
    from database.catalog_sections import get_section
    if not get_section('movies')['enabled']:
        await call.answer('Раздел «Фильмы» сейчас отключён.', show_alert=True)
        return
    chat_id = call.message.chat.id
    check = await check_subscription(call.from_user.id)
    if check != False:
        await call.message.delete()
        await _open_catalog(chat_id, "movie", state, 'movies')
    else:
        await call.answer("Вы не подписались на каналы !", show_alert=True)


@dp.callback_query_handler(lambda call: call.data == "find_movie_series")
async def find_series_button_handler(call: types.CallbackQuery, state: FSMContext):
    from database.catalog_sections import get_section
    if not get_section('series')['enabled']:
        await call.answer('Раздел «Сериалы» сейчас отключён.', show_alert=True)
        return
    chat_id = call.message.chat.id
    check = await check_subscription(call.from_user.id)
    if check != False:
        await call.message.delete()
        await _open_catalog(chat_id, "series", state, 'series')
    else:
        await call.answer("Вы не подписались на каналы !", show_alert=True)


@dp.callback_query_handler(lambda call: call.data.startswith('catalog_section:'), state='*')
async def catalog_section_handler(call: types.CallbackQuery, state: FSMContext):
    from database.catalog_sections import get_section
    key = call.data.split(':', 1)[1]
    section = get_section(key)
    if not section or not section['enabled']:
        await call.answer('Этот раздел сейчас отключён.', show_alert=True)
        return
    if not await check_subscription(call.from_user.id):
        await call.answer('Вы не подписались на каналы !', show_alert=True)
        return
    await call.message.delete()
    if section['required_content_type'] is None:
        markup = types.InlineKeyboardMarkup(row_width=1).add(
            types.InlineKeyboardButton('🎬 Полнометражные', callback_data=f'catalog_format:{key}:movie'),
            types.InlineKeyboardButton('📺 Сезоны и серии', callback_data=f'catalog_format:{key}:series'),
            types.InlineKeyboardButton('↩️ Назад', callback_data='catalog:home'))
        await bot.send_message(call.message.chat.id, f"<strong>{section['title']}</strong>\n\nВыберите формат:",
                               reply_markup=markup)
        return
    await _open_catalog(call.message.chat.id, section['required_content_type'], state, key)


@dp.callback_query_handler(lambda call: call.data.startswith('catalog_format:'), state='*')
async def catalog_format_handler(call: types.CallbackQuery, state: FSMContext):
    from database.catalog_sections import get_section
    _, key, content_type = call.data.split(':', 2)
    section = get_section(key)
    if not section or not section['enabled'] or section['required_content_type'] is not None \
            or content_type not in ('movie', 'series'):
        await call.answer('Раздел недоступен.', show_alert=True)
        return
    await call.message.delete()
    await _open_catalog(call.message.chat.id, content_type, state, key)


@dp.callback_query_handler(lambda call: call.data.startswith("moviepage_"), state=Users_.find_movie)
async def movie_page_handler(call: types.CallbackQuery, state: FSMContext):
    _, ct, page = call.data.split("_", 2)
    content_type = None if ct == "all" else ct
    await call.message.edit_reply_markup(
        reply_markup=_catalog_markup(max(0, int(page)), content_type, call.from_user.id)
    )
    await call.answer()


@dp.callback_query_handler(lambda call: call.data.startswith('catalog_page:'), state=Users_.find_movie)
async def catalog_section_page_handler(call: types.CallbackQuery, state: FSMContext):
    from database.catalog_sections import get_section
    _, section, content_type, page = call.data.split(':', 3)
    current = None if section == 'all' else get_section(section)
    if current is not None and not current['enabled']:
        await call.answer('Этот раздел сейчас отключён.', show_alert=True)
        return
    content_type = None if content_type == 'all' else content_type
    await call.message.edit_reply_markup(reply_markup=_catalog_markup(
        max(0, int(page)), content_type, call.from_user.id,
        None if section == 'all' else section))
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


def _episode_file_markup(episode_id, user_id, variant_id=0):
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
                types.InlineKeyboardButton("⬅️ Предыдущая серия", callback_data=f"epnext:{episode_id}:{variant_id}:{ids[index - 1]}")
            )
        if index + 1 < len(ids):
            navigation.append(
                types.InlineKeyboardButton("Следующая серия ➡️", callback_data=f"epnext:{episode_id}:{variant_id}:{ids[index + 1]}")
            )
        if navigation:
            markup.row(*navigation)
        watched = episode_id in progress.episode_marks(user_id, category_id)
        markup.add(
            types.InlineKeyboardButton(
                "✅ Просмотрено · снять отметку" if watched else "☑️ Отметить просмотренным",
                callback_data=f"wp:e:{episode_id}:{int(not watched)}:m:{variant_id}",
            )
        )
        markup.add(
            types.InlineKeyboardButton("↩️ Назад к сериям", callback_data=f"wpage:{category_id}:{index // 8}")
        )
        markup.add(types.InlineKeyboardButton('🎞 Выбрать другое качество', callback_data=f'movsub_{episode_id}'))
    else:
        markup.add(types.InlineKeyboardButton("↩️ Назад", callback_data=f"movcat_{category_id}"))
    if category:
        markup.add(types.InlineKeyboardButton("↩️ Назад к карточке", callback_data=f"filecard_{category[1]}"))
    return markup


def _episode_choice(episode_id, variant_id):
    return next((choice for choice in episode_choices(episode_id) if choice.id == variant_id), None)


def _choice_from_media(message, episode_id):
    media = message.video or message.document
    if not media:
        return None
    matches = [choice for choice in episode_choices(episode_id) if choice.file_id == media.file_id]
    return matches[0] if len(matches) == 1 else None


def _legacy_episode_choice(message, target):
    """Recover the selected file from buttons delivered before this update."""
    if not message.reply_markup or not (message.video or message.document):
        return None
    for row in message.reply_markup.inline_keyboard:
        for button in row:
            parts = (button.callback_data or '').split(':')
            if len(parts) != 6 or parts[:2] != ['wp', 'e'] or parts[4] != 'm':
                continue
            if not parts[2].isascii() or not parts[2].isdigit() or len(parts[2]) > 18:
                continue
            origin = get_subcategory_by_id(int(parts[2]))
            if not origin or origin[1] != target[1]:
                continue
            ids = [item[0] for item in get_subcategories_by_category(target[1])]
            if abs(ids.index(origin[0]) - ids.index(target[0])) != 1:
                continue
            choice = _choice_from_media(message, origin[0])
            if choice:
                return choice
    return None


def _episode_quality_label(choice):
    if not choice.quality:
        return 'Исходный файл'
    return 'Исходное качество' if choice.quality == 'source' else choice.quality


def _clip_episode_title(title, limit=3000):
    encoded = title.encode('utf-16-le')
    if len(encoded) <= limit * 2:
        return title
    return encoded[:max(0, limit - 1) * 2].decode('utf-16-le', errors='ignore') + '…'


def _episode_back_button(episode):
    ids = [row[0] for row in get_subcategories_by_category(episode[1])]
    return types.InlineKeyboardButton(
        '↩️ Назад к сериям', callback_data=f'wpage:{episode[1]}:{ids.index(episode[0]) // 8}')


async def _send_episode_selection_card(call, episode, markup, prompt, selected=None, notice=None):
    category = get_category_by_id(episode[1])
    info = get_movie_from_numb(category[1]) if category else None
    if info is None or info.get('content_type') != 'series':
        await call.answer('Сериал или сезон удалён.', show_alert=True)
        return False
    quality = _episode_quality_label(selected) if selected is not None else None

    def card_text(limit):
        return selection_text(info, category[1], category[2], episode[2], prompt,
                              quality=quality, notice=notice, limit=limit)

    await call.answer()
    if not await _catalog_call_allowed(call):
        return False
    if info.get('poster_image'):
        try:
            await bot.send_photo(call.message.chat.id, photo=info['poster_image'],
                                 caption=card_text(1024), parse_mode='HTML', reply_markup=markup)
            return True
        except Exception:
            logger.exception('Не удалось показать постер при выборе варианта серии %s', episode[0])
    await bot.send_message(call.message.chat.id, card_text(4096), parse_mode='HTML',
                           disable_web_page_preview=True, reply_markup=markup)
    return True


async def _show_episode_qualities(call, episode, notice=None):
    groups = quality_groups(episode[0])
    if not groups:
        await call.answer('Файл недоступен', show_alert=True)
        return
    markup = types.InlineKeyboardMarkup(row_width=1)
    for group in groups:
        markup.add(types.InlineKeyboardButton(
            group.label, callback_data=f'epvoices:{episode[0]}:{group.choices[0].id}'))
    markup.add(_episode_back_button(episode))
    if await _send_episode_selection_card(call, episode, markup, 'Выберите качество:', notice=notice):
        await _remove_navigation_message(call.message)


async def _show_episode_voices(call, episode, selected, notice=None):
    choices = [choice for choice in episode_choices(episode[0]) if choice.quality == selected.quality]
    markup = types.InlineKeyboardMarkup(row_width=1)
    for choice in choices:
        markup.add(types.InlineKeyboardButton(
            choice.voice or 'Озвучка не указана', callback_data=f'epquality:{episode[0]}:{choice.id}'))
    markup.add(types.InlineKeyboardButton('↩️ Назад к качествам', callback_data=f'movsub_{episode[0]}'))
    markup.add(_episode_back_button(episode))
    if await _send_episode_selection_card(call, episode, markup, 'Выберите озвучку:',
                                         selected=selected, notice=notice):
        await _remove_navigation_message(call.message)


@asynccontextmanager
async def _episode_transfer(call):
    # Concurrent repeats from the same button must not start two video sends.
    key = (call.from_user.id, call.message.chat.id, call.message.message_id, call.data)
    if key in _episode_transfers:
        await call.answer('Отправка уже выполняется…')
        yield False
        return
    _episode_transfers.add(key)
    try:
        yield True
    finally:
        _episode_transfers.discard(key)


async def _send_episode_choice(call, episode, choice):
    from services.storage_backup import send_catalog_file

    async with _episode_transfer(call) as acquired:
        if not acquired:
            return
        label = _episode_quality_label(choice) + (' · ' + choice.voice if choice.voice else '')
        suffix = ' · ' + label
        title_limit = max(1, 1024 - len(suffix.encode('utf-16-le')) // 2)
        await call.answer()
        if not await _catalog_call_allowed(call):
            return
        await send_catalog_file(
            bot, call.message.chat.id, choice.file_id, choice.file_type,
            caption=_clip_episode_title(episode[2], title_limit) + suffix,
            reply_markup=_episode_file_markup(episode[0], call.from_user.id, choice.id))
        await _remove_navigation_message(call.message)


async def _continue_episode(call, target, selected):
    if selected is None:
        await _show_episode_qualities(call, target, 'Предыдущий вариант удалён. Выберите качество для этой серии.')
        return
    choices = episode_choices(target[0])
    matching = next((choice for choice in choices
                     if (choice.quality, choice.voice) == (selected.quality, selected.voice)), None)
    if matching:
        await _send_episode_choice(call, target, matching)
        return
    same_quality = next((choice for choice in choices if choice.quality == selected.quality), None)
    if same_quality:
        await _show_episode_voices(call, target, same_quality,
                                   'Выбранной озвучки у этой серии нет. Выберите доступную озвучку.')
    else:
        await _show_episode_qualities(call, target,
                                      'Выбранного качества у этой серии нет. Выберите доступное качество.')


def _episode_callback_ids(data, count):
    parts = data.split(':')[1:]
    if len(parts) != count or any(not p.isascii() or not p.isdigit() or len(p) > 18 for p in parts):
        return None
    return tuple(map(int, parts))


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
    if series:
        markup.add(types.InlineKeyboardButton('↩️ Назад к сезонам', callback_data=f'epseasons:{movie_number}'))
    markup.add(
        types.InlineKeyboardButton(
            text="↩️ Назад к списку сериалов" if series else "↩️ Назад к списку фильмов",
            callback_data=f"filecard_{movie_number}",
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
    if not movie_info.get("movie_trailer"):
        pick_markup.add(types.InlineKeyboardButton("🎬 Смотреть трейлер", callback_data=f"trailer_{code}"))
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
    from services.movie_sharing import share_token
    pick_markup.add(types.InlineKeyboardButton(
        '📤 Поделиться сериалом' if movie_info.get('content_type') == 'series' else '📤 Поделиться фильмом',
        switch_inline_query='share_' + share_token(code)
    ))
    pick_markup.add(types.InlineKeyboardButton("↩️ Назад к списку", callback_data=f"movcatback_{code}"))
    pick_markup.add(types.InlineKeyboardButton("🏠 К выбору фильмов и сериалов", callback_data="catalog:home"))
    return pick_markup


@dp.callback_query_handler(lambda call: call.data.startswith("trailer_"), state="*")
async def trailer_link_handler(call: types.CallbackQuery):
    from services.trailers import find_trailer
    from services.tmdb import TMDB_API_KEY

    raw = call.data.split("_", 1)[1]
    if not raw.isascii() or not raw.isdigit() or len(raw) > 18:
        await call.answer("Кнопка недействительна.", show_alert=True)
        return
    info = get_movie_from_numb(int(raw))
    if not info:
        await call.answer("Проект удалён.", show_alert=True)
        return
    if not TMDB_API_KEY:
        await call.answer("Поиск трейлеров пока не настроен.", show_alert=True)
        return
    await call.answer("Ищу трейлер…")
    url = await find_trailer(info["movie_title"], info.get("content_type", "movie"))
    if not url:
        await bot.send_message(call.message.chat.id,
                               "Трейлер пока не найден или поиск временно недоступен. Попробуйте позже.")
        return
    await bot.send_message(call.message.chat.id, "🎬 Трейлер: " + info["movie_title"], parse_mode="",
                           reply_markup=types.InlineKeyboardMarkup().add(
                               types.InlineKeyboardButton("🎬 Смотреть трейлер", url=url)))


@dp.callback_query_handler(lambda call: call.data.startswith("sharefilm_"), state="*")
async def share_film_handler(call: types.CallbackQuery):
    from services import movie_sharing
    raw = call.data.split('_', 1)[1]
    if not raw.isascii() or not raw.isdigit() or len(raw) > 18:
        await call.answer("Кнопка недействительна.", show_alert=True)
        return
    number = int(raw)
    info = get_movie_from_numb(number)
    if not info:
        await call.answer("Фильм недоступен.", show_alert=True)
        return
    try:
        if not movie_sharing.bot_username:
            movie_sharing.bot_username = (await bot.get_me()).username
        url = movie_sharing.share_url(number, info['movie_title'])
        if not url:
            raise ValueError('Bot username unavailable')
    except Exception:
        logger.warning('Could not resolve bot username for sharing')
        await call.answer("Не удалось подготовить ссылку. Попробуйте ещё раз.", show_alert=True)
        return
    await call.answer()
    await bot.send_message(call.message.chat.id, "Выберите, кому отправить карточку:",
                           reply_markup=types.InlineKeyboardMarkup().add(
                               types.InlineKeyboardButton("📤 Поделиться сериалом" if info.get("content_type")=="series" else "📤 Поделиться фильмом", url=url)))
    # Subsequent clicks on this card open Telegram's recipient picker directly.
    try:
        await call.message.edit_reply_markup(reply_markup=_build_pick_markup(number, info, call.from_user.id))
    except MessageNotModified:
        pass


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
        or len(parts[5]) > (18 if parts[4] == 'm' else 8)
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
        selected = _choice_from_media(call.message, int(parts[2])) if parts[5] == '0' else None
        markup = _episode_file_markup(int(parts[2]), call.from_user.id,
                                      selected.id if selected else int(parts[5]))
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
    selection_menu = call.message.reply_markup and any(
        (button.callback_data or '').startswith(('epvoices:', 'epquality:'))
        for row in call.message.reply_markup.inline_keyboard for button in row)
    if call.message.video or call.message.document or selection_menu:
        info = get_movie_from_numb(category[1])
        if info is None:
            await call.answer("Сериал удалён.", show_alert=True)
            return
        await call.answer()
        await _send_movie_card(
            call.message.chat.id,
            category[1],
            info,
            user_id=call.from_user.id,
            markup_override=markup,
            navigation=True,
        )
        await _remove_navigation_message(call.message)
        return
    try:
        await call.message.edit_reply_markup(reply_markup=markup)
    except MessageNotModified:
        pass
    await call.answer()


async def _send_movie_card(
    chat_id: int, code: int, movie_info: dict, user_id: int = None, markup_override=None, navigation=False,
    extra_buttons=None
):
    from database.age_access import allowed
    if not allowed(user_id if user_id is not None else chat_id, movie_info):
        from services.age_access import deny
        await deny(bot, chat_id)
        return
    categories = get_categories_by_movie(code)
    content_type = movie_info.get("content_type", "movie")
    if categories:
        caption_hint = "\n\nВыберите сезон :" if content_type == "series" else "\n\nВыберите озвучку :"
    else:
        caption_hint = ""
    pick_markup = (
        markup_override
        if markup_override is not None
        else _build_pick_markup(code, movie_info, user_id or chat_id)
    )
    if markup_override is not None:
        caption_hint = "\n\nВыберите серию:" if content_type == "series" else "\n\nВыберите файл:"
    for button in extra_buttons or []:
        pick_markup.add(button)

    from services.ratings import line
    ratings = line(movie_info)
    ratings_text = "\n\n" + ratings if ratings else ""
    sent_ok = False
    if movie_info.get("poster_image"):
        caption = f"<strong>Код : {code}</strong>\n\n<strong>{movie_info['movie_title']}</strong>"
        caption += ratings_text
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
                f"<strong>{movie_info['movie_title']}</strong>{ratings_text}{caption_hint}",
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

    if user_id is not None:
        from database.contests import confirm_card
        confirm_card(user_id, code)

    # Вместе с карточкой фильма отправляем видео-трейлер (если он загружен),
    # чтобы пользователь мог посмотреть его перед выбором озвучки/скачиванием
    if movie_info.get("movie_trailer") and not navigation:
        try:
            await bot.send_video(chat_id, video=movie_info["movie_trailer"], caption="🎬 Трейлер")
        except Exception:
            logger.exception(
                "Не удалось отправить трейлер фильма %s (code=%s) - вероятно, невалидный file_id трейлера",
                movie_info.get("movie_title"),
                code,
            )

    if user_id is not None and not navigation:
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
    info = get_movie_from_numb(movie_number)
    if call.message.video or call.message.document or (info and info['content_type'] == 'series'):
        if info is None:
            await call.answer('Проект удалён.', show_alert=True)
            return
        await call.answer()
        await _send_movie_card(call.message.chat.id, movie_number, info, user_id=call.from_user.id,
                               markup_override=_subcategories_markup(
                                   movie_number, category_id, subcategories, call.from_user.id), navigation=True)
        await _remove_navigation_message(call.message)
        return
    await call.message.edit_reply_markup(
        reply_markup=_subcategories_markup(movie_number, category_id, subcategories, call.from_user.id)
    )
    await call.answer()


@dp.callback_query_handler(lambda call: call.data.startswith("movcatback_"), state="*")
async def movie_category_back_handler(call: types.CallbackQuery, state: FSMContext):
    movie_number = call.data.split("_", 1)[1]
    movie_info = get_movie_from_numb(movie_number)
    if movie_info is None:
        await call.answer("Фильм удалён", show_alert=True)
        return
    await call.answer()
    await state.finish()
    # Return to the exact catalog section the card came from.  Using only
    # content_type would send animated series back to the generic Series list
    # (and full-length anime back to Movies).
    await _open_catalog(
        call.message.chat.id,
        movie_info.get("content_type") or "movie",
        state,
        movie_info.get("catalog_section"),
    )
    await _remove_navigation_message(call.message)


@dp.callback_query_handler(lambda call: call.data.startswith("movsub_"), state="*")
async def movie_subcategory_handler(call: types.CallbackQuery):
    raw = call.data.split('_', 1)[1]
    if not raw.isascii() or not raw.isdigit() or len(raw) > 18:
        await call.answer('Кнопка недействительна.', show_alert=True)
        return
    subcategory_id = int(raw)
    subcategory = get_subcategory_by_id(subcategory_id)
    chat_id = call.message.chat.id
    if subcategory is None:
        await call.answer("Файл недоступен", show_alert=True)
        return
    _, category_id, name, file_id, file_type = subcategory
    category = get_category_by_id(category_id)
    info = get_movie_from_numb(category[1]) if category else None
    if info and info['content_type'] == 'series':
        previous = _legacy_episode_choice(call.message, subcategory)
        if previous:
            await _continue_episode(call, subcategory, previous)
            return
        await _show_episode_qualities(call, subcategory)
        return
    markup = _episode_file_markup(subcategory_id, call.from_user.id)
    await call.answer()
    if not await _catalog_call_allowed(call):
        return
    from services.storage_backup import send_catalog_file
    await send_catalog_file(bot, chat_id, file_id, file_type, caption=name, reply_markup=markup)


@dp.callback_query_handler(lambda call: call.data.startswith('epseasons:'), state='*')
async def episode_seasons_handler(call: types.CallbackQuery):
    code = call.data.split(':', 1)[1]
    info = get_movie_from_numb(code)
    if not info or info['content_type'] != 'series':
        await call.answer('Сериал недоступен', show_alert=True)
        return
    await call.answer()
    await _send_movie_card(call.message.chat.id, code, info, user_id=call.from_user.id, navigation=True)
    await _remove_navigation_message(call.message)


@dp.callback_query_handler(lambda call: call.data.startswith('epquality:'), state='*')
async def episode_quality_handler(call: types.CallbackQuery):
    ids = _episode_callback_ids(call.data, 2)
    if ids is None:
        await call.answer('Кнопка недействительна.', show_alert=True)
        return
    episode_id, variant_id = ids
    episode = get_subcategory_by_id(episode_id)
    choice = _episode_choice(episode_id, variant_id)
    if not episode or not choice:
        await call.answer('Файл недоступен', show_alert=True)
        return
    await _send_episode_choice(call, episode, choice)


@dp.callback_query_handler(lambda call: call.data.startswith('epvoices:'), state='*')
async def episode_voices_handler(call: types.CallbackQuery):
    ids = _episode_callback_ids(call.data, 2)
    if ids is None:
        await call.answer('Кнопка недействительна.', show_alert=True)
        return
    episode_id, variant_id = ids
    episode = get_subcategory_by_id(episode_id)
    choice = _episode_choice(episode_id, variant_id)
    if not episode or not choice:
        await call.answer('Это качество недоступно. Выберите серию заново.', show_alert=True)
        return
    await _show_episode_voices(call, episode, choice)


@dp.callback_query_handler(lambda call: call.data.startswith('epnext:'), state='*')
async def episode_next_handler(call: types.CallbackQuery):
    ids = _episode_callback_ids(call.data, 3)
    if ids is None:
        await call.answer('Кнопка недействительна.', show_alert=True)
        return
    episode_id, variant_id, target_id = ids
    episode = get_subcategory_by_id(episode_id)
    target = get_subcategory_by_id(target_id)
    category = get_category_by_id(episode[1]) if episode else None
    info = get_movie_from_numb(category[1]) if category else None
    if not episode or not target or episode[1] != target[1] or not info or info['content_type'] != 'series':
        await call.answer('Серия недоступна.', show_alert=True)
        return
    episode_ids = [row[0] for row in get_subcategories_by_category(episode[1])]
    if abs(episode_ids.index(episode_id) - episode_ids.index(target_id)) != 1:
        await call.answer('Кнопка устарела. Выберите серию из списка.', show_alert=True)
        return
    selected = _episode_choice(episode_id, variant_id)
    await _continue_episode(call, target, selected)


@dp.callback_query_handler(lambda call: call.data.startswith("dlfile_"))
async def download_movie_file_handler(call: types.CallbackQuery):
    numb = call.data.split("_", 1)[1]
    chat_id = call.message.chat.id
    movie_info = get_movie_from_numb(numb)
    if movie_info is None or not movie_info.get("movie_file"):
        await call.answer("Файл недоступен", show_alert=True)
        return
    await call.answer()
    markup = types.InlineKeyboardMarkup().add(
        types.InlineKeyboardButton("↩️ Назад к карточке", callback_data=f"filecard_{numb}")
    )
    from services.storage_backup import send_catalog_file
    if not await _catalog_call_allowed(call):
        return
    await send_catalog_file(bot, chat_id, movie_info["movie_file"],
                            movie_info.get("movie_file_type") or "document", reply_markup=markup)


@dp.callback_query_handler(lambda call: call.data.startswith("filecard_"), state="*")
async def file_card_back_handler(call: types.CallbackQuery, state: FSMContext):
    raw = call.data.split('_', 1)[1]
    if not raw.isascii() or not raw.isdigit() or len(raw) > 18:
        await call.answer("Кнопка недействительна.", show_alert=True)
        return
    number = int(raw)
    info = get_movie_from_numb(number)
    if not info:
        await call.answer("Произведение удалено из каталога.", show_alert=True)
        return
    await call.answer()
    await state.finish()
    # Keep the delivered file in the chat; send a fresh navigable card below it.
    await _send_movie_card(call.message.chat.id, number, info, user_id=call.from_user.id, navigation=True)


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
    from services.storage_backup import send_catalog_file
    if not await _catalog_call_allowed(call):
        return
    await send_catalog_file(bot, chat_id, file_id, 'document', caption=f"📁 {name}")


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
    catalog_section = data.get('catalog_section')
    if catalog_section:
        from database.catalog_sections import get_section
        section = get_section(catalog_section)
        if not section or not section['enabled']:
            await state.finish()
            await bot.send_message(chat_id, 'Этот раздел сейчас отключён.',
                                   reply_markup=find_movie_markup())
            return

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

    matches = get_movies_by_title(query, content_type, catalog_section, user_id=message.from_user.id)
    log_search(chat_id, query, len(matches))
    label = _TYPE_LABELS.get(content_type, "фильм")
    if len(matches) == 0:
        await state.finish()
        from handlers.users.content_requests import send_missing_cards
        await bot.send_message(chat_id, f"<strong>В каталоге пока нет «{query}».</strong> Проверяю базу произведений…")
        if not await send_missing_cards(message, query[:200], content_type):
            await state.update_data(missing_query=query[:200],missing_kind=content_type)
            request_markup=find_movie_markup()
            request_markup.insert(types.InlineKeyboardButton('Предложить вручную', callback_data='request:search'))
            await bot.send_message(chat_id, "Карточку найти не удалось. Отправьте предложение вручную.", reply_markup=request_markup)
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
