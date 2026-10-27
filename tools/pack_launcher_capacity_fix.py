"""Build the capacity patch from code-only paths; never bundle live data."""
import hashlib
import json
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

ROOT = Path(__file__).resolve().parents[1]
LOCAL_FILES = ('tools/desktop_launcher.py', 'tools/torrent_queue.py', 'tools/torrent_panel.py')
SERVER_FILES = ('webpanel/imports.py', 'webpanel/repository.py', 'webpanel/server.py', 'webpanel/static/app.js')
FILES = (*LOCAL_FILES, *SERVER_FILES, 'tests/test_import_capacity.py')


def main():
    archive = ROOT / 'kinobot-launcher-1000-files-fix.zip'
    hashes = {}
    with ZipFile(archive, 'w', compression=ZIP_DEFLATED, compresslevel=9) as package:
        for filename in FILES:
            path = (ROOT / filename).resolve()
            if ROOT not in path.parents or path.suffix not in ('.py', '.js'):
                raise ValueError(f'Unexpected patch path: {filename}')
            data = path.read_bytes()
            hashes[filename] = hashlib.sha256(data).hexdigest()
            package.writestr(filename, data)
        data = (ROOT / 'docs/LAUNCHER_1000_FILES_FIX.md').read_bytes()
        hashes['INSTALL.md'] = hashlib.sha256(data).hexdigest()
        package.writestr('INSTALL.md', data)
        package.writestr('MANIFEST.json', json.dumps({
            'update': 'launcher-1000-files-fix', 'server_update_required': True,
            'database_migration_required': False, 'max_import_files': 1000,
            'local_runtime_files': LOCAL_FILES, 'server_runtime_files': SERVER_FILES, 'sha256': hashes,
        }, ensure_ascii=False, indent=2))
    with ZipFile(archive) as package:
        assert package.testzip() is None
        assert set(package.namelist()) == {*hashes, 'MANIFEST.json'}
        for name, digest in hashes.items():
            assert hashlib.sha256(package.read(name)).hexdigest() == digest
    print(f'{archive}\nFiles: {len(hashes) + 1}; bytes: {archive.stat().st_size}; integrity verified.')


if __name__ == '__main__':
    main()
