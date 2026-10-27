"""Isolated local UI fixture. Uses an in-memory database and never contacts Telegram."""
import asyncio
import contextlib
import io
import os
import socket
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from aiohttp import web
from database.connection import database
from database.migrations import migrate
from database.movies import add_new_movie
from database.people import add_user
from data import config
from services import movie_sharing
from webpanel import auth
from webpanel.server import create_app


async def main():
    config.ADMIN = [99]
    movie_sharing.bot_username = 'ExampleBot'
    database.open(':memory:')
    with contextlib.redirect_stdout(io.StringIO()):
        migrate()
    add_user(100, 'ExampleUser')
    add_new_movie('Тестовый фильм')
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    origin = f'http://127.0.0.1:{port}'
    with tempfile.TemporaryDirectory() as folder:
        settings = SimpleNamespace(WEB_PUBLIC_URL=origin, WEB_CHUNK_BYTES=1024,
                                   WEB_MAX_FILE_BYTES=1000, BOT_API_BASE='https://api.telegram.org',
                                   UPLOAD_DIR=Path(folder), UPLOAD_QUOTA_BYTES=10000)
        app = create_app(None, settings)
        async def add_friend(request):
            from database import contests
            item = contests.current()
            member = contests.join(item['id'], 100)
            fresh = add_user(200, 'InvitedUser')
            contests.accept_start(200, f"ref_{item['id']}_{member['code']}", fresh)
            contests.confirm_card(200, 1)
            return web.json_response({'ok': True})
        app.router.add_post('/__fixture_friend', add_friend)
        runner = web.AppRunner(app)
        await runner.setup()
        await web.TCPSite(runner, '127.0.0.1', port).start()
        print(f'PID={os.getpid()} URL={origin}/#login={auth.create_login(99)}', flush=True)
        try:
            await asyncio.sleep(240)
        finally:
            await runner.cleanup()
            database.close()


if __name__ == '__main__':
    asyncio.run(main())
