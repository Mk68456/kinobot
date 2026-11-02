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
    provider_options.finish(request.match_info['id'],owner(request),data.get('lease'),data.get('choices'),data.get('binding'))
    return web.json_response({'ok':True})
