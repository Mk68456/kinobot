"""Register manifests; video bytes arrive exclusively as Telegram messages."""

import hashlib
import json
import re
import secrets
import time
import uuid
from pathlib import PurePosixPath

from database.connection import database
from services.access import is_admin
from webpanel import repository

VIDEO_EXTENSIONS = {'.mp4', '.mkv', '.avi', '.mov', '.m4v', '.webm', '.ts'}


def pair(admin_id):
    token = secrets.token_urlsafe(32)
    with database.transaction():
        database.execute('DELETE FROM DesktopSessions WHERE expires_at<?', (time.time(),))
        database.execute('INSERT INTO DesktopSessions(token_hash,admin_id,expires_at) VALUES (?,?,?)',
                         (hashlib.sha256(token.encode()).hexdigest(), admin_id, time.time() + 48 * 3600))
    return token


def authenticate(token, instance_id=''):
    if not isinstance(token, str) or not 30 <= len(token) <= 100:
        return None
    from webpanel.launcher_control import valid_instance
    if instance_id and not valid_instance(instance_id):
        return None
    key = hashlib.sha256(token.encode()).hexdigest()
    with database.transaction():
        row = database.execute('SELECT admin_id,instance_id FROM DesktopSessions WHERE token_hash=? AND expires_at>?',
                               (key, time.time())).fetchone()
        if not row or not is_admin(row[0]) or (row[1] and row[1] != instance_id):
            return None
        if instance_id and not row[1]:
            database.execute('UPDATE DesktopSessions SET instance_id=? WHERE token_hash=?', (instance_id, key))
    return row[0]


def device_draft(admin_id, draft_id, instance_id=''):
    current = repository.draft(draft_id, admin_id)
    if not current:
        raise ValueError('Карточка недоступна.')
    owner = current['launcher_instance']
    if owner != instance_id:
        raise ValueError('Карточка принадлежит другому компьютеру. Продолжите очередь на нём.')
    return current


def create(admin_id, manifest, instance_id='', *, allow_append=False):
    if not isinstance(manifest, dict) or manifest.get('version') != 1:
        raise ValueError('Неподдерживаемый формат списка файлов.')
    title = str(manifest.get('title', '')).strip()
    root = str(manifest.get('root', '')).strip()
    entries = manifest.get('files')
    content_type = manifest.get('content_type', 'movie')
    if content_type not in ('movie', 'series'):
        raise ValueError('Выберите фильм или сериал.')
    from database.catalog_sections import default_section, validate_section
    catalog_section = str(manifest.get('catalog_section') or default_section(content_type))
    validate_section(catalog_section, content_type)
    series_fields = {}
    if content_type == 'series':
        movie_number, season_number = manifest.get('movie_number'), manifest.get('season_number')
        new_series = manifest.get('new_series') is True and movie_number is None
        if not new_series:
            if (type(movie_number) is not int or not 0 < movie_number < 2**53
                    or type(season_number) is not int or not 0 < season_number < 2**53):
                raise ValueError('Выберите существующий сериал и положительный номер сезона.')
            movie = database.execute('SELECT movie_title,content_type FROM Movies WHERE movie_number=?',
                                     (movie_number,)).fetchone()
            if not movie or movie[1] != 'series':
                raise ValueError('Выбранный сериал недоступен.')
            title = movie[0]
        elif season_number is not None and (type(season_number) is not int or not 1 <= season_number <= 999):
            raise ValueError('Укажите корректный номер сезона нового сериала.')
        if manifest.get('selection') is not None:
            raise ValueError('Для сезона выберите папку с сериями, а не отдельный фильм.')
        series_fields = {'movie_number': movie_number, 'season_number': season_number,
                         'season_name': f'Сезон {season_number}' if season_number else ''}
    if not title or len(title) > 200 or not root or len(root) > 2000:
        raise ValueError('Проверьте название фильма и путь к папке.')
    if not isinstance(entries, list) or not 1 <= len(entries) <= repository.MAX_IMPORT_FILES:
        raise ValueError(f'Выберите от 1 до {repository.MAX_IMPORT_FILES} видео одной карточки.')
    normalized, seen = [], set()
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError('В списке видео есть некорректная запись. Повторите сканирование папки.')
        path = str(entry.get('path', '')).replace('\\', '/')
        rel = PurePosixPath(path)
        size = entry.get('size')
        if (not path or len(path) > 2000 or rel.is_absolute() or ':' in path
                or '..' in rel.parts or rel.suffix.lower() not in VIDEO_EXTENSIONS
                or path.casefold() in seen or type(size) is not int or not 0 < size <= 4 * 1024**3):
            raise ValueError('Некорректный путь, размер или повтор видео.')
        seen.add(path.casefold())
        category = rel.parts[0] if len(rel.parts) > 1 else 'Варианты'
        name = ' / '.join((*rel.parts[1:-1], rel.stem)) if len(rel.parts) > 1 else rel.stem
        quality = str(entry.get('quality') or next((part for part in rel.parts[:-1]
            if part.lower() in ('1080p','720p','480p','360p','2160p','source')), '')).strip()
        if len(quality) > 30:
            raise ValueError('Слишком длинное название качества.')
        voice = ''
        entry_season = None
        if content_type == 'series':
            entry_season = entry.get('season_number') or season_number
            if new_series and (type(entry_season) is not int or not 1 <= entry_season <= 999):
                raise ValueError('Для нового сериала укажите сезон каждого файла.')
            voice = str(entry.get('voice') or '').strip()
            if len(quality)>30 or len(voice)>80:
                raise ValueError('Слишком длинное название качества или озвучки.')
            category = ''
            name = str(entry.get('name', rel.stem)).strip()
            if not name or any(e['name'].casefold() == name.casefold() and e.get('season_number') == entry_season
                               and (not quality or (e['quality'],e['voice']) == (quality,voice)) for e in normalized):
                raise ValueError('Укажите название серии и разные качества или озвучки для её вариантов.')
        digest = entry.get('content_sha256', '')
        if not isinstance(digest, str) or (digest and not re.fullmatch(r'[a-f0-9]{64}', digest)):
            raise ValueError('Некорректная контрольная сумма файла.')
        normalized.append({'path': path, 'size': size, 'mtime': str(entry.get('mtime', ''))[:30], 'digest': digest,
                           'quality':quality, 'voice':voice, 'category': category, 'name': name, 'filename': rel.name,
                           **({'season_number': entry_season} if content_type == 'series' else {})})
    selection = manifest.get('selection')
    if selection is not None and (not isinstance(selection, str) or len(normalized) != 1
                                   or selection != normalized[0]['path']):
        raise ValueError('Для отдельного фильма выберите ровно один указанный файл.')
    # Files in the same folder must not resume each other's movie drafts.
    identity = root if selection is None else root + '\0file:' + selection
    if content_type == 'series':
        identity += f'\0series:{movie_number}:season:{season_number}'
    legacy_key = hashlib.sha256(identity.encode()).hexdigest()
    key = hashlib.sha256((instance_id + '\0' + identity).encode()).hexdigest() if instance_id else legacy_key
    old = database.execute('SELECT id FROM WebDrafts WHERE admin_id=? AND import_key=? AND launcher_instance=?', (admin_id, key, instance_id)).fetchone()
    if not old and instance_id:
        # Resume pre-upgrade drafts only on their original computer; atomically adopt unowned ones.
        with database.transaction():
            old = database.execute("SELECT id FROM WebDrafts WHERE admin_id=? AND import_key=? AND launcher_instance IN ('',?)",
                                   (admin_id, legacy_key, instance_id)).fetchone()
            if old:
                database.execute('UPDATE WebDrafts SET launcher_instance=?,import_key=? WHERE id=?', (instance_id, key, old[0]))
    if old:
        current = repository.draft(old[0], admin_id)
        expected = []
        recovered = False
        prefix = root.replace('\\', '/').rstrip('/') + '/'
        by_path = {e['path']: e for e in normalized}
        for item in current['body']['items']:
            row = database.execute('SELECT size,source_mtime,source_path,content_sha256 FROM WebAssets WHERE id=? AND admin_id=?',
                                   (item.get('asset_id'), admin_id)).fetchone()
            if not row:
                raise ValueError('В сохранённом черновике не найден исходный файл. Проверьте список файлов в админке.')
            relative_path = item.get('relative_path')
            if not relative_path:
                # Older repository versions dropped these fields on Save. Recover only
                # from the asset's registered source path, never from filename/size guesses.
                source = (row[2] or '').replace('\\', '/')
                if not source.casefold().startswith(prefix.casefold()):
                    raise ValueError('У файла в черновике утрачен исходный путь. Нужна проверка данных импорта на сервере.')
                relative_path = source[len(prefix):]
                if relative_path not in by_path:
                    raise ValueError('Исходный файл из черновика не найден в выбранной папке. Проверьте её состав.')
                item['relative_path'] = relative_path
                recovered = True
            if 'category' not in item and relative_path in by_path:
                item['category'] = by_path[relative_path]['category']
                recovered = True
            expected.append((relative_path, row[0], row[1]))
            digest = by_path.get(relative_path, {}).get('digest')
            if digest and row[3] and digest != row[3]:
                raise ValueError('Содержимое ранее импортированного файла изменилось. Используйте новую папку.')
        actual = [(e['path'], e['size'], e['mtime']) for e in normalized]
        if sorted(expected) != sorted(actual):
            if not allow_append or current['status'] != 'draft' or not set(expected).issubset(set(actual)):
                raise ValueError('Состав ранее импортированной папки изменился. Используйте новую папку для нового импорта.')
            previous_paths = {e[0] for e in expected}
            with database.transaction():
                additions = _register_entries(admin_id, root, [e for e in normalized if e['path'] not in previous_paths])
                current = repository.save_draft(admin_id, {**current['body'],
                    'items': current['body']['items'] + additions}, current['id'])
        if recovered:
            with database.transaction():
                database.execute('UPDATE WebDrafts SET body=? WHERE id=? AND admin_id=?',
                                 (json.dumps(current['body'], ensure_ascii=False), current['id'], admin_id))
        if manifest.get('retry_rejected') is True and current['status'] == 'rejected':
            current = reopen(admin_id, current['id'], 'rejected', instance_id)
        if manifest.get('retry_published') is True and current['status'] == 'published':
            current = reopen(admin_id, current['id'], 'published', instance_id)
        with database.transaction():
            for item in current['body']['items']:
                digest = by_path[item['relative_path']]['digest']
                if digest:
                    database.execute('UPDATE WebAssets SET content_sha256=? WHERE id=? AND content_sha256 IN (?,\'\')',
                                     (digest, item['asset_id'], digest))
        current['backend_patch'] = 'draft-reopen-v4'
        return current
    if content_type == 'series' and database.execute(
            'SELECT 1 FROM MovieCategories WHERE movie_number=? AND (season_number=? OR name=?)',
            (movie_number, season_number, series_fields['season_name'])).fetchone():
        raise ValueError('Этот сезон уже опубликован. Для добавления серий используйте редактор сериала.')
    with database.transaction():
        items = _register_entries(admin_id, root, normalized)
        current = repository.save_draft(admin_id, {'content_type': content_type, 'title': title,
            'catalog_section': catalog_section,
            **series_fields,
            'items': items, 'description': '' if content_type == 'series' else
            ('Доступные варианты: ' + ', '.join(dict.fromkeys(e['category'] for e in normalized)))[:2500]})
        database.execute('UPDATE WebDrafts SET import_key=?,launcher_instance=? WHERE id=?', (key, instance_id, current['id']))
    current = repository.draft(current['id'], admin_id)
    current['created'] = True
    return current


def _register_entries(admin_id, root, normalized):
    items = []
    for entry in normalized:
        asset_id = uuid.uuid4().hex
        database.execute('''INSERT INTO WebAssets
            (id,admin_id,source,filename,size,file_type,status,source_path,source_mtime,content_sha256)
            VALUES (?,?,'desktop',?,?,'document','waiting_telegram',?,?,?)''',
            (asset_id, admin_id, entry['filename'], entry['size'],
             root.rstrip('/\\') + '/' + entry['path'], entry['mtime'], entry['digest']))
        items.append({'quality': entry['quality'], 'voice': entry['voice'], 'asset_id': asset_id,
                      'name': entry['name'], 'category': entry['category'], 'relative_path': entry['path'],
                      **({'season_number': entry['season_number']} if entry.get('season_number') else {})})
    return items


def receive(admin_id, message):
    match = re.fullmatch(r'KTFILE:([a-f0-9]{32})', (message.caption or '').strip())
    if not match:
        raise ValueError('Не найдена метка файла лаунчера.')
    asset_id = match[1]
    file = repository.asset(asset_id, admin_id)
    media = message.document or message.video
    if not file or file['source'] != 'desktop' or not media:
        raise ValueError('Файл не зарегистрирован для этого администратора.')
    if file['status'] == 'ready':
        return asset_id
    if file['status'] != 'waiting_telegram':
        raise ValueError('Приём файла недоступен.')
    if media.file_size != file['size']:
        reason = (f"Размер файла изменился: ожидалось {file['size']} байт, получено {media.file_size}. "
                  'Файл не привязан. Проверьте отправку без сжатия и повторите этот файл в лаунчере.')
        with database.transaction():
            database.execute('UPDATE WebAssets SET error=? WHERE id=?', (reason, asset_id))
            database.execute('DELETE FROM LauncherTransfers WHERE asset_id=? AND admin_id=?', (asset_id, admin_id))
        raise ValueError(reason)
    # The administrator and unique caption identify the registered upload.
    # Telegram may sanitize the filename; keep the original registered name.

    with database.transaction():
        database.execute('''UPDATE WebAssets SET status='ready',error=NULL,received=size,file_id=?,file_type=?,
            source_chat_id=?,source_message_id=? WHERE id=?''',
            (media.file_id, 'document' if message.document else 'video', message.chat.id, message.message_id, asset_id))
        database.execute('DELETE FROM LauncherTransfers WHERE asset_id=? AND admin_id=?', (asset_id, admin_id))
    return asset_id


def reopen(admin_id, draft_id, kind, instance_id=None):
    if kind not in ('rejected', 'published'):
        raise ValueError('Некорректный тип восстановления.')
    with database.transaction():
        current = (device_draft(admin_id, draft_id, instance_id) if instance_id is not None
                   else repository.draft(draft_id, admin_id))
        if not current:
            raise ValueError('Карточка недоступна.')
        if current['status'] == 'draft':
            return current
        if current['status'] != kind:
            raise ValueError('Карточка не имеет выбранного статуса.')
        if kind == 'published':
            number = current['movie_number']
            if not number or database.execute('SELECT 1 FROM Movies WHERE movie_number=?', (number,)).fetchone():
                raise ValueError('Произведение ещё есть в каталоге. Сброс запрещён, чтобы не создать дубль.')
            body = current['body']
            if body.get('movie_number') == number:
                body.update(movie_number=None, category_id=None)
            database.execute("UPDATE WebDrafts SET movie_number=NULL,body=?,status='draft' WHERE id=?",
                             (json.dumps(body, ensure_ascii=False), draft_id))
        repository.review(draft_id, admin_id, 'draft', 'Восстановление из лаунчера')
    return repository.draft(draft_id, admin_id)
