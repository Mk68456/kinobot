"""Small, read-only client for the public web player's Filmix source.

Protocol observed in https://kinoserial.online/loader.js (27 October 2026).
The downloaded JavaScript is never executed by this client. This prototype
supports the player's progressive MP4 variant only, not DRM or HLS conversion.
"""

from __future__ import annotations

import base64
import json
import re
import secrets
import time
from urllib.parse import quote, urlsplit


USER_AGENT = "KinoTime-Source-Prototype/0.2"
PLAYER_HEADERS = {"User-Agent": USER_AGENT, "Origin": "https://kinoserial.online",
                  "Referer": "https://kinoserial.online/"}
API_ORIGIN = "https://apiw1.mzona.net"
STREAM_ORIGIN = "https://vsw01.zonasearch.com"


def encode_query(text, salt=17):
    key = secrets.token_bytes(12)
    payload = bytes((value + key[index % len(key)] + index + salt % 256 + index * (salt % 7)) % 256
                    for index, value in enumerate(text.encode("utf-8")))
    return base64.b64encode(bytes([len(key)]) + key + payload).decode("ascii")


def decode_response(text, salt=31):
    data = base64.b64decode(text, validate=True)
    if not data or not 8 <= data[0] <= 16 or len(data) <= data[0] + 1:
        raise ValueError("Неизвестный формат ответа источника")
    length = data[0]
    key = data[1:length + 1]
    return bytes((value - key[index % length] - index - salt % 256 - index * (salt % 7)) % 256
                 for index, value in enumerate(data[length + 1:])).decode("utf-8")


def client_time(seconds=None):
    seconds = int(time.time()) if seconds is None else int(seconds)
    value = 0
    for char in str(seconds) + USER_AGENT + "qARLl3":
        value = (31 * value + ord(char)) & 0xFFFFFFFF
    signed_hash = value if value < 2 ** 31 else value - 2 ** 32
    stamp = seconds * 1000 + abs(signed_hash) % 1000
    return str(stamp) + "." + str(10 ^ (999 - stamp % 1000))


def player_request(origin, path, query, request):
    encoded = quote(encode_query(query + "&client_time=" + client_time()), safe="")
    with request(origin + path + "?q=" + encoded, PLAYER_HEADERS) as response:
        raw = response.read(4 * 1024 * 1024 + 1)
        if len(raw) > 4 * 1024 * 1024:
            raise ValueError("Слишком большой ответ источника")
        try:
            return json.loads(decode_response(raw.decode("ascii")))
        except (ValueError, UnicodeError) as exc:
            raise ValueError("Плеер вернул неизвестный ответ; интерфейс мог измениться") from exc


def page_entity(html, slug):
    """Read Next.js JSON records, never evaluate script bodies or JavaScript."""
    decoder = json.JSONDecoder()
    chunks = []
    for match in re.finditer(r"self\.__next_f\.push\(", html):
        try:
            value, _ = decoder.raw_decode(html[match.end():])
            if isinstance(value, list) and len(value) > 1 and value[0] == 1 and isinstance(value[1], str):
                chunks.append(value[1])
        except ValueError:
            continue

    def find(value):
        if isinstance(value, dict):
            if value.get("slug") == slug and value.get("kp_id"):
                return value
            for child in value.values():
                result = find(child)
                if result is not None:
                    return result
        elif isinstance(value, list):
            for child in value:
                result = find(child)
                if result is not None:
                    return result
        return None

    for line in "".join(chunks).splitlines():
        _, separator, raw = line.partition(":")
        if not separator:
            continue
        try:
            result = find(json.loads(raw))
            if result is not None:
                return result
        except ValueError:
            continue
    raise ValueError("Не удалось прочитать карточку из страницы Zona: формат сайта мог измениться")


def discover_modern(page_url, request):
    parts = urlsplit(page_url)
    segments = parts.path.strip("/").split("/")
    slug = segments[1]
    with request(page_url, {"User-Agent": USER_AGENT}) as response:
        raw = response.read(4 * 1024 * 1024 + 1)
        if len(raw) > 4 * 1024 * 1024:
            raise ValueError("Страница слишком большая")
    entity = page_entity(raw.decode("utf-8"), slug)
    season = episode = None
    episode_key = ""
    if segments[0] == "tvseries":
        season = int(segments[2].removeprefix("season-")) if len(segments) > 2 else 1
        seasons = entity.get("seasons", [])
        selected_season = next((item for item in seasons if int(item.get("part", 0)) == season), None)
        if selected_season and selected_season.get("is_banned"):
            raise ValueError("Выбранный сезон недоступен на сайте")
        episodes = entity.get("episodes", [])
        if not episodes:
            raise ValueError("На странице нет списка серий")
        first = min(episodes, key=lambda item: int(item["part"]))
        if first.get("is_banned"):
            raise ValueError("Первая серия недоступна на сайте")
        episode = int(first["part"])
        episode_key = f"S{season:02d}E{episode:02d}"
    kp_id = int(entity["kp_id"])
    # One source is enough for this experiment; no bulk querying of providers.
    sources = player_request(API_ORIGIN, "/getVideoSources",
                             f"movie_source_types=3&kinopoisk_id={kp_id}&episode_key={episode_key}&trailer=false", request)
    records = sources.get("data", []) if isinstance(sources, dict) else []
    record = next((item for item in records if item.get("video_source_type_id") == 3
                   and (not episode_key or item.get("episode_key") == episode_key)), None)
    if record is None:
        raise ValueError("У выбранного видео нет поддерживаемого источника Filmix")
    key = str(record["download_link_key"]).split("-", 1)[0]
    if not key.isdigit():
        raise ValueError("Неизвестный идентификатор источника")
    query = f"extractor=filmix720p&key={key}"
    if season is not None:
        query += f"&season={season}&episode={episode}"
    streams = player_request(STREAM_ORIGIN, "/getStreams/", query, request)
    if not isinstance(streams, list):
        raise ValueError("Неизвестный формат списка видео")
    found = []
    for stream in streams:
        link = stream.get("url")
        if stream.get("isTrailer") or not isinstance(link, str):
            continue
        video_url = urlsplit(link)
        if video_url.scheme not in {"http", "https"} or not video_url.path.lower().endswith(".mp4"):
            continue
        translation = str(stream.get("translation") or "Основная озвучка")
        resolution = str(stream.get("resolution") or "MP4")
        label = str(entity.get("title") or slug)
        if episode_key:
            label += " — " + episode_key
        label += f" · {translation} · {resolution}"
        source_id = f"zona-{kp_id}-{episode_key or 'movie'}-filmix-{translation}-{resolution}"
        found.append({"url": link, "label": label, "source_id": source_id,
                      "headers": {**(stream.get("headers") or {}), **PLAYER_HEADERS},
                      "metadata": {"title": entity.get("title"), "season": season, "episode": episode,
                                   "kp_id": kp_id, "translation": translation, "resolution": resolution,
                                   "expected_size": stream.get("size"),
                                   "duration_seconds": (stream.get("duration") or 0) / 1_000_000,
                                   "page_url": page_url, "provider": "filmix"},
                      "language": stream.get("language")})
    # Russian variants first. Respect source order within the same language.
    found.sort(key=lambda item: item.get("language") != "ru")
    if not found:
        raise ValueError("Плеер не вернул полноценный MP4 для выбранного видео")
    return found
