"""Local computer identity and exact content hashes; no credentials are saved."""
import hashlib
import json
import os
import re
import socket
import uuid
from pathlib import Path


def valid_instance(value):
    return isinstance(value, str) and bool(re.fullmatch(r'[A-Za-z0-9_-]{8,100}', value))


def machine_key():
    parts = [socket.gethostname(), os.environ.get('USERNAME', '')]
    if os.name == 'nt':
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r'SOFTWARE\Microsoft\Cryptography',
                                0, winreg.KEY_READ | winreg.KEY_WOW64_64KEY) as key:
                parts.append(str(winreg.QueryValueEx(key, 'MachineGuid')[0]))
        except OSError:
            pass
    return hashlib.sha256('\0'.join(parts).encode()).hexdigest()


def identity(saved):
    key = machine_key()
    instance = saved.get('instance_id', '')
    copied = saved.get('machine_key') not in (None, key)
    return {'instance_id': uuid.uuid4().hex if copied or not valid_instance(instance) else instance,
            'machine_key': key,
            'name': socket.gethostname()[:80] if copied else str(saved.get('name') or socket.gethostname())[:80]}


def hash_manifest(manifest, report=lambda message: None):
    result = {**manifest, 'files': [dict(entry) for entry in manifest['files']]}
    root = Path(manifest['root']).resolve()
    cache_file = root / '.kinotime-sha256.json'
    try:
        cache = json.loads(cache_file.read_text(encoding='utf-8'))
        if not isinstance(cache, dict):
            cache = {}
    except (OSError, ValueError):
        cache = {}
    for index, entry in enumerate(result['files'], 1):
        path = root / entry['path']
        if path.is_symlink() or root not in path.resolve().parents:
            raise ValueError('Исходный файл находится за пределами папки.')
        before = path.stat()
        signature = [before.st_size, str(before.st_mtime_ns)]
        if signature != [entry['size'], entry['mtime']]:
            raise ValueError(f'{path.name}: файл изменился после подготовки.')
        saved = cache.get(entry['path'], {})
        if not isinstance(saved, dict):
            saved = {}
        digest = saved.get('digest', '')
        if saved.get('signature') != signature or not isinstance(digest, str) or not re.fullmatch(r'[a-f0-9]{64}', digest):
            report(f'Проверка файла {index}/{len(result["files"])}: {path.name}')
            with path.open('rb') as stream:
                digest = hashlib.file_digest(stream, 'sha256').hexdigest()
            after = path.stat()
            if [after.st_size, str(after.st_mtime_ns)] != signature:
                raise ValueError(f'{path.name}: файл изменился при проверке.')
            cache[entry['path']] = {'signature': signature, 'digest': digest}
        entry['content_sha256'] = digest
    temp = cache_file.with_name(cache_file.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        temp.write_text(json.dumps(cache, ensure_ascii=False), encoding='utf-8')
        temp.replace(cache_file)
    except OSError:
        temp.unlink(missing_ok=True)
    return result
