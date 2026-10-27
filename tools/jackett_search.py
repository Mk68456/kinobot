"""Local Jackett integration. Credentials and proxy download links stay out of journals."""

import hashlib
import json
import os
import re
import secrets
import shutil
import socket
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

try:
    from .launcher_runtime import resource_root, state_path
    from .release_description import parse_description, plain_text
    from .release_search import magnet_hash
except ImportError:
    from launcher_runtime import resource_root, state_path
    from release_description import parse_description, plain_text
    from release_search import magnet_hash


JACKETT_VERSION = '0.24.2910'
DEFAULT_URL = 'http://127.0.0.1:9118'
_START_LOCK = threading.Lock()


def local_url(value):
    parts = urllib.parse.urlsplit(value.strip())
    if (parts.scheme != 'http' or parts.hostname not in ('127.0.0.1', 'localhost', '::1')
            or parts.username or parts.password or parts.query or parts.fragment or parts.path not in ('', '/')
            or not parts.port):
        raise ValueError('Адрес Jackett должен быть локальным, например http://127.0.0.1:9118')
    return value.strip().rstrip('/')


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def torrent_hash(data):
    """Hash the original bencoded info slice; reject HTML, truncated and excessive trees."""
    count = 0

    def read(pos, depth=0):
        nonlocal count
        count += 1
        if depth > 60 or count > 300_000 or pos >= len(data):
            raise ValueError('Некорректный .torrent от Jackett.')
        start = pos
        token = data[pos:pos + 1]
        if token in (b'd', b'l'):
            pos += 1
            items = []
            while pos < len(data) and data[pos:pos + 1] != b'e':
                item, pos = read(pos, depth + 1)
                items.append(item)
            if pos >= len(data) or (token == b'd' and len(items) % 2):
                raise ValueError('Неполный .torrent от Jackett.')
            return (start, pos + 1, items), pos + 1
        if token == b'i':
            end = data.find(b'e', pos)
            if end < 0 or not re.fullmatch(rb'-?\d{1,20}', data[pos + 1:end]):
                raise ValueError('Некорректное число в .torrent.')
            return (start, end + 1, None), end + 1
        match = re.match(rb'(\d{1,8}):', data[pos:pos + 10])
        if not match:
            raise ValueError('Jackett вернул не .torrent файл.')
        begin = pos + match.end()
        end = begin + int(match[1])
        if end > len(data):
            raise ValueError('Неполный .torrent от Jackett.')
        return (start, end, data[begin:end]), end

    node, end = read(0)
    if data[:1] != b'd' or end != len(data):
        raise ValueError('Jackett вернул не .torrent файл.')
    items = node[2]
    info = [items[i + 1] for i in range(0, len(items), 2) if items[i][2] == b'info']
    if len(info) != 1 or data[info[0][0]:info[0][0] + 1] != b'd':
        raise ValueError('В .torrent нет единственного словаря info.')
    return hashlib.sha1(data[info[0][0]:info[0][1]]).hexdigest()


class JackettClient:
    def __init__(self, url=DEFAULT_URL, api_key='', *, stop=None):
        self.url = local_url(url)
        self.api_key = api_key.strip()
        if not re.fullmatch(r'[a-zA-Z0-9]{16,128}', self.api_key):
            raise ValueError('Укажите полный API-ключ Jackett или запустите встроенный Jackett.')
        self.stop = stop if stop is not None else threading.Event()
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())

    def request(self, path, params=None, *, download=False):
        if self.stop.is_set():
            raise InterruptedError('Поиск остановлен.')
        url = self.url + path
        if params is not None:
            url += '?' + urllib.parse.urlencode({**params, 'apikey': self.api_key})
        try:
            with self.opener.open(urllib.request.Request(url, headers={'User-Agent': 'KinoTime'}), timeout=45) as response:
                raw = response.read(10 * 1024**2 + 1)
                if len(raw) > 10 * 1024**2:
                    raise ValueError('Ответ Jackett превышает 10 МБ.')
        except urllib.error.HTTPError as error:
            location = error.headers.get('Location', '')
            if download and error.code in (301, 302, 303, 307, 308) and magnet_hash(location):
                return location
            if error.code in (401, 403):
                raise ValueError('Jackett не принял API-ключ.') from None
            raise ValueError(f'Jackett: HTTP {error.code}. Проверьте источник в настройках Jackett.') from None
        except OSError:
            raise ValueError('Jackett недоступен. Запустите его и проверьте локальный адрес.') from None
        if self.stop.is_set():
            raise InterruptedError('Поиск остановлен.')
        return raw

    def indexers(self):
        raw = self.request('/api/v2.0/indexers/all/results/torznab/api', {'t': 'indexers', 'configured': 'true'})
        try:
            if b'<!DOCTYPE' in raw.upper() or b'<!ENTITY' in raw.upper():
                raise ValueError()
            root = ET.fromstring(raw)
            if root.tag != 'indexers':
                raise ValueError()
            return [{'id': node.attrib['id'], 'name': node.findtext('title') or node.attrib['id']}
                    for node in root.findall('indexer')
                    if node.attrib.get('configured', '').lower() == 'true'
                    and re.fullmatch(r'[a-zA-Z0-9_-]{1,100}', node.attrib.get('id', ''))]
        except (ET.ParseError, KeyError, ValueError):
            raise ValueError('Не удалось получить источники. Проверьте API-ключ Jackett и настройку трекеров.') from None

    def download_url(self, value):
        parts = urllib.parse.urlsplit(value)
        if (parts.scheme not in ('http', 'https') or parts.netloc != urllib.parse.urlsplit(self.url).netloc
                or parts.scheme != 'http' or parts.username or parts.password or parts.fragment
                or not re.fullmatch(r'/dl/[a-zA-Z0-9_-]+/?', parts.path)):
            return ''
        return parts.path + ('?' + parts.query if parts.query else '')

    def search(self, query, indexers, report=lambda message: None):
        rows, errors = [], []
        self.stats = {'pages': 0, 'scanned': 0, 'limit_reached': False}
        for source in indexers:
            if not re.fullmatch(r'[a-zA-Z0-9_-]{1,100}', source['id']):
                raise ValueError('Некорректный идентификатор источника Jackett.')
            report('Jackett: поиск в ' + source['name'] + '…')
            try:
                raw = self.request('/api/v2.0/indexers/' + source['id'] + '/results', {'Query': query})
                payload = json.loads(raw)
                if not isinstance(payload, dict) or not isinstance(payload.get('Results'), list):
                    raise ValueError('Не удалось прочитать ответ поиска Jackett.')
                self.stats['pages'] += 1
                for status in payload.get('Indexers', []):
                    if status.get('Status') not in (2, 'OK'):
                        errors.append(source['name'] + ': ошибка трекера; проверьте его в Jackett.')
                releases = payload['Results']
                if len(releases) > 1000:
                    self.stats['limit_reached'] = True
                for entry in releases[:1000]:
                    self.stats['scanned'] += 1
                    try:
                        rows.append(self.candidate(entry, source))
                    except (ValueError, TypeError, KeyError):
                        errors.append(source['name'] + ': не удалось разобрать одну из раздач.')
            except (ValueError, TypeError, KeyError) as error:
                # Never show raw backend exceptions: they may contain tracker passkeys or API URLs.
                errors.append(source['name'] + ': ' + (str(error) if type(error) is ValueError
                              and str(error).startswith(('Jackett', 'Не удалось')) else 'ошибка чтения ответа.'))
        return rows, errors

    def candidate(self, entry, source):
        title = entry['Title']
        if not isinstance(title, str) or not title.strip() or len(title) > 2000:
            raise ValueError('Некорректное название.')
        description = plain_text(entry['Description']) if entry.get('Description') else ''
        parsed = parse_description('Релиз: ' + title + '\n' + description)
        if source['id'] == 'lostfilm':
            parsed.add('translation', 'LostFilm', 'Источник раздачи: LostFilm.tv через Jackett')
        size = entry.get('Size')
        if type(size) is int and size > 0:
            # Search metadata reports total bytes; retain conflicting claims for strict filtering.
            if parsed.size_bytes is None:
                parsed.size_bytes = size
                parsed.add('size', f'{size / 1024**3:.2f} ГиБ', 'Jackett: ' + str(size) + ' байт')
            elif abs(parsed.size_bytes - size) > max(1024**2, size * .02):
                parsed.add('size', f'{size / 1024**3:.2f} ГиБ (Jackett)', 'Jackett: ' + str(size) + ' байт')
        link = entry.get('Link') or ''
        magnet = entry.get('MagnetUri') or (link if magnet_hash(link) else '')
        download = self.download_url(link)
        # Prefer original .torrent for authenticated trackers: it preserves the announce/passkey.
        source_value = '' if download else magnet if magnet_hash(magnet) else ''
        identity = str(entry.get('Guid') or entry.get('Details') or title)
        release_id = 'jackett-' + source['id'] + '-' + hashlib.sha256(identity.encode()).hexdigest()[:24]
        details = urllib.parse.urlsplit(entry.get('Details') or '')
        page = urllib.parse.urlunsplit((details.scheme, details.netloc, details.path, '', '')) if details.scheme in ('http', 'https') else ''
        seeds = entry.get('Seeders')
        return {'id': release_id, 'title': title, 'url': page, 'description': parsed,
                'source': source_value, 'download_path': download, 'source_available': bool(download or source_value),
                'provider': 'jackett', 'tracker': source['name'], 'tracker_id': source['id'],
                'info_hash': entry.get('InfoHash') or magnet_hash(magnet),
                'seeds': seeds if type(seeds) is int and seeds >= 0 else None,
                'category': entry.get('CategoryDesc') or ''}

    def materialize(self, candidate):
        if candidate.get('source') and magnet_hash(candidate['source']):
            return dict(candidate)
        path = candidate.get('download_path') or ''
        if not self.download_url(self.url + path):
            raise ValueError('Нет ссылки скачивания через Jackett.')
        data = self.request(path, download=True)
        if isinstance(data, str):
            return {**candidate, 'source': data, 'info_hash': magnet_hash(data)}
        digest = torrent_hash(data)
        directory = state_path('jackett-torrents', __file__)
        directory.mkdir(exist_ok=True)
        target = directory / (hashlib.sha256(data).hexdigest() + '.torrent')
        temporary = target.with_suffix('.tmp-' + secrets.token_hex(5))
        try:
            temporary.write_bytes(data)
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)
        return {**candidate, 'source': str(target), 'info_hash': digest}


def source_identity(candidate):
    value = candidate.get('info_hash') or magnet_hash(candidate.get('source', ''))
    return value.casefold() if re.fullmatch(r'[a-fA-F0-9]{40}|[a-zA-Z2-7]{32}', value or '') else ''


def merge_sources(rows):
    """Keep alternatives with different descriptions; collapse only equal hashes and parsed facts."""
    result, seen = [], {}
    for row in rows:
        parsed = row['description']
        facts = tuple((key, tuple(c.value for c in claims)) for key, claims in sorted(parsed.facts.items()))
        identity = source_identity(row)
        key = (identity, facts) if identity else (row['id'],)
        if key in seen:
            previous = seen[key]
            previous['tracker'] = ', '.join(dict.fromkeys((previous.get('tracker', 'Rutor') + ', ' + row.get('tracker', 'Rutor')).split(', ')))
            continue
        copy = {**row, 'tracker': row.get('tracker', 'Rutor')}
        seen[key] = copy
        result.append(copy)
    return result


def managed_connection():
    config = state_path('jackett-data', __file__) / 'ServerConfig.json'
    try:
        values = json.loads(config.read_text('utf-8-sig'))
        return DEFAULT_URL, values['APIKey']
    except (OSError, ValueError, KeyError):
        return DEFAULT_URL, ''


def start_managed(stop=None):
    """Run the bundled console privately with isolated settings, never install a service."""
    stop = stop if stop is not None else threading.Event()
    with _START_LOCK:
        url, key = managed_connection()
        if key:
            try:
                JackettClient(url, key, stop=stop).indexers()
                return url, key
            except ValueError:
                pass
        with socket.socket() as probe:
            if probe.connect_ex(('127.0.0.1', 9118)) == 0:
                raise ValueError('Порт 9118 занят. Подключите существующий Jackett по его адресу и API-ключу.')
        source = resource_root() / 'jackett'
        if not (source / 'JackettConsole.exe').is_file():
            raise ValueError('В этой сборке нет встроенного Jackett. Обновите полный EXE или подключите установленный Jackett.')
        runtime = state_path('jackett-runtime', __file__) / JACKETT_VERSION
        if not (runtime / '.ready').is_file():
            runtime.mkdir(parents=True, exist_ok=True)
            shutil.copytree(source, runtime, dirs_exist_ok=True)
            (runtime / '.ready').write_text(JACKETT_VERSION, 'utf-8')
        data = state_path('jackett-data', __file__)
        data.mkdir(exist_ok=True)
        config = data / 'ServerConfig.json'
        if not config.exists():
            config.write_text(json.dumps({'Port': 9118, 'LocalBindAddress': '127.0.0.1', 'AllowExternal': False,
                                         'APIKey': secrets.token_hex(16), 'UpdateDisabled': True}), 'utf-8')
        if stop.is_set():
            raise InterruptedError('Запуск Jackett остановлен.')
        log = data / 'launcher-start.log'
        with log.open('ab') as output:
            process = subprocess.Popen([str(runtime / 'JackettConsole.exe'), '--DataFolder', str(data),
                                        '--Port', '9118', '--ListenPrivate', '--NoUpdates', '--NoRestart'],
                                       cwd=runtime.parent, stdin=subprocess.DEVNULL, stdout=output, stderr=output,
                                       creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        deadline = time.monotonic() + 40
        while time.monotonic() < deadline:
            if stop.wait(.5):
                process.terminate()
                raise InterruptedError('Запуск Jackett остановлен.')
            if process.poll() is not None:
                raise ValueError('Встроенный Jackett не запустился. Подробности в jackett-data/launcher-start.log.')
            url, key = managed_connection()
            try:
                JackettClient(url, key, stop=stop).indexers()
                return url, key
            except ValueError:
                continue
        process.terminate()
        raise ValueError('Jackett не ответил вовремя. Проверьте jackett-data/launcher-start.log.')
