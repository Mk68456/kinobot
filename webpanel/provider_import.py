"""Review-only provider drafts; link exact unique catalogue titles, never publish."""

from database.connection import database
from webpanel import imports


def title_key(value):
    return ' '.join(str(value).split()).casefold()


def create(admin_id, manifest, instance_id):
    if (not isinstance(manifest, dict) or manifest.get('content_type') not in {'movie', 'series'}
            or not isinstance(manifest.get('files'), list) or len(manifest['files']) != 1
            or not str(manifest.get('root', '')).startswith('yougen/')):
        raise ValueError('Импорт источника принимает один файл на задание.')
    kind = manifest['content_type']
    title = manifest.get('title')
    season = manifest.get('season_number')
    if (not isinstance(title, str) or not title.strip()
            or kind == 'series' and (type(season) is not int or not 1 <= season <= 999)):
        raise ValueError('Укажите точное название сериала и номер сезона.')
    with database.transaction():
        rows = [row for row in database.execute(
            'SELECT movie_number,movie_title,catalog_section FROM Movies WHERE content_type=?',
            (kind,)).fetchall() if title_key(row[1]) == title_key(title)]
        explicit = manifest.get('movie_number')
        if explicit is not None:
            if type(explicit) is not int or not any(r[0] == explicit for r in rows):
                raise ValueError('Выбранная карточка не соответствует названию сериала.')
            rows = [r for r in rows if r[0] == explicit]
        if len(rows) > 1:
            raise ValueError('Несколько сериалов имеют это название. Выберите карточку вручную.')
        data = dict(manifest)
        linked = bool(rows)
        if linked:
            number, canonical, section = rows[0]
            seasons = database.execute('SELECT id,season_number FROM MovieCategories '
                                       'WHERE movie_number=? AND (season_number=? OR name=?)',
                                       (number, season, f'Сезон {season}')).fetchall() if kind == 'series' else []
            if len(seasons) > 1 or (seasons and seasons[0][1] != season):
                raise ValueError('Номер сезона в каталоге неоднозначен. Проверьте существующий сезон.')
            data.update(movie_number=number, title=canonical,
                        catalog_section=section or ('series' if kind == 'series' else 'movies'), new_series=False)
        else:
            data.update(movie_number=None, catalog_section='series' if kind == 'series' else 'movies', new_series=True)
        current = imports.create(admin_id, data, instance_id, allow_existing_season=True)
        if current['status'] != 'draft':
            raise ValueError('Черновик уже обработан. Проверьте его вручную, повторная передача остановлена.')
        # Never create a catalogue row or alter an already-published season here.
        return current, linked
