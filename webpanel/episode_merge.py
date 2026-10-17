"""Preview and atomically merge published episode records within one season."""
import hashlib
import json
import re
import secrets
from datetime import datetime, timezone

from database.connection import database

MAX_EPISODES = 2000
MAX_ID = 2**53 - 1


class EpisodeMergeConflict(ValueError):
    def __init__(self, preview):
        self.conflicts = preview['conflicts']
        self.preview = preview
        message = self.conflicts[0]['reason'] if self.conflicts else 'Проверьте объединение снова.'
        super().__init__('Объединение недоступно: ' + message)


def _valid_id(value):
    return type(value) is int and 1 <= value <= MAX_ID


def _arguments(movie_number, folder_id, target_id, episode_ids, name):
    if not all(_valid_id(value) for value in (movie_number, folder_id, target_id)):
        raise ValueError('Некорректный идентификатор карточки, сезона или серии.')
    if (not isinstance(episode_ids, list) or not 2 <= len(episode_ids) <= MAX_EPISODES
            or any(not _valid_id(value) for value in episode_ids)):
        raise ValueError(f'Выберите от 2 до {MAX_EPISODES} серий в одном сезоне.')
    if len(set(episode_ids)) != len(episode_ids):
        raise ValueError('Одна серия указана несколько раз.')
    if target_id not in episode_ids:
        raise ValueError('Основная серия должна входить в выбранные серии.')
    if not isinstance(name, str) or not name.strip() or any(char in name for char in '\r\n\0'):
        raise ValueError('Название объединённой серии должно быть непустым и записанным в одной строке.')
    return sorted(episode_ids), name.strip()


def _timestamp_order(value):
    try:
        parsed = datetime.fromisoformat(str(value))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return (0, parsed.timestamp(), str(value))
    except (ValueError, OverflowError, OSError):
        return (1, 0, str(value))


def _plan(movie_number, folder_id, target_id, episode_ids, name):
    ids, name = _arguments(movie_number, folder_id, target_id, episode_ids, name)
    movie = database.execute('SELECT * FROM Movies WHERE movie_number=?', (movie_number,)).fetchone()
    info = database.execute('SELECT movie_title,content_type FROM Movies WHERE movie_number=?', (movie_number,)).fetchone()
    if not info:
        raise ValueError('Карточка не найдена.')
    if info[1] != 'series':
        raise ValueError('Объединять серии можно только у сериалов и мультсериалов.')
    folder = database.execute('SELECT id,movie_number,name,season_number FROM MovieCategories WHERE id=? AND movie_number=?',
                              (folder_id, movie_number)).fetchone()
    if not folder:
        raise ValueError('Выбранный сезон не принадлежит этой карточке.')
    season_rows = database.execute('SELECT id,category_id,name,file_id,file_type FROM MovieSubcategories WHERE category_id=? ORDER BY id',
                                   (folder_id,)).fetchall()
    known = {row[0]: row for row in season_rows}
    if any(episode not in known for episode in ids):
        raise ValueError('Все выбранные серии должны находиться в одном выбранном сезоне.')
    episodes = {episode: {'id': episode, 'category_id': known[episode][1], 'name': known[episode][2],
                         'file_id': known[episode][3], 'file_type': known[episode][4], 'variants': [], 'progress': []}
                for episode in ids}
    for row in database.execute('''SELECT v.id,v.episode_id,v.quality,v.voice,v.file_id,v.file_type
        FROM EpisodeVariants v JOIN MovieSubcategories s ON s.id=v.episode_id
        WHERE s.category_id=? ORDER BY v.id''', (folder_id,)).fetchall():
        if row[1] in episodes:
            episodes[row[1]]['variants'].append({'id': row[0], 'episode_id': row[1], 'quality': row[2],
                'voice': row[3], 'file_id': row[4], 'file_type': row[5]})
    watched = {}
    for user_id, episode_id, watched_at in database.execute('''SELECT p.user_id,p.episode_id,p.watched_at
        FROM EpisodeProgress p JOIN MovieSubcategories s ON s.id=p.episode_id
        WHERE s.category_id=? ORDER BY p.user_id,p.episode_id''', (folder_id,)).fetchall():
        if episode_id in episodes:
            episodes[episode_id]['progress'].append((user_id, watched_at))
            if user_id not in watched or _timestamp_order(watched_at) < _timestamp_order(watched[user_id]):
                watched[user_id] = watched_at
    variants = [variant for episode in episodes.values() for variant in episode['variants']]
    represented = {variant['file_id'] for variant in variants}
    bases, before = [], len(variants)
    for episode in episodes.values():
        if episode['file_id'] and not any(row['file_id'] == episode['file_id'] for row in episode['variants']):
            before += 1
            if episode['file_id'] not in represented:
                bases.append({'id': 0, 'episode_id': episode['id'], 'quality': '', 'voice': '',
                              'file_id': episode['file_id'], 'file_type': episode['file_type']})
    groups = {}
    for choice in variants + bases:
        groups.setdefault((choice['quality'], choice['voice']), []).append(choice)
    conflicts, retained = [], []
    for (quality, voice), selections in groups.items():
        files = {(row['file_id'], row['file_type']) for row in selections}
        if len(files) > 1:
            legacy = not quality and not voice
            conflicts.append({'code': 'legacy_metadata_required' if legacy else 'variant_conflict',
                'quality': quality, 'voice': voice,
                'episodes': [{'id': row['episode_id'], 'name': episodes[row['episode_id']]['name'],
                              'variant_id': row['id']} for row in selections],
                'reason': ('У исходных файлов не указаны качество и озвучка. Уточните их перед объединением.' if legacy
                           else f'Для качества «{quality}» и озвучки «{voice or "Без указания озвучки"}» сохранены разные файлы.')})
        real = [row for row in selections if row['id']]
        if real:
            retained.append(min(real, key=lambda row: (row['episode_id'] != target_id, row['id'])))
    outsiders = [row for row in season_rows if row[0] not in episodes and str(row[2] or '').strip().casefold() == name.casefold()]
    if outsiders:
        conflicts.append({'code': 'name_conflict', 'quality': '', 'voice': '',
            'episodes': [{'id': target_id, 'name': name, 'variant_id': 0}]
                + [{'id': row[0], 'name': row[2], 'variant_id': 0} for row in outsiders],
            'reason': 'В этом сезоне уже есть другая серия с выбранным названием. Выберите её тоже или измените название.'})
    snapshot = {'movie': movie, 'folder': folder, 'target_id': target_id, 'ids': ids, 'name': name,
                'episodes': list(episodes.values()), 'season_names': [(row[0], row[2]) for row in season_rows]}
    token = hashlib.sha256(json.dumps(snapshot, ensure_ascii=False, sort_keys=True).encode('utf-8')).hexdigest()
    after = sum(len({(row['file_id'], row['file_type']) for row in selections}) for selections in groups.values())
    result = {'token': token, 'movie_number': movie_number, 'title': info[0], 'folder_id': folder_id,
              'folder_name': folder[2], 'season_number': folder[3], 'target_id': target_id,
              'episode_ids': ids, 'name': name,
              'episodes': [{'id': episode, 'name': episodes[episode]['name']} for episode in ids],
              'counts': {'episodes_before': len(ids), 'episodes_after': 1, 'variants_before': before,
                         'variants_after': after, 'duplicates': before - after,
                         'watched_users': len(watched),
                         'progress_before': sum(len(episode['progress']) for episode in episodes.values()),
                         'progress_after': len(watched)},
              'conflicts': conflicts, 'can_merge': not conflicts}
    primary = (episodes[target_id]['file_id'], episodes[target_id]['file_type'])
    if bases:
        # One unknown original keeps its real metadata in the legacy primary slot.
        primary = (bases[0]['file_id'], bases[0]['file_type'])
    elif primary[0] and retained:
        represented_primary = next((row for row in retained if row['file_id'] == primary[0]), None)
        if represented_primary:
            primary = (represented_primary['file_id'], represented_primary['file_type'])
    elif not primary[0] and retained:
        primary = (retained[0]['file_id'], retained[0]['file_type'])
    return result, variants, retained, watched, primary


def preview(movie_number, folder_id, target_id, episode_ids, name):
    with database.transaction():
        return _plan(movie_number, folder_id, target_id, episode_ids, name)[0]


def merge(movie_number, folder_id, target_id, episode_ids, name, expected_token):
    if not isinstance(expected_token, str) or not re.fullmatch(r'[a-f0-9]{64}', expected_token):
        raise ValueError('Сначала проверьте объединение выбранных серий.')
    with database.transaction():
        result, variants, retained, watched, primary = _plan(movie_number, folder_id, target_id, episode_ids, name)
        if not secrets.compare_digest(expected_token, result['token']):
            raise ValueError('Серии изменились. Проверьте объединение снова.')
        if result['conflicts']:
            raise EpisodeMergeConflict(result)
        keep = {row['id'] for row in retained}
        for row in variants:
            if row['id'] not in keep:
                database.execute('DELETE FROM EpisodeVariants WHERE id=?', (row['id'],))
        for row in retained:
            if row['episode_id'] != target_id:
                database.execute('UPDATE EpisodeVariants SET episode_id=? WHERE id=?', (target_id, row['id']))
        database.execute('UPDATE MovieSubcategories SET name=?,file_id=?,file_type=? WHERE id=? AND category_id=?',
                         (result['name'], primary[0], primary[1], target_id, folder_id))
        for user_id, watched_at in watched.items():
            database.execute('''INSERT INTO EpisodeProgress(user_id,episode_id,watched_at) VALUES (?,?,?)
                ON CONFLICT(user_id,episode_id) DO UPDATE SET watched_at=excluded.watched_at''',
                             (user_id, target_id, watched_at))
        for episode in result['episode_ids']:
            if episode != target_id:
                database.execute('DELETE FROM MovieSubcategories WHERE id=? AND category_id=?', (episode, folder_id))
    return {**result, 'merged': True}
