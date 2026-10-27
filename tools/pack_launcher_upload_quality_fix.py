"""Build a local launcher patch from an explicit allowlist."""
import hashlib
import json
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile


ROOT = Path(__file__).resolve().parents[1]
RUNTIME_FILES = ('tools/desktop_launcher.py', 'tools/torrent_queue.py', 'tools/torrent_panel.py')
FILES = (*RUNTIME_FILES, 'tests/test_launcher_season_preparation.py', 'tests/test_desktop_import.py',
         'tests/test_torrent_queue.py', 'tests/test_torrent_panel.py')


def main():
    archive = ROOT / 'kinobot-launcher-upload-quality-fix.zip'
    hashes = {}
    with ZipFile(archive, 'w', compression=ZIP_DEFLATED, compresslevel=9) as package:
        for filename in FILES:
            path = (ROOT / filename).resolve()
            if ROOT not in path.parents or path.suffix != '.py':
                raise ValueError(f'Unexpected release path: {filename}')
            data = path.read_bytes()
            hashes[filename] = hashlib.sha256(data).hexdigest()
            package.writestr(filename, data)
        data = (ROOT / 'docs/LAUNCHER_UPLOAD_QUALITY_FIX.md').read_bytes()
        hashes['INSTALL.md'] = hashlib.sha256(data).hexdigest()
        package.writestr('INSTALL.md', data)
        package.writestr('MANIFEST.json', json.dumps({
            'update': 'launcher-upload-quality-fix', 'server_update_required': False,
            'database_migration_required': False, 'runtime_files': RUNTIME_FILES, 'sha256': hashes,
        }, ensure_ascii=False, indent=2))
    with ZipFile(archive) as package:
        assert package.testzip() is None
        assert set(package.namelist()) == {*hashes, 'MANIFEST.json'}
        for name, digest in hashes.items():
            assert hashlib.sha256(package.read(name)).hexdigest() == digest
    print(f'{archive}\nFiles: {len(hashes) + 1}; bytes: {archive.stat().st_size}; integrity verified.')


if __name__ == '__main__':
    main()
