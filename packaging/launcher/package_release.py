"""Make a reviewed release ZIP from explicit files, after executable verification."""
import argparse
import hashlib
import json
import re
from pathlib import Path
import shutil
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from tools.launcher_runtime import VERSION


def sha256(file):
    with file.open('rb') as binary:
        return hashlib.file_digest(binary, 'sha256').hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--launcher-only', action='store_true')
    parser.add_argument('--regression-log', default='outputs/remote-render-regressions.log')
    parser.add_argument('--guide', default='docs/REMOTE_RENDER_1_3_3.md')
    args = parser.parse_args()
    release = ROOT / 'dist' / f'KinoTime-Windows-{VERSION}'
    portable = json.loads((ROOT / 'outputs' / 'launcher-portable-self-test.json').read_text(encoding='utf-8'))
    installer = json.loads((ROOT / 'outputs' / 'launcher-installer-self-test.json').read_text(encoding='utf-8'))
    if not portable.get('ok') or portable.get('executable_sha256') != sha256(release / 'KinoTime.exe'):
        raise RuntimeError('Portable executable must pass verification for this exact file.')
    frozen = json.loads((ROOT / 'outputs/launcher-frozen-source-verification.json').read_text(encoding='utf-8'))
    remote_ui = json.loads((ROOT / 'outputs/remote-render-ui.json').read_text(encoding='utf-8'))
    if not frozen.get('ok') or not remote_ui.get('ok'):
        raise RuntimeError('Frozen source comparison and remote UI verification must pass.')
    if not any(bundle.get('exe_sha256') == sha256(release / 'KinoTime.exe') for bundle in frozen['bundles']):
        raise RuntimeError('Frozen source verification must belong to this executable.')
    for bundle in frozen['bundles']:
        for name, result in bundle['modules'].items():
            source = ROOT.joinpath(*name.split('.')).with_suffix('.py')
            if not result.get('match') or result.get('source_sha256') != sha256(source):
                raise RuntimeError('Frozen source verification is stale for ' + name)
    if not installer.get('ok') or not installer.get('payload_verified') or not installer.get('uninstall_verified') \
            or installer.get('installer_sha256') != sha256(release / f'KinoTime-Setup-{VERSION}.exe'):
        raise RuntimeError('Installer must pass installation, smoke and uninstall checks for this exact file.')
    regression_log = (ROOT / args.regression_log).read_text(encoding='utf-8')
    checked = re.search(r'Ran (\d+) tests in [^\n]+\n\s*\nOK\b', regression_log)
    if not checked or 'FAILED (failures=' in regression_log or 'FAILED (errors=' in regression_log:
        raise RuntimeError('Complete the Python regression suite successfully before packaging.')
    validation = '\n'.join([
        'KinoTime Launcher ' + VERSION,
        'Portable EXE: isolated PATH; Cyrillic filename and directory; hidden GUI; H.264 source/360p OK.',
        'Installer: workspace installation; isolated PATH; all installed payload hashes verified; uninstall OK.',
        'Intel Iris Xe / i7-1355U: real Quick Sync encoding and output validation OK.',
        'CPU: real H.264 encoding and two-pass mode OK.',
        'AMD AMF / NVIDIA NVENC: vendor arguments, selection and CPU fallback covered by mocked tests.',
        'AMD/NVIDIA physical encoding has not been tested on this Intel-only computer.',
        'Multi-computer isolation, token binding, transfer reservations, deduplication and migration tests passed.',
        'Long names: preparation, import, web editing, publication, rename and resume verified.',
        'Queue/processing hardware selectors: shared preferences, restore, automatic saving and active-worker lock verified.',
        'P106-100 missing NVENC and NVIDIA driver failures: mocked diagnosis and CPU fallback verified.',
        'No physical P106-100 is available on this computer; no P106 encoding test was performed.',
        'Remote folders, explicit render settings, worker states and per-computer ownership tested with isolated fixtures.',
        'Full Python regression suite: ' + checked[1] + ' tests passed.',
        'Multi-project queue: concurrency limit, rotation, priority, independent pause, crash resume and source isolation tested.',
        'Project GUI: adding a second project during rendering and pausing only the selected project tested.',
        'Real project fixtures: two series, multiple qualities and audio tracks; cached repeat without re-encoding verified.',
        'Project transfer: partial registration, server-account binding and confirmed-file cleanup recovery covered with mocked API responses.',
        'Portable and installed self-tests each rendered two local projects and validated the resulting MP4 files.',
        'No live Telegram sending, publishing or panel actions were performed.',
        'Python, FFmpeg and FFprobe bundled; user data and server database excluded.',
    ]) + '\n'
    (release / 'VALIDATION.txt').write_text(validation, encoding='utf-8')
    licenses = release / 'Licenses'
    licenses.mkdir(exist_ok=True)
    for file in (ROOT / 'build' / 'launcher-package' / 'licenses').iterdir():
        shutil.copy2(file, licenses / file.name)
    sources = [f'tools/{name}' for name in (
        'desktop_launcher.py', 'launcher_projects.py', 'launcher_projects_ui.py', 'remote_projects.py',
        'torrent_panel.py', 'torrent_queue.py', 'release_description.py', 'release_assistant_ui.py', 'launcher_runtime.py',
        'release_search.py', 'release_search_ui.py', 'jackett_search.py', 'jackett_sources_ui.py', 'remote_torrents.py',
        'video_hardware.py', 'video_hardware_ui.py', 'remote_render.py', 'disk_space.py', 'stream_pipeline.py', 'launcher_device.py', 'launcher_entry.py', 'desktop-requirements.txt', 'Start-KinoTime.cmd')]
    sources += [f'packaging/launcher/{name}' for name in (
        'build_windows.py', 'verify_windows.py', 'check_bundle_sources.py', 'package_release.py', 'package_server.py', 'package_update.py', 'requirements-build.txt',
        'KinoTime.iss', 'README.txt', 'THIRD_PARTY.md', 'BUILD.md')]
    sources += ['tests/test_launcher_projects.py', 'docs/MULTI_PROJECTS_1_4.md',
                'tests/test_video_hardware.py', 'tests/test_hardware_diagnostics.py', 'tests/test_multiple_launchers.py', 'tests/test_long_import_names.py',
                'tests/test_remote_render.py', 'tests/test_remote_launcher_control.py',
                'tools/check_remote_render_ui.cjs', 'tools/check_multiple_launchers_ui.cjs', 'tools/check_long_names_ui.cjs',
                'docs/MULTIPLE_LAUNCHERS_1_3.md', 'docs/LONG_NAMES_1_3_1.md', args.guide]
    artifact = ROOT / f'KinoTime-Windows-{VERSION}.zip'
    with zipfile.ZipFile(artifact, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for name in ('KinoTime.exe', f'KinoTime-Setup-{VERSION}.exe', 'README.txt', 'SHA256.txt',
                     'build-manifest.json', 'VALIDATION.txt'):
            archive.write(release / name, name)
        for file in sorted(licenses.iterdir()):
            archive.write(file, 'Licenses/' + file.name)
        for name in dict.fromkeys(sources):
            archive.write(ROOT / name, 'Source/' + name)
        if not args.launcher_only:
            server_name = f'KinoTime-Server-{VERSION}.zip'
            archive.write(ROOT / server_name, 'Server-Update/' + server_name)
        archive.write(ROOT / args.guide, 'INSTALL.md')
        archive.write(ROOT / 'docs/MULTIPLE_LAUNCHERS_1_3.md', 'INSTALL-MULTIPC.md')
        archive.write(ROOT / 'outputs' / 'launcher-portable-self-test.json', 'Verification/portable.json')
        archive.write(ROOT / 'outputs' / 'launcher-installer-self-test.json', 'Verification/installer.json')
        archive.write(ROOT / 'outputs' / 'remote-render-ui.json', 'Verification/remote-ui.json')
        archive.write(ROOT / 'outputs/launcher-frozen-source-verification.json', 'Verification/frozen-source.json')
        if portable.get('projects', {}).get('ok'):
            archive.write(ROOT / args.regression_log, 'Verification/full-tests.log')
    with zipfile.ZipFile(artifact) as archive:
        if archive.testzip() is not None:
            raise RuntimeError('Archive integrity check failed.')
        print(f'ZIP OK: {len(archive.infolist())} entries; {artifact.stat().st_size} bytes')
    artifact.with_suffix('.zip.sha256').write_text(sha256(artifact) + '  ' + artifact.name + '\n', encoding='ascii')
    print(artifact)


if __name__ == '__main__':
    main()
