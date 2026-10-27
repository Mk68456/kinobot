"""Package only verified streaming launcher 1.3.5 and server binaries and explicit code, preserving old ZIPs."""
import hashlib
import json
from pathlib import Path
import re
import runpy
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from tools.launcher_runtime import VERSION
from package_update import LAUNCHER_FILES
from package_server import FILES as DEVICE_SERVER_FILES
AGE_SERVER_FILES = runpy.run_path(str(ROOT / 'packaging/age_access/package_update.py'))['FILES']
SERVER_FILES = tuple(dict.fromkeys((*AGE_SERVER_FILES, *DEVICE_SERVER_FILES, 'webpanel/streaming.py')))


def sha(path):
    with path.open('rb') as file:
        return hashlib.file_digest(file, 'sha256').hexdigest()


def main():
    release = ROOT / 'dist' / f'KinoTime-Windows-{VERSION}'
    checks = ROOT / 'outputs' / f'launcher-disk-{VERSION}'
    validation = json.loads((checks / 'validation.json').read_text(encoding='utf-8'))
    frozen = json.loads((checks / 'frozen-sources.json').read_text(encoding='utf-8'))
    log = (ROOT / 'outputs/stream-final-regressions.log').read_text(encoding='utf-8')
    match = re.search(r'Ran (\d+) tests in [\d.]+s\s+OK\b', log)
    if not match or int(match[1]) < 547 or not validation.get('ok') or not frozen.get('ok'):
        raise RuntimeError('Complete regression, frozen-source and offline binary checks first.')
    ui = json.loads((ROOT / 'outputs/stream-pipeline-ui.json').read_text(encoding='utf-8'))
    if not ui.get('ok'):
        raise RuntimeError('Streaming UI verification must pass.')
    layout = json.loads((ROOT / 'outputs/stream-launcher-layout.json').read_text(encoding='utf-8'))
    if not layout.get('ok'):
        raise RuntimeError('Minimum launcher width check must pass.')
    binaries = ('KinoTime.exe', f'KinoTime-Setup-{VERSION}.exe')
    if sha(release / binaries[0]) != validation['portable_sha256'] or \
            sha(release / binaries[1]) != validation['installer_sha256']:
        raise RuntimeError('Binary verification belongs to a different release.')
    for bundle in frozen['bundles']:
        for name, checked in bundle['modules'].items():
            if not checked.get('match') or sha(ROOT.joinpath(*name.split('.')).with_suffix('.py')) != checked['source_sha256']:
                raise RuntimeError('Frozen source verification is stale: ' + name)
    (release / 'SHA256.txt').write_text('\n'.join(sha(release / name) + '  ' + name for name in binaries) + '\n', encoding='ascii')
    manifest_path = release / 'build-manifest.json'
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    manifest['files'] = {name: sha(release / name) for name in binaries}
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    entries = {name: release / name for name in (*binaries, 'README.txt', 'SHA256.txt')}
    entries['INSTALL.md'] = ROOT / 'docs/STREAM_PIPELINE_1_3_5.md'
    entries.update({'Source/' + name: ROOT / name for name in LAUNCHER_FILES})
    entries.update({'Server/' + name: ROOT / name for name in SERVER_FILES})
    entries['Checks/check_stream_pipeline_ui.cjs'] = ROOT / 'tools/check_stream_pipeline_ui.cjs'
    entries['Verification/web-ui.json'] = ROOT / 'outputs/stream-pipeline-ui.json'
    entries['Checks/check_stream_launcher_layout.py'] = ROOT / 'tools/check_stream_launcher_layout.py'
    entries['Verification/launcher-layout.json'] = ROOT / 'outputs/stream-launcher-layout.json'
    for name in ('test_stream_pipeline.py', 'test_desktop_import.py', 'test_disk_space.py', 'test_launcher_season_preparation.py', 'test_torrent_panel.py'):
        entries['Checks/' + name] = ROOT / 'tests' / name
    for name in ('portable.json', 'installer.json', 'validation.json', 'frozen-sources.json'):
        entries['Verification/' + name] = checks / name
    entries['Verification/regressions.log'] = ROOT / 'outputs/stream-final-regressions.log'
    for path in (ROOT / 'build/launcher-package/licenses').iterdir():
        if path.is_file():
            entries['Licenses/' + path.name] = path
    for name in ('build_windows.py', 'verify_disk_update.py', 'check_bundle_sources.py', 'package_stream_update.py',
                 'KinoTime.iss', 'requirements-build.txt', 'README.txt', 'package_update.py', 'package_server.py'):
        entries['Source/packaging/launcher/' + name] = ROOT / 'packaging/launcher' / name
    records = {name: sha(path) for name, path in entries.items()}
    previous = {path: sha(path) for path in ROOT.glob('KinoTime-*.zip')}
    target = ROOT / f'KinoTime-Streaming-{VERSION}.zip'
    with zipfile.ZipFile(target, 'x', zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for name, path in entries.items():
            archive.write(path, name)
        archive.writestr('FILES-SHA256.json', json.dumps(records, indent=2))
        archive.writestr('VALIDATION.txt', f'{match[1]} regression tests passed. '
            'Portable EXE and extracted installer EXE passed offline video/GUI self-tests. '
            'Installer payload hashes and frozen Python source code verified. '
            'No production files, live Telegram sending or Windows installation used.\n')
    with zipfile.ZipFile(target) as archive:
        if any(name.lower().endswith(('.sqlite', '.db', '.env', '.sqlite-wal', '.sqlite-shm')) for name in archive.namelist()):
            raise RuntimeError('Runtime data must not be packaged.')
        if archive.testzip() is not None:
            raise RuntimeError('ZIP CRC failure')
        for name, digest in records.items():
            with archive.open(name) as file:
                if hashlib.file_digest(file, 'sha256').hexdigest() != digest:
                    raise RuntimeError('ZIP checksum mismatch: ' + name)
    for path, digest in previous.items():
        if sha(path) != digest:
            raise RuntimeError('Previous release was modified: ' + path.name)
    target.with_suffix('.zip.sha256').write_text(sha(target) + '  ' + target.name + '\n', encoding='ascii')
    print(f'{target.name}: {target.stat().st_size} bytes; {match[1]} tests passed; '
          f'CRC and checksums OK; {len(previous)} earlier archives preserved.')


if __name__ == '__main__':
    main()
