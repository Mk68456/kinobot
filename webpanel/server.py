import json
import logging
import secrets
import re
import time
from pathlib import Path
from urllib.parse import urlsplit
from aiohttp import web
from data import settings
from database.connection import database
from webpanel import auth, repository, imports
from webpanel.series_validation import SeriesVariantConflict
from webpanel.episode_merge import EpisodeMergeConflict
from webpanel.uploads import Uploads

logger = logging.getLogger(__name__)
STATIC = Path(__file__).parent / "static"
MAX_JSON_BYTES = 16 * 1024**2


@web.middleware
async def errors(request, handler):
    try:
        response = await handler(request)
    except (ValueError, KeyError, TypeError, json.JSONDecodeError) as error:
        if isinstance(error, ValueError):
            message = str(error)
        else:
            reference = secrets.token_hex(4)
            logger.exception('Web data processing failed [%s]: %s', reference, request.path)
            message = f'Не удалось обработать данные на сервере. Код ошибки: {reference}. Проверьте журнал бота.'
        payload = {"error": message}
        if isinstance(error, (SeriesVariantConflict, EpisodeMergeConflict)):
            payload['conflicts'] = error.conflicts
        response = web.json_response(payload, status=400)
    except web.HTTPException as error:
        response = web.json_response({"error": error.reason}, status=error.status)
    except Exception:
        logger.exception("Web request failed: %s", request.path)
        response = web.json_response({"error": "Ошибка сервера. Данные черновика сохранены."}, status=500)
    response.headers.update(
        {
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
            "Referrer-Policy": "no-referrer",
            "X-Frame-Options": "DENY",
            "Content-Security-Policy": "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data: https:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'",
        }
    )
    return response


@web.middleware
async def access(request, handler):
    if request.path.startswith('/api/desktop/'):
        token = request.headers.get('Authorization', '').removeprefix('Bearer ')
        instance = request.headers.get('X-KinoTime-Instance', '')
        admin_id = imports.authenticate(token, instance)
        if admin_id is None:
            raise web.HTTPUnauthorized(reason='Получите отдельный свежий код /desktop для этого компьютера.')
        if not instance and database.execute("SELECT 1 FROM DesktopSessions WHERE admin_id=? AND instance_id<>'' AND expires_at>?", (admin_id, time.time())).fetchone():
            raise ValueError('Обновите все лаунчеры до версии 1.3.0. Старое окно не поддерживает безопасную работу нескольких компьютеров.')
        request['admin_id'] = admin_id
        request['instance_id'] = instance
        return await handler(request)
    if request.path.startswith("/api/"):
        origin = request.headers.get("Origin")
        if request.method not in ("GET", "HEAD") and origin != request.app["origin"]:
            raise web.HTTPForbidden(reason="Откройте панель по её основному адресу.")
        if request.path != "/api/login":
            session = auth.get_session(request.cookies.get("kinobot_session", ""))
            if not session:
                raise web.HTTPUnauthorized(reason="Войдите через команду /web в Telegram.")
            request["admin_id"] = session["admin_id"]
            request["session"] = session
            if request.method not in ("GET", "HEAD") and not secrets.compare_digest(
                request.headers.get("X-CSRF-Token", ""), session["csrf"]
            ):
                raise web.HTTPForbidden(reason="Сессия изменилась. Обновите страницу.")
    return await handler(request)


async def body(request):
    if request.content_length and request.content_length > MAX_JSON_BYTES:
        raise ValueError("Слишком большой запрос.")
    raw = bytearray()
    async for part in request.content.iter_chunked(8192):
        raw.extend(part)
        if len(raw) > MAX_JSON_BYTES:
            raise ValueError("Слишком большой запрос.")
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError("Ожидается объект запроса.")
    return value


async def index(request):
    return web.FileResponse(STATIC / "index.html")


async def static_file(request):
    name = request.match_info["name"]
    if name not in ("app.js", "contests.js", "style.css", "favicon.svg"):
        raise web.HTTPNotFound()
    return web.FileResponse(STATIC / name)


async def login(request):
    data = await body(request)
    token = str(data.get("token", ""))
    if not 30 <= len(token) <= 100:
        raise web.HTTPUnauthorized(reason="Неверная ссылка входа.")
    session = auth.exchange_login(token)
    response = web.json_response({"ok": True})
    response.set_cookie(
        "kinobot_session",
        session,
        httponly=True,
        secure=request.app["secure"],
        samesite="Strict",
        max_age=43200,
        path="/",
    )
    return response


async def session(request):
    return web.json_response(
        {
            **request["session"],
            "max_file_bytes": request.app["uploads"].max_bytes,
            "chunk_bytes": request.app["uploads"].chunk_bytes,
        }
    )


async def logout(request):
    auth.logout(request.cookies.get("kinobot_session", ""))
    response = web.json_response({"ok": True})
    response.del_cookie("kinobot_session", path="/")
    return response


async def list_assets(request):
    return web.json_response({"assets": repository.assets(request["admin_id"], request.query.get('draft_id'))})


async def create_upload(request):
    return web.json_response(
        request.app["uploads"].create(request["admin_id"], await body(request)), status=201
    )


async def get_upload(request):
    file = repository.asset(request.match_info["id"], request["admin_id"])
    if not file:
        raise web.HTTPNotFound(reason="Файл не найден.")
    return web.json_response(file)


async def preview_upload(request):
    result = repository.asset_preview(request.match_info['id'], request['admin_id'])
    from webpanel.media_preview import connection_settings
    api_id, api_hash, session = connection_settings()
    result['player_configured'] = bool(api_id.isdecimal() and api_hash and session.is_file())
    return web.json_response(result)


async def stream_preview_upload(request):
    return await request.app['media_preview'].handle(request)


async def stream_preview_status(request):
    try:
        await request.app['media_preview'].source(request.match_info['id'], request['admin_id'])
    except ValueError as error:
        return web.json_response({'error': str(error)})
    return web.json_response({'error': 'Если видео не воспроизводится, проверьте кодек файла и журнал сервера.'})


async def upload_chunk(request):
    chunk = await request.read()
    offset = int(request.headers.get("X-Upload-Offset", "-1"))
    received = await request.app["uploads"].append(
        request.match_info["id"],
        request["admin_id"],
        offset,
        chunk,
        request.headers.get("X-Chunk-SHA256", ""),
    )
    return web.json_response({"received": received})


async def complete_upload(request):
    request.app["uploads"].enqueue(request.match_info["id"], request["admin_id"])
    return web.json_response({"ok": True})


async def discard_upload(request):
    await request.app["uploads"].discard(request.match_info["id"], request["admin_id"])
    return web.json_response({"ok": True})


async def list_drafts(request):
    return web.json_response({"drafts": repository.drafts(request["admin_id"])})


async def get_draft(request):
    current = repository.draft(request.match_info['id'], request['admin_id'])
    if not current:
        raise web.HTTPNotFound(reason='Черновик недоступен.')
    return web.json_response(current)


async def save_draft(request):
    data = await body(request)
    current = repository.save_draft(request["admin_id"], data.get("body"), data.get("id"))
    return web.json_response({**current, 'review_token': repository.review_token(current)})


async def publish_draft(request):
    number = repository.publish(request.match_info["id"], request["admin_id"])
    await notify_request_for_draft(request, request.match_info["id"], number)
    return web.json_response({"movie_number": number})


async def catalog_merge_cards(request):
    from webpanel import catalog_merge
    data = await body(request)
    if data.get('action') == 'preview':
        return web.json_response(catalog_merge.preview(data.get('source'), data.get('target')))
    if data.get('action') == 'merge':
        return web.json_response(catalog_merge.merge(data.get('source'), data.get('target'), data.get('token')))
    raise ValueError('Некорректное действие объединения.')


async def review_queue(request):
    return web.json_response(repository.review_queue(request['admin_id'], request.query.get('offset', 0)))


async def reject_all_drafts(request):
    data = await body(request)
    return web.json_response({'rejected': repository.reject_all(request['admin_id'], data.get('expected'))})


async def publish_batch(request):
    data = await body(request)
    results = repository.publish_many(request['admin_id'], data.get('items'))
    for result in results:
        if result.get('ok'):
            await notify_request_for_draft(request, result['id'], result['movie_number'])
    return web.json_response({'results': results})


async def publication_package(request):
    return web.json_response(repository.publication_package(request['admin_id']))


async def update_package(request):
    data = await body(request)
    if data.get('action') == 'remove':
        result = repository.package_remove(request['admin_id'], request.match_info['id'])
    else:
        result = repository.package_add(request['admin_id'], request.match_info['id'], data.get('review_token'))
    return web.json_response(result)


async def publish_package(request):
    data = await body(request)
    results = repository.package_publish(request['admin_id'], data.get('items'))
    for result in results:
        if result.get('ok'):
            await notify_request_for_draft(request, result['id'], result['movie_number'])
    return web.json_response({'results': results})


async def notify_request_for_draft(request, draft_id, number):
    """Queue one completion message for everyone who requested this draft."""
    from database import content_requests
    current = content_requests.by_draft(draft_id)
    if not current or current['status'] == 'added':
        return
    if not content_requests.target_available(current, number, manual=True):
        return
    try:
        bot = request.app['uploads'].bot
        username = (await bot.get_me()).username
        keyboard = None
        if username:
            from aiogram import types
            keyboard = types.InlineKeyboardMarkup().add(types.InlineKeyboardButton(
                'Открыть в боте', url=f'https://t.me/{username}?start=movie_{number}'
            ))
        source = await bot.send_message(
            request['admin_id'],
            'По вашему запросу произведение добавлено в KinoTime: ' + current['title'] + content_requests.target_label(current),
            parse_mode='', reply_markup=keyboard,
        )
        content_requests.complete(current['id'], number, request['admin_id'],
                                  source.chat.id, source.message_id)
        from services.broadcasts import broadcasts
        broadcasts.notify()
    except Exception:
        logger.exception('Unable to queue request completion for draft %s', draft_id)


async def content_request_list(request):
    from database import content_requests
    return web.json_response(content_requests.web_page(request.query.get('offset', 0)))


async def content_request_action(request):
    from database import content_requests
    request_id = int(request.match_info['id'])
    current = content_requests.get(request_id)
    if not current:
        raise web.HTTPNotFound(reason='Заявка не найдена.')
    data = await body(request)
    action = data.get('action')
    if action in ('new', 'planned', 'rejected'):
        content_requests.set_status(request_id, action)
        return web.json_response({'request': content_requests.get(request_id)})
    if action != 'create_draft':
        raise ValueError('Неизвестное действие с заявкой.')
    if current.get('draft_id'):
        draft = repository.draft(current['draft_id'], request['admin_id'])
        if draft:
            return web.json_response({'draft': draft, 'request': current})
    draft = repository.save_draft(request['admin_id'], {
        'content_type': current['content_type'], 'title': current['title'],
        'description': current.get('overview') or '', 'poster_url': current.get('poster_url') or '',
        'season_name': f"Сезон {current['season']}" if current['season'] else '',
        'season_number': current['season'] or (1 if current['content_type'] == 'series' else None),
        'movie_number': None, 'category_id': None, 'items': [],
    })
    current = content_requests.link_draft(request_id, draft['id'])
    return web.json_response({'draft': draft, 'request': current})


async def review_draft(request):
    data = await body(request)
    return web.json_response(repository.review(request.match_info['id'], request['admin_id'],
                                               data.get('status'), data.get('comment', '')))


async def promo_draft(request):
    me = await request.app['uploads'].bot.get_me()
    return web.json_response(repository.promo(request.match_info['id'], request['admin_id'], me.username))


async def desktop_import(request):
    current = imports.create(request['admin_id'], await body(request), request['instance_id'])
    if current.get('created') and not current['body'].get('movie_number'):
        from services.tmdb import search
        title = current['body']['title']
        match = re.fullmatch(r'(.*?)\s*\((\d{4})\)', title)
        query, year = (match[1], match[2]) if match else (title, None)
        candidates = await search(query, 'movie')
        exact = [c for c in candidates if c['title'].casefold() == query.casefold()
                 and (year is None or c['year'] == year)]
        latest = repository.draft(current['id'], request['admin_id'])
        # Metadata lookup must never overwrite intervening review/editor changes.
        if len(exact) == 1 and latest['status'] == 'draft' and latest['body'] == current['body']:
            card = dict(current['body'])
            card.update(description=(exact[0]['overview'] or card['description'])[:2500],
                        poster_url=exact[0]['poster_url'] or '')
            current = repository.save_draft(request['admin_id'], card, current['id'])
    me = await request.app['uploads'].bot.get_me()
    return web.json_response({'draft': current, 'bot_username': me.username, 'bot_name': me.full_name})


async def desktop_stream(request):
    from webpanel import streaming
    current = streaming.append(request['admin_id'], request['instance_id'], await body(request))
    me = await request.app['uploads'].bot.get_me()
    return web.json_response({'draft': current, 'bot_username': me.username, 'bot_name': me.full_name})


async def desktop_provider_import(request):
    from webpanel.provider_import import create
    current, linked = create(request['admin_id'], await body(request), request['instance_id'])
    me = await request.app['uploads'].bot.get_me()
    return web.json_response({'draft': current, 'linked_existing': linked,
                              'bot_username': me.username, 'bot_name': me.full_name})


async def desktop_stream_finish(request):
    from webpanel import streaming
    current = streaming.finish(request['admin_id'], request['instance_id'], request.match_info['id'])
    return web.json_response({'draft': current})


async def desktop_status(request):
    if not repository.draft(request.match_info['id'], request['admin_id']):
        raise web.HTTPNotFound()
    current = imports.device_draft(request['admin_id'], request.match_info['id'], request['instance_id'])
    if not current:
        raise web.HTTPNotFound()
    if request.method == 'POST':
        for item in current['body']['items']:
            file = repository.asset(item['asset_id'], request['admin_id'])
            if file and file['status'] == 'waiting_telegram':
                from database.connection import database
                with database.transaction():
                    database.execute('UPDATE WebAssets SET error=NULL WHERE id=?', (file['id'],))
    return web.json_response({'draft_status': current['status'], 'files': [
        {'id': i['asset_id'], 'status': repository.asset(i['asset_id'], request['admin_id'])['status'],
         'error': repository.asset(i['asset_id'], request['admin_id'])['error']}
        for i in current['body']['items']]})


async def desktop_reopen(request):
    data = await body(request)
    current = imports.reopen(request['admin_id'], request.match_info['id'], data.get('kind'), request['instance_id'])
    return web.json_response({'draft': current})


async def desktop_session(request):
    me = await request.app['uploads'].bot.get_me()
    return web.json_response({'protocol': 2, 'admin_id': request['admin_id'],
                              'max_import_files': repository.MAX_IMPORT_FILES,
                              'capabilities': {'multi_launcher': 1, 'remote_control': 1, 'streaming_import': 1,
                                               'provider_import': 1, 'instant_requests': 1, 'provider_batches': 1},
                              'bot_username': me.username, 'bot_name': me.full_name})


async def desktop_control_heartbeat(request):
    from webpanel import launcher_control
    data = await body(request)
    if request['instance_id'] and data.get('instance_id') != request['instance_id']:
        raise ValueError('Компьютер в запросе не соответствует коду подключения.')
    if not request['instance_id']:
        data['protocol'] = 2
    return web.json_response(launcher_control.heartbeat(request['admin_id'], data))


async def desktop_control_complete(request):
    from webpanel import launcher_control
    data = await body(request)
    return web.json_response(launcher_control.complete(request['admin_id'], int(request.match_info['id']),
                                                       data.get('ok'), data.get('result', ''), request['instance_id'], data.get('data')))


async def launcher_control_state(request):
    from webpanel import launcher_control
    return web.json_response(launcher_control.state(request['admin_id']))


async def launcher_control_command(request):
    from webpanel import launcher_control
    data = await body(request)
    return web.json_response(launcher_control.enqueue(request['admin_id'], data.get('action'), data.get('payload') or {}, data.get('instance_id', '')))


async def desktop_transfer(request):
    from webpanel import launcher_transfers
    data = await body(request)
    return web.json_response(launcher_transfers.operate(request['admin_id'], request['instance_id'],
        request.match_info['id'], request.match_info['asset'], data.get('action'), data.get('verified_not_sent')))


async def desktop_transfer_reset(request):
    from webpanel import launcher_transfers
    data = await body(request)
    return web.json_response(launcher_transfers.reset(request['admin_id'], request['instance_id'],
        data.get('asset_ids'), data.get('verified_not_sent')))


async def storage_state(request):
    from services import storage_backup
    return web.json_response(storage_backup.status())


async def storage_action(request):
    from services import storage_backup
    data = await body(request)
    action = data.get('action')
    if action == 'scan':
        added = storage_backup.discover()
        return web.json_response({**storage_backup.status(), 'added': added})
    if action == 'retry':
        storage_backup.retry_errors()
        return web.json_response(storage_backup.status())
    if action in ('pause', 'resume'):
        storage_backup.set_enabled(action == 'resume')
        return web.json_response(storage_backup.status())
    if action == 'check':
        current = storage_backup.configuration()
        if not current:
            raise ValueError('Подключите канал командой /storage в Telegram.')
        chat = await request.app['uploads'].bot.get_chat(current['chat_id'])
        me = await request.app['uploads'].bot.get_me()
        member = await request.app['uploads'].bot.get_chat_member(current['chat_id'], me.id)
        if member.status not in ('administrator', 'creator') or not getattr(member, 'can_post_messages', False):
            raise ValueError('У бота нет права публикации в Storage-канале.')
        return web.json_response({**storage_backup.status(), 'checked': True, 'title': chat.title})
    raise ValueError('Неизвестное действие резервирования.')


async def card_candidates(request):
    from services.tmdb import search
    data = await body(request)
    query = str(data.get('title', '')).strip()[:200]
    return web.json_response({'candidates': await search(query, data.get('content_type', 'movie'))})


async def group_draft_files(request):
    data=await body(request)
    return web.json_response(repository.group_files(request['admin_id'],data.get('items'),data.get('body') or {}))


async def renumber_catalog_movie(request):
    data=await body(request)
    return web.json_response(repository.renumber_movie(request['admin_id'],request.match_info['id'],data.get('new_movie_number')))


async def catalog(request):
    query = request.query.get("q", "").strip()[:120]
    page = max(0, min(int(request.query.get("page", "0")), 100000))
    age_filter = " AND (age_rating IS NULL OR age_rating NOT IN ('0+','6+','12+','16+','18+'))" if request.query.get('unrated') == '1' else ''
    rows = database.execute(
        "SELECT movie_number,movie_title,content_type,catalog_section,age_rating FROM Movies WHERE movie_title LIKE ?" + age_filter + " ORDER BY movie_number DESC LIMIT 51 OFFSET ?",
        (f"%{query}%", page * 50),
    ).fetchall()
    return web.json_response(
        {
            "movies": [{"id": r[0], "title": str(r[1]), "content_type": r[2] or "movie",
                        "catalog_section": r[3] or ('series' if r[2] == 'series' else 'movies'), 'age_rating': r[4]}
                       for r in rows[:50]],
            "has_more": len(rows) > 50,
        }
    )


async def catalog_sections(request):
    from database.catalog_sections import all_sections, set_enabled
    if request.method == 'POST':
        data = await body(request)
        key = str(data.get('key') or '')
        enabled = data.get('enabled')
        if type(enabled) is not bool:
            raise ValueError('Укажите состояние раздела.')
        set_enabled(key, enabled)
    return web.json_response({'sections': all_sections()})


async def catalog_movie_section(request):
    from database.catalog_sections import validate_section
    number = int(request.match_info['id'])
    data = await body(request)
    row = database.execute('SELECT content_type FROM Movies WHERE movie_number=?', (number,)).fetchone()
    if not row:
        raise web.HTTPNotFound(reason='Карточка не найдена.')
    key = str(data.get('catalog_section') or '')
    validate_section(key, row[0] or 'movie')
    with database.transaction():
        database.execute('UPDATE Movies SET catalog_section=? WHERE movie_number=?', (key, number))
    return web.json_response({'ok': True, 'catalog_section': key})


async def catalog_folders(request):
    number = request.match_info['id']
    if request.method == 'POST':
        data = await body(request)
        return web.json_response(repository.rename_catalog_folders(number, data.get('folders')))
    return web.json_response(repository.catalog_folders(number))


async def merge_catalog_episodes(request):
    from webpanel import episode_merge

    raw = request.match_info['id']
    if not re.fullmatch(r'[0-9]{1,18}', raw):
        raise ValueError('Некорректный код карточки.')
    number = int(raw)
    data = await body(request)
    values = (number, data.get('folder_id'), data.get('target_id'), data.get('episode_ids'), data.get('name'))
    if data.get('action') == 'preview':
        return web.json_response(episode_merge.preview(*values))
    if data.get('action') != 'apply':
        raise ValueError('Сначала проверьте объединение серий.')
    result = episode_merge.merge(*values, data.get('expected_token'))
    return web.json_response({**result, 'catalog': repository.catalog_folders(number)})


async def rating_lookup(request):
    from services.poiskkino import lookup
    data = await body(request)
    return web.json_response(await lookup(data.get('query',''),data.get('content_type','movie'),
                                         data.get('source_id'),data.get('refresh') is True))


async def catalog_ratings(request):
    from services.ratings import normalize
    number = int(request.match_info['id'])
    if not database.execute('SELECT 1 FROM Movies WHERE movie_number=?', (number,)).fetchone():
        raise web.HTTPNotFound()
    if request.method == 'POST':
        from services.poiskkino import provenance
        data = await body(request)
        values = {**normalize(data), **provenance(data)}
        with database.transaction():
            database.execute('UPDATE Movies SET rating_kp=?,rating_imdb=?,age_rating=?,rating_source_id=?,ratings_fetched_at=? WHERE movie_number=?',
                             (values['rating_kp'],values['rating_imdb'],values['age_rating'],values['rating_source_id'],values['ratings_fetched_at'],number))
    row = database.execute('SELECT rating_kp,rating_imdb,age_rating,rating_source_id,ratings_fetched_at FROM Movies WHERE movie_number=?', (number,)).fetchone()
    return web.json_response(dict(zip(('rating_kp','rating_imdb','age_rating','rating_source_id','ratings_fetched_at'),row)))


async def seasons(request):
    number = int(request.match_info["id"])
    rows = database.execute(
        "SELECT id,name,season_number FROM MovieCategories WHERE movie_number=? ORDER BY season_number,id",
        (number,),
    ).fetchall()
    return web.json_response({"seasons": [{"id": r[0], "name": r[1], "number": r[2]} for r in rows]})


def create_app(bot, config=settings):
    from webpanel import instant as instant_api
    url = urlsplit(config.WEB_PUBLIC_URL)
    if (
        url.scheme not in ("http", "https")
        or not url.hostname
        or url.username
        or url.password
        or url.path not in ("", "/")
        or url.query
        or url.fragment
    ):
        raise ValueError("WEB_PUBLIC_URL должен быть адресом без пути, например https://kino.example.com")
    if url.scheme == "http" and url.hostname not in ("localhost", "127.0.0.1", "::1"):
        raise ValueError("Для удалённого доступа задайте HTTPS-адрес WEB_PUBLIC_URL.")
    app = web.Application(middlewares=[errors, access], client_max_size=max(config.WEB_CHUNK_BYTES + 1024, MAX_JSON_BYTES))
    app["origin"] = f"{url.scheme}://{url.netloc}"
    app["secure"] = url.scheme == "https"
    max_bytes = (
        config.WEB_MAX_FILE_BYTES
        if config.BOT_API_BASE != "https://api.telegram.org"
        else min(config.WEB_MAX_FILE_BYTES, 50_000_000)
    )
    app['contest_bot'] = bot
    from webpanel.media_preview import PreviewStream
    app['media_preview'] = PreviewStream()
    from webpanel import contests as contest_admin
    app["uploads"] = Uploads(
        bot, config.UPLOAD_DIR, max_bytes, config.UPLOAD_QUOTA_BYTES, config.WEB_CHUNK_BYTES
    )
    from webpanel import provider_batches as batch_api
    app.add_routes(
        [
            web.get("/", index),
            web.get("/static/{name}", static_file),
            web.post("/api/login", login),
            web.get("/api/session", session),
            web.post("/api/logout", logout),
            web.get("/api/assets", list_assets),
            web.post("/api/uploads", create_upload),
            web.get("/api/uploads/{id}", get_upload),
            web.get('/api/uploads/{id}/preview', preview_upload),
            web.get('/api/uploads/{id}/stream', stream_preview_upload),
            web.get('/api/uploads/{id}/stream-status', stream_preview_status),
            web.put("/api/uploads/{id}/chunk", upload_chunk),
            web.post("/api/uploads/{id}/complete", complete_upload),
            web.delete("/api/uploads/{id}", discard_upload),
            web.get("/api/drafts", list_drafts),
            web.get('/api/drafts/{id}', get_draft),
            web.post("/api/drafts", save_draft),
            web.get('/api/review-queue', review_queue),
            web.post('/api/catalog-merge', catalog_merge_cards),
            web.post('/api/review-queue/reject-all', reject_all_drafts),
            web.get('/api/content-requests', content_request_list),
            web.post('/api/content-requests/{id}', content_request_action),
            web.post('/api/publish-batch', publish_batch),
            web.get('/api/publication-package', publication_package),
            web.post('/api/publication-package/publish', publish_package),
            web.post('/api/publication-package/{id}', update_package),
            web.post("/api/drafts/{id}/publish", publish_draft),
            web.post('/api/drafts/{id}/review', review_draft),
            web.get('/api/drafts/{id}/promo', promo_draft),
            web.post('/api/card-candidates', card_candidates),
            web.post('/api/desktop/import', desktop_import),
            web.post('/api/desktop/provider-import', desktop_provider_import),
            web.post('/api/desktop/instant/claim', instant_api.claim),
            web.post('/api/desktop/instant/{id}/draft', instant_api.draft),
            web.post('/api/desktop/instant/{id}/result', instant_api.result),
            web.post('/api/desktop/batches/claim', batch_api.claim),
            web.post('/api/desktop/batches/{id}/plan', batch_api.plan),
            web.post('/api/desktop/batches/{id}/heartbeat', batch_api.heartbeat),
            web.post('/api/desktop/stream', desktop_stream),
            web.post('/api/desktop/stream/{id}/finish', desktop_stream_finish),
            web.post('/api/desktop/transfers/reset', desktop_transfer_reset),
            web.post('/api/desktop/import/{id}/files/{asset}/transfer', desktop_transfer),
            web.get('/api/desktop/session', desktop_session),
            web.post('/api/desktop/import/{id}/reopen', desktop_reopen),
            web.post('/api/desktop/control/heartbeat', desktop_control_heartbeat),
            web.post('/api/desktop/control/commands/{id}/complete', desktop_control_complete),
            web.get('/api/desktop/import/{id}', desktop_status),
            web.post('/api/desktop/import/{id}', desktop_status),
            web.post("/api/ratings/lookup", rating_lookup),
            web.get("/api/catalog", catalog),
            web.get('/api/catalog-sections', catalog_sections),
            web.post('/api/catalog-sections', catalog_sections),
            web.post('/api/catalog/{id}/section', catalog_movie_section),
            web.get('/api/catalog/{id}/folders', catalog_folders),
            web.post('/api/catalog/{id}/folders', catalog_folders),
            web.post('/api/catalog/{id}/episodes/merge', merge_catalog_episodes),
            web.post("/api/drafts/group-files", group_draft_files),
            web.post("/api/catalog/{id}/renumber", renumber_catalog_movie),
            web.get("/api/catalog/{id}/ratings", catalog_ratings),
            web.post("/api/catalog/{id}/ratings", catalog_ratings),
            web.get("/api/catalog/{id}/seasons", seasons),
            web.get('/api/launcher-control', launcher_control_state),
            web.post('/api/launcher-control/commands', launcher_control_command),
            web.get('/api/storage', storage_state),
            web.post('/api/storage', storage_action),
            web.get('/api/contests', contest_admin.list_contests),
            web.post('/api/contests', contest_admin.create_contest),
            web.get('/api/contests/{id}/page', contest_admin.contest_page),
            web.get('/api/contests/{id}/export', contest_admin.contest_export),
            web.get('/api/contests/{id}', contest_admin.get_contest),
            web.post('/api/contests/{id}', contest_admin.contest_action),
        ]
    )

    async def lifecycle(app):
        app["uploads"].start()
        yield
        await app['media_preview'].close()
        await app["uploads"].stop()

    app.cleanup_ctx.append(lifecycle)
    return app


async def start(bot):
    app = create_app(bot)
    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    try:
        await web.TCPSite(runner, settings.WEB_HOST, settings.WEB_PORT, shutdown_timeout=30).start()
    except BaseException:
        await runner.cleanup()
        raise
    logger.info("Web panel: %s", settings.WEB_PUBLIC_URL)
    return runner
