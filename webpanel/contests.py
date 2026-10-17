"""Authenticated contest administration. This module never publishes Telegram posts."""

from aiohttp import web

from database import contests
from services.contests import announcement, link, result_text, username


async def list_contests(request):
    return web.json_response({'items': contests.history()})


async def create_contest(request):
    from webpanel.server import body
    data = await body(request)
    item = contests.save(request['admin_id'], data.get('body'))
    return web.json_response(item, status=201)


async def get_contest(request):
    item = contests.overview(request.match_info['id'])
    name = await username(request.app['contest_bot'])
    item['campaign_link'] = link(name, f"campaign_{item['id']}")
    item['announcement'] = announcement(item, name)
    item['result_text'] = result_text(item, name)
    item['claim'] = None
    from database.connection import database
    row = database.execute('SELECT user_id,claimed_at FROM ContestClaims WHERE contest_id=?', (item['id'],)).fetchone()
    if row:
        item['claim'] = {'user_id': row[0], 'claimed_at': row[1]}
    return web.json_response(item)


async def contest_action(request):
    from webpanel.server import body
    data = await body(request)
    admin_id, contest_id = request['admin_id'], request.match_info['id']
    action = data.get('action')
    if action == 'save':
        result = contests.save(admin_id, data.get('body'), contest_id, data.get('revision'))
    elif action == 'launch':
        result = contests.launch(admin_id, contest_id, data.get('revision'))
    elif action == 'close':
        result = contests.close(admin_id, contest_id, data.get('token'))
    elif action == 'draw':
        result = contests.draw(admin_id, contest_id)
    elif action == 'cancel':
        result = contests.cancel(admin_id, contest_id, data.get('revision'))
    elif action == 'review_referral':
        result = contests.review_referral(admin_id, contest_id, data.get('user_id'), data.get('exclude'), data.get('reason'))
    elif action == 'delivered':
        result = contests.delivered(admin_id, contest_id, data.get('note'))
    else:
        raise ValueError('Неизвестное действие конкурса.')
    return web.json_response(result)


async def contest_page(request):
    return web.json_response(contests.page(request.match_info['id'], request.query.get('kind', 'members'), request.query.get('offset', 0)))


async def contest_export(request):
    contest_id = contests.get(request.match_info['id'])['id']
    return web.Response(body=('\ufeff' + contests.export(contest_id)).encode('utf-8'), content_type='text/csv', charset='utf-8',
                        headers={'Content-Disposition': f'attachment; filename="contest-{contest_id}.csv"'})
