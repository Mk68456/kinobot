"""Remote commands are addressed to one registered computer, never an account."""
import json
import re
import time
from datetime import datetime, timezone
from pathlib import PurePosixPath, PureWindowsPath

from database.connection import database

ACTIONS = {'prepare', 'send', 'stop', 'refresh', 'switch_folder', 'browse_folder'}
QUALITIES = {'source', '1080p', '720p', '480p', '360p'}
RENDER_OPTIONS = {
    'mode': {'single', 'batch'},
    'hardware': {'auto', 'cpu', 'intel', 'nvidia', 'amd'},
    'load': {'eco', 'balanced', 'performance'},
    'catalog_section': {'movies', 'cartoons', 'anime'},
}
MAX_BROWSE_ENTRIES = 100
MAX_BROWSE_BYTES = 128 * 1024
MAX_FOLDER_LENGTH = 4000


def absolute_folder(value, roots=False):
    if not isinstance(value, str):
        raise ValueError('Укажите абсолютный путь к папке на выбранном компьютере.')
    value = value.strip()
    if roots and not value:
        return ''
    if not value or len(value) > MAX_FOLDER_LENGTH or any(ord(char) < 32 for char in value):
        raise ValueError('Укажите абсолютный путь к папке на выбранном компьютере.')
    if not (PureWindowsPath(value).is_absolute() or PurePosixPath(value).is_absolute()):
        raise ValueError('Укажите абсолютный путь к папке на выбранном компьютере.')
    return value


def supports(current, capability):
    capabilities = current['status'].get('capabilities')
    return current['protocol'] == 3 and isinstance(capabilities, dict) and capabilities.get(capability) == 1


def remote_folders_enabled(current):
    return supports(current, 'folder_browser') and current['status'].get('remote_browse_enabled') is True


def permitted_folder(current, value):
    folder = absolute_folder(value)
    if folder not in current['folders'] and not remote_folders_enabled(current):
        raise ValueError('Эта папка не разрешена в настройках выбранного компьютера. Включите удалённый выбор папок в лаунчере.')
    return folder


def browse_data(data):
    if not isinstance(data, dict) or set(data) != {'folder', 'parent', 'entries', 'offset', 'next_offset'}:
        raise ValueError('Некорректный список папок.')
    folder = absolute_folder(data['folder'], roots=True)
    parent = None if data['parent'] is None else absolute_folder(data['parent'], roots=True)
    entries = data['entries']
    offset, next_offset = data['offset'], data['next_offset']
    if (not isinstance(entries, list) or len(entries) > MAX_BROWSE_ENTRIES
            or type(offset) is not int or not 0 <= offset <= 1_000_000
            or (next_offset is not None and (type(next_offset) is not int or not offset < next_offset <= 1_000_000))):
        raise ValueError('Некорректный список папок.')
    clean = []
    for entry in entries:
        if (not isinstance(entry, dict) or set(entry) != {'name', 'path'}
                or not isinstance(entry['name'], str) or not entry['name'].strip()
                or len(entry['name']) > MAX_FOLDER_LENGTH or any(ord(char) < 32 for char in entry['name'])):
            raise ValueError('Некорректный список папок.')
        clean.append({'name': entry['name'], 'path': absolute_folder(entry['path'])})
    result = {'folder': folder, 'parent': parent, 'entries': clean, 'offset': offset, 'next_offset': next_offset}
    if len(json.dumps(result, ensure_ascii=False).encode('utf-8')) > MAX_BROWSE_BYTES:
        raise ValueError('Список папок слишком большой. Используйте страницы по 100 папок.')
    return result


def valid_instance(instance):
    return isinstance(instance, str) and bool(re.fullmatch(r'[A-Za-z0-9_-]{8,100}', instance))


def heartbeat(admin_id, data):
    instance = data.get('instance_id')
    status, folders = data.get('status') or {}, data.get('folders') or []
    protocol = data.get('protocol', 2)
    if not valid_instance(instance) or not isinstance(status, dict) or not isinstance(folders, list) or protocol not in (2, 3):
        raise ValueError('Некорректное состояние лаунчера.')
    folders = [path[:MAX_FOLDER_LENGTH] for path in folders if isinstance(path, str) and path.strip()][:30]
    name = str(data.get('name') or 'Компьютер (старая версия)').strip()[:80]
    with database.transaction():
        old = database.execute('SELECT protocol FROM LauncherAgents WHERE admin_id=? AND instance_id=?', (admin_id, instance)).fetchone()
        if old and old[0] == 3 and protocol != 3:
            raise ValueError('Обновите старое окно лаунчера: этот компьютер использует новый протокол.')
        database.execute('''INSERT INTO LauncherAgents(admin_id,instance_id,name,protocol,status,folders,last_seen)
            VALUES (?,?,?,?,?,?,CURRENT_TIMESTAMP) ON CONFLICT(admin_id,instance_id) DO UPDATE SET
            name=excluded.name,protocol=excluded.protocol,status=excluded.status,
            folders=excluded.folders,last_seen=CURRENT_TIMESTAMP''',
            (admin_id, instance, name, protocol, json.dumps(status, ensure_ascii=False), json.dumps(folders, ensure_ascii=False)))
        database.execute("UPDATE LauncherTransfers SET expires_at=?,updated_at=? WHERE admin_id=? AND instance_id=? AND status='claimed'",
                         (time.time() + 90, time.time(), admin_id, instance))
        database.execute("UPDATE LauncherCommands SET status='failed',result='Команда истекла: компьютер не ответил вовремя.' WHERE admin_id=? AND instance_id=? AND status='pending' AND created_at < datetime('now','-2 minutes')", (admin_id, instance))
        row = database.execute("SELECT id,action,payload FROM LauncherCommands WHERE admin_id=? AND instance_id=? AND status='pending' ORDER BY id LIMIT 1", (admin_id, instance)).fetchone()
        command = None
        if row:
            database.execute("UPDATE LauncherCommands SET status='claimed',updated_at=CURRENT_TIMESTAMP WHERE id=?", (row[0],))
            command = {'id': row[0], 'action': row[1], 'payload': json.loads(row[2]), 'instance_id': instance}
    return {'command': command}


def complete(admin_id, command_id, ok, result='', instance_id='', data=None):
    if type(command_id) is not int or type(ok) is not bool:
        raise ValueError('Некорректный результат команды.')
    with database.transaction():
        row = database.execute('''SELECT c.instance_id,a.protocol,c.action FROM LauncherCommands c
            LEFT JOIN LauncherAgents a ON a.admin_id=c.admin_id AND a.instance_id=c.instance_id
            WHERE c.id=? AND c.admin_id=?''', (command_id, admin_id)).fetchone()
        if not row or (instance_id != row[0] and (instance_id or row[1] != 2)):
            raise ValueError('Команда предназначена другому компьютеру.')
        message = str(result)[:2000]
        if data is not None:
            if row[2] != 'browse_folder' or not ok:
                raise ValueError('Данные списка папок допустимы только для завершённого просмотра папок.')
            data = browse_data(data)
        if row[2] == 'browse_folder':
            message = json.dumps({'message': message, 'data': data}, ensure_ascii=False)
        changed = database.execute("UPDATE LauncherCommands SET status=?,result=?,updated_at=CURRENT_TIMESTAMP WHERE id=? AND admin_id=? AND status='claimed'",
            ('done' if ok else 'failed', message, command_id, admin_id)).rowcount
        if not changed:
            raise ValueError('Команда уже завершена или недоступна.')
    return {'ok': True}


def state(admin_id):
    agents = []
    for row in database.execute('SELECT instance_id,name,protocol,status,folders,last_seen FROM LauncherAgents WHERE admin_id=? ORDER BY name,instance_id', (admin_id,)):
        agent = dict(zip(('instance_id', 'name', 'protocol', 'status', 'folders', 'last_seen'), row))
        agent.update(status=json.loads(agent['status']), folders=json.loads(agent['folders']))
        agent['online'] = time.time() - datetime.fromisoformat(agent['last_seen']).replace(tzinfo=timezone.utc).timestamp() < 30
        agents.append(agent)
    commands = database.execute('SELECT id,action,status,result,created_at,updated_at,instance_id FROM LauncherCommands WHERE admin_id=? ORDER BY id DESC LIMIT 50', (admin_id,)).fetchall()
    results = []
    latest_browse = set()
    for row in commands:
        command = dict(zip(('id', 'action', 'status', 'result', 'created_at', 'updated_at', 'instance_id'), row))
        if command['action'] == 'browse_folder' and command['result']:
            try:
                envelope = json.loads(command['result'])
            except (ValueError, TypeError):
                envelope = None
            if isinstance(envelope, dict) and isinstance(envelope.get('message'), str):
                command['result'] = envelope['message']
                if (command['status'] == 'done' and command['instance_id'] not in latest_browse
                        and isinstance(envelope.get('data'), dict)):
                    command['data'] = envelope['data']
            if command['status'] == 'done':
                latest_browse.add(command['instance_id'])
        results.append(command)
    return {'agents': agents, 'agent': agents[0] if len(agents) == 1 else None, 'commands': results}


def enqueue(admin_id, action, payload, instance_id=''):
    if action not in ACTIONS or not isinstance(payload, dict):
        raise ValueError('Неизвестная команда лаунчера.')
    agents = state(admin_id)['agents']
    if not instance_id and len(agents) == 1:
        instance_id = agents[0]['instance_id']
    current = next((a for a in agents if a['instance_id'] == instance_id), None)
    if not current:
        raise ValueError('Выберите компьютер, которому предназначена команда.')
    if not current['online']:
        raise ValueError('Этот компьютер не в сети. Запустите лаунчер и проверьте подключение.')
    payload = dict(payload)
    if action in ('prepare', 'send', 'switch_folder') and current['status'].get('busy'):
        raise ValueError('Лаунчер занят. Дождитесь завершения текущей операции или остановите её.')
    if action == 'browse_folder':
        if not remote_folders_enabled(current):
            raise ValueError('Включите удалённый выбор папок в обновлённом лаунчере на выбранном компьютере.')
        folder = absolute_folder(payload.get('folder', ''), roots=True)
        offset = payload.get('offset', 0)
        if type(offset) is not int or not 0 <= offset <= 1_000_000:
            raise ValueError('Некорректная страница списка папок.')
        payload = {'folder': folder, 'offset': offset}
    elif action == 'switch_folder':
        payload = {'folder': permitted_folder(current, payload.get('folder'))}
    if action in ('prepare', 'send'):
        if any(key in payload for key in ('folder', *RENDER_OPTIONS)) and not supports(current, 'remote_render'):
            raise ValueError('Обновите лаунчер: эта версия не поддерживает запуск с выбранной папкой и параметрами.')
        if 'folder' in payload:
            payload['folder'] = permitted_folder(current, payload['folder'])
        for key, allowed in RENDER_OPTIONS.items():
            if key not in payload:
                continue
            value = payload[key]
            if not isinstance(value, str) or value.strip().lower() not in allowed:
                raise ValueError('Некорректный параметр запуска: ' + key + '.')
            payload[key] = value.strip().lower()
        qualities = payload.get('qualities')
        if qualities is None and payload.get('quality_policy') in ('adaptive', 'source_only'):
            qualities = ['source'] if payload['quality_policy'] == 'source_only' else ['1080p', '720p']
        if not isinstance(qualities, list) or not qualities or any(not isinstance(v, str) or v not in QUALITIES for v in qualities):
            raise ValueError('Выберите хотя бы одно допустимое качество.')
        payload = {**payload, 'qualities': list(dict.fromkeys(qualities))}
    with database.transaction():
        # Never replay an unacknowledged operation: it may already have started.
        database.execute("UPDATE LauncherCommands SET status='failed',result='Компьютер не подтвердил команду.' WHERE admin_id=? AND instance_id=? AND status='claimed' AND updated_at < datetime('now','-5 minutes')", (admin_id, instance_id))
        blockers = " AND action<>'browse_folder'" if action == 'stop' else ''
        if database.execute("SELECT 1 FROM LauncherCommands WHERE admin_id=? AND instance_id=? AND status IN ('pending','claimed')" + blockers,
                            (admin_id, instance_id)).fetchone():
            raise ValueError('Предыдущая команда для этого компьютера ещё выполняется.')
        cursor = database.execute('INSERT INTO LauncherCommands(admin_id,instance_id,action,payload) VALUES (?,?,?,?)',
                                  (admin_id, instance_id, action, json.dumps(payload, ensure_ascii=False)))
    return {'id': cursor.lastrowid, 'action': action, 'instance_id': instance_id, 'status': 'pending'}
