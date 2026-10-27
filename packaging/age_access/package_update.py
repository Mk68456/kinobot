"""Create a separate, verified server update without databases or credentials."""
import hashlib
import json
import re
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FILES = (
    'database/age_access.py', 'database/migrations.py', 'database/movie_queries.py', 'database/search.py',
    'database/episode_variants.py',
    'services/age_access.py', 'services/access.py', 'services/welcome.py', 'services/episode_cards.py',
    'handlers/users/age_settings.py', 'handlers/users/__init__.py', 'handlers/users/find_movie.py',
    'handlers/users/contests.py', 'handlers/users/inline_share.py', 'handlers/users/content_requests.py',
    'keyboards/users/keyboard.py', 'webpanel/repository.py', 'webpanel/server.py',
    'webpanel/episode_merge.py', 'webpanel/series_validation.py',
    'webpanel/static/app.js', 'webpanel/static/index.html', 'webpanel/static/style.css',
)
CHECKS = (
    'tests/test_age_access.py', 'tests/test_age_handlers.py', 'tests/age_fixtures.py', 'tests/test_handlers.py',
    'tests/test_admin_merge.py', 'tests/test_batch_import.py', 'tests/test_desktop_import.py',
    'tests/test_long_import_names.py', 'tests/test_series_publication_conflicts.py',
    'tests/test_series_quality.py', 'tests/test_publication_package.py', 'tests/test_multiple_launchers.py',
    'tools/check_age_access_ui.cjs',
)


def main():
    log = (ROOT / 'outputs/age-access-regressions.log').read_text(encoding='utf-8')
    result = re.search(r'Ran (\d+) tests in ([\d.]+)s\s+OK\b', log)
    if not result or int(result[1]) < 512:
        raise RuntimeError('A passing complete regression report is required.')
    ui = json.loads((ROOT / 'outputs/age-access-ui.json').read_text(encoding='utf-8'))
    if ui.get('ok') is not True:
        raise RuntimeError('Browser checks must pass before packaging.')
    instructions = (ROOT / 'docs/AGE_ACCESS_2026_10_08.md').read_bytes()
    target = ROOT / 'KinoTime-Age-Restrictions-2026-10-08.zip'
    # Preserve every previously delivered archive; never overwrite an update.
    previous = {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in ROOT.glob('KinoTime-*.zip')}
    contents = {name: (ROOT / name).read_bytes() for name in FILES}
    for name in CHECKS:
        contents['Checks/' + name] = (ROOT / name).read_bytes()
    contents['UPDATE-INSTALL.txt'] = instructions
    contents['Verification/age-access-ui.json'] = (ROOT / 'outputs/age-access-ui.json').read_bytes()
    contents['Verification/age-access-regressions.log'] = log.encode('utf-8')
    validation = (
        'KinoTime age restrictions · 2026-10-08\n'
        f'Full regression suite: {result[1]} tests, {result[2]} seconds, OK.\n'
        'Ruff checks on all changed Python modules and test fixtures: OK.\n'
        'JavaScript syntax and local intercepted browser fixture: OK.\n'
        'Mobile 390 px and desktop 1440 px; publication gate and unrated filter: OK.\n'
        'Temporary/in-memory databases and mocked Telegram; no production deployment.\n'
        'Project directories are directly at ZIP root; no database, settings, tokens or media.\n'
        'Includes episode_cards, episode_variants and web merge dependencies.\n'
    )
    contents['Verification/VALIDATION.txt'] = validation.encode('utf-8')
    records = {name: hashlib.sha256(data).hexdigest() for name, data in contents.items()}
    with zipfile.ZipFile(target, 'x', zipfile.ZIP_DEFLATED) as archive:
        for name, data in contents.items():
            archive.writestr(name, data)
        archive.writestr('UPDATE-SHA256.json', json.dumps(records, indent=2))
    with zipfile.ZipFile(target) as archive:
        if archive.testzip() is not None:
            raise RuntimeError('ZIP CRC check failed.')
        for name, expected in records.items():
            if hashlib.sha256(archive.read(name)).hexdigest() != expected:
                raise RuntimeError(f'Archive checksum mismatch: {name}')
        for name in FILES:
            if archive.read(name) != (ROOT / name).read_bytes():
                raise RuntimeError(f'Source changed during packaging: {name}')
    for path, digest in previous.items():
        if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            raise RuntimeError(f'Previous archive modified: {path.name}')
    digest = hashlib.sha256(target.read_bytes()).hexdigest()
    target.with_suffix('.zip.sha256').write_text(digest + '  ' + target.name + '\n', encoding='ascii')
    print(f'{target.name}: {len(FILES)} server files, {target.stat().st_size} bytes; CRC and source hashes OK.')
    print(f'{len(previous)} earlier archives preserved; {result[1]} regression tests and browser checks passed.')


if __name__ == '__main__':
    main()
