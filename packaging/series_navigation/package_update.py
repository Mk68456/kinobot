"""Package the bot navigation patch without user databases or launcher binaries."""
import hashlib
import json
from pathlib import Path
import re
import zipfile

ROOT = Path(__file__).resolve().parents[2]
FILES = ('handlers/users/find_movie.py', 'database/episode_variants.py')
CHECKS = ('tests/test_episode_variant_choices.py', 'tests/test_series_navigation_handlers.py',
          'tests/test_handlers.py')


def main():
    log = (ROOT / 'outputs/series-navigation-regressions.log').read_text(encoding='utf-8')
    checked = re.search(r'Ran (\d+) tests in [^\n]+\n\s*\nOK\b', log)
    if not checked:
        raise RuntimeError('Complete the full regression suite successfully before packaging.')
    target = ROOT / 'KinoTime-Series-Navigation-2026-10-08.zip'
    records = {}
    with zipfile.ZipFile(target, 'w', zipfile.ZIP_DEFLATED) as archive:
        for prefix, files in (('Server', FILES), ('Checks', CHECKS)):
            for name in files:
                path = f'{prefix}/{name}'
                records[path] = hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
                archive.write(ROOT / name, path)
        archive.write(ROOT / 'docs/SERIES_NAVIGATION_2026_10_08.md', 'INSTALL.md')
        archive.writestr('Verification/VALIDATION.txt',
                        f'Full Python regression suite: {checked[1]} tests passed.\n'
                        'Separate quality and voice selection; exact per-message next/previous parameters.\n'
                        'Legacy buttons, missing variants, media preservation and subscription checks verified.\n'
                        'Concurrent duplicate send and retry after error verified.\n'
                        'In-memory databases and mocked Telegram API only; no live uploads or publishing.\n')
        archive.writestr('FILES-SHA256.json', json.dumps(records, indent=2))
    with zipfile.ZipFile(target) as archive:
        if archive.testzip() is not None:
            raise RuntimeError('ZIP CRC verification failed.')
        for name, digest in records.items():
            if hashlib.sha256(archive.read(name)).hexdigest() != digest:
                raise RuntimeError(f'Archive content mismatch: {name}')
        if any(name.endswith(('.db', '.sqlite', '.env', '.sqlite-wal', '.sqlite-shm'))
               for name in archive.namelist()):
            raise RuntimeError('User data must not be packaged.')
    digest = hashlib.sha256(target.read_bytes()).hexdigest()
    target.with_suffix('.zip.sha256').write_text(digest + '  ' + target.name + '\n', encoding='ascii')
    print(f'{target.name}: {len(FILES)} server files; {target.stat().st_size} bytes; hashes and CRC OK')


if __name__ == '__main__':
    main()
