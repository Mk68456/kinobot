from database.episode_variants import add_series_file, variants as episode_variants
from webpanel.series_validation import SeriesVariantConflict, variant_conflicts
import json
import hashlib
import secrets
import uuid
from datetime import datetime, timezone
from html import escape
from urllib.parse import urlsplit
from database.connection import database
from database.movies import add_new_movie
from database.categories import add_movie_category, add_movie_subcategory, episode_order

MAX_IMPORT_FILES = 1000
ASSET_FIELDS = (
    "id,admin_id,source,filename,size,file_type,file_id,status,received,first_hash,error,created_at"
)


def asset(asset_id, admin_id):
    result = database.execute(
        f"SELECT {ASSET_FIELDS} FROM WebAssets WHERE id=? AND admin_id=?", (asset_id, admin_id)
    ).fetchone()
    return with_storage_order([dict(zip(ASSET_FIELDS.split(","), result))])[0] if result else None


def asset_preview(asset_id, admin_id):
    file = asset(asset_id, admin_id)
    if not file:
        raise ValueError('Файл недоступен.')
    from services.storage_backup import storage_reference
    reference = storage_reference(file['file_id']) if file['file_id'] else None
    if not reference:
        row = database.execute('SELECT source_chat_id,source_message_id FROM WebAssets WHERE id=? AND admin_id=?',
                               (asset_id, admin_id)).fetchone()
        reference = {'chat_id': row[0], 'message_id': row[1]} if row and row[0] and row[1] else None
    link = None
    if reference and str(reference['chat_id']).startswith('-100') and int(reference['message_id']) > 0:
        link = f"https://t.me/c/{str(reference['chat_id'])[4:]}/{int(reference['message_id'])}"
    return {'filename': file['filename'], 'size': file['size'], 'telegram_url': link,
            'can_send': file['status'] == 'ready' and bool(file['file_id'])}


def with_storage_order(files):
    """Attach channel message order without using the database recovery timestamp."""
    metadata = {}
    for offset in range(0, len(files), 400):
        ids = [file['id'] for file in files[offset:offset+400]]
        placeholders = ','.join('?' for _ in ids)
        for asset_id, source_chat, source_message, file_id, backup_chat, backup_message in database.execute(
                f'''SELECT a.id,a.source_chat_id,a.source_message_id,a.file_id,
                    s.storage_chat_id,s.storage_message_id
                    FROM WebAssets a LEFT JOIN TelegramStorageFiles s ON s.file_id=a.file_id AND s.status='ready'
                    WHERE a.id IN ({placeholders})''', ids):
            original = bool(source_chat and source_message and str(source_chat).startswith('-100') and (
                (file_id or '').startswith('storage:') or database.execute(
                    'SELECT 1 FROM TelegramStorage WHERE chat_id=?', (source_chat,)).fetchone()))
            chat = source_chat if original else backup_chat
            message = source_message if original else backup_message
            if chat and message and str(chat).startswith('-100') and message > 0:
                metadata[asset_id] = {'storage_chat_id': chat, 'storage_message_id': message}
    return [{**file, **metadata.get(file['id'], {})} for file in files]


def assets(admin_id, draft_id=None):
    result = [
        dict(zip(ASSET_FIELDS.split(","), row))
        for row in database.execute(
            f"SELECT {ASSET_FIELDS} FROM WebAssets WHERE admin_id=? ORDER BY created_at DESC,id DESC LIMIT 500",
            (admin_id,),
        )
    ]
    if draft_id:
        current = draft(draft_id, admin_id)
        if not current:
            raise ValueError('Черновик недоступен.')
        known = {a['id'] for a in result}
        missing = [i['asset_id'] for i in current['body']['items'] if i['asset_id'] not in known]
        # Fetch one large draft in batches rather than making up to 1000 HTTP requests.
        for offset in range(0, len(missing), 400):
            keys = missing[offset:offset + 400]
            placeholders = ','.join('?' for _ in keys)
            result.extend(dict(zip(ASSET_FIELDS.split(','), row)) for row in database.execute(
                f'SELECT {ASSET_FIELDS} FROM WebAssets WHERE admin_id=? AND id IN ({placeholders})',
                (admin_id, *keys)))
    return with_storage_order(result)


def receive_telegram(admin_id, message):
    media = message.video or message.document
    if not media:
        raise ValueError("Нужно видео или документ.")
    file_type = "video" if message.video else "document"
    name = media.file_name or message.caption or f"Видео {message.message_id}.mp4"
    asset_id = uuid.uuid4().hex
    with database.transaction():
        database.execute(
            """INSERT OR IGNORE INTO WebAssets
            (id,admin_id,source,filename,size,file_type,file_id,status,source_chat_id,source_message_id)
            VALUES (?,?,'telegram',?,?,?,?,'ready',?,?)""",
            (
                asset_id,
                admin_id,
                str(name)[:240],
                media.file_size or 0,
                file_type,
                media.file_id,
                message.chat.id,
                message.message_id,
            ),
        )
    return database.execute(
        "SELECT id FROM WebAssets WHERE source_chat_id=? AND source_message_id=?",
        (message.chat.id, message.message_id),
    ).fetchone()[0]


def draft(draft_id, admin_id):
    row = database.execute(
        """SELECT id,body,status,movie_number,review_history,launcher_instance,
        (SELECT name FROM LauncherAgents a WHERE a.admin_id=WebDrafts.admin_id AND a.instance_id=WebDrafts.launcher_instance)
        FROM WebDrafts WHERE id=? AND admin_id=?""", (draft_id, admin_id)
    ).fetchone()
    return (
        {"id": row[0], "body": json.loads(row[1]), "status": row[2], "movie_number": row[3],
         "review_history": json.loads(row[4]), "launcher_instance": row[5], "launcher_name": row[6],
         "stream_pending": stream_pending(row[0])} if row else None
    )


def drafts(admin_id):
    result = [
        {"id": row[0], "body": json.loads(row[1]), "status": row[2], "movie_number": row[3],
         "review_history": json.loads(row[4]), "launcher_instance": row[5], "launcher_name": row[6]}
        for row in database.execute(
            """SELECT id,body,status,movie_number,review_history,launcher_instance,
            (SELECT name FROM LauncherAgents a WHERE a.admin_id=WebDrafts.admin_id AND a.instance_id=WebDrafts.launcher_instance)
            FROM WebDrafts WHERE admin_id=? ORDER BY updated_at DESC,id DESC LIMIT 100""",
            (admin_id,),
        )
    ]
    return [{**d, "stream_pending": stream_pending(d['id']), "review_token": review_token(d)} for d in result]


def stream_pending(draft_id):
    from webpanel.streaming import pending
    return pending(draft_id)


def review_token(current):
    snapshot = json.dumps({'body': current['body'], 'status': current['status']},
                          sort_keys=True, ensure_ascii=False, separators=(',', ':'))
    return hashlib.sha256(snapshot.encode()).hexdigest()


def review_queue(admin_id, offset=0, limit=20):
    offset = max(0, int(offset))
    limit = max(1, min(int(limit), 50))
    total = database.execute("SELECT count(*) FROM WebDrafts WHERE admin_id=? AND status IN ('draft','revision')",
                             (admin_id,)).fetchone()[0]
    rows = database.execute("""SELECT id FROM WebDrafts WHERE admin_id=? AND status IN ('draft','revision')
        ORDER BY updated_at DESC,id DESC LIMIT ? OFFSET ?""", (admin_id, limit, offset)).fetchall()
    items = []
    for row in rows:
        current = draft(row[0], admin_id)
        files = [asset(i['asset_id'], admin_id) for i in current['body']['items']]
        ready = sum(bool(f and f['status'] == 'ready' and f['file_id']) for f in files)
        reason = ''
        if current.get('stream_pending'):
            reason = f"Поточная подготовка ещё не завершена. План: {current['stream_pending']} файлов."
        elif current['status'] != 'draft':
            reason = 'На доработке: сначала верните карточку на проверку.'
        elif not files:
            reason = 'Нет файлов.'
        elif ready != len(files):
            reason = f'Получено файлов: {ready} из {len(files)}.'
        elif not current['body']['title']:
            reason = 'Не заполнено название.'
        elif not current['body']['movie_number'] and not current['body']['age_rating']:
            reason = 'Укажите возрастную маркировку перед публикацией.'
        items.append({**current, 'review_token': review_token(current), 'can_publish': not reason,
                      'reason': reason, 'grouped_assets':[i['asset_id'] for i in current['body']['items'] if database.execute('SELECT 1 FROM GroupedFiles WHERE asset_id=? AND draft_id!=?',(i['asset_id'],current['id'])).fetchone()], 'ready_files': ready, 'total_files': len(files)})
    return {'items': items, 'total': total, 'offset': offset, 'limit': limit}


def publish_many(admin_id, selections):
    if (not isinstance(selections, list) or not 1 <= len(selections) <= 100
            or any(not isinstance(i, dict) or not isinstance(i.get('id'), str)
                   or len(i['id']) > 100 or not isinstance(i.get('review_token'), str)
                   or len(i['review_token']) != 64 for i in selections)
            or len({i['id'] for i in selections}) != len(selections)):
        raise ValueError('Выберите от 1 до 100 разных карточек для публикации.')
    results = []
    for selected in selections:
        try:
            with database.transaction():
                current = draft(selected['id'], admin_id)
                if not current:
                    raise ValueError('Карточка недоступна.')
                if current['status'] != 'published' and not secrets.compare_digest(
                        review_token(current), selected['review_token']):
                    raise ValueError('Карточка изменилась после просмотра. Проверьте её ещё раз.')
                number = publish(selected['id'], admin_id)
            results.append({'id': selected['id'], 'ok': True, 'movie_number': number})
        except ValueError as error:
            results.append({'id': selected['id'], 'ok': False, 'error': str(error)})
    return results


def publication_package(admin_id):
    items = []
    with database.transaction():
        rows = database.execute('SELECT draft_id,review_token FROM PublicationPackage WHERE admin_id=? ORDER BY added_at,draft_id',
                                (admin_id,)).fetchall()
        for draft_id, token in rows:
            current = draft(draft_id, admin_id)
            if not current or current['status'] != 'draft' or review_token(current) != token:
                database.execute('DELETE FROM PublicationPackage WHERE draft_id=? AND admin_id=?', (draft_id, admin_id))
                continue
            items.append({**current, 'review_token': token})
    return {'items': items}


def package_add(admin_id, draft_id, token):
    with database.transaction():
        current = draft(draft_id, admin_id)
        if not current or current['status'] != 'draft' or review_token(current) != token:
            raise ValueError('Карточка изменилась или недоступна. Откройте её и проверьте ещё раз.')
        body = current['body']
        if current.get('stream_pending'):
            raise ValueError('Дождитесь завершения поточной подготовки проекта.')
        if body['content_type'] != 'movie' or not body['title'] or not body['items']:
            raise ValueError('В пакет можно добавить заполненную карточку фильма с файлами.')
        if not body['movie_number'] and not body['age_rating']:
            raise ValueError('Укажите возрастную маркировку перед публикацией.')
        if any(database.execute('SELECT 1 FROM GroupedFiles WHERE asset_id=? AND draft_id!=?', (i['asset_id'],draft_id)).fetchone() for i in body['items']):
            raise ValueError('Файлы перенесены в общую карточку. Добавьте в пакет её.')
        files = [asset(i['asset_id'], admin_id) for i in body['items']]
        if any(not f or f['status'] != 'ready' or not f['file_id'] for f in files):
            raise ValueError('Дождитесь получения всех файлов фильма из Telegram.')
        package = publication_package(admin_id)['items']
        if len(package) >= 100 and not any(d['id'] == draft_id for d in package):
            raise ValueError('В одном пакете может быть до 100 фильмов.')
        database.execute('INSERT OR REPLACE INTO PublicationPackage(draft_id,admin_id,review_token) VALUES (?,?,?)',
                         (draft_id, admin_id, token))
    return publication_package(admin_id)


def package_remove(admin_id, draft_id):
    with database.transaction():
        database.execute('DELETE FROM PublicationPackage WHERE draft_id=? AND admin_id=?', (draft_id, admin_id))
    return publication_package(admin_id)


def package_publish(admin_id, selections):
    # Check membership and the reviewed snapshot again inside each publication transaction.
    if not isinstance(selections, list) or not 1 <= len(selections) <= 100:
        raise ValueError('Пакет должен содержать от 1 до 100 фильмов.')
    if (any(not isinstance(i, dict) or not isinstance(i.get('id'), str)
            or not 1 <= len(i['id']) <= 100 or not isinstance(i.get('review_token'), str)
            or len(i['review_token']) != 64 for i in selections)
            or len({i['id'] for i in selections}) != len(selections)):
        raise ValueError('Некорректный состав пакета.')
    results = []
    for selected in selections:
        with database.transaction():
            row = database.execute('SELECT review_token FROM PublicationPackage WHERE draft_id=? AND admin_id=?',
                                   (selected['id'], admin_id)).fetchone()
            current = draft(selected['id'], admin_id)
            if current and current['status'] == 'published':
                results.append({'id': selected['id'], 'ok': True, 'movie_number': current['movie_number']})
            elif not row or row[0] != selected.get('review_token'):
                results.append({'id': selected['id'], 'ok': False, 'error': 'Фильм не в пакете или требует повторной проверки.'})
            else:
                results.extend(publish_many(admin_id, [selected]))
    publication_package(admin_id)
    return results


def normalize_draft(body):
    if not isinstance(body, dict):
        raise ValueError("Некорректный черновик.")
    result = {
        key: str(body.get(key) or "").strip() for key in ("title", "description", "poster_url", "season_name")
    }
    for key, limit in [("title", 200), ("description", 2500), ("poster_url", 1000)]:
        if len(result[key]) > limit:
            raise ValueError(f"Слишком длинное поле: {key}.")
    if result["poster_url"]:
        url = urlsplit(result["poster_url"])
        if url.scheme != "https" or not url.hostname or url.username or url.password:
            raise ValueError("Постер должен быть HTTPS-ссылкой.")
    if body.get("content_type") not in ("movie", "series"):
        raise ValueError("Выберите фильм или сериал.")
    from services.ratings import normalize
    result.update(normalize(body))
    from services.poiskkino import provenance
    result.update(provenance(body))
    result["content_type"] = body["content_type"]
    from database.catalog_sections import default_section, validate_section
    result['catalog_section'] = str(body.get('catalog_section') or
                                    default_section(body['content_type'])).strip()
    validate_section(result['catalog_section'], result['content_type'])
    for key in ("movie_number", "category_id", "season_number"):
        value = body.get(key)
        if value in ("", None):
            result[key] = None
        elif type(value) is int and 0 < value < 2**53:
            result[key] = value
        else:
            raise ValueError("Некорректный номер фильма, сезона или категории.")
    items = body.get("items", [])
    if not isinstance(items, list) or len(items) > MAX_IMPORT_FILES:
        raise ValueError(f"В черновике может быть до {MAX_IMPORT_FILES} файлов.")
    result["items"] = []
    seen = set()
    for item in items:
        if not isinstance(item, dict):
            raise ValueError("Некорректный файл.")
        asset_id = str(item.get("asset_id", ""))
        name = str(item.get("name", "")).strip()
        if len(asset_id) != 32 or asset_id in seen or not name:
            raise ValueError("Проверьте названия файлов и повторы в списке.")
        seen.add(asset_id)
        category = str(item.get('category') or '').strip()
        relative_path = str(item.get('relative_path') or '')
        if len(relative_path) > 2000:
            raise ValueError('Слишком длинный путь к файлу.')
        season_number = item.get('season_number')
        if season_number in ('',None):season_number=None
        elif type(season_number) is not int or not 1<=season_number<=999:
            raise ValueError('Номер сезона файла должен быть от 1 до 999.')
        quality = str(item.get('quality') or '').strip()
        if quality.casefold() in ('2160p','1080p','720p','480p','360p','source'):
            quality = quality.casefold()
        voice = str(item.get('voice') or '').strip()
        if voice and not quality and result['content_type'] == 'series':
            raise ValueError('Укажите качество файла перед выбором озвучки.')
        if len(quality) > 30 or len(voice) > 80:
            raise ValueError('Слишком длинное название качества или озвучки.')
        result["items"].append({"quality":quality,"voice":voice,"season_number":season_number,"asset_id": asset_id, "name": name,
                                "category": category, "relative_path": relative_path})
    return result


def save_draft(admin_id, body, draft_id=None):
    body = normalize_draft(body)
    for item in body["items"]:
        if not asset(item["asset_id"], admin_id):
            raise ValueError("Файл не найден в вашей библиотеке.")
    with database.transaction():
        if draft_id:
            old = draft(draft_id, admin_id)
            if not old or old["status"] not in ("draft", "revision"):
                raise ValueError("Черновик уже опубликован или недоступен.")
            retained = {i['asset_id'] for i in body['items']}
            for (assigned,) in database.execute('SELECT asset_id FROM GroupedFiles WHERE draft_id=?', (draft_id,)).fetchall():
                if assigned not in retained:
                    database.execute('DELETE FROM GroupedFiles WHERE asset_id=?', (assigned,))
            for source_id, source_body in database.execute("SELECT id,body FROM WebDrafts WHERE admin_id=? AND status='grouped'", (admin_id,)).fetchall():
                if any(not database.execute('SELECT 1 FROM GroupedFiles WHERE asset_id=?', (i['asset_id'],)).fetchone() for i in json.loads(source_body)['items']):
                    database.execute("UPDATE WebDrafts SET status='draft' WHERE id=?", (source_id,))
            if old['body'] != body:
                database.execute('DELETE FROM PublicationPackage WHERE draft_id=? AND admin_id=?', (draft_id, admin_id))
            database.execute(
                "UPDATE WebDrafts SET body=?,updated_at=CURRENT_TIMESTAMP WHERE id=?",
                (json.dumps(body, ensure_ascii=False), draft_id),
            )
        else:
            draft_id = uuid.uuid4().hex
            database.execute(
                "INSERT INTO WebDrafts(id,admin_id,body) VALUES (?,?,?)",
                (draft_id, admin_id, json.dumps(body, ensure_ascii=False)),
            )
    return draft(draft_id, admin_id)


def publish(draft_id, admin_id):
    with database.transaction():
        current = draft(draft_id, admin_id)
        if not current:
            raise ValueError("Черновик не найден.")
        if current.get('stream_pending'):
            raise ValueError('Дождитесь завершения поточной подготовки проекта.')
        if current["status"] == "published":
            return current["movie_number"]
        if current['status'] != 'draft':
            raise ValueError('Верните доработанный черновик на проверку перед одобрением.')
        body = normalize_draft(current["body"])
        if any(database.execute('SELECT 1 FROM GroupedFiles WHERE asset_id=? AND draft_id!=?',(i['asset_id'],draft_id)).fetchone() for i in body['items']):
            raise ValueError('Часть файлов объединена в другую карточку. Публикуйте объединённую карточку или соберите оставшиеся файлы отдельно.')
        items = body["items"]
        if not items:
            raise ValueError("Добавьте хотя бы один файл.")
        files = [asset(item["asset_id"], admin_id) for item in items]
        if any(not file or file["status"] != "ready" or not file["file_id"] for file in files):
            raise ValueError("Дождитесь загрузки всех файлов в Telegram.")
        conflicts = variant_conflicts(body)
        if conflicts:
            raise SeriesVariantConflict(conflicts)
        movie_number = body["movie_number"]
        if movie_number:
            movie = database.execute(
                "SELECT content_type,catalog_section FROM Movies WHERE movie_number=?", (movie_number,)
            ).fetchone()
            if not movie or movie[0] != body["content_type"]:
                raise ValueError("Выбранный фильм или сериал недоступен.")
            if movie[1] != body['catalog_section']:
                raise ValueError('Раздел существующей карточки изменился. Откройте её заново.')
        else:
            if not body["title"]:
                raise ValueError("Введите название фильма или сериала.")
            from database.age_access import RATINGS
            if body['age_rating'] not in RATINGS:
                raise ValueError('Укажите возрастную маркировку перед публикацией.')
            movie_number = add_new_movie(
                body["title"],
                poster_image=body["poster_url"],
                card_description=escape(body["description"]),
                content_type=body["content_type"],
                catalog_section=body['catalog_section'],
            )
        if not body['movie_number']:
            database.execute('UPDATE Movies SET rating_kp=?,rating_imdb=?,age_rating=?,rating_source_id=?,ratings_fetched_at=? WHERE movie_number=?',
                             (body['rating_kp'],body['rating_imdb'],body['age_rating'],body['rating_source_id'],body['ratings_fetched_at'],movie_number))
        if body["content_type"] == "movie" and any(item['category'] for item in items):
            categories = {
                str(name).casefold(): category_id
                for category_id, name in database.execute(
                    'SELECT id,name FROM MovieCategories WHERE movie_number=?', (movie_number,)
                ).fetchall()
            }
            names = {
                (str(category).casefold(), str(name).casefold())
                for category, name in database.execute(
                    '''SELECT c.name,s.name FROM MovieCategories c
                       JOIN MovieSubcategories s ON s.category_id=c.id
                       WHERE c.movie_number=?''', (movie_number,)
                ).fetchall()
            }
            if body['movie_number']:
                old_file = database.execute(
                    'SELECT movie_file,movie_file_type FROM Movies WHERE movie_number=?',
                    (movie_number,),
                ).fetchone()
                if old_file and old_file[0]:
                    base_name = 'Варианты'
                    category_id = categories.get(base_name.casefold())
                    if category_id is None:
                        category_id = add_movie_category(movie_number, base_name)
                        categories[base_name.casefold()] = category_id
                    main_name = 'Основной файл'
                    suffix = 2
                    while (base_name.casefold(), main_name.casefold()) in names:
                        main_name = f'Основной файл {suffix}'
                        suffix += 1
                    add_movie_subcategory(category_id, main_name, old_file[0], old_file[1])
                    names.add((base_name.casefold(), main_name.casefold()))
                    database.execute(
                        "UPDATE Movies SET movie_file='',movie_file_type='' WHERE movie_number=?",
                        (movie_number,),
                    )
            for item, file in zip(items, files):
                name = item['category'] or 'Варианты'
                identity = (name.casefold(), item['name'].casefold())
                if identity in names:
                    raise ValueError('Названия вариантов в одной категории не должны повторяться.')
                names.add(identity)
                category_id = categories.get(name.casefold())
                if category_id is None:
                    category_id = add_movie_category(movie_number, name)
                    categories[name.casefold()] = category_id
                add_movie_subcategory(category_id, item['name'], file['file_id'], file['file_type'])
        elif body["content_type"] == "movie":
            if len(items) != 1:
                raise ValueError(
                    "У фильма должен быть один основной файл. Для другого фильма создайте новый черновик."
                )
            old_file = database.execute(
                "SELECT movie_file FROM Movies WHERE movie_number=?", (movie_number,)
            ).fetchone()[0]
            if old_file:
                raise ValueError("У фильма уже есть файл. Заменить его можно через Telegram-админку.")
            if body['movie_number'] and database.execute(
                    'SELECT 1 FROM MovieCategories WHERE movie_number=? LIMIT 1', (movie_number,)
            ).fetchone():
                raise ValueError('У фильма уже есть варианты. Укажите категорию нового файла.')
            database.execute(
                "UPDATE Movies SET movie_file=?,movie_file_type=? WHERE movie_number=?",
                (files[0]["file_id"], files[0]["file_type"], movie_number),
            )
        elif any(item.get('season_number') for item in items):
            seasons = {}
            for item, file in zip(items, files):
                number = item.get('season_number') or body['season_number']
                if not number:raise ValueError('Укажите сезон для каждого файла.')
                if number not in seasons:
                    existing = database.execute('SELECT id FROM MovieCategories WHERE movie_number=? AND season_number=?',(movie_number,number)).fetchall()
                    if len(existing)>1:raise ValueError('Несколько сезонов имеют один номер. Исправьте каталог.')
                    seasons[number] = existing[0][0] if existing else add_movie_category(movie_number,f'Сезон {number}',number)
                category_id=seasons[number]
                add_series_file(category_id, item, file)
        else:
            category_id = body["category_id"]
            if category_id:
                if not database.execute(
                    "SELECT 1 FROM MovieCategories WHERE id=? AND movie_number=?", (category_id, movie_number)
                ).fetchone():
                    raise ValueError("Сезон не принадлежит выбранному сериалу.")
            else:
                name = body["season_name"] or (
                    f"Сезон {body['season_number']}" if body["season_number"] else ""
                )
                if not name:
                    raise ValueError("Введите название или номер сезона.")
                if database.execute(
                    "SELECT 1 FROM MovieCategories WHERE movie_number=? AND (name=? OR season_number=?)",
                    (movie_number, name, body["season_number"]),
                ).fetchone():
                    raise ValueError("Этот сезон уже существует — выберите его в списке.")
                category_id = add_movie_category(movie_number, name, body["season_number"])
            names = {
                row[0].casefold()
                for row in database.execute(
                    "SELECT name FROM MovieSubcategories WHERE category_id=?", (category_id,)
                )
            }
            for item, file in zip(items, files):
                add_series_file(category_id, item, file)
        database.execute(
            "UPDATE WebDrafts SET status='published',movie_number=? WHERE id=?", (movie_number, draft_id)
        )
        _review_event(current, admin_id, 'published', '')
        return movie_number


def _review_event(current, admin_id, status, comment):
    history = current['review_history'] + [{'status': status, 'comment': comment,
        'admin_id': admin_id, 'at': datetime.now(timezone.utc).isoformat()}]
    database.execute('UPDATE WebDrafts SET review_history=?,updated_at=CURRENT_TIMESTAMP WHERE id=?',
                     (json.dumps(history, ensure_ascii=False), current['id']))


def review(draft_id, admin_id, status, comment=''):
    if status not in ('rejected', 'revision', 'draft') or not isinstance(comment, str) or len(comment) > 2000:
        raise ValueError('Некорректное решение или комментарий.')
    if status == 'revision' and not comment.strip():
        raise ValueError('Напишите, что нужно доработать.')
    with database.transaction():
        current = draft(draft_id, admin_id)
        if not current or current['status'] == 'published':
            raise ValueError('Черновик недоступен для изменения решения.')
        database.execute('DELETE FROM PublicationPackage WHERE draft_id=? AND admin_id=?', (draft_id, admin_id))
        _review_event(current, admin_id, status, comment.strip())
        database.execute('UPDATE WebDrafts SET status=? WHERE id=?', (status, draft_id))
    return draft(draft_id, admin_id)


def promo(draft_id, admin_id, username):
    current = draft(draft_id, admin_id)
    if not current or current['status'] != 'published':
        raise ValueError('Рекламный пост доступен после одобрения и публикации.')
    body = current['body']
    url = f"https://t.me/{username}?start=movie_{current['movie_number']}"
    variants = list(dict.fromkeys((i.get('category', '') + ' / ' + i['name']).strip(' /') for i in body['items']))
    text = f"🎬 {body['title']}\n\n{body['description']}\n\n" + 'Варианты: ' + '; '.join(variants)
    return {'text': text[:3500].rstrip() + '\n\n▶️ Смотреть в KinoTime: ' + url,
            'url': url, 'poster_url': body['poster_url']}


def renumber_movie(admin_id, old_number, new_number):
    if isinstance(old_number,str) and old_number.isascii() and old_number.isdigit():
        old_number=int(old_number)
    if type(old_number) is not int or type(new_number) is not int or not 0<old_number<2**53 or not 0<new_number<2**53:
        raise ValueError('ID должен быть положительным целым числом.')
    if old_number==new_number:raise ValueError('Новый ID совпадает с текущим.')
    with database.transaction():
        movie=database.execute('SELECT movie_title,content_type FROM Movies WHERE movie_number=?',(old_number,)).fetchone()
        if not movie:raise ValueError('Фильм не найден.')
        if database.execute('SELECT 1 FROM Movies WHERE movie_number=?',(new_number,)).fetchone():
            raise ValueError('Новый ID уже занят.')
        database.execute('PRAGMA defer_foreign_keys=ON')
        # Discover actual columns, including custom tables, and never swallow update errors.
        tables=database.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'").fetchall()
        for (table,) in tables:
            quoted='"'+table.replace('"','""')+'"'
            columns={r[1] for r in database.execute('PRAGMA table_info('+quoted+')')}
            if 'movie_number' in columns:
                database.execute('UPDATE '+quoted+' SET movie_number=? WHERE movie_number=?',(new_number,old_number))
        for draft_id,raw in database.execute('SELECT id,body FROM WebDrafts').fetchall():
            body=json.loads(raw)
            if body.get('movie_number')==old_number:
                body['movie_number']=new_number
                database.execute('UPDATE WebDrafts SET body=? WHERE id=?',(json.dumps(body,ensure_ascii=False),draft_id))
                database.execute('DELETE FROM PublicationPackage WHERE draft_id=?',(draft_id,))
        database.execute('UPDATE MovieSequence SET value=MAX(value,?) WHERE id=1',(new_number,))
    return {'old_movie_number':old_number,'movie_number':new_number,'title':movie[0],'content_type':movie[1]}


def catalog_folders(movie_number):
    """Return the user-visible folders and their files for a published card."""
    try:
        movie_number = int(movie_number)
    except (TypeError, ValueError):
        raise ValueError('Некорректный код карточки.')
    movie = database.execute(
        'SELECT movie_title,content_type FROM Movies WHERE movie_number=?', (movie_number,)
    ).fetchone()
    if not movie:
        raise ValueError('Карточка не найдена.')
    folders = []
    for folder_id, name, season_number in database.execute(
        '''SELECT id,name,season_number FROM MovieCategories
           WHERE movie_number=? ORDER BY COALESCE(season_number,2147483647),id''',
        (movie_number,),
    ).fetchall():
        files = database.execute(
            'SELECT id,name FROM MovieSubcategories WHERE category_id=? ORDER BY id',
            (folder_id,),
        ).fetchall()
        if movie[1] == 'series':
            files = sorted(files, key=episode_order)
        folders.append({
            'id': folder_id,
            'name': name,
            'season_number': season_number,
            'files': [{'id': row[0], 'name': row[1], **({'qualities': [v[1] for v in episode_variants(row[0])]} if movie[1] == 'series' else {})} for row in files],
        })
    return {
        'movie_number': movie_number,
        'title': movie[0],
        'content_type': movie[1] or 'movie',
        'folders': folders,
    }


def rename_catalog_folders(movie_number, changes):
    current = catalog_folders(movie_number)
    if not isinstance(changes, list) or not 1 <= len(changes) <= 500:
        raise ValueError('Передайте папки, которые нужно переименовать.')
    normalized = []
    normalized_files = []
    for change in changes:
        if not isinstance(change, dict) or type(change.get('id')) is not int:
            raise ValueError('Некорректный идентификатор папки.')
        name = str(change.get('name') or '').strip()
        if not name or any(char in name for char in '\r\n\0'):
            raise ValueError('Название папки должно быть непустым и записанным в одной строке.')
        normalized.append((change['id'], name))
        files = change.get('files', [])
        if not isinstance(files, list) or len(files) > 2000:
            raise ValueError('Некорректный список файлов в папке.')
        seen_file_ids = set()
        seen_file_names = set()
        for file_change in files:
            if not isinstance(file_change, dict) or type(file_change.get('id')) is not int:
                raise ValueError('Некорректный идентификатор файла.')
            file_name = str(file_change.get('name') or '').strip()
            if not file_name or any(char in file_name for char in '\r\n\0'):
                raise ValueError('Название файла должно быть непустым и записанным в одной строке.')
            if file_change['id'] in seen_file_ids:
                raise ValueError('Один файл указан несколько раз.')
            if file_name.casefold() in seen_file_names:
                raise ValueError('В одной папке не может быть файлов с одинаковым названием.')
            seen_file_ids.add(file_change['id'])
            seen_file_names.add(file_name.casefold())
            normalized_files.append((change['id'], file_change['id'], file_name))
    if len({folder_id for folder_id, _ in normalized}) != len(normalized):
        raise ValueError('Одна папка указана несколько раз.')
    known = {folder['id']: folder for folder in current['folders']}
    if any(folder_id not in known for folder_id, _ in normalized):
        raise ValueError('Одна из папок не принадлежит выбранной карточке.')
    known_files = {
        (folder['id'], file['id'])
        for folder in current['folders']
        for file in folder['files']
    }
    if any((folder_id, file_id) not in known_files for folder_id, file_id, _ in normalized_files):
        raise ValueError('Один из файлов не принадлежит выбранной папке.')
    resulting_file_names = {
        folder['id']: {file['id']: file['name'].strip() for file in folder['files']}
        for folder in current['folders']
    }
    for folder_id, file_id, name in normalized_files:
        resulting_file_names[folder_id][file_id] = name
    if any(
        len([name.casefold() for name in names.values()])
        != len({name.casefold() for name in names.values()})
        for names in resulting_file_names.values()
    ):
        raise ValueError('В одной папке не может быть файлов с одинаковым названием.')
    resulting_names = {
        folder_id: known[folder_id]['name'].strip()
        for folder_id in known
    }
    resulting_names.update(dict(normalized))
    merge_groups = {}
    for folder in current['folders']:
        merge_groups.setdefault(resulting_names[folder['id']].casefold(), []).append(folder['id'])
    for folder_ids in merge_groups.values():
        merged_file_names = [
            name.casefold()
            for folder_id in folder_ids
            for name in resulting_file_names[folder_id].values()
        ]
        if len(merged_file_names) != len(set(merged_file_names)):
            raise ValueError(
                'В объединяемых папках есть файлы с одинаковым названием. '
                'Сначала задайте им разные названия.'
            )
    merged_count = sum(max(0, len(folder_ids) - 1) for folder_ids in merge_groups.values())
    with database.transaction():
        for folder_id, name in normalized:
            database.execute(
                '''UPDATE MovieCategories SET name=?
                   WHERE id=? AND movie_number=?''',
                (name, folder_id, current['movie_number']),
            )
        for folder_id, file_id, name in normalized_files:
            database.execute(
                '''UPDATE MovieSubcategories SET name=?
                   WHERE id=? AND category_id=?''',
                (name, file_id, folder_id),
            )
        for folder_ids in merge_groups.values():
            target_id = folder_ids[0]
            for source_id in folder_ids[1:]:
                database.execute(
                    'UPDATE MovieSubcategories SET category_id=? WHERE category_id=?',
                    (target_id, source_id),
                )
                database.execute(
                    'DELETE FROM MovieCategories WHERE id=? AND movie_number=?',
                    (source_id, current['movie_number']),
                )
    result = catalog_folders(current['movie_number'])
    result['merged_folders'] = merged_count
    return result


def group_files(admin_id, selections, body):
    if not isinstance(body, dict):
        raise ValueError('Укажите оформление карточки.')
    if not isinstance(selections,list) or not 1<=len(selections)<=MAX_IMPORT_FILES or any(not isinstance(i,dict) or not isinstance(i.get('asset_id'),str) or not isinstance(i.get('draft_id'),str) for i in selections):
        raise ValueError(f'Выберите от 1 до {MAX_IMPORT_FILES} файлов.')
    if len({i['asset_id'] for i in selections})!=len(selections):raise ValueError('Файлы повторяются.')
    with database.transaction():
        target_id = body.get('target_id')
        target = draft(target_id, admin_id) if isinstance(target_id,str) else None
        if target_id and (not target or target.get('stream_pending') or target['status']!='draft' or target['body'].get('movie_number') or review_token(target)!=body.get('target_token')):
            raise ValueError('Карточка назначения изменилась или недоступна. Обновите список.')
        sources = {}
        items = []
        for selection in selections:
            source=sources.setdefault(selection['draft_id'],draft(selection['draft_id'],admin_id))
            if selection['draft_id']==target_id:
                raise ValueError('Нельзя добавить карточку в саму себя.')
            if not source or source.get('stream_pending') or source['status']!='draft' or source['body'].get('movie_number'):
                raise ValueError('Выберите файлы новых черновиков на проверке.')
            if source.get('body') and review_token(source)!=selection.get('review_token'):
                raise ValueError('Карточка изменилась. Обновите список перед объединением.')
            file=asset(selection['asset_id'],admin_id)
            if not file or file['status'] not in ('ready','waiting_telegram'):
                raise ValueError('Дождитесь подготовки файлов.')
            assignment=database.execute('SELECT draft_id FROM GroupedFiles WHERE asset_id=?',(file['id'],)).fetchone()
            if assignment and assignment[0]!=source['id']:
                raise ValueError('Файл уже объединён в другую карточку.')
            item=next((i for i in source['body']['items'] if i['asset_id']==file['id']),None)
            if not item:raise ValueError('Файл не принадлежит выбранной карточке.')
            merged_item = {**item, 'category': item.get('category') or 'Варианты'}
            if source['body'].get('content_type') == 'series' and not merged_item.get('season_number'):
                merged_item['season_number'] = source['body'].get('season_number')
            items.append(merged_item)
        existing = target['body']['items'] if target else []
        if {i['asset_id'] for i in existing} & {i['asset_id'] for i in items}:
            raise ValueError('Эти файлы уже есть в карточке назначения.')
        if target:
            target=save_draft(admin_id,{**target['body'],'items':existing+items},target_id)
        else:
            target=save_draft(admin_id,{**body,'movie_number':None,'items':items})
        for item in items:database.execute('INSERT OR REPLACE INTO GroupedFiles VALUES (?,?)',(item['asset_id'],target['id']))
        for source in sources.values():
            database.execute('DELETE FROM PublicationPackage WHERE draft_id=?',(source['id'],))
            if all(database.execute('SELECT 1 FROM GroupedFiles WHERE asset_id=?',(i['asset_id'],)).fetchone() for i in source['body']['items']):
                database.execute("UPDATE WebDrafts SET status='grouped' WHERE id=?",(source['id'],))
        return target


def reject_all(admin_id, expected):
    if type(expected) is not int or expected < 1:
        raise ValueError('Обновите очередь перед отклонением.')
    with database.transaction():
        rows = database.execute("SELECT id FROM WebDrafts WHERE admin_id=? AND status IN ('draft','revision')", (admin_id,)).fetchall()
        if len(rows) != expected:
            raise ValueError('Очередь изменилась. Обновите её и подтвердите количество снова.')
        for row in rows:
            review(row[0], admin_id, 'rejected', 'Массовое отклонение из очереди проверки')
    return len(rows)
