"""Package the series conflict patch without databases, settings or media."""
import hashlib
import json
from pathlib import Path
import re
import zipfile

ROOT = Path(__file__).resolve().parents[2]
FILES = ('webpanel/series_validation.py', 'webpanel/repository.py', 'webpanel/server.py',
         'webpanel/static/app.js', 'webpanel/static/index.html', 'webpanel/static/style.css')


def main():
    log = (ROOT / 'outputs/series-conflicts-regressions.log').read_text(encoding='utf-8')
    checked = re.search(r'Ran (\d+) tests in [^\n]+\n\s*\nOK\b', log)
    if not checked:
        raise RuntimeError('Run the complete regression suite successfully before packaging.')
    report = json.loads((ROOT / 'outputs/series-conflicts-ui.json').read_text(encoding='utf-8'))
    if not report.get('ok'):
        raise RuntimeError('Complete the isolated browser check before packaging.')
    target = ROOT / 'KinoTime-Series-Publication-Fix-2026-10-07.zip'
    records = {}
    with zipfile.ZipFile(target, 'w', zipfile.ZIP_DEFLATED) as archive:
        for name in FILES:
            records['Server/' + name] = hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
            archive.write(ROOT / name, 'Server/' + name)
        archive.write(ROOT / 'docs/SERIES_PUBLICATION_CONFLICTS.md', 'INSTALL.md')
        archive.write(ROOT / 'tests/test_series_publication_conflicts.py', 'Checks/test_series_publication_conflicts.py')
        archive.write(ROOT / 'tools/check_series_conflicts_ui.cjs', 'Checks/check_series_conflicts_ui.cjs')
        archive.write(ROOT / 'outputs/series-conflicts-ui.json', 'Verification/browser.json')
        archive.writestr('Verification/VALIDATION.txt',
            f"Full Python suite: {checked[1]} tests passed.\n"
            'Three seasons and distinct voices remain separate; exact repeats identify their rows.\n'
            'Publication is atomic; failed checks retain the draft and uploaded assets.\n'
            'Browser fixture: 280 files, conflict highlights, jump, catalog duplicate and edit recovery OK.\n'
            'No production database, live publishing or Telegram messages were used.\n')
        archive.writestr('FILES-SHA256.json', json.dumps(records, indent=2))
    with zipfile.ZipFile(target) as archive:
        assert archive.testzip() is None
        for name, digest in records.items():
            assert hashlib.sha256(archive.read(name)).hexdigest() == digest
        assert not any(name.endswith(('.db', '.sqlite', '.env', '.sqlite-wal', '.sqlite-shm')) for name in archive.namelist())
    digest = hashlib.sha256(target.read_bytes()).hexdigest()
    target.with_suffix('.zip.sha256').write_text(digest + '  ' + target.name + '\n', encoding='ascii')
    print(f'{target.name}: {len(FILES)} server files, {target.stat().st_size} bytes; hashes and CRC OK')


if __name__ == '__main__':
    main()
