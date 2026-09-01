import re
from difflib import SequenceMatcher
from loader import cursor

# Разделитель слов для поиска: пробелы, дефисы, двоеточия и любые другие
# небуквенные/нецифровые символы. Это нужно, чтобы составные названия вроде
# "Человек-паук:" превращались в отдельные слова ["человек", "паук"], а не
# оставались одним длинным токеном - иначе опечатки в коротких словах
# (например "павук" вместо "паук") не находились бы через difflib, потому
# что сравнивать 5-буквенное слово с 13-буквенным токеном бессмысленно.
_WORD_SPLIT_RE = re.compile(r'[^a-zа-яё0-9]+')


def _split_words(text):
    return [w for w in _WORD_SPLIT_RE.split(text.lower()) if w]


def get_movie_from_numb(numb : int):
    cursor.execute("PRAGMA table_info(Movies)")
    columns = [row[1] for row in cursor.fetchall()]

    cursor.execute("SELECT * FROM Movies WHERE movie_number=?", (numb,))
    row = cursor.fetchone()
    if row is None:
        return None

    info = dict(zip(columns, row))
    return {'movie_title': info.get('movie_title'),
            'poster_image': info.get('poster_image') or None,
            'card_description': info.get('card_description') or None,
            'card_style': info.get('card_style') or 'simple',
            'movie_file': info.get('movie_file') or None,
            'movie_file_type': info.get('movie_file_type') or None,
            'movie_trailer': info.get('movie_trailer') or None,
            'content_type': info.get('content_type') or 'movie'}


def _word_matches(keyword, title_words, threshold=0.75):
    """Проверяет, есть ли среди слов названия слово, похожее на keyword.
    Сначала пробуем точное вхождение (быстро и надёжно), и только если оно
    не сработало - нечёткое сравнение через difflib (прощает опечатки)."""
    for word in title_words:
        if keyword in word:
            return True
        if len(keyword) >= 4 and SequenceMatcher(None, keyword, word).ratio() >= threshold:
            return True
    return False


def get_movies_by_title(query:str, content_type=None):
    """Ищет фильмы по ключевым словам в названии (без учёта регистра,
    с прощением небольших опечаток). Запрос разбивается на отдельные
    слова, и фильм подходит, если КАЖДОЕ слово из запроса встречается
    (точно или почти точно) где-то в его названии - слова можно вводить
    в любом порядке. SQLite сам не умеет регистронезависимо сравнивать
    кириллицу, поэтому фильтруем в Python.
    content_type: если задан ('movie'/'series') - ищет только среди этого типа.
    Возвращает список (movie_title, movie_number)."""
    cursor.execute("PRAGMA table_info(Movies)")
    columns = {row[1] for row in cursor.fetchall()}
    if content_type and 'content_type' in columns:
        cursor.execute("SELECT movie_title, movie_number FROM Movies WHERE COALESCE(content_type,'movie')=?",
                       (content_type,))
    else:
        cursor.execute("SELECT movie_title, movie_number FROM Movies")
    all_movies = cursor.fetchall()

    keywords = _split_words(query)
    if not keywords:
        return []

    matches = []
    for title, numb in all_movies:
        if not title:
            continue
        title_words = _split_words(title)
        if all(_word_matches(keyword, title_words) for keyword in keywords):
            matches.append((title, numb))
    return matches


