"""Conservative, offline extraction of release claims with source evidence.

Claims describe the supplied text, never verified media. No network or model calls.
"""

from dataclasses import dataclass, field
from html.parser import HTMLParser
import re


LABELS = {
    'title': 'Название', 'original_title': 'Оригинальное название', 'year': 'Год',
    'season': 'Сезон', 'episodes': 'Серии (заявлено)', 'quality': 'Источник видео',
    'resolution': 'Формат изображения', 'dimensions': 'Размер кадра',
    'video_codec': 'Видеокодек', 'container': 'Контейнер',
    'translation': 'Перевод / озвучка', 'audio': 'Звуковые дорожки (описание)',
    'size': 'Размер всей раздачи', 'advertising': 'Реклама',
}
MAX_TEXT = 150_000


class _Text(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.hidden = 0

    def handle_starttag(self, tag, attrs):
        if tag in ('script', 'style'):
            self.hidden += 1
        if not self.hidden and tag in ('br', 'p', 'div', 'tr', 'li', 'h1', 'h2', 'h3'):
            self.parts.append('\n')
        if not self.hidden and tag in ('td', 'th'):
            self.parts.append(' ')

    def handle_endtag(self, tag):
        if tag in ('script', 'style'):
            self.hidden = max(0, self.hidden - 1)
        if not self.hidden and tag in ('p', 'div', 'tr', 'li', 'h1', 'h2', 'h3'):
            self.parts.append('\n')

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)


def plain_text(text):
    if not isinstance(text, str) or not text.strip():
        raise ValueError('Вставьте текст описания раздачи.')
    if len(text) > MAX_TEXT:
        raise ValueError('Описание слишком большое: максимум 150 000 символов.')
    if re.search(r'<(?:html|body|div|p|br|table|b|strong|span|script)\b', text, re.I):
        parser = _Text()
        parser.feed(text)
        text = ''.join(parser.parts)
    text = re.sub(r'\[/?(?:b|i|u|color|size|url|quote|spoiler)(?:=[^\]]*)?\]', '', text, flags=re.I)
    text = '\n'.join(re.sub(r'[^\S\n]+', ' ', line).strip()
                     for line in text.replace('\r', '\n').splitlines() if line.strip())
    # Copied HTML tables often place labels and their values on separate lines.
    text = re.sub(r'(?m)^([^\n:：]{1,45}[:：])[ \t]*\n(?![^\n:：]{1,45}[:：])', r'\1 ', text)
    if not text.strip():
        raise ValueError('В описании нет текста для разбора.')
    return text


@dataclass
class Claim:
    value: str
    evidence: list[str] = field(default_factory=list)


@dataclass
class Description:
    text: str
    facts: dict[str, list[Claim]] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    size_bytes: int | None = None

    def add(self, key, value, evidence):
        value = value.strip(' \t:;')
        if not value:
            return
        claims = self.facts.setdefault(key, [])
        existing = next((claim for claim in claims if claim.value.casefold() == value.casefold()), None)
        if existing is None:
            existing = Claim(value)
            claims.append(existing)
        if evidence not in existing.evidence:
            existing.evidence.append(evidence)

    def value(self, key):
        return ' / '.join(claim.value for claim in self.facts.get(key, [])) or 'Не указано'

    def report(self):
        lines = ['Сведения из описания. Параметры файлов ещё не проверены.', '']
        for key, label in LABELS.items():
            lines.append(f'{label}: {self.value(key)}')
            for claim in self.facts.get(key, []):
                for evidence in claim.evidence:
                    lines.append(f'  Основание: {evidence}')
        lines.extend(['', 'Требует внимания:'])
        lines.extend('• ' + warning for warning in self.warnings)
        return '\n'.join(lines)


def parse_description(text):
    result = Description(plain_text(text))
    lines = result.text.splitlines()
    translation_continuation = False
    episode_lines = []
    in_episode_list = False
    for line in lines:
        if translation_continuation and re.match(r'^(?:профессиональн|любительск|дублирован|авторск)', line, re.I) and ':' not in line:
            result.add('translation', line, line)
        else:
            translation_continuation = False
        if re.fullmatch(r'список (?:серий|эпизодов)', line, re.I):
            in_episode_list = True
        elif in_episode_list:
            if re.match(r'^\d{1,3}[.)]\s+\S', line):
                episode_lines.append(line)
            else:
                in_episode_list = False
        for key, label in (
            ('original_title', r'оригинальное название|original title'),
            ('title', r'название(?: фильма| сериала)?|русское название|title'),
            ('year', r'год(?: выхода| выпуска)?|year'),
            ('translation', r'перевод|озвучка|озвучивание'),
            ('audio', r'звук|аудио(?:дорожк[аи])?|audio(?: track)?|звуковая дорожка'),
        ):
            language = r'(?:\s+(?:rus|eng|русский|английский|ukr|jpn))?' if key == 'audio' else ''
            match = re.match(rf'^(?:{label}){language}\s*(?:[#№]?\s*\d+)?\s*[:：—–-]\s*(.+)$', line, re.I)
            if match:
                result.add(key, match[1], line)
                if key == 'translation':
                    translation_continuation = True

        # Only technical lines and release headings can supply video properties.
        technical = bool(re.match(r'^(?:качество|видео|video|формат|format|контейнер|релиз|раздача|скачать)\b',
                                  line, re.I)) or line == lines[0]
        if technical:
            for match in re.finditer(r'\b(WEB[ ._-]?DL(?:Rip)?|WEB[ ._-]?Rip|BDRip|BRRip|Blu[ ._-]?Ray|'
                                     r'BDRemux|HDRip|DVDRip|HDTVRip|HDTV|CAMRip|HDCAM|TS|TC)\b', line, re.I):
                quality = re.sub(r'[ ._-]', '', match[0]).upper()
                result.add('quality', quality, line)
            for match in re.finditer(r'(?<!\d)(2160|1440|1080|720|576|480|360)[piр]\b', line, re.I):
                result.add('resolution', match[0].lower().replace('р', 'p'), line)
            for match in re.finditer(r'(?<!\d)([1-9]\d{2,3})\s*[xх×]\s*([1-9]\d{2,3})(?!\d)', line, re.I):
                # Dimensions alone do not establish progressive/interlaced scan.
                result.add('dimensions', match[1] + '×' + match[2], line)
            for codec, pattern in (
                ('H.264 / AVC', r'\b(?:[hx][ ._-]?264|AVC)\b'),
                ('H.265 / HEVC', r'\b(?:[hx][ ._-]?265|HEVC)\b'),
                ('AV1', r'\bAV1\b'), ('Xvid', r'\bXvid\b'),
            ):
                if re.search(pattern, line, re.I):
                    result.add('video_codec', codec, line)
            for container, pattern in (('MKV', r'\b(?:Matroska|MKV)\b'),
                                       ('MP4', r'\bMP4\b'), ('AVI', r'\bAVI\b')):
                if re.search(pattern, line, re.I):
                    result.add('container', container, line)

        for match in re.finditer(r'(?<![a-z0-9])S(\d{1,2})(?=E\d|\b|[._ -])', line, re.I):
            result.add('season', str(int(match[1])), line)
        if technical:
            match = re.search(r'(?i)\bS(\d{1,2})[-–](\d{1,2})\b', line)
            if match:
                first, last = int(match[1]), int(match[2])
                for number in range(first, last + 1):
                    result.add('season', str(number), line)
        match = re.search(r'\bсезон\s*[:：#№-]?\s*(\d{1,2})(?!\d)', line, re.I)
        if not match:
            match = re.search(r'(?<!\d)(\d{1,2})[- ]?(?:й|ый)?\s+сезон\b', line, re.I)
        if match:
            result.add('season', str(int(match[1])), line)
        if technical:
            for match in re.finditer(r'(?<!\d)(\d{1,2})[xх](\d{1,3})(?:[-–]\d{1,3})?', line, re.I):
                result.add('season', str(int(match[1])), line)
            match = re.search(r'\[(?:\d{1,2}[xх])?(\d{1,3}\s*[-–]\s*\d{1,3}\s+из\s+\d{1,3})\]', line, re.I)
            if match:
                result.add('episodes', match[1], line)
        if re.match(r'^(?:серии|эпизоды|episodes)\s*[:：]', line, re.I):
            result.add('episodes', re.split(r'[:：]', line, maxsplit=1)[1], line)
        match = re.search(r'\bпродолжительность\s*:\s*(\d+)\s*[xх×]', line, re.I)
        if match:
            result.add('episodes', match[1] + ' (по продолжительности)', line)

        if re.match(r'^(?:размер(?: раздачи| файлов)?|общий размер|size|вес)\s*[:：—-]?\s*\d', line, re.I):
            match = re.search(r'(\d+(?:[.,]\d+)?)\s*(GiB|MiB|TiB|GB|MB|TB|ГиБ|МиБ|ТиБ|ГБ|МБ|ТБ)\b', line, re.I)
            if match:
                amount = float(match[1].replace(',', '.'))
                unit = match[2].casefold()
                binary = unit in ('gib', 'mib', 'tib', 'гиб', 'миб', 'тиб')
                power = 3 if unit.startswith(('g', 'г')) else 2 if unit.startswith(('m', 'м')) else 4
                size = round(amount * (1024 if binary else 1000) ** power)
                exact = re.search(r'\((\d+)\s*(?:bytes|байт(?:а|ов)?)\)', line, re.I)
                if exact:
                    size = int(exact[1])
                if result.size_bytes is not None and result.size_bytes != size:
                    result.warnings.append('Указаны разные размеры раздачи. Лимит размера нельзя проверить уверенно.')
                else:
                    result.size_bytes = size
                result.add('size', match[0] + (f' ({size} байт)' if exact else ''), line)

        # Negative and positive claims are both retained to expose contradictions.
        if re.search(r'\bреклам[аыау]\b', line, re.I):
            negative = re.search(r'без рекламы|реклам[аы]\s*[:：—-]?\s*(?:нет|отсутствует)|'
                                 r'(?:нет|отсутствует)\s+реклам[аы]', line, re.I)
            positive = re.search(r'(?:присутствует|есть|содержит)\s+реклам[ауы]|'
                                 r'реклама\s*[:：—-]?\s*(?:есть|присутствует|да)', line, re.I)
            if negative:
                result.add('advertising', 'Заявлено отсутствие', line)
            if positive:
                result.add('advertising', 'Заявлено наличие', line)

    if episode_lines:
        numbers = [int(re.match(r'\d+', line)[0]) for line in episode_lines]
        if numbers == list(range(1, len(numbers) + 1)):
            result.add('episodes', str(len(numbers)) + ' (по списку серий)',
                       'Список серий:\n' + '\n'.join(episode_lines)[:4000])
    for key in ('title', 'original_title', 'year', 'season', 'quality', 'resolution', 'dimensions', 'video_codec', 'container',
                'advertising'):
        if len(result.facts.get(key, [])) > 1:
            result.warnings.append(f'{LABELS[key]}: несколько значений; проверьте, относятся ли они к одному варианту.')
    heights = {int(c.value[:-1]) for c in result.facts.get('resolution', [])}
    dimensions = {int(c.value.split('×')[1]) for c in result.facts.get('dimensions', [])}
    if heights and dimensions and heights != dimensions:
        result.warnings.append('Формат изображения и размер кадра различаются; возможны обрезка кадра или ошибка описания.')
    for key in ('title', 'quality', 'resolution', 'translation', 'audio', 'size', 'advertising'):
        if key not in result.facts:
            result.warnings.append(f'{LABELS[key]}: не удалось определить.')
    if 'advertising' in result.facts and any(c.value == 'Заявлено наличие' for c in result.facts['advertising']):
        result.warnings.append('В описании заявлена реклама.')
    if 'season' in result.facts or 'episodes' in result.facts:
        result.warnings.append('Состав сезона и номера серий нужно сверить со списком файлов.')
    result.warnings.append('Название студии озвучки и отсутствие рекламы не подтверждены проверкой файла.')
    return result


def assess(description, preferred_voice='', max_gib=None, avoid_ads=True):
    """Return explicit issues, not a guessed confidence percentage or auto approval."""
    issues = []
    if preferred_voice.strip():
        voices = description.facts.get('translation', [])
        if not voices:
            issues.append('Нужная озвучка не подтверждена')
        elif not any(preferred_voice.strip().casefold() in c.value.casefold() for c in voices):
            issues.append('Нужная озвучка не найдена в поле перевода')
    if max_gib is not None:
        if description.size_bytes is None or len(description.facts.get('size', [])) > 1:
            issues.append('Размер требует проверки')
        elif description.size_bytes > max_gib * 1024 ** 3:
            issues.append('Размер выше лимита исходника')
    if avoid_ads:
        ads = description.facts.get('advertising', [])
        if any(c.value == 'Заявлено наличие' for c in ads):
            issues.append('Заявлена реклама')
        elif not ads:
            issues.append('Отсутствие рекламы не подтверждено')
    return issues
