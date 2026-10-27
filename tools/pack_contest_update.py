"""Build a patch archive from an explicit allowlist; never include a database or config."""
import hashlib
import json
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile


ROOT = Path(__file__).resolve().parents[1]
FILES = (
    'database/contests.py', 'database/migrations.py', 'database/people.py',
    'handlers/start.py', 'handlers/users/__init__.py', 'handlers/users/contests.py',
    'handlers/users/find_movie.py', 'keyboards/users/keyboard.py', 'keyboards/inline/sub_keyboard.py',
    'services/contests.py', 'services/welcome.py', 'utils/set_bot_commands.py',
    'webpanel/contests.py', 'webpanel/server.py', 'webpanel/static/index.html',
    'webpanel/static/app.js', 'webpanel/static/contests.js', 'webpanel/static/style.css',
    'tests/test_contests.py', 'tests/test_handlers.py',
)


def main():
    archive = ROOT / 'kinobot-referral-contest-update.zip'
    hashes = {}
    with ZipFile(archive, 'w', compression=ZIP_DEFLATED, compresslevel=9) as package:
        for filename in FILES:
            path = (ROOT / filename).resolve()
            if ROOT not in path.parents or path.suffix not in ('.py', '.js', '.css', '.html'):
                raise ValueError(f'Unexpected release path: {filename}')
            data = path.read_bytes()
            hashes[filename] = hashlib.sha256(data).hexdigest()
            package.writestr(filename, data)
        data = (ROOT / 'docs/REFERRAL_CONTEST.md').read_bytes()
        package.writestr('INSTALL.md', data)
        hashes['INSTALL.md'] = hashlib.sha256(data).hexdigest()
        package.writestr('MANIFEST.json', json.dumps({'schema_version': 18, 'sha256': hashes}, ensure_ascii=False, indent=2))
    with ZipFile(archive) as package:
        assert package.testzip() is None
        assert set(package.namelist()) == {*hashes, 'MANIFEST.json'}
        for name, digest in hashes.items():
            assert hashlib.sha256(package.read(name)).hexdigest() == digest
    print(f'{archive}\nFiles: {len(hashes)+1}; bytes: {archive.stat().st_size}; integrity verified.')


if __name__ == '__main__':
    main()
