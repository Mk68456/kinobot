"""Append ready variants to one device-owned draft, locked until the plan finishes."""
import json

from database.connection import database
from webpanel import imports, repository


def pending(draft_id):
    row = database.execute('SELECT expected,completed FROM StreamProjects WHERE draft_id=?', (draft_id,)).fetchone()
    return row[0] if row and not row[1] else 0


def append(admin_id, instance, manifest):
    if not isinstance(manifest, dict):
        raise ValueError('Некорректный проект.')
    expected = manifest.get('stream_expected')
    root = manifest.get('root')
    if not isinstance(root, str) or not root.strip() or len(root) > 2000:
        raise ValueError('Проверьте путь проекта.')
    if type(expected) is not int or not 1 <= expected <= repository.MAX_IMPORT_FILES:
        raise ValueError('Укажите число вариантов проекта от 1 до 1000.')
    entries = manifest.get('files')
    if (not instance or not isinstance(entries, list) or not 1 <= len(entries) <= expected or
            any(not isinstance(e, dict) or not isinstance(e.get('path'), str) for e in entries) or
            len({e['path'] for e in entries}) != len(entries)):
        raise ValueError('Некорректные файлы поточного проекта или компьютер не указан.')
    header = {k: v for k, v in manifest.items() if k not in ('files', 'stream_expected')}
    with database.transaction():
        row = database.execute('''SELECT draft_id,expected,completed,manifest FROM StreamProjects
            WHERE admin_id=? AND instance_id=? AND root=?''', (admin_id, instance, manifest.get('root'))).fetchone()
        if row:
            saved = json.loads(row[3])
            if row[1] != expected or {k: v for k, v in saved.items() if k != 'files'} != header:
                raise ValueError('План проекта изменился. Запустите его как новый проект.')
            current = imports.device_draft(admin_id, row[0], instance)
            if not current or current['status'] != 'draft':
                raise ValueError('Карточка закрыта или на доработке. Верните её на проверку перед продолжением.')
            files = {e['path']: e for e in saved['files']}
            for entry in manifest.get('files', []):
                if entry['path'] in files and files[entry['path']] != entry:
                    raise ValueError('Ранее подготовленный файл изменился. Повторная отправка остановлена.')
                if row[2] and entry['path'] not in files:
                    raise ValueError('Этот проект уже завершён.')
                files[entry['path']] = entry
            cumulative = {**header, 'files': list(files.values())}
        else:
            cumulative = {**header, 'files': manifest.get('files')}
        if not isinstance(cumulative['files'], list) or not 1 <= len(cumulative['files']) <= expected:
            raise ValueError('Список файлов превышает план проекта.')
        current = imports.create(admin_id, cumulative, instance, allow_append=True)
        if row and current['id'] != row[0]:
            raise ValueError('Не удалось продолжить исходную карточку.')
        database.execute('''INSERT INTO StreamProjects(draft_id,admin_id,instance_id,root,expected,manifest)
            VALUES (?,?,?,?,?,?) ON CONFLICT(draft_id) DO UPDATE SET manifest=excluded.manifest''',
            (current['id'], admin_id, instance, manifest['root'], expected,
             json.dumps(cumulative, ensure_ascii=False)))
    return repository.draft(current['id'], admin_id)


def finish(admin_id, instance, draft_id):
    with database.transaction():
        current = imports.device_draft(admin_id, draft_id, instance)
        row = database.execute('SELECT expected,manifest FROM StreamProjects WHERE draft_id=?', (draft_id,)).fetchone()
        if not current or not row or current['status'] != 'draft':
            raise ValueError('Проект недоступен для завершения.')
        assets = [repository.asset(i['asset_id'], admin_id) for i in current['body']['items']]
        planned = {e['path'] for e in json.loads(row[1])['files']}
        actual = {i.get('relative_path') for i in current['body']['items']}
        if (len(assets) != row[0] or planned != actual or
                any(not a or a['status'] != 'ready' or not a['file_id'] for a in assets)):
            raise ValueError('Дождитесь подготовки и подтверждения всех файлов проекта.')
        database.execute('UPDATE StreamProjects SET completed=1 WHERE draft_id=?', (draft_id,))
    return repository.draft(draft_id, admin_id)
