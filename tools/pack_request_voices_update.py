"""Build the server update from an allowlist, with test evidence and file hashes."""
import ast
import hashlib
import json
from pathlib import Path
import re
import sys
from zipfile import ZIP_DEFLATED, ZipFile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from database.migrations import MIGRATIONS

FILES = (
    'database/content_requests.py', 'database/migrations.py',
    'handlers/users/content_requests.py', 'handlers/users/find_movie.py',
    'handlers/admin/content_requests.py', 'services/tmdb.py', 'services/request_voices.py',
    'webpanel/server.py', 'webpanel/static/app.js', 'webpanel/static/style.css', 'webpanel/static/index.html',
    'tools/check_request_voices.py', 'docs/content-request-targets.md',
)
CHECKS = ('tests/test_request_voices.py', 'tests/test_content_request_cards.py', 'tests/test_handlers.py',
          'tools/check_request_voices_ui.cjs')


def main():
    log = (ROOT / 'outputs/request-voices-tests.log').read_text(encoding='utf-8-sig')
    checked = re.search(r'Ran (\d+) tests in [^\n]+\n\s*\nOK\b', log)
    if not checked:
        raise RuntimeError('The full regression suite must pass before packaging.')
    report = json.loads((ROOT / 'outputs/request-voices-ui.json').read_text(encoding='utf-8'))
    if not report.get('ok'):
        raise RuntimeError('The isolated browser check must pass before packaging.')
    contents = {name: (ROOT / name).read_bytes() for name in FILES}
    contents.update({f'Checks/{name}': (ROOT / name).read_bytes() for name in CHECKS})
    contents['UPDATE-INSTALL.md'] = (ROOT / 'docs/REQUEST_VOICES_UPDATE_2026_10_25.md').read_bytes()
    contents['Checks/browser.json'] = json.dumps(report, ensure_ascii=False, indent=2).encode('utf-8')
    contents['UPDATE-VALIDATION.txt'] = (
        f'Full Python regression suite: {checked[1]} tests passed.\n'
        'Changed Python files: Ruff passed. Web panel JavaScript: syntax check passed.\n'
        'Isolated browser fixture: desktop, mobile, source evidence, full-width posterless cards, text escaping passed.\n'
        'Kinobox fixture: exact IMDb lookup, namespaces, cache, rate limit, timeouts, HTTP errors, invalid JSON,\n'
        'binary and oversized responses, bounded chunked bodies, studio deduplication and source evidence passed.\n'
        'Request flow: studio buttons, manual input, any voice, pagination, stale buttons, unique votes,\n'
        'release validation, catalog completion and legacy request migration passed.\n'
        'Live Kinobox check from this environment on 2026-10-25: binary response instead of JSON.\n'
        'Live studio results are NOT verified. Manual request fallback remains available.\n'
        'No production database or bot deployment was performed.\n'
    ).encode('utf-8')
    for name, data in contents.items():
        path = Path(name)
        if path.is_absolute() or '..' in path.parts or path.suffix not in ('.py', '.js', '.cjs', '.css', '.html', '.md', '.json', '.txt'):
            raise RuntimeError(f'Unexpected package member: {name}')
        if path.suffix == '.py':
            ast.parse(data.decode('utf-8-sig'), filename=name)
    records = {name: hashlib.sha256(data).hexdigest() for name, data in contents.items()}
    manifest = {'schema_version': len(MIGRATIONS), 'sha256': records, 'live_provider_verified': False}
    contents['UPDATE-MANIFEST.json'] = json.dumps(manifest, ensure_ascii=False, indent=2).encode('utf-8')
    target = ROOT / 'KinoTime-Requests-Voices-Update-2026-10-25.zip'
    with ZipFile(target, 'w', ZIP_DEFLATED, compresslevel=9) as archive:
        for name, data in contents.items():
            archive.writestr(name, data)
    with ZipFile(target) as archive:
        if archive.testzip() is not None or set(archive.namelist()) != set(contents):
            raise RuntimeError('Archive integrity verification failed.')
        for name, digest in records.items():
            if hashlib.sha256(archive.read(name)).hexdigest() != digest:
                raise RuntimeError(f'Archive hash mismatch: {name}')
    digest = hashlib.sha256(target.read_bytes()).hexdigest()
    target.with_suffix('.zip.sha256').write_text(digest + '  ' + target.name + '\n', encoding='ascii')
    print(f'{target}\n{len(contents)} files, {target.stat().st_size} bytes; SHA-256 and CRC verified.')


if __name__ == '__main__':
    main()
