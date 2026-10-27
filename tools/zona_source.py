"""Resolve a single title/episode using the public Zona Plus web interface.

Protocol references:
https://github.com/l0v3m0n3y/zona/blob/main/src/zona.js
https://github.com/vlmaksime/plugin.video.zona.mobi/blob/master/plugin.video.zona.mobi/resources/lib/zonamobi.py
This is a fresh implementation; no upstream code is imported or executed.
"""

from __future__ import annotations

import json
import re
import time
from urllib.parse import urlsplit, urlunsplit


ZONA_HOST = re.compile(r"w\d+\.zona\.(?:im|plus)\Z", re.IGNORECASE)
AJAX_HEADERS = {
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "X-Requested-With": "XMLHttpRequest",
}


def is_zona_page(url):
    parts = urlsplit(url)
    return bool(parts.scheme in {"http", "https"} and ZONA_HOST.fullmatch(parts.hostname or "")
                and re.fullmatch(r"/(?:movies|tvseries)/[^/]+(?:/season-\d+)?/?", parts.path))


def read_json(url, request):
    with request(url, AJAX_HEADERS) as response:
        body = response.read(4 * 1024 * 1024 + 1)
        if len(body) > 4 * 1024 * 1024:
            raise ValueError("Ответ каталога слишком большой")
        try:
            value = json.loads(body)
        except (ValueError, UnicodeDecodeError) as exc:
            raise ValueError("Zona вернула страницу вместо данных каталога") from exc
        if not isinstance(value, dict):
            raise ValueError("Неизвестный формат ответа Zona")
        return value


def sources_from_data(data, page_url, request, origin):
    """Choose first episode in the requested season, never fabricate video URLs."""
    serial = data.get("serial")
    title = data.get("movie") if serial is None else serial
    if not isinstance(title, dict):
        raise ValueError("В ответе Zona нет фильма или сериала")
    season = episode = None
    if serial is not None:
        episodes = data.get("episodes", {}).get("items", [])
        if isinstance(episodes, dict):
            episodes = list(episodes.values())
        episodes = [item for item in episodes if isinstance(item, dict)]
        if not episodes:
            raise ValueError("У сериала нет доступных серий")
        try:
            selected = min(episodes, key=lambda item: (int(item["season"]), int(item["episode"])))
            season, episode = int(selected["season"]), int(selected["episode"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("Zona вернула неизвестный формат списка серий") from exc
        media_id = selected.get("mobi_link_id")
    else:
        media_id = title.get("mobi_link_id")
    if not str(media_id or "").isdigit():
        raise ValueError("У выбранного видео нет идентификатора источника")
    payload = read_json(f"{origin}/ajax/video/{media_id}?client_time={int(time.time() * 1000)}", request)
    name = str(title.get("name_rus") or title.get("name_original") or "Видео")
    label = name + (f" — S{season:02d}E{episode:02d}" if season is not None else "")
    results = []
    for field, quality_label in (("lqUrl", "низкое качество"), ("mqUrl", "среднее качество"), ("url", "основной вариант")):
        link = payload.get(field)
        if not isinstance(link, str):
            continue
        parts = urlsplit(link)
        if parts.scheme not in {"http", "https"} or not parts.hostname or not parts.path.lower().endswith(".mp4"):
            continue
        if link in [item["url"] for item in results]:
            continue
        results.append({"url": link, "label": f"{label} · {quality_label}",
                        "headers": {"Referer": origin + "/"},
                        "source_id": f"zona-{media_id}-{field}",
                        "metadata": {"title": name, "season": season, "episode": episode,
                                     "media_id": str(media_id), "variant": field,
                                     "page_url": page_url, "catalog_url": origin}})
    if not results:
        raise ValueError("Zona не вернула прямой MP4 для выбранного видео. HLS пока не поддерживается.")
    return results


def discover_zona(page_url, request):
    if not is_zona_page(page_url):
        raise ValueError("Нужна ссылка на страницу фильма или сериала Zona")
    parts = urlsplit(page_url)
    if parts.hostname.lower().endswith(".im"):
        if __package__:
            from .zona_modern import discover_modern
        else:
            from zona_modern import discover_modern
        return discover_modern(page_url, request)
    # The older Plus frontend exposes JSON on the same page routes.
    origin = f"{parts.scheme}://{parts.netloc}"
    catalog_url = urlunsplit((*urlsplit(origin)[:2], parts.path, "", ""))
    data = read_json(catalog_url, request)
    return sources_from_data(data, page_url, request, origin)
