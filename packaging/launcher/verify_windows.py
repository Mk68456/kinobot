"""Offline portable and installer checks; no Telegram or panel interaction."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import winreg

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from tools.launcher_runtime import VERSION


def smoke(executable, report):
    environment = {key: value for key, value in os.environ.items() if not key.upper().startswith('PYTHON')}
    environment['PATH'] = str(Path(os.environ['SYSTEMROOT']) / 'System32')
    result = subprocess.run([str(executable), '--self-test', str(report)], cwd=executable.parent,
                            env=environment, timeout=180)
    if result.returncode != 0 or not report.exists():
        raise RuntimeError(f'Executable smoke failed: {executable}; exit {result.returncode}')
    data = json.loads(report.read_text(encoding='utf-8'))
    if not data.get('ok') or not data.get('frozen'):
        raise RuntimeError(str(data))
    with executable.open('rb') as binary:
        data['executable_sha256'] = hashlib.file_digest(binary, 'sha256').hexdigest()
    report.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f'Smoke OK: {executable.name}; GPU: {data["hardware"]["encoders"]}', flush=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--install', action='store_true', help='Also install/uninstall in workspace, without shortcuts.')
    args = parser.parse_args()
    release = ROOT / 'dist' / f'KinoTime-Windows-{VERSION}'
    checks = ROOT / 'outputs' / 'launcher-install-check'
    checks.mkdir(parents=True, exist_ok=True)
    moved = checks / 'Проверка переноса' / 'KinoTime на другом ПК.exe'
    moved.parent.mkdir(exist_ok=True)
    shutil.copy2(release / 'KinoTime.exe', moved)
    smoke(moved, ROOT / 'outputs' / 'launcher-portable-self-test.json')
    if not args.install:
        return
    key = r'Software\Microsoft\Windows\CurrentVersion\Uninstall\{8FB1B599-E889-4A60-8ABA-0D1CB7D1D2B2}_is1'
    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        for view in (winreg.KEY_WOW64_64KEY, winreg.KEY_WOW64_32KEY):
            try:
                with winreg.OpenKey(hive, key, 0, winreg.KEY_READ | view):
                    raise RuntimeError('Existing KinoTime installation found: do not overwrite it in a smoke test.')
            except FileNotFoundError:
                pass
    installation = checks / 'Программа KinoTime'
    if installation.exists():
        raise RuntimeError('Test installation directory already exists: inspect it before retrying.')
    subprocess.run([str(release / f'KinoTime-Setup-{VERSION}.exe'), '/VERYSILENT', '/SUPPRESSMSGBOXES',
                    '/NORESTART', '/NOICONS', '/TASKS=', f'/DIR={installation}',
                    f'/LOG={checks / "install.log"}'], check=True, timeout=180)
    try:
        smoke(installation / 'KinoTime.exe', ROOT / 'outputs' / 'launcher-installer-self-test.json')
        source = ROOT / 'build' / 'launcher-package' / 'installed' / 'KinoTime'
        for file in source.rglob('*'):
            if file.is_file():
                installed = installation / file.relative_to(source)
                if not installed.is_file() or hashlib.sha256(file.read_bytes()).digest() != hashlib.sha256(installed.read_bytes()).digest():
                    raise RuntimeError('Installed file differs: ' + str(file.relative_to(source)))
        print('Installer payload hashes OK', flush=True)
    finally:
        subprocess.run([str(installation / 'unins000.exe'), '/VERYSILENT', '/SUPPRESSMSGBOXES',
                        '/NORESTART', f'/LOG={checks / "uninstall.log"}'], check=True, timeout=180)
    if (installation / 'KinoTime.exe').exists():
        raise RuntimeError('Uninstaller did not remove the application.')
    with (release / f'KinoTime-Setup-{VERSION}.exe').open('rb') as binary:
        installer_hash = hashlib.file_digest(binary, 'sha256').hexdigest()
    report = ROOT / 'outputs' / 'launcher-installer-self-test.json'
    data = json.loads(report.read_text(encoding='utf-8'))
    data.update(installer_sha256=installer_hash, payload_verified=True, uninstall_verified=True)
    report.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    print('Installer installation/uninstallation OK', flush=True)


if __name__ == '__main__':
    main()
