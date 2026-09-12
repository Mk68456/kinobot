import json
import logging
import secrets
from pathlib import Path
from urllib.parse import urlsplit
from aiohttp import web
from data import settings
from database.connection import database
from webpanel import auth, repository
from webpanel.uploads import Uploads

logger = logging.getLogger(__name__)
STATIC = Path(__file__).parent / "static"


@web.middleware
async def errors(request, handler):
    try:
        response = await handler(request)
    except (ValueError, KeyError, TypeError, json.JSONDecodeError) as error:
        message = str(error) if isinstance(error, ValueError) else "Некорректный запрос."
        response = web.json_response({"error": message}, status=400)
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
            "Content-Security-Policy": "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'",
        }
    )
    return response


@web.middleware
async def access(request, handler):
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
    if request.content_length and request.content_length > 100_000:
        raise ValueError("Слишком большой запрос.")
    raw = bytearray()
    async for part in request.content.iter_chunked(8192):
        raw.extend(part)
        if len(raw) > 100_000:
            raise ValueError("Слишком большой запрос.")
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError("Ожидается объект запроса.")
    return value


async def index(request):
    return web.FileResponse(STATIC / "index.html")


async def static_file(request):
    name = request.match_info["name"]
    if name not in ("app.js", "style.css", "favicon.svg"):
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
    return web.json_response({"assets": repository.assets(request["admin_id"])})


async def create_upload(request):
    return web.json_response(
        request.app["uploads"].create(request["admin_id"], await body(request)), status=201
    )


async def get_upload(request):
    file = repository.asset(request.match_info["id"], request["admin_id"])
    if not file:
        raise web.HTTPNotFound(reason="Файл не найден.")
    return web.json_response(file)


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


async def save_draft(request):
    data = await body(request)
    return web.json_response(repository.save_draft(request["admin_id"], data.get("body"), data.get("id")))


async def publish_draft(request):
    number = repository.publish(request.match_info["id"], request["admin_id"])
    return web.json_response({"movie_number": number})


async def catalog(request):
    query = request.query.get("q", "").strip()[:120]
    page = max(0, min(int(request.query.get("page", "0")), 100000))
    rows = database.execute(
        "SELECT movie_number,movie_title,content_type FROM Movies WHERE movie_title LIKE ? ORDER BY movie_number DESC LIMIT 51 OFFSET ?",
        (f"%{query}%", page * 50),
    ).fetchall()
    return web.json_response(
        {
            "movies": [{"id": r[0], "title": str(r[1]), "content_type": r[2] or "movie"} for r in rows[:50]],
            "has_more": len(rows) > 50,
        }
    )


async def seasons(request):
    number = int(request.match_info["id"])
    rows = database.execute(
        "SELECT id,name,season_number FROM MovieCategories WHERE movie_number=? ORDER BY season_number,id",
        (number,),
    ).fetchall()
    return web.json_response({"seasons": [{"id": r[0], "name": r[1], "number": r[2]} for r in rows]})


def create_app(bot, config=settings):
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
    app = web.Application(middlewares=[errors, access], client_max_size=config.WEB_CHUNK_BYTES + 1024)
    app["origin"] = f"{url.scheme}://{url.netloc}"
    app["secure"] = url.scheme == "https"
    max_bytes = (
        config.WEB_MAX_FILE_BYTES
        if config.BOT_API_BASE != "https://api.telegram.org"
        else min(config.WEB_MAX_FILE_BYTES, 50_000_000)
    )
    app["uploads"] = Uploads(
        bot, config.UPLOAD_DIR, max_bytes, config.UPLOAD_QUOTA_BYTES, config.WEB_CHUNK_BYTES
    )
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
            web.put("/api/uploads/{id}/chunk", upload_chunk),
            web.post("/api/uploads/{id}/complete", complete_upload),
            web.delete("/api/uploads/{id}", discard_upload),
            web.get("/api/drafts", list_drafts),
            web.post("/api/drafts", save_draft),
            web.post("/api/drafts/{id}/publish", publish_draft),
            web.get("/api/catalog", catalog),
            web.get("/api/catalog/{id}/seasons", seasons),
        ]
    )

    async def lifecycle(app):
        app["uploads"].start()
        yield
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
