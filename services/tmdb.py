import os
import logging

import aiohttp

logger = logging.getLogger(__name__)

# Ключ нужно получить бесплатно на https://www.themoviedb.org/settings/api
# и добавить как переменную окружения TMDB_API_KEY (там же, где BOT_TOKEN и т.п.)
TMDB_API_KEY = os.getenv('TMDB_API_KEY', '').strip()

_BASE_URL = 'https://api.themoviedb.org/3'
_IMAGE_BASE = 'https://image.tmdb.org/t/p/w500'
_TIMEOUT = aiohttp.ClientTimeout(total=8)


async def _get_json(session, url, params):
    async with session.get(url, params=params, timeout=_TIMEOUT) as resp:
        resp.raise_for_status()
        return await resp.json()


async def search(query: str, content_type: str = 'movie', limit: int = 5):
    """Ищет фильм/сериал в TMDB на русском языке.

    Возвращает список словарей {tmdb_id, title, year, overview, poster_url}.
    Если ключ TMDB_API_KEY не задан, ничего не найдено, либо TMDB недоступен -
    возвращает пустой список, и админ просто заполняет карточку вручную, как раньше
    (это НЕ ошибка, которая должна ломать флоу добавления фильма).
    """
    if not TMDB_API_KEY:
        logger.warning("TMDB_API_KEY не задан - автопоиск карточки через TMDB пропущен")
        return []

    endpoint = 'tv' if content_type == 'series' else 'movie'
    params = {'api_key': TMDB_API_KEY, 'query': query, 'language': 'ru-RU', 'include_adult': 'false'}

    try:
        async with aiohttp.ClientSession() as session:
            data = await _get_json(session, f'{_BASE_URL}/search/{endpoint}', params)
    except Exception:
        logger.exception("Ошибка запроса к TMDB при поиске «%s»", query)
        return []

    results = []
    for item in (data.get('results') or [])[:limit]:
        title = item.get('title') or item.get('name') or query
        date_field = item.get('release_date') or item.get('first_air_date') or ''
        year = date_field.split('-')[0] if date_field else None
        poster_path = item.get('poster_path')
        results.append({
            'tmdb_id': item.get('id'),
            'title': title,
            'year': year,
            'overview': (item.get('overview') or '').strip() or None,
            'poster_url': f'{_IMAGE_BASE}{poster_path}' if poster_path else None,
        })
    return results


async def get_overview(tmdb_id, content_type: str = 'movie'):
    """Отдельно подтягивает описание, если в результатах поиска его не было
    на русском - пробует ru-RU, а если там пусто (частая ситуация для не очень
    известных тайтлов), пробует en-US, чтобы хоть какое-то описание у карточки было."""
    if not TMDB_API_KEY or not tmdb_id:
        return None

    endpoint = 'tv' if content_type == 'series' else 'movie'
    for lang in ('ru-RU', 'en-US'):
        try:
            async with aiohttp.ClientSession() as session:
                data = await _get_json(session, f'{_BASE_URL}/{endpoint}/{tmdb_id}',
                                       {'api_key': TMDB_API_KEY, 'language': lang})
        except Exception:
            logger.exception("Ошибка получения описания TMDB id=%s", tmdb_id)
            return None
        overview = (data.get('overview') or '').strip()
        if overview:
            return overview
    return None
