"""Local UI preview with disposable data and no Telegram connection."""
import asyncio
import contextlib
import io
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
        preview = SimpleNamespace(**{name:getattr(settings,name) for name in dir(settings) if name.isupper()})
        preview.WEB_PUBLIC_URL = 'http://127.0.0.1:8088'
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
        app=create_app(PreviewBot(),preview)
        runner=web.AppRunner(app,access_log=None)
        await runner.setup()
        await web.TCPSite(runner,'127.0.0.1',8088).start()
        print(f'PREVIEW http://127.0.0.1:8088/#login={create_login(admin_id)}',flush=True)
        try:
            await asyncio.Event().wait()
        finally:
            await runner.cleanup()
            database.close()


if __name__=='__main__':
    asyncio.run(main())
