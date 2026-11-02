"""Poll the bot's durable queue. Uses a private config; never publishes catalogue cards."""

import argparse
import asyncio
import json
from pathlib import Path
import re
import sys
import time
import traceback
from urllib.parse import urlsplit

from tools.yougen_bridge import BridgeError, connection, session_path
from tools.yougen_queue import Queue
from tools.yougen_worker import Panel, process


def error_summary(error):
    """Log only code-owned static messages, never remote bodies or credentials."""
    import ast
    if isinstance(error, BridgeError):
        message = str(error)
        for name in ('yougen_agent.py', 'yougen_worker.py', 'yougen_bridge.py', 'yougen_discovery.py', 'yougen_options.py'):
            tree = ast.parse(Path(__file__).with_name(name).read_text(encoding='utf-8-sig'))
            for node in ast.walk(tree):
                if (isinstance(node, ast.Raise) and isinstance(node.exc, ast.Call)
                        and isinstance(node.exc.func, ast.Name) and node.exc.func.id == 'BridgeError'
                        and node.exc.args and isinstance(node.exc.args[0], ast.Constant)
                        and node.exc.args[0].value == message):
                    return 'BridgeError: ' + message
        http = re.search(r'HTTP ([1-5][0-9]{2})', message)
        if http:
            return 'BridgeError: API HTTP ' + http[1]
    frames = traceback.extract_tb(error.__traceback__)
    where = (' at ' + Path(frames[-1].filename).name + ':' + str(frames[-1].lineno)) if frames else ''
    return type(error).__name__ + where


def canonical(text):
    return ' '.join(text.split()).casefold()


class SearchNotFound(BridgeError):
    pass


async def resolve_result(client, source, payload):
    """Try bounded query spellings, but never relax identity or choose ambiguously."""
    from tools.provider_identity import canonical_title
    title = payload['title'].strip()
    binding = payload.get('provider_binding')
    original = (payload.get('identity') or {}).get('original_title')
    candidates = ([binding['query']] if binding else []) + [payload['query'], title, canonical_title(title)]
    if original:
        candidates.append(original)
    if payload.get('year'):
        candidates.append(title + ' ' + str(payload['year']))
    if ':' in title:
        candidates.append(title.split(':', 1)[0].strip())
    seen = set()
    for query in candidates:
        key = query.strip().casefold()
        if not key or key in seen:
            continue
        seen.add(key)
        results = await client.inline_query(source, query)
        try:
            return query, matching_result(results, payload)
        except SearchNotFound:
            continue
    raise BridgeError('Произведение не подтверждено в результатах источника после нескольких вариантов поиска. Проверьте название, тип и год карточки.')


def matching_result(results, payload):
    from tools.provider_matching import identity_score
    matches = []
    for row in results:
        title, description = row.title or '', row.description or ''
        # Provider distinguishes animation; KinoTime groups it by movie/series.
        header = description.splitlines()[0] if description else ''
        tag = header.split('|', 1)[0].strip().casefold().replace('ё', 'е')
        tag = re.sub(r'^[^\w]+', '', tag).strip()
        tag = tag.replace('-', ' ')
        kind = ('series' if tag in {'сериал', 'мультсериал', 'аниме сериал', 'телесериал'}
                else 'movie' if tag in {'фильм', 'мультфильм', 'аниме фильм', 'короткометражка'} else None)
        years = re.findall(r'\b(?:19|20)[0-9]{2}\b', description.splitlines()[0] if description else '')
        score = identity_score(row, payload)
        if (kind == payload['content_type'] and score is not None
                and (not payload.get('year') or str(payload['year']) in years)):
            matches.append((score, title))
    if not matches:
        raise SearchNotFound('Нет точного совпадения названия, типа и года в этой выдаче.')
    strongest = max(score for score, title in matches)
    matches = [title for score, title in matches if score == strongest]
    if len(matches) != 1:
        raise BridgeError('Найдено несколько совпадающих произведений. Автоматический выбор остановлен.')
    return matches[0]


class JobPanel:
    def __init__(self, panel, remote):
        self.panel, self.remote = panel, remote

    def request(self, path, body=None):
        if path == 'provider-import':
            return self.panel.request(f"instant/{self.remote['id']}/draft",
                                      {'lease': self.remote['lease'], 'manifest': body})
        if path == 'import':
            raise BridgeError('Для пользовательского запроса нужен режим привязки к каталогу.')
        return self.panel.request(path, body)


async def refresh_pairing(config, path):
    """Refresh the existing 48-hour pairing scope, without an interactive account login."""
    async with connection(session_path(config['session']), config.get('api_id'), config.get('api_hash'),
                          config.get('profile', 'desktop-copy'), config['user_id']) as (client, source):
        target = await client.get_entity(config['target'])
        if not target.bot or target.username.lower() != config['target'].lstrip('@').lower():
            raise BridgeError('Не подтверждён ваш бот для обновления подключения.')
        sent = await client.send_message(target, '/desktop')
        deadline = time.monotonic() + 35
        token = None
        while time.monotonic() < deadline and token is None:
            for message in await client.get_messages(target, limit=8, min_id=sent.id):
                if message.out or message.sender_id != target.id:
                    continue
                text = message.raw_text or ''
                match = re.search(r'Код:\s*([A-Za-z0-9_-]{30,100})', text)
                links = re.findall(r'https://[^\s<>]+', text)
                if match and any(urlsplit(link).netloc == urlsplit(config['panel_url']).netloc for link in links):
                    token = match[1]
                    break
            if token is None:
                await asyncio.sleep(2)
        if token is None:
            raise BridgeError('Бот не выдал новый код подключения.')
    updated = {**config, 'panel_token': token, 'paired_at': time.time()}
    info = await asyncio.to_thread(Panel(updated['panel_url'], token, updated['instance_id']).request, 'session')
    if (info.get('admin_id') != config['user_id']
            or info.get('bot_username', '').lower() != config['target'].lstrip('@').lower()):
        raise BridgeError('Новый код не подтвердил нужного администратора и бота.')
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(updated, ensure_ascii=False, indent=2), encoding='utf-8')
    temporary.replace(path)
    config.update(updated)


async def run_once(config, queue):
    panel = Panel(config['panel_url'], config['panel_token'], config['instance_id'])
    info = await asyncio.to_thread(panel.request, 'session')
    if (info.get('capabilities', {}).get('instant_requests') != 1
            or info.get('admin_id') != config['user_id']
            or info.get('bot_username', '').lower() != config['target'].lstrip('@').lower()):
        raise BridgeError('Обновите сервер и проверьте подключение этого обработчика.')
    if info.get('capabilities', {}).get('provider_options') == 1:
        lookup = (await asyncio.to_thread(panel.request, 'options/claim', {})).get('lookup')
        if lookup:
            from tools.yougen_options import inspect_options
            try:
                async with connection(session_path(config['session']), config.get('api_id'), config.get('api_hash'),
                                      config.get('profile', 'desktop-copy'), config['user_id']) as (client, source):
                    binding = {}
                    choices = await asyncio.wait_for(inspect_options(client, source, lookup['payload'], binding_out=binding), 120)
                await asyncio.to_thread(panel.request, f"options/{lookup['id']}/result",
                                        {'lease': lookup['lease'], 'choices': choices, 'binding': binding})
                return {'status': 'options_ready', 'lookup_id': lookup['id']}
            except Exception:
                await asyncio.to_thread(panel.request, f"options/{lookup['id']}/result",
                                        {'lease': lookup['lease'], 'choices': None})
                raise
    claimed = await asyncio.to_thread(panel.request, 'instant/claim', {})
    remote = claimed.get('job')
    if not remote:
        if info.get('capabilities', {}).get('provider_batches') == 1 and claimed.get('enabled'):
            batch = (await asyncio.to_thread(panel.request, 'batches/claim', {})).get('batch')
            if batch:
                from tools.yougen_discovery import discover
                try:
                    async with connection(session_path(config['session']), config.get('api_id'), config.get('api_hash'),
                                          config.get('profile', 'desktop-copy'), config['user_id']) as (client, source):
                        query, chosen = await resolve_result(client, source, batch['payload'])
                        task = asyncio.create_task(discover(client, source, batch['payload'], chosen, source_query=query))
                        try:
                            while not task.done():
                                await asyncio.wait({task}, timeout=30)
                                active = await asyncio.to_thread(panel.request, f"batches/{batch['id']}/heartbeat",
                                                                 {'lease': batch['lease']})
                                if not active['active']:
                                    raise BridgeError('Обход приостановлен или отменён администратором.')
                            items = await task
                        finally:
                            if not task.done():
                                task.cancel()
                            await asyncio.gather(task, return_exceptions=True)
                    result = await asyncio.to_thread(panel.request, f"batches/{batch['id']}/plan",
                                                     {'lease': batch['lease'], 'items': items})
                    return {'batch_id': batch['id'], **result}
                except Exception as error:
                    await asyncio.to_thread(panel.request, f"batches/{batch['id']}/plan",
                                             {'lease': batch['lease'], 'error': str(error)[:200] if isinstance(error, BridgeError)
                                              else type(error).__name__})
                    raise
        return {'status': 'idle', 'enabled': claimed.get('enabled', False)}
    try:
        async with connection(session_path(config['session']), config.get('api_id'), config.get('api_hash'),
                              config.get('profile', 'desktop-copy'), config['user_id']) as (client, source):
            query, chosen = await resolve_result(client, source, remote['payload'])
        p = remote['payload']
        from tools.provider_identity import valid_voice_name
        from tools.yougen_bridge import VOICES
        if valid_voice_name(p.get('voice')):
            VOICES.add(p['voice'])
        local = {key: p[key] for key in ('query', 'title', 'content_type', 'voice', 'quality', 'season', 'episode')}
        local['result_title'] = chosen
        local['query'] = query
        for key in ('identity', 'provider_binding', 'year'):
            if key in p:
                local[key] = p[key]
        job_id = queue.enqueue(local, identity=remote['id'])
        saved = queue.get(job_id)
        if saved['status'] == 'queued':
            job = queue.claim()
            if not job or job['id'] != job_id:
                raise BridgeError('Локальный обработчик занят; нужна проверка очереди.')
            try:
                await asyncio.wait_for(process(queue, job, {**config, 'link_existing_catalog': True},
                                               JobPanel(panel, remote)), timeout=300)
            except Exception as error:
                queue.update(job_id, 'stopped', status='needs_review', error=type(error).__name__)
                raise
        elif saved['status'] != 'complete':
            raise BridgeError('Предыдущая попытка требует проверки; повтор запрещён.')
        response = await asyncio.to_thread(panel.request, f"instant/{remote['id']}/result",
                                            {'lease': remote['lease'], 'ok': True})
        return {'job_id': remote['id'], 'status': response['status']}
    except Exception:
        # Completion is idempotent: if the server already sent the file, this cannot resend it.
        await asyncio.to_thread(panel.request, f"instant/{remote['id']}/result",
                               {'lease': remote['lease'], 'ok': False})
        raise


async def run(config, queue, serve, config_path):
    last_auth_repair = float('-inf')
    while True:
        try:
            result = await run_once(config, queue)
            if result['status'] != 'idle' or not serve:
                print(json.dumps(result, ensure_ascii=False), flush=True)
            # A failed proactive renewal must not block a still-valid API token.
            if (time.time() - config.get('paired_at', 0) > 24 * 3600
                    and time.monotonic() - last_auth_repair >= 300):
                last_auth_repair = time.monotonic()
                try:
                    await refresh_pairing(config, config_path)
                except Exception:
                    print('Не удалось заранее обновить /desktop; действующее подключение продолжает работу.',
                          file=sys.stderr, flush=True)
        except Exception as error:
            if (isinstance(error, BridgeError) and 'HTTP 401' in str(error)
                    and time.monotonic() - last_auth_repair >= 300):
                last_auth_repair = time.monotonic()
                try:
                    await refresh_pairing(config, config_path)
                    continue
                except Exception:
                    print('Не удалось восстановить подключение /desktop.', file=sys.stderr, flush=True)
            print('Обработчик остановил попытку: ' + error_summary(error), file=sys.stderr, flush=True)
            if not serve:
                raise
            await asyncio.sleep(25)
        if not serve:
            return
        await asyncio.sleep(5)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, default=Path('runtime/yougen-client/bridge.json'))
    parser.add_argument('--queue', type=Path, default=Path('runtime/yougen-client/remote.sqlite'))
    parser.add_argument('--serve', action='store_true')
    args = parser.parse_args()
    config = json.loads(args.config.read_text(encoding='utf-8'))
    asyncio.run(run(config, Queue(args.queue), args.serve, args.config))


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print('Ошибка запуска: ' + type(error).__name__, file=sys.stderr)
        raise SystemExit(1)
