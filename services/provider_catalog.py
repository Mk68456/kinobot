"""Verified provider variants become ordinary catalogue folders, never overwrite files."""
import hashlib
import json
import re

from database.connection import database
from webpanel import provider_import, repository


def identity(payload, variant=False):
    fields = ('content_type', 'title', 'year') + (('season', 'episode', 'voice', 'quality') if variant else ())
    values = [' '.join(str(payload.get(k, '')).split()).casefold() for k in fields]
    return hashlib.sha256(json.dumps(values, ensure_ascii=False).encode()).hexdigest()


def policy():
    row = database.execute('SELECT automatic,age_rating FROM ProviderPolicy WHERE id=1').fetchone()
    return (bool(row[0]), row[1]) if row else (False, '')


def set_policy(automatic, age=''):
    if type(automatic) is not bool or age not in ('', '0+', '6+', '12+', '16+', '18+'):
        raise ValueError('Укажите возраст: 0+, 6+, 12+, 16+ или 18+.')
    with database.transaction():
        database.execute('INSERT OR REPLACE INTO ProviderPolicy VALUES (1,?,?)', (int(automatic), age))


def title_number(payload):
    row = database.execute('SELECT movie_number FROM ProviderTitles WHERE identity=?', (identity(payload),)).fetchone()
    if row:
        return row[0]
    title, year = payload['title'], str(payload.get('year') or '')
    if not re.fullmatch(r'(19|20)\d{2}', year):
        raise ValueError('Для автоматического распределения нужен подтверждённый год.')
    canonical = provider_import.title_key(f'{title} ({year})')
    rows = [r[0] for r in database.execute('SELECT movie_number,movie_title FROM Movies WHERE content_type=?',
                                          (payload['content_type'],)) if provider_import.title_key(r[1]) == canonical]
    if len(rows) > 1:
        raise ValueError('Несколько карточек совпадают по названию и году.')
    if not rows and any(provider_import.title_key(r[0]) == provider_import.title_key(title)
                        for r in database.execute('SELECT movie_title FROM Movies WHERE content_type=?', (payload['content_type'],))):
        raise ValueError('Есть старая карточка без года. Привяжите её командой /provider_link перед импортом.')
    return rows[0] if rows else None


def link(number, payload):
    row = database.execute('SELECT movie_title,content_type FROM Movies WHERE movie_number=?', (number,)).fetchone()
    if not row or row[1] != payload['content_type'] or provider_import.title_key(
            re.sub(r'\s*\((?:19|20)\d{2}\)$', '', row[0])) != provider_import.title_key(payload['title']):
        raise ValueError('Название или тип существующей карточки не совпадают.')
    year = re.search(r'\(((?:19|20)\d{2})\)$', row[0])
    if year and year[1] != str(payload['year']):
        raise ValueError('Год существующей карточки не совпадает.')
    with database.transaction():
        database.execute('INSERT OR REPLACE INTO ProviderTitles VALUES (?,?)', (identity(payload), number))


def reconcile(draft_id, body):
    if not database.execute("SELECT 1 FROM sqlite_master WHERE name='ProviderFiles'").fetchone():
        return body
    row = database.execute('SELECT payload FROM ProviderFiles WHERE draft_id=?', (draft_id,)).fetchone()
    if row:
        payload = json.loads(row[0])
        number = title_number(payload)
        if number:
            movie = database.execute('SELECT movie_title,catalog_section FROM Movies WHERE movie_number=?', (number,)).fetchone()
            body = {**body, 'movie_number': number, 'title': movie[0], 'catalog_section': movie[1]}
        else:
            body = {**body, 'title': f"{payload['title']} ({payload['year']})"}
        if payload['content_type'] == 'movie' and len(body['items']) == 1:
            category, name = movie_labels(number, payload)
            body = {**body, 'items': [{**body['items'][0], 'category': category, 'name': name,
                                      'voice': payload['voice'], 'quality': payload['quality']}]}
    return body


def variant_exists(payload):
    number = title_number(payload)
    if not number:
        return False
    if payload['content_type'] == 'movie':
        rows = database.execute('SELECT c.name,s.name FROM MovieCategories c JOIN MovieSubcategories s ON s.category_id=c.id '
                                'WHERE c.movie_number=? AND s.file_id IS NOT NULL', (number,)).fetchall()
        return any((provider_import.title_key(c), provider_import.title_key(n)) in {
            (provider_import.title_key(payload['voice']), payload['quality']),
            (payload['quality'], provider_import.title_key(payload['voice']))} for c, n in rows)
    from database.categories import episode_order
    rows = database.execute('SELECT s.id,s.name,v.quality,v.voice FROM MovieCategories c '
        'JOIN MovieSubcategories s ON s.category_id=c.id JOIN EpisodeVariants v ON v.episode_id=s.id '
        'WHERE c.movie_number=? AND c.season_number=?', (number, payload['season'])).fetchall()
    return any(episode_order(r)[:2] == (0, payload['episode']) and r[2] == payload['quality']
               and provider_import.title_key(r[3]) == provider_import.title_key(payload['voice']) for r in rows)


def movie_labels(number, payload):
    from tools.yougen_bridge import VOICES
    categories = [provider_import.title_key(r[0]) for r in database.execute(
        'SELECT name FROM MovieCategories WHERE movie_number=?', (number,))] if number else []
    qualities = any(re.fullmatch(r'(?:240|360|480|720|1080|1440|2160)p', name) for name in categories)
    voices = any(name in {provider_import.title_key(v) for v in VOICES} for name in categories)
    if qualities and voices:
        raise ValueError('В фильме смешаны папки качеств и озвучек. Проверьте структуру вручную.')
    return (payload['quality'], payload['voice']) if qualities else (payload['voice'], payload['quality'])


def published(draft_id, number):
    if not database.execute("SELECT 1 FROM sqlite_master WHERE name='ProviderFiles'").fetchone():
        return
    row = database.execute('SELECT payload FROM ProviderFiles WHERE draft_id=?', (draft_id,)).fetchone()
    if row:
        database.execute('INSERT OR REPLACE INTO ProviderTitles VALUES (?,?)', (identity(json.loads(row[0])), number))
        database.execute('UPDATE ProviderFiles SET publish_error=NULL WHERE draft_id=?', (draft_id,))


def prepare(admin_id, spec, instance_id, payload):
    key = identity(payload, True)
    with database.transaction():
        old = database.execute('SELECT admin_id,draft_id FROM ProviderFiles WHERE identity=?', (key,)).fetchone()
        if old:
            if old[0] != admin_id:
                raise ValueError('Этот вариант уже обрабатывает другой администратор.')
            current = repository.draft(old[1], admin_id)
            if not current or current['status'] not in ('draft', 'published'):
                raise ValueError('Предыдущий вариант требует проверки.')
            return current, bool(current['body']['movie_number'] or current['movie_number'])
        # Use the year in the lookup; equal titles of different remakes never share a card.
        canonical = f"{payload['title']} ({payload.get('year', '')})"
        number = title_number(payload)
        selected = {**spec, 'title': canonical}
        if number:
            row = database.execute('SELECT movie_title FROM Movies WHERE movie_number=?', (number,)).fetchone()
            if not row:
                raise ValueError('Привязанная карточка удалена.')
            selected.update(title=row[0], movie_number=number)
            if payload['content_type'] == 'series':
                seasons = database.execute('SELECT season_number FROM MovieCategories WHERE movie_number=?', (number,)).fetchall()
                if any(s[0] is None for s in seasons) and not any(s[0] == payload['season'] for s in seasons):
                    raise ValueError('Есть сезоны без номера. Укажите их номера перед автоматическим дополнением.')
        current, linked = provider_import.create(admin_id, selected, instance_id)
        body = current['body']
        if not linked and isinstance(payload.get('metadata'), dict):
            body.update(description=str(payload['metadata'].get('description') or '')[:2500],
                        poster_url=str(payload['metadata'].get('poster_url') or '')[:1000])
        if number and payload['content_type'] == 'series':
            from database.categories import episode_order
            rows = database.execute('SELECT s.id,s.name FROM MovieSubcategories s JOIN MovieCategories c ON c.id=s.category_id '
                                    'WHERE c.movie_number=? AND c.season_number=?', (number, payload['season'])).fetchall()
            names = [r[1] for r in rows if episode_order(r)[:2] == (0, payload['episode'])]
            if len(names) > 1:
                raise ValueError('Несколько серий имеют один номер. Проверьте существующую структуру.')
            if not names and any(episode_order(r)[0] != 0 for r in rows):
                raise ValueError('Есть серии без распознаваемого номера. Проверьте названия перед добавлением.')
            if names:
                body['items'][0]['name'] = names[0]
        if payload['content_type'] == 'movie':
            category, name = movie_labels(number, payload)
            body['items'][0].update(category=category, name=name,
                                    voice=payload['voice'], quality=payload['quality'])
        current = repository.save_draft(admin_id, body, current['id'])
        database.execute('INSERT INTO ProviderFiles(identity,admin_id,draft_id,asset_id,payload) VALUES (?,?,?,?,?)',
                         (key, admin_id, current['id'], body['items'][0]['asset_id'], json.dumps(payload)))
        return current, linked


def publish(draft_id, admin_id, *, automatic=False):
    """Re-link at publication time: earlier variants may have created the card meanwhile."""
    with database.transaction():
        row = database.execute('SELECT payload FROM ProviderFiles WHERE draft_id=? AND admin_id=?',
                               (draft_id, admin_id)).fetchone()
        if not row:
            raise ValueError('Не найдено подтверждение метаданных источника.')
        payload = json.loads(row[0])
        current = repository.draft(draft_id, admin_id)
        if not current:
            raise ValueError('Черновик удалён или недоступен.')
        if current['status'] == 'published':
            return current['movie_number']
        active, age = policy()
        if automatic and not active:
            return None
        number = title_number(payload)
        body = current['body']
        if number:
            row = database.execute('SELECT movie_title,catalog_section FROM Movies WHERE movie_number=?', (number,)).fetchone()
            body.update(movie_number=number, title=row[0], catalog_section=row[1])
        elif automatic:
            if not age:
                raise ValueError('Новой карточке нужна возрастная маркировка: откройте черновик.')
            body['age_rating'] = age
        repository.save_draft(admin_id, body, draft_id)
        number = repository.publish(draft_id, admin_id)
        database.execute('INSERT OR REPLACE INTO ProviderTitles VALUES (?,?)', (identity(payload), number))
        return number
