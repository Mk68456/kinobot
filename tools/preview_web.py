"""Local UI preview with disposable data and no Telegram connection."""
import asyncio
import contextlib
import io
import os
import sys
import tempfile
import uuid
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
from aiohttp import web
from data import config, settings
from database.connection import database
from database.migrations import migrate
from webpanel.auth import create_login
from webpanel.repository import save_draft
from webpanel.server import create_app


class PreviewBot:
    async def get_me(self):
        return SimpleNamespace(username='KinoTimePreviewBot', full_name='KinoTime Preview')

    @contextlib.contextmanager
    def request_timeout(self, value):
        yield

    async def send_video(self, *args, **kwargs):
        raise ValueError('Тестовый предпросмотр не подключён к Telegram.')

    send_document = send_video


async def main():
    with tempfile.TemporaryDirectory(prefix='kinobot-web-preview-') as folder:
        database.open(Path(folder)/'preview.sqlite')
        with contextlib.redirect_stdout(io.StringIO()):
            migrate()
        admin_id = config.ADMIN[0]
        from database import content_requests
        content_requests.vote(1001, 'Редкий фильм (2004)', 'movie', tmdb_id=777,
                              year='2004', overview='Тестовое описание редкого фильма для очереди администратора.')
        content_requests.vote(1002, 'Редкий фильм (2004)', 'movie', tmdb_id=777)
        content_requests.vote(1003, 'Неизвестный сериал (1999)', 'series', tmdb_id=778,
                              year='1999', overview='Пользователь запросил сериал, которого пока нет в каталоге.')
        preview = SimpleNamespace(**{name:getattr(settings,name) for name in dir(settings) if name.isupper()})
        port = int(os.environ.get('PREVIEW_PORT', '8088'))
        preview.WEB_PUBLIC_URL = f'http://127.0.0.1:{port}'
        preview.BOT_API_BASE = 'http://preview.invalid'
        preview.UPLOAD_DIR = Path(folder)/'uploads'
        items=[]
        for index,name in enumerate(['Серия 1 — Пилот','Серия 2 — Пёс-газонокосильщик','Серия 3 — Анатомический парк'],1):
            asset_id=uuid.uuid4().hex
            database.execute("INSERT INTO WebAssets(id,admin_id,source,filename,size,file_type,file_id,status) VALUES (?,?,'telegram',?,?,'video','preview','ready')",
                             (asset_id,admin_id,f'Rick.and.Morty.S01E0{index}.mp4',180_000_000+index*10_000_000))
            items.append({'asset_id':asset_id,'name':name})
        database.commit()
        save_draft(admin_id,{'title':'Рик и Морти · тестовый предпросмотр','content_type':'series','season_number':1,'items':items})
        from webpanel.imports import create
        imported = create(admin_id, {'version': 1, 'title': 'Фильм · тест импорта', 'root': 'D:/Preview/Movie',
            'files': [{'path': '1080p/Дубляж/movie.mkv', 'size': 500, 'mtime': '1'},
                      {'path': '720p/Оригинал/movie.mkv', 'size': 500, 'mtime': '1'}]})
        for item in ([] if os.environ.get('PREVIEW_WAITING') == '1' else imported['body']['items']):
            database.execute("UPDATE WebAssets SET status='ready',file_id='preview',received=size WHERE id=?", (item['asset_id'],))
        database.commit()
        app=create_app(PreviewBot(),preview)
        runner=web.AppRunner(app,access_log=None)
        await runner.setup()
        await web.TCPSite(runner,'127.0.0.1',port).start()
        print(f'PREVIEW http://127.0.0.1:{port}/#login={create_login(admin_id)}',flush=True)
        try:
            await asyncio.Event().wait()
        finally:
            await runner.cleanup()
            database.close()


if __name__=='__main__':
    asyncio.run(main())
