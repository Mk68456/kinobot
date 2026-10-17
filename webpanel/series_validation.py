"""Report the exact rows that repeat an episode's quality/voice variant."""
from database.connection import database


class SeriesVariantConflict(ValueError):
    def __init__(self, conflicts):
        self.conflicts = conflicts
        first = conflicts[0]
        rows = ', '.join(map(str, first['rows']))
        origin = ('совпадает с уже опубликованным вариантом' if first['existing'] else
                  'повторяют один вариант в этом черновике')
        message = (f"{first['season']} · «{first['name']}» · {first['quality']} · "
                   f"{first['voice'] or 'озвучка не указана'}: строки {rows} {origin}. "
                   'Совпадают сезон, название серии, качество и озвучка. '
                   'Проверьте эти строки: исправьте неверные поля или уберите повтор из черновика.')
        if len(conflicts) > 1:
            message += f' Всего конфликтующих вариантов: {len(conflicts)}.'
        super().__init__(message)


def variant_conflicts(body):
    """The body is normalized. A file path is not part of the catalog identity."""
    if body['content_type'] != 'series':
        return []
    individual = any(item.get('season_number') for item in body['items'])
    groups = {}
    for index, item in enumerate(body['items'], 1):
        if not item['quality']:
            continue  # Legacy files without variants keep their existing validation.
        number = item.get('season_number') or body['season_number']
        season_key = ('number', number) if individual else ('category', body['category_id'])
        season = f'Сезон {number}' if number else body['season_name'] or 'Выбранный сезон'
        key = (season_key, item['name'].casefold(), item['quality'], item['voice'])
        group = groups.setdefault(key, {'season': season, 'name': item['name'],
            'quality': item['quality'], 'voice': item['voice'], 'rows': [], 'asset_ids': [], 'existing': False})
        group['rows'].append(index)
        group['asset_ids'].append(item['asset_id'])

    if body['movie_number'] and (individual or body['category_id']):
        for category, number, season, name, quality, voice in database.execute('''
            SELECT c.id,c.season_number,c.name,s.name,v.quality,v.voice
            FROM MovieCategories c JOIN MovieSubcategories s ON s.category_id=c.id
            JOIN EpisodeVariants v ON v.episode_id=s.id WHERE c.movie_number=?
        ''', (body['movie_number'],)).fetchall():
            season_key = ('number', number) if individual else ('category', category)
            group = groups.get((season_key, name.casefold(), quality, voice))
            if group is not None:
                group['existing'] = True
                group['season'] = season
    return [group for group in groups.values() if group['existing'] or len(group['rows']) > 1]
