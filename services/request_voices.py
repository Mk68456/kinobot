"""Kinobox metadata for request buttons; never fetch players or media files."""
import asyncio
from collections import OrderedDict
from copy import deepcopy
from datetime import datetime, timezone
import json
import logging
import os
import time

import aiohttp

from services.tmdb import external_imdb_id

logger = logging.getLogger(__name__)
API_URL = os.getenv("KINOBOX_API_URL", "https://kinobox.tv/api/players").strip()
ENABLED = os.getenv("KINOBOX_ENABLED", "1") == "1"
MAX_BYTES = 256 * 1024
CACHE_TTL = 3600
FAILURE_TTL = 60
_cache = OrderedDict()
_rate_lock = asyncio.Lock()
_last_request = 0.0


async def _fetch(imdb_id):
    global _last_request
    async with _rate_lock:
        delay = 0.55 - (time.monotonic() - _last_request)
        if delay > 0:
            await asyncio.sleep(delay)
        _last_request = time.monotonic()
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=7)) as session:
        async with session.get(API_URL, params={"imdb": imdb_id}, allow_redirects=False,
                               headers={"Accept": "application/json"}) as response:
            response.raise_for_status()
            if response.status != 200:
                raise ValueError("Unexpected Kinobox status")
            if response.content_type != "application/json":
                raise ValueError("Kinobox did not return JSON")
            if response.content_length and response.content_length > MAX_BYTES:
                raise ValueError("Kinobox response is too large")
            raw = bytearray()
            async for chunk in response.content.iter_chunked(8192):
                raw.extend(chunk)
                if len(raw) > MAX_BYTES:
                    raise ValueError("Kinobox response is too large")
            return json.loads(raw)


def _text(value, limit):
    return " ".join(value.split())[:limit] if isinstance(value, str) else ""


def parse_voices(payload, checked_at):
    if not isinstance(payload, list):
        raise ValueError("Unexpected Kinobox response shape")
    studios = {}
    succeeded = not payload
    for player in payload[:100]:
        if not isinstance(player, dict) or player.get("success") is not True:
            continue
        source = _text(player.get("source"), 60)
        translations = player.get("translations")
        if not source or not isinstance(translations, list):
            continue
        succeeded = True
        for translation in translations[:200]:
            if not isinstance(translation, dict):
                continue
            name = _text(translation.get("name"), 80)
            # Subtitles are not voice-over studios.
            if not name or any(word in name.casefold() for word in ("субтитр", "subtitle")):
                continue
            identifier = translation.get("id")
            if type(identifier) not in (str, int):
                continue
            proof = {"source": source, "translation_id": str(identifier)[:80], "checked_at": checked_at}
            entry = studios.setdefault(name.casefold(), {"name": name, "evidence": []})
            if proof not in entry["evidence"] and len(entry["evidence"]) < 10:
                entry["evidence"].append(proof)
    voices = sorted(studios.values(), key=lambda item: item["name"].casefold())[:100]
    return {"status": "ok" if voices else "empty" if succeeded else "unavailable", "voices": voices}


async def lookup(tmdb_id, kind):
    if not ENABLED:
        return {"status": "disabled", "voices": []}
    if kind not in ("movie", "series") or type(tmdb_id) is not int or tmdb_id <= 0:
        return {"status": "unmapped", "voices": []}
    key = (kind, tmdb_id)
    cached = _cache.get(key)
    if cached and cached[0] > time.monotonic():
        _cache.move_to_end(key)
        return deepcopy(cached[1])
    try:
        imdb_id = await external_imdb_id(tmdb_id, kind)
        if not imdb_id:
            result = {"status": "unmapped", "voices": []}
        else:
            checked_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
            result = parse_voices(await _fetch(imdb_id), checked_at)
    except (aiohttp.ClientError, asyncio.TimeoutError, ValueError, UnicodeError):
        logger.warning("Kinobox voice metadata unavailable; manual requests remain enabled")
        result = {"status": "unavailable", "voices": []}
    ttl = CACHE_TTL if result["status"] in ("ok", "empty") else FAILURE_TTL
    _cache[key] = (time.monotonic() + ttl, deepcopy(result))
    _cache.move_to_end(key)
    while len(_cache) > 256:
        _cache.popitem(last=False)
    return result
