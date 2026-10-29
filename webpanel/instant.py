"""Authenticated agent protocol; user IDs and file IDs stay on the bot server."""

import re
from aiohttp import web

from database import instant_jobs as jobs
from database.connection import database
from webpanel import repository, provider_import
from tools.yougen_worker import manifest as expected_manifest
from tools.yougen_bridge import BOT


def owner(request):
    if not request['instance_id']:
        raise ValueError('Нужен отдельный идентификатор обработчика.')
    return f"{request['admin_id']}:{request['instance_id']}"


async def claim(request):
    from database.instant_settings import effective
    from webpanel.server import body
    await body(request)
    if not effective()[0]:
        return web.json_response({'enabled': False, 'job': None})
    return web.json_response({'enabled': True, 'job': jobs.claim(owner(request))})


async def draft(request):
    from webpanel.server import body
    data = await body(request)
    with database.transaction():
        job = jobs.leased(request.match_info['id'], owner(request), data.get('lease'))
        if job['status'] != 'working':
            raise ValueError('Задание уже завершено или остановлено.')
        payload, spec = job['payload'], data.get('manifest')
        if not isinstance(spec, dict) or not isinstance(spec.get('files'), list) or len(spec['files']) != 1:
            raise ValueError('Нужен один файл выбранного произведения.')
        root = re.fullmatch(r'yougen/' + re.escape(BOT) + r'/([0-9]{1,20})', str(spec.get('root', '')))
        file = spec['files'][0]
        if not root or not isinstance(file, dict) or type(file.get('size')) is not int or not 0 < file['size'] <= 2_000_000_000:
            raise ValueError('Некорректный файл или превышен размер. Выберите меньшее качество.')
        expected = expected_manifest(payload, {'message_id': int(root[1]), 'size': file['size']})
        if spec != expected:
            raise ValueError('Метаданные файла не соответствуют запросу пользователя.')
        if job['draft_id']:
            current = repository.draft(job['draft_id'], request['admin_id'])
            if not current or current['status'] != 'draft':
                raise ValueError('Черновик недоступен или уже обработан.')
            linked = bool(current['body']['movie_number'])
        else:
            current, linked = provider_import.create(request['admin_id'], spec, request['instance_id'])
            jobs.update(job['id'], draft_id=current['id'], asset_id=current['body']['items'][0]['asset_id'])
    bot = request.app['uploads'].bot
    me = await bot.get_me()
    return web.json_response({'draft': current, 'linked_existing': linked,
                              'bot_username': me.username, 'bot_name': me.full_name})


async def result(request):
    from webpanel.server import body
    from services.instant import notify
    data = await body(request)
    with database.transaction():
        job = jobs.leased(request.match_info['id'], owner(request), data.get('lease'))
        if job['status'] != 'working':
            return web.json_response({'status': job['status']})
        if data.get('ok') is not True:
            jobs.update(job['id'], status='review', error='source_unavailable')
        else:
            asset = repository.asset(job['asset_id'], request['admin_id']) if job['asset_id'] else None
            if not asset or asset['status'] != 'ready' or not asset['file_id']:
                raise ValueError('Сервер ещё не принял файл этого задания.')
            jobs.update(job['id'], status='ready', file_id=asset['file_id'], error=None)
            if job['request_id']:
                database.execute("UPDATE ContentRequests SET draft_id=?,status=CASE WHEN status='new' THEN 'planned' ELSE status END WHERE id=?",
                                 (job['draft_id'], job['request_id']))
    await notify(request.app['uploads'].bot, job['id'])
    return web.json_response({'status': jobs.get(job['id'])['status']})
