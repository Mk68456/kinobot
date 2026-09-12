from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

MOVIES_PER_PAGE = 5


def find_movie_markup():
    markup = InlineKeyboardMarkup(row_width=1)
    markup.add(
        InlineKeyboardButton(text="🎬 Фильмы", callback_data="find_movie_movie"),
        InlineKeyboardButton(text="📺 Сериалы", callback_data="find_movie_series"),
    )
    return markup


def get_movies_pick_markup(page_movies, page=0, content_type=None, has_more=False, watched=None):
    markup = InlineKeyboardMarkup(row_width=1)
    for movie in page_movies:
        title, numb = movie[0], movie[1]
        label = f"{numb} - {title}"
        if len(label) > 64:
            label = label[:61] + "..."
        label = label[:60] + (" ✅" if numb in (watched or set()) else " ❌")
        markup.add(InlineKeyboardButton(text=label, callback_data=f"moviepick_{numb}"))

    nav_row = []
    ct = content_type or "all"
    if page > 0:
        nav_row.append(
            InlineKeyboardButton(text="⬅️ Предыдущая страница", callback_data=f"moviepage_{ct}_{page - 1}")
        )
    if has_more:
        nav_row.append(
            InlineKeyboardButton(text="Следующая страница ➡️", callback_data=f"moviepage_{ct}_{page + 1}")
        )
    if nav_row:
        markup.row(*nav_row)
    return markup if (page_movies or nav_row) else None


def torrents_pick_markup(code, torrents):
    markup = InlineKeyboardMarkup(row_width=1)
    for torrent_id, name in torrents:
        markup.add(InlineKeyboardButton(text=f"📁 {name}", callback_data=f"gettorrent_{torrent_id}"))
    markup.add(InlineKeyboardButton(text="⬅️ Назад к карточке", callback_data=f"torrback_{code}"))
    return markup
