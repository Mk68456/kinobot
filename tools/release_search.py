"""Bounded Rutor search, evidence extraction and conservative automatic choice."""

from dataclasses import dataclass, field
from html.parser import HTMLParser
import re
import threading
import urllib.error
import urllib.parse
import urllib.request

try:
    from .release_description import LABELS, assess, parse_description
except ImportError:
    from release_description import LABELS, assess, parse_description


MAX_PAGE = 2 * 1024 * 1024
DEFAULT_SITE = 'https://cat.dark-proxy.ru'


def parse_seasons(value):
    if value == 0 or str(value).strip() == '0':
        return 0
    if isinstance(value, int):
        if 1 <= value <= 99:
            return value
        raise ValueError('Номер сезона должен быть от 1 до 99.')
    if isinstance(value, (list, tuple)):
        seasons = set(value)
        if not seasons or any(type(n) is not int or not 1 <= n <= 99 for n in seasons):
            raise ValueError('Проверьте номера сезонов.')
    else:
        text = str(value).strip().casefold()
        if text in ('все', 'всё', 'all', '*'):
            return 'all'
        seasons = set()
        for part in text.replace('–', '-').replace('—', '-').split(','):
            match = re.fullmatch(r'\s*(\d{1,2})(?:\s*-\s*(\d{1,2}))?\s*', part)
            if not match:
                raise ValueError('Сезоны: 0 — фильм; 1; 1-3; 1,3,5; все.')
            first, last = int(match[1]), int(match[2] or match[1])
            if not 1 <= first <= last <= 99:
                raise ValueError('Проверьте диапазон сезонов: от 1 до 99.')
            seasons.update(range(first, last + 1))
    return next(iter(seasons)) if len(seasons) == 1 else tuple(sorted(seasons))


def season_set(value):
    value = parse_seasons(value)
    return set() if value in (0, 'all') else {value} if isinstance(value, int) else set(value)


def episode_identity(path, fallback_season=None):
    path = str(path).replace('\\', '/')
    match = re.search(r'(?i)(?:S(\d{1,2})E(\d{1,3})|(\d{1,2})x(\d{1,3}))', path)
    if match:
        return (int(match[1]), int(match[2])) if match[1] else (int(match[3]), int(match[4]))
    folder = re.search(r'(?i)(?:^|/)(?:S|Season[ ._-]*|Сезон[ ._-]*)(\d{1,2})(?:/|[ ._-])', path)
    season = int(folder[1]) if folder else fallback_season
    episode = re.search(r'(?i)(?:^|/)(?:серия[ ._-]*|episode[ ._-]*|E)?(\d{1,3})(?:[ ._-]|\.[a-z0-9]+$)', path)
    if season and episode:
        return int(season), int(episode[1])
    return None


@dataclass
class Node:
    tag: str
    attrs: dict = field(default_factory=dict)
    children: list = field(default_factory=list)

    def walk(self):
        yield self
        for child in self.children:
            if isinstance(child, Node):
                yield from child.walk()

    def text(self):
        if self.tag in ('script', 'style'):
            return ''
        content = ''.join(child.text() if isinstance(child, Node) else child for child in self.children)
        if self.tag in ('br', 'p', 'div', 'tr', 'li', 'h1', 'h2', 'h3'):
            return '\n' + content + '\n'
        return ' ' + content + ' ' if self.tag in ('td', 'th') else content


class Page(HTMLParser):
    def __init__(self, html):
        super().__init__(convert_charrefs=True)
        self.root = Node('root')
        self.stack = [self.root]
        self.count = 0
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        self.count += 1
        if self.count > 40_000 or len(self.stack) > 100:
            raise ValueError('Страница слишком сложная для разбора.')
        node = Node(tag, dict(attrs))
        self.stack[-1].children.append(node)
        if tag not in ('area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input', 'link', 'meta', 'param', 'source', 'wbr'):
            self.stack.append(node)

    def handle_endtag(self, tag):
        for index in range(len(self.stack) - 1, 0, -1):
            if self.stack[index].tag == tag:
                del self.stack[index:]
                break

    def handle_data(self, data):
        self.stack[-1].children.append(data)


def clean(text):
    return re.sub(r'\s+', ' ', text).strip()


def search_rows(html, base_url):
    page = Page(html)
    containers = [n for n in page.root.walk() if n.attrs.get('id') in ('index', 'searchresult', 'search-results')]
    if not containers:
        if re.search(r'ничего не найдено|нет результатов|по вашему запросу.*не найдено', page.root.text(), re.I):
            return []
        raise ValueError('Не удалось распознать результаты поиска. Возможно, сайт изменился или показывает защитную страницу.')
    result, seen = [], set()
    for row in containers[0].walk():
        if row.tag != 'tr':
            continue
        link = next((n for n in row.walk() if n.tag == 'a' and
                     re.match(r'(?:https?://[^/]+)?/torrent/\d+(?:/|$)', n.attrs.get('href', ''))), None)
        if not link:
            continue
        url = urllib.parse.urljoin(base_url, link.attrs['href'])
        release_id = re.search(r'/torrent/(\d+)', url)[1]
        if release_id in seen:
            continue
        seen.add(release_id)
        result.append({'id': release_id, 'title': clean(link.text()), 'url': url})
    return result


def release_details(html, row):
    page = Page(html)
    nodes = list(page.root.walk())
    details = next((n for n in nodes if n.attrs.get('id') == 'details'), None)
    description = next((n for n in nodes if n.attrs.get('id') == 'description'), None)
    if description is None and details is not None:
        # Current Rutor puts the description in the first details row, without an id.
        # Inspect only direct rows, never the nested related-release table.
        for tr in details.children:
            if not isinstance(tr, Node) or tr.tag != 'tr':
                continue
            cells = [n for n in tr.children if isinstance(n, Node) and n.tag == 'td']
            if cells and not any(n.attrs.get('class') == 'header' for n in cells):
                possible = max(cells, key=lambda n: len(n.text()))
                if re.search(r'качество\s*:|перевод\s*:|оригинальное название\s*:', possible.text(), re.I):
                    description = possible
                break
    if description is None:
        raise ValueError('Описание раздачи не найдено; автоматическая загрузка остановлена.')
    title_node = next((n for n in nodes if n.tag == 'h1'), None)
    title = clean(title_node.text()) if title_node else row['title']
    text = description.text()
    # Do not mistake MediaInfo track titles, comments or related releases for film metadata.
    text = re.split(r'(?im)^\s*(?:M/I(?:\s|$)|MediaInfo\b|General\s*$|Скриншоты\s*$)', text)[0]
    seeds, category = None, ''
    if details:
        for tr in details.children:
            if not isinstance(tr, Node) or tr.tag != 'tr':
                continue
            cells = [n for n in tr.children if isinstance(n, Node) and n.tag in ('td', 'th')]
            if len(cells) < 2:
                continue
            label, value = clean(cells[0].text()).rstrip(':'), clean(cells[1].text())
            if label.casefold() in ('размер', 'size'):
                text += '\nРазмер: ' + value
            elif label.casefold() == 'раздают' and value.isdigit():
                seeds = int(value)
            elif label.casefold() == 'категория':
                category = value
    parsed = parse_description('Релиз: ' + title + '\n' + text)
    magnets = [n.attrs.get('href', '') for n in nodes if n.tag == 'a' and n.attrs.get('href', '').startswith('magnet:?')]
    magnet = next((m for m in magnets if magnet_hash(m)), '')
    return {**row, 'title': title, 'description': parsed, 'source': magnet, 'seeds': seeds, 'category': category}


def magnet_hash(magnet):
    parsed = urllib.parse.urlsplit(magnet)
    if parsed.scheme != 'magnet' or any(c.isspace() for c in magnet):
        return ''
    for value in urllib.parse.parse_qs(parsed.query).get('xt', []):
        match = re.fullmatch(r'urn:btih:([a-f0-9]{40}|[a-z2-7]{32})', value, re.I)
        if match:
            return match[1].casefold()
    return ''


def normalized(text):
    return ' '.join(re.findall(r'[a-zа-яё0-9]+', text.casefold().replace('ё', 'е')))


def identity_matches(query, candidate):
    parsed = candidate['description']
    titles = [c.value for key in ('title', 'original_title') for c in parsed.facts.get(key, [])]
    title = re.split(r'\[|\((?:19|20)\d{2}|\b(?:WEB|BDRip|BluRay|HDRip|DVDRip)', candidate['title'], maxsplit=1, flags=re.I)[0]
    titles.extend(title.split(' / '))
    if candidate.get('provider') == 'jackett':
        # Jackett providers often return "Series - S01E01 - Episode - 1080p".
        prefix = re.split(r'(?i)(?<!\w)(?:S\d{1,2}(?:E\d{1,3})?|\d{1,2}x\d{1,3})(?!\d)', candidate['title'], maxsplit=1)[0]
        titles.extend(prefix.strip(' ._-[]()').split(' / '))
    return normalized(query) in [normalized(re.sub(r'\s*\((?:19|20)\d{2}\)\s*$', '', t)) for t in titles]


def required_issues(candidate, query, *, year='', season=0, preferred_voice='', max_gib=30, avoid_ads=True, height=1080):
    """Hard user filters, applied both to displayed results and automatic preparation."""
    parsed = candidate['description']
    issues = assess(parsed, preferred_voice, max_gib, avoid_ads)
    if not identity_matches(query, candidate):
        issues.append('Точное совпадение названия не подтверждено')
    years = {m[0] for m in re.finditer(r'(?<!\d)(?:19|20)\d{2}(?!\d)', candidate['title'])}
    years.update(c.value for c in parsed.facts.get('year', []))
    if year and str(year) not in years:
        issues.append('Нужный год не подтверждён')
    season = parse_seasons(season)
    seasons = {int(c.value) for c in parsed.facts.get('season', [])}
    if season:
        if not seasons or (season != 'all' and not seasons.intersection(season_set(season))):
            issues.append('Нужный сезон не подтверждён описанием')
    elif seasons or 'сериал' in candidate.get('category', '').casefold():
        issues.append('Это сериал: укажите нужный сезон')
    if height is not None:
        resolutions = {int(c.value[:-1]) for c in parsed.facts.get('resolution', [])}
        if resolutions != {int(height)}:
            issues.append('Нужное разрешение не подтверждено однозначно')
    if max_gib is not None and len(parsed.facts.get('size', [])) != 1:
        issues.append('Общий размер не подтверждён однозначно')
    return list(dict.fromkeys(issues))


def evaluate(candidate, query, *, year='', season=0, preferred_voice='', max_gib=30, avoid_ads=True, height=1080):
    parsed = candidate['description']
    issues = required_issues(candidate, query, year=year, season=season, preferred_voice=preferred_voice,
                             max_gib=max_gib, avoid_ads=avoid_ads, height=height)
    qualities = parsed.facts.get('quality', [])
    if len(qualities) != 1 or qualities[0].value in ('CAMRIP', 'HDCAM', 'TS', 'TC'):
        issues.append('Источник видео отсутствует, противоречив или является экранной записью')
    if not parsed.facts.get('translation'):
        issues.append('Перевод не указан')
    if not parsed.facts.get('audio'):
        issues.append('Звуковые дорожки не описаны')
    if len(parsed.facts.get('size', [])) != 1:
        issues.append('Общий размер не подтверждён однозначно')
    for key in ('title', 'original_title', 'year', 'video_codec', 'container', 'advertising'):
        if len(parsed.facts.get(key, [])) > 1:
            issues.append('Противоречие: ' + LABELS[key])
    if not (magnet_hash(candidate.get('source', '')) or
            (candidate.get('provider') == 'jackett' and candidate.get('source_available'))):
        issues.append('Ссылка скачивания не найдена')
    if candidate.get('seeds') is None or candidate['seeds'] < 1:
        issues.append('Нет подтверждённых раздающих')
    return list(dict.fromkeys(issues))


def episode_count(description):
    counts = set()
    for claim in description.facts.get('episodes', []):
        match = re.fullmatch(r'(\d+)\s*[-–]\s*(\d+)\s+из\s+(\d+)', claim.value)
        if match:
            if int(match[1]) != 1 or int(match[2]) != int(match[3]):
                return None
            counts.add(int(match[3]))
        else:
            match = re.fullmatch(r'(\d+) \((?:по продолжительности|по списку серий)\)', claim.value)
            if match:
                counts.add(int(match[1]))
    return next(iter(counts)) if len(counts) == 1 and next(iter(counts)) > 0 else None


def rank(candidates, query, **settings):
    rows = [{**c, 'issues': evaluate(c, query, **settings),
             'filter_issues': required_issues(c, query, **settings),
             'identity_matches': identity_matches(query, c)} for c in candidates]
    if not settings.get('year') and not settings.get('season'):
        years = set()
        for candidate in rows:
            if not candidate['filter_issues']:
                years.update(re.findall(r'(?<!\d)(?:19|20)\d{2}(?!\d)', candidate['title']))
        if len(years) > 1:
            for row in rows:
                if not row['filter_issues']:
                    row['issues'].append('Найдены разные годы выпуска; укажите год для автовыбора')
    return sorted(rows, key=lambda c: (bool(c['issues']), len(c['issues']), -(c.get('seeds') or 0),
                                       c['description'].size_bytes or 10**18, c['id']))


def header_rejection(row, query, settings):
    """Skip only explicit contradictions in search titles; unknown fields need a detail page."""
    parsed = parse_description('Релиз: ' + row['title'])
    candidate = {**row, 'description': parsed}
    if not identity_matches(query, candidate):
        return 'Название не совпадает'
    season = parse_seasons(settings.get('season', 0))
    seasons = {int(c.value) for c in parsed.facts.get('season', [])}
    if seasons and (not season or (season != 'all' and not seasons.intersection(season_set(season)))):
        return 'Другой сезон'
    year = str(settings.get('year') or '')
    years = set(re.findall(r'(?<!\d)(?:19|20)\d{2}(?!\d)', row['title']))
    if year and years and year not in years:
        return 'Другой год выпуска'
    heights = {int(c.value[:-1]) for c in parsed.facts.get('resolution', [])}
    height = settings.get('height')
    if height is not None and heights and heights != {int(height)}:
        return 'Другое разрешение'
    return ''


def has_next_page(html, current, query):
    for node in Page(html).root.walk():
        if node.tag != 'a':
            continue
        path = urllib.parse.unquote(urllib.parse.urlsplit(node.attrs.get('href', '')).path)
        match = re.fullmatch(r'/search/(\d+)/0/000/0/(.+?)/?', path)
        if match and int(match[1]) == current + 1 and match[2].rstrip('/') == query:
            return True
    return False


class _Redirects(urllib.request.HTTPRedirectHandler):
    def __init__(self, allowed):
        self.allowed = allowed

    def redirect_request(self, request, fp, code, msg, headers, newurl):
        target = urllib.parse.urlsplit(newurl)
        if target.scheme not in ('http', 'https') or target.netloc not in self.allowed or target.username or target.password:
            raise ValueError('Сайт перенаправил запрос на неподтверждённый адрес.')
        return super().redirect_request(request, fp, code, msg, headers, newurl)


class RutorClient:
    def __init__(self, base_url=DEFAULT_SITE, *, stop=None, transport=None):
        parts = urllib.parse.urlsplit(base_url.strip())
        if parts.scheme not in ('http', 'https') or not parts.hostname or parts.username or parts.password or parts.query or parts.fragment or parts.path not in ('', '/'):
            raise ValueError('Укажите адрес сайта без пути, логина и параметров, например https://rutor.info')
        self.base = urllib.parse.urlunsplit((parts.scheme, parts.netloc, '', '', ''))
        self.allowed = {parts.netloc}
        if parts.hostname in ('rutor.info', 'rutor.is', 'www.rutor.info', 'www.rutor.is'):
            self.allowed.update(('rutor.info', 'rutor.is', 'www.rutor.info', 'www.rutor.is'))
        self.stop = stop if stop is not None else threading.Event()
        self.transport = transport
        self.opener = urllib.request.build_opener(_Redirects(self.allowed))
        self.stats = {}

    def get(self, url):
        if self.stop.is_set():
            raise InterruptedError('Поиск остановлен.')
        parts = urllib.parse.urlsplit(url)
        if parts.netloc not in self.allowed or parts.scheme not in ('http', 'https') or parts.username or parts.password:
            raise ValueError('Ссылка ведёт за пределы выбранного сайта.')
        if self.transport:
            return self.transport(url)
        headers = {'User-Agent': 'Mozilla/5.0 KinoTime/1.4.3',
                   'Accept': 'text/html', 'Accept-Encoding': 'identity'}
        # Public routing header used by the Mozilla-hosted "Rutor" extension.
        # It belongs only to this mirror; never attach it to arbitrary addresses.
        if parts.hostname == 'cat.dark-proxy.ru' and parts.scheme == 'https':
            headers['X-Proxy-Access'] = 'rutor'
        request = urllib.request.Request(url, headers=headers)
        try:
            with self.opener.open(request, timeout=15) as response:
                if response.headers.get_content_type() not in ('text/html', 'application/xhtml+xml'):
                    raise ValueError('Сайт вернул страницу неизвестного формата.')
                data = response.read(MAX_PAGE + 1)
                if len(data) > MAX_PAGE:
                    raise ValueError('Страница превышает лимит размера.')
                return data.decode(response.headers.get_content_charset() or 'utf-8', errors='replace')
        except (urllib.error.URLError, OSError) as error:
            raise ValueError('Не удалось подключиться к сайту. Проверьте адрес и доступ к нему из сети компьютера. '
                             f'Причина: {type(error).__name__}.') from error

    def search(self, query, report=lambda text: None, limit=None, *, settings=None, max_pages=10):
        query = query.strip()
        if not 3 <= len(query) <= 120:
            raise ValueError('Введите название длиной от 3 до 120 символов.')
        limit = min(1000, max(1, int(limit))) if limit is not None else None
        max_pages = min(20, max(1, int(max_pages)))
        self.stats = {'pages': 0, 'scanned': 0, 'descriptions': 0, 'prefiltered': 0, 'limit_reached': False}
        report('Ищу раздачи по названию…')
        candidates, errors, seen = [], [], set()
        for page_number in range(max_pages):
            if self.stop.is_set():
                raise InterruptedError('Поиск остановлен.')
            try:
                html = self.get(self.base + f'/search/{page_number}/0/000/0/' + urllib.parse.quote(query, safe=''))
                rows = search_rows(html, self.base)
            except ValueError as error:
                if page_number == 0:
                    raise
                errors.append(f'Страница {page_number + 1}: {error}')
                break
            self.stats['pages'] += 1
            for row in rows:
                if row['id'] in seen:
                    continue
                seen.add(row['id'])
                self.stats['scanned'] += 1
                if settings is not None:
                    reason = header_rejection(row, query, settings)
                    if reason:
                        self.stats['prefiltered'] += 1
                        candidates.append({**row, 'description': parse_description('Релиз: ' + row['title']),
                                           'source': '', 'seeds': None, 'category': '', 'header_rejection': reason})
                        continue
                if self.stop.wait(0.6):
                    raise InterruptedError('Поиск остановлен.')
                report(f'Страница {page_number + 1}: читаю описание {self.stats["descriptions"] + 1}; '
                       f'проверено названий {self.stats["scanned"]}: {row["title"][:70]}')
                self.stats['descriptions'] += 1
                try:
                    candidates.append(release_details(self.get(row['url']), row))
                except InterruptedError:
                    raise
                except ValueError as error:
                    errors.append(row['title'] + ': ' + str(error))
                if limit is not None and self.stats['descriptions'] >= limit:
                    self.stats['limit_reached'] = True
                    return candidates, errors
            more = has_next_page(html, page_number, query)
            if not more:
                break
            if page_number + 1 == max_pages:
                self.stats['limit_reached'] = True
            elif self.stop.wait(0.6):
                raise InterruptedError('Поиск остановлен.')
        return candidates, errors


def auto_file_ids(files, *, season=0, expected_count=None):
    """Select complete episode files or one movie; ambiguity stops before downloading."""
    try:
        from .torrent_queue import VIDEO_EXTENSIONS
    except ImportError:
        from torrent_queue import VIDEO_EXTENSIONS
    videos = [f for f in files if '.' + f['name'].rsplit('.', 1)[-1].casefold() in VIDEO_EXTENSIONS]
    videos = [f for f in videos if not re.search(r'(?:^|[/\\ ._-])(?:sample|trailer|семпл|трейлер)(?:$|[/\\ ._-])', f['name'], re.I)]
    season = parse_seasons(season)
    if not season:
        if len(videos) != 1:
            raise ValueError('В раздаче несколько видео или нет фильма. Нужен ручной выбор файлов.')
        return [videos[0]['index']]
    episodes = {}
    requested = season_set(season)
    fallback = next(iter(requested)) if len(requested) == 1 else None
    for file in videos:
        if re.search(r'(?i)E\d{1,3}(?:E|[-–]E?)\d', file['name']):
            raise ValueError('Несколько серий в одном файле. Нужен ручной выбор файлов.')
        identity = episode_identity(file['name'], fallback)
        if not identity:
            raise ValueError('Номера серий в именах не распознаны. Нужен ручной выбор файлов.')
        sn, en = identity
        if requested and sn not in requested:
            continue
        if identity in episodes:
            raise ValueError('Найдены повторные варианты серий. Нужен ручной выбор файлов.')
        episodes[identity] = file['index']
    if not episodes:
        raise ValueError('В раздаче нет файлов выбранных сезонов.')
    if expected_count is not None and len(episodes) != expected_count:
        raise ValueError('Число файлов не совпадает с заявленным составом сезона. Нужен ручной выбор файлов.')
    return [episodes[n] for n in sorted(episodes)]


def automatic_candidates(rows, season):
    eligible = [row for row in rows if not row['issues']]
    if not eligible:
        raise ValueError('Нет раздач, прошедших проверку автовыбора.')
    season = parse_seasons(season)
    if not season:
        return [(eligible[0], 0)]
    target = season_set(season)
    if season == 'all':
        target = {int(c.value) for row in rows if row.get('identity_matches', True)
                  for c in row['description'].facts.get('season', [])}
    selected = []
    remaining = set(target)
    for row in eligible:
        available = {int(c.value) for c in row['description'].facts.get('season', [])}
        if available.intersection(remaining):
            selected.append((row, tuple(sorted(available.intersection(remaining)))))
            remaining.difference_update(available)
        if not remaining:
            return selected
    raise ValueError('Нет подходящих раздач для сезонов: ' + ', '.join(map(str, sorted(remaining))))
