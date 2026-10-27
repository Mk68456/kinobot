"""Verify portable and extracted installer offline, without installing into Windows."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from tools.launcher_runtime import VERSION


def digest(path):
    with path.open('rb') as binary:
        return hashlib.file_digest(binary, 'sha256').hexdigest()


def smoke(exe, report):
    env = {key: value for key, value in os.environ.items() if not key.upper().startswith('PYTHON')}
    env['PATH'] = str(Path(os.environ['SYSTEMROOT']) / 'System32')
    subprocess.run([str(exe), '--self-test', str(report)], cwd=exe.parent, env=env, check=True, timeout=180)
    data = json.loads(report.read_text(encoding='utf-8'))
    if not data.get('ok') or not data.get('frozen') or data.get('version') != VERSION:
        raise RuntimeError(str(data))
    data['executable_sha256'] = digest(exe)
    report.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    print('Offline smoke OK: ' + str(exe), flush=True)


def main():
    release = ROOT / 'dist' / f'KinoTime-Windows-{VERSION}'
    checks = ROOT / 'outputs' / f'launcher-disk-{VERSION}'
    checks.mkdir(parents=True, exist_ok=True)
    smoke(release / 'KinoTime.exe', checks / 'portable.json')
    extraction = checks / ('installer-payload-' + digest(release / f'KinoTime-Setup-{VERSION}.exe')[:12])
    subprocess.run([str(ROOT / 'build/launcher-vendor/innoextract/innoextract.exe'), '--extract', '--silent',
                    '--output-dir', str(extraction), str(release / f'KinoTime-Setup-{VERSION}.exe')],
                   check=True, timeout=180)
    payload = extraction / 'app'
    if digest(payload / 'README.txt') != digest(release / 'README.txt'):
        raise RuntimeError('Installer README differs from this release.')
    source = ROOT / 'build/launcher-package/installed/KinoTime'
    count = 0
    for path in source.rglob('*'):
        if path.is_file():
            installed = payload / path.relative_to(source)
            if not installed.is_file() or digest(installed) != digest(path):
                raise RuntimeError('Installer payload mismatch: ' + str(path.relative_to(source)))
            count += 1
    smoke(payload / 'KinoTime.exe', checks / 'installer.json')
    report = {'ok': True, 'version': VERSION, 'files_verified': count,
              'portable_sha256': digest(release / 'KinoTime.exe'),
              'installer_sha256': digest(release / f'KinoTime-Setup-{VERSION}.exe'),
              'verification': 'Installer extracted and payload compared byte-for-byte; both EXEs run offline self-tests. '
                              'No installation, live Telegram or panel actions.'}
    (checks / 'validation.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(f'Installer: {count} payload files verified; both offline executable checks passed.', flush=True)


if __name__ == '__main__':
    main()
