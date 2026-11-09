from aiohttp import web
from database import provider_options
from database.instant_settings import effective
from webpanel.instant import owner


async def claim(request):
    from webpanel.server import body
    await body(request)
    return web.json_response({'lookup': provider_options.claim(owner(request)) if effective()[0] else None})


async def result(request):
    from webpanel.server import body
    data=await body(request)
    provider_options.finish(request.match_info['id'],owner(request),data.get('lease'),data.get('choices'),data.get('binding'),data.get('candidates'), defer=data.get('defer') is True)
    return web.json_response({'ok':True})


async def queue_state(request):
    from webpanel.server import body
    from database import provider_queue
    from database.connection import database
    import time
    data = await body(request)
    identity = owner(request)
    previous = provider_queue.control()
    result = provider_queue.report(data.get('until', 0), data.get('manual', False), data.get('generation', 0))
    database.execute('INSERT OR REPLACE INTO InstantAgent VALUES (1,?,?)', (identity,time.time()))
    database.commit()
    if result['manual'] and not previous['manual']:
        try:
            await request.app['uploads'].bot.send_message(request['admin_id'],
                'Очередь KinoTime остановлена: источник или Telegram требует проверки. '
                'Проверьте аккаунт и пройдите проверку у поставщика вручную. '
                'После устранения ограничения: /instant_queue resume. Ожидающие запросы сохранены.')
        except Exception:
            pass
    return web.json_response(result)
