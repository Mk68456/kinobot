from aiohttp import web
from database import provider_batches as batches
from webpanel.instant import owner


async def claim(request):
    from webpanel.server import body
    from database.instant_settings import effective
    await body(request)
    return web.json_response({'batch': batches.claim(owner(request), request['admin_id']) if effective()[0] else None})


async def plan(request):
    from webpanel.server import body
    data = await body(request)
    batch = batches.get(request.match_info['id'], request['admin_id'])
    if not batch:
        raise ValueError('Массовая загрузка недоступна.')
    result = batches.plan(batch['id'], owner(request), data.get('lease'), data.get('items'), data.get('error'))
    return web.json_response({'status': result['status'], 'counts': result['counts']})


async def heartbeat(request):
    from webpanel.server import body
    from database.instant_settings import effective
    data = await body(request)
    batch = batches.get(request.match_info['id'], request['admin_id'])
    if not batch:
        raise ValueError('Массовая загрузка недоступна.')
    return web.json_response({'active': batches.heartbeat(batch['id'], owner(request), data.get('lease')) and effective()[0]})
