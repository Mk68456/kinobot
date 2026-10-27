"""Package only the server patch, never databases or user state."""
import hashlib
import argparse
import json
from pathlib import Path
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from tools.launcher_runtime import VERSION
FILES = (
    'database/migrations.py', 'webpanel/imports.py', 'webpanel/launcher_control.py',
    'webpanel/launcher_transfers.py', 'webpanel/repository.py', 'webpanel/server.py', 'webpanel/series_validation.py',
    'webpanel/static/app.js', 'webpanel/static/index.html', 'webpanel/static/style.css',
    'services/storage_backup.py',
)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--guide', default='docs/REMOTE_RENDER_1_3_3.md')
    args = parser.parse_args()
    target = ROOT / f'KinoTime-Server-{VERSION}.zip'
    records = {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in FILES}
    with zipfile.ZipFile(target, 'w', zipfile.ZIP_DEFLATED) as archive:
        for name in FILES:
            archive.write(ROOT / name, 'Server/' + name)
        archive.write(ROOT / args.guide, 'INSTALL.md')
        archive.write(ROOT / 'docs/MULTIPLE_LAUNCHERS_1_3.md', 'INSTALL-MULTIPC.md')
        archive.writestr('FILES-SHA256.json', json.dumps(records, indent=2))
    with zipfile.ZipFile(target) as archive:
        if archive.testzip() is not None:
            raise RuntimeError('ZIP integrity check failed')
        for name, digest in records.items():
            assert hashlib.sha256(archive.read('Server/' + name)).hexdigest() == digest
        assert not any(name.lower().endswith(('.sqlite', '.db', '.env', '.sqlite-wal', '.sqlite-shm')) for name in archive.namelist())
    digest = hashlib.sha256(target.read_bytes()).hexdigest()
    target.with_suffix('.zip.sha256').write_text(digest + '  ' + target.name + '\n', encoding='ascii')
    print(f'{target.name}: {len(FILES)} server files, {target.stat().st_size} bytes; hashes and CRC OK')


if __name__ == '__main__':
    main()
