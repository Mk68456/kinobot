"""Find trailer links without downloading videos or modifying the catalog."""

import asyncio
import re
import time
from collections import OrderedDict

from services import tmdb

_cache = OrderedDict()
_TTL = 86400
_MAX_CACHE = 256


def _normalized(title):
    return re.sub(r"[\W_]+", " ", title.casefold().replace("ё", "е")).strip()


def _video_url(videos):
    candidates = []
    for video in videos:
        if video.get("type") != "Trailer":
            continue
        key = str(video.get("key") or "")
        if video.get("site") == "YouTube" and re.fullmatch(r"[A-Za-z0-9_-]{11}", key):
            url = f"https://www.youtube.com/watch?v={key}"
        elif video.get("site") == "Vimeo" and re.fullmatch(r"[0-9]{1,15}", key):
            url = f"https://vimeo.com/{key}"
        else:
            continue
        candidates.append((bool(video.get("official")), video.get("published_at") or "", url))
    return max(candidates)[2] if candidates else None


async def _find(title, kind):
    year_match = re.search(r"\s*\((\d{4})\)\s*$", title)
    year = year_match[1] if year_match else None
    query = title[: year_match.start()].strip() if year_match else title.strip()
    results = await tmdb.search(query, kind, limit=20)
    matches = [
        item
        for item in results
        if _normalized(item["title"]) == _normalized(query) and (not year or str(item.get("year")) == year)
    ]
    # Avoid choosing the wrong remake when the title alone is ambiguous.
    if len(matches) != 1 or not matches[0].get("tmdb_id"):
        return None
    endpoint = "tv" if kind == "series" else "movie"
    async with tmdb._new_session() as session:
        for language in ("ru-RU", "en-US"):
            data = await tmdb._get_json(
                session,
                f"{tmdb._BASE_URL}/{endpoint}/{matches[0]['tmdb_id']}/videos",
                {"api_key": tmdb.TMDB_API_KEY, "language": language},
            )
            url = _video_url(data.get("results") or [])
            if url:
                return url
    return None


async def find_trailer(title, content_type="movie"):
    if not tmdb.TMDB_API_KEY or not title:
        return None
    kind = "series" if content_type == "series" else "movie"
    key = (title, kind)
    cached = _cache.get(key)
    if cached and cached[0] > time.monotonic():
        _cache.move_to_end(key)
        return cached[1]
    try:
        url = await asyncio.wait_for(_find(title, kind), timeout=20)
    except Exception:
        # Do not log request URLs, which contain the TMDB API key.
        tmdb.logger.warning("Trailer lookup unavailable for %r (%s)", title, kind)
        return None
    if url:
        _cache[key] = (time.monotonic() + _TTL, url)
        _cache.move_to_end(key)
        while len(_cache) > _MAX_CACHE:
            _cache.popitem(last=False)
    return url
