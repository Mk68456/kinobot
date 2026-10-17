"""Quality variants share one episode ID and watched mark."""
import re
from dataclasses import dataclass

from database.connection import database
from database.categories import add_movie_subcategory


@dataclass(frozen=True)
class EpisodeChoice:
    id: int
    quality: str
    voice: str
    file_id: str
    file_type: str | None


@dataclass(frozen=True)
class QualityGroup:
    quality: str
    label: str
    choices: tuple[EpisodeChoice, ...]


def choices(episode_id):
    """Return stored fields without decoding the legacy combined button label."""
    episode = database.execute('SELECT file_id,file_type FROM MovieSubcategories WHERE id=?', (episode_id,)).fetchone()
    if not episode:
        return []
    rows = database.execute('SELECT id,quality,voice,file_id,file_type FROM EpisodeVariants WHERE episode_id=? ORDER BY id',
                            (episode_id,)).fetchall()
    result = [EpisodeChoice(int(row[0]), row[1], row[2], row[3], row[4]) for row in rows]
    if episode[0] and not any(choice.file_id == episode[0] for choice in result):
        result.append(EpisodeChoice(0, '', '', episode[0], episode[1]))
    return result


def _quality_order(quality):
    if quality == 'source':
        return (0, 0, ())
    resolution = re.fullmatch(r'(\d+)[pi]', quality, re.IGNORECASE)
    if resolution:
        return (1, -int(resolution[1]), ((1, quality.casefold()),))
    if not quality:
        return (3, 0, ())
    natural = tuple((0, int(part)) if part.isdigit() else (1, part.casefold())
                    for part in re.split(r'(\d+)', quality))
    return (2, 0, natural)


def quality_groups(episode_id):
    """Group exact quality keys; the original source never aliases a resolution."""
    grouped = {}
    for choice in choices(episode_id):
        grouped.setdefault(choice.quality, []).append(choice)
    return [QualityGroup(quality, 'Исходное качество' if quality == 'source' else quality or 'Исходный файл',
                         tuple(grouped[quality])) for quality in sorted(grouped, key=_quality_order)]


def add_series_file(category_id, item, file):
    rows = database.execute('SELECT id,name FROM MovieSubcategories WHERE category_id=?', (category_id,)).fetchall()
    existing = next((r[0] for r in rows if r[1].casefold() == item['name'].casefold()), None)
    quality, voice = item.get('quality', ''), item.get('voice', '')
    if existing and not quality:
        raise ValueError('Серия уже есть. Для дополнительного варианта укажите качество.')
    episode_id = existing or add_movie_subcategory(category_id, item['name'], file['file_id'], file['file_type'])
    if quality:
        if database.execute('SELECT 1 FROM EpisodeVariants WHERE episode_id=? AND quality=? AND voice=?',
                            (episode_id, quality, voice)).fetchone():
            raise ValueError('У серии уже есть это качество и озвучка.')
        database.execute('INSERT INTO EpisodeVariants (episode_id,quality,voice,file_id,file_type) VALUES (?,?,?,?,?)',
                         (episode_id, quality, voice, file['file_id'], file['file_type']))
    return episode_id


def variants(episode_id):
    episode = database.execute('SELECT file_id,file_type FROM MovieSubcategories WHERE id=?', (episode_id,)).fetchone()
    if not episode:
        return []
    rows = database.execute('SELECT id,quality,voice,file_id,file_type FROM EpisodeVariants WHERE episode_id=? ORDER BY id',
                            (episode_id,)).fetchall()
    result = [(r[0], ('Исходное качество' if r[1] == 'source' else r[1]) + (' · ' + r[2] if r[2] else ''), r[3], r[4]) for r in rows]
    if episode[0] and not any(r[2] == episode[0] for r in result):
        result.append((0, 'Исходный файл', episode[0], episode[1]))
    return result
