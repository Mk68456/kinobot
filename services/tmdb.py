import os
import socket
import logging

import aiohttp
from aiohttp.resolver import AbstractResolver

logger = logging.getLogger(__name__)

# Ключ нужно получить бесплатно на https://www.themoviedb.org/settings/api
# и добавить как переменную окружения TMDB_API_KEY (там же, где BOT_TOKEN и т.п.)
TMDB_API_KEY = os.getenv('TMDB_API_KEY', '').strip()

_BASE_URL = 'https://api.themoviedb.org/3'
_IMAGE_BASE = 'https://image.tmdb.org/t/p/w500'
_TIMEOUT = aiohttp.ClientTimeout(total=8)

# TMDB (api.themoviedb.org) заблокирован Роскомнадзором в РФ - обычный DNS хостинга
# отдаёт для него 127.0.0.1 (DNS-заглушка), из-за чего прямое подключение падает.
# Обходим это через DNS-over-HTTPS (Cloudflare 1.1.1.1): сам запрос идёт по IP,
# поэтому DNS для него не нужен, а ответ содержит настоящий (не подменённый) IP TMDB.
# Обычная картинка постера (image.tmdb.org) тут ни при чём - её скачивает сам Telegram
# по URL, который мы просто передаём в send_photo, так что это не наша проблема.
_DOH_ENDPOINT = 'https://1.1.1.1/dns-query'


class _DoHResolver(AbstractResolver):
    """DNS-резолвер поверх DNS-over-HTTPS, в обход DNS-заглушки хостинга."""

    def __init__(self):
        self._cache = {}

    async def resolve(self, host, port=0, family=socket.AF_INET):
        ip = self._cache.get(host)
        if ip is None:
            ip = await self._doh_lookup(host)
            self._cache[host] = ip
        return [{
            'hostname': host,
            'host': ip,
            'port': port,
            'family': socket.AF_INET,
            'proto': 0,
            'flags': 0,
        }]

    @staticmethod
    async def _doh_lookup(host):
        # '1.1.1.1' - это IP-адрес, для запроса к нему самому DNS не требуется,
        # поэтому используем самую обычную сессию (без нашего резолвера).
        async with aiohttp.ClientSession() as session:
            async with session.get(_DOH_ENDPOINT, params={'name': host, 'type': 'A'},
                                   headers={'Accept': 'application/dns-json'},
                                   timeout=_TIMEOUT) as resp:
                resp.raise_for_status()
                # Cloudflare отдаёт Content-Type: application/dns-json, а не
                # application/json - по умолчанию aiohttp считает это несоответствием
                # и падает с ContentTypeError. content_type=None отключает эту проверку.
                data = await resp.json(content_type=None)
        answers = [a['data'] for a in (data.get('Answer') or []) if a.get('type') == 1]
        if not answers:
            raise RuntimeError(f"DoH: не удалось получить A-запись для {host}")
        return answers[0]

    async def close(self):
        pass


def _new_session():
    """Сессия с обходом DNS-заглушки - для запросов к самому TMDB API."""
    connector = aiohttp.TCPConnector(resolver=_DoHResolver())
    return aiohttp.ClientSession(connector=connector)


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
        async with _new_session() as session:
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
            async with _new_session() as session:
                data = await _get_json(session, f'{_BASE_URL}/{endpoint}/{tmdb_id}',
                                       {'api_key': TMDB_API_KEY, 'language': lang})
        except Exception:
            logger.exception("Ошибка получения описания TMDB id=%s", tmdb_id)
            return None
        overview = (data.get('overview') or '').strip()
        if overview:
            return overview
    return None


async def check_connectivity():
    """Проверка при старте бота: доступен ли TMDB вообще с этого сервера.
    Не должна ничего ломать - только пишет понятный результат в лог, чтобы
    проблему с сетью/allowlist доменов на хостинге было видно сразу, а не
    только при первом поиске фильма админом."""
    if not TMDB_API_KEY:
        logger.info("TMDB: TMDB_API_KEY не задан - автопоиск карточек через TMDB выключен")
        return

    try:
        async with _new_session() as session:
            await _get_json(session, f'{_BASE_URL}/configuration', {'api_key': TMDB_API_KEY})
        logger.info("TMDB: соединение с api.themoviedb.org успешно, автопоиск карточек включён")
    except Exception:
        logger.exception(
            "TMDB: не удалось подключиться к api.themoviedb.org при старте, даже через DNS-over-HTTPS. "
            "Возможные причины: 1) TMDB заблокирован не только на уровне DNS, но и по IP - "
            "тогда обход через DoH не поможет; 2) исходящий доступ к 1.1.1.1:443 (Cloudflare DoH) "
            "тоже заблокирован на этом хостинге. Пока это не исправлено, поиск карточек "
            "будет автоматически откатываться на ручной ввод."
        )
