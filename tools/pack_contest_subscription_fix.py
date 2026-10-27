"""Package the subscription fix without databases, configuration, or media."""
import hashlib
import json
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile


ROOT = Path(__file__).resolve().parents[1]
FILES = (
    'handlers/users/contests.py',
    'keyboards/inline/sub_keyboard.py',
    'services/contests.py',
    'tests/test_handlers.py',
)


def main():
    archive = ROOT / 'kinobot-contest-subscription-fix.zip'
    hashes = {}
    with ZipFile(archive, 'w', compression=ZIP_DEFLATED, compresslevel=9) as package:
        for filename in FILES:
            path = (ROOT / filename).resolve()
            if ROOT not in path.parents or path.suffix != '.py':
                raise ValueError(f'Unexpected release path: {filename}')
            data = path.read_bytes()
            hashes[filename] = hashlib.sha256(data).hexdigest()
            package.writestr(filename, data)
        data = (ROOT / 'docs/CONTEST_SUBSCRIPTION_FIX.md').read_bytes()
        package.writestr('INSTALL.md', data)
        hashes['INSTALL.md'] = hashlib.sha256(data).hexdigest()
        package.writestr('MANIFEST.json', json.dumps({
            'update': 'contest-subscription-fix', 'schema_version': 18,
            'database_migration_required': False, 'sha256': hashes,
        }, ensure_ascii=False, indent=2))
    with ZipFile(archive) as package:
        assert package.testzip() is None
        assert set(package.namelist()) == {*hashes, 'MANIFEST.json'}
        for name, digest in hashes.items():
            assert hashlib.sha256(package.read(name)).hexdigest() == digest
    print(f'{archive}\nFiles: {len(hashes) + 1}; bytes: {archive.stat().st_size}; integrity verified.')


if __name__ == '__main__':
    main()
