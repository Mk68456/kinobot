"""Small code-only update for existing Python launchers and their server."""
import argparse
import hashlib
import json
from pathlib import Path
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from tools.launcher_runtime import VERSION
from package_server import FILES as SERVER_FILES

LAUNCHER_FILES = tuple('tools/' + name for name in (
    'desktop_launcher.py', 'launcher_projects.py', 'launcher_projects_ui.py', 'remote_projects.py',
    'torrent_panel.py', 'torrent_queue.py', 'release_description.py', 'release_assistant_ui.py', 'launcher_runtime.py',
    'release_search.py', 'release_search_ui.py', 'jackett_search.py', 'jackett_sources_ui.py', 'remote_torrents.py',
    'video_hardware.py', 'video_hardware_ui.py', 'remote_render.py', 'disk_space.py', 'stream_pipeline.py', 'launcher_device.py', 'launcher_entry.py', 'desktop-requirements.txt',
    'Start-KinoTime.cmd'))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--launcher-only', action='store_true')
    parser.add_argument('--topic', choices=('Hardware', 'LongNames', 'RemoteRender', 'MultiProjects', 'RemoteProjects', 'RemoteTorrents'), default='RemoteRender')
    parser.add_argument('--guide', default='docs/REMOTE_RENDER_1_3_3.md')
    args = parser.parse_args()
    target = ROOT / f'KinoTime-{args.topic}-{VERSION}.zip'
    records = {}
    with zipfile.ZipFile(target, 'w', zipfile.ZIP_DEFLATED) as archive:
        groups = [('Launcher/', LAUNCHER_FILES)]
        if not args.launcher_only:
            groups.insert(0, ('Server/', SERVER_FILES))
        if args.topic in ('RemoteProjects', 'RemoteTorrents'):
            groups = [('Launcher/', tuple('tools/' + name for name in (
                'desktop_launcher.py', 'remote_projects.py', 'launcher_projects_ui.py', 'launcher_runtime.py',
                'torrent_panel.py', 'remote_torrents.py', 'jackett_sources_ui.py')))]
            if not args.launcher_only:
                groups.insert(0, ('Server/', ('webpanel/launcher_control.py', 'webpanel/static/app.js',
                                             'webpanel/static/index.html', 'webpanel/static/style.css')))
        for prefix, files in groups:
            for name in files:
                records[prefix + name] = hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
                archive.write(ROOT / name, prefix + name)
        archive.write(ROOT / args.guide, 'INSTALL.md')
        archive.write(ROOT / 'docs/MULTIPLE_LAUNCHERS_1_3.md', 'INSTALL-MULTIPC.md')
        archive.write(ROOT / 'tests/test_long_import_names.py', 'Checks/test_long_import_names.py')
        archive.write(ROOT / 'tests/test_video_hardware.py', 'Checks/test_video_hardware.py')
        archive.write(ROOT / 'tests/test_hardware_diagnostics.py', 'Checks/test_hardware_diagnostics.py')
        if args.topic == 'MultiProjects':
            archive.write(ROOT / 'tests/test_launcher_projects.py', 'Checks/test_launcher_projects.py')
        if args.topic == 'RemoteProjects':
            for name in ('tests/test_remote_projects.py', 'tools/check_remote_projects_ui.cjs'):
                archive.write(ROOT / name, 'Checks/' + Path(name).name)
        if args.topic == 'RemoteTorrents':
            for name in ('tests/test_remote_torrents.py', 'tools/check_remote_torrents_ui.cjs'):
                archive.write(ROOT / name, 'Checks/' + Path(name).name)
        if args.topic == 'RemoteRender':
            for name in ('tests/test_remote_render.py', 'tests/test_remote_launcher_control.py', 'tools/check_remote_render_ui.cjs'):
                archive.write(ROOT / name, 'Checks/' + Path(name).name)
            archive.write(ROOT / 'outputs/remote-render-ui.json', 'Checks/remote-ui.json')
        archive.writestr('FILES-SHA256.json', json.dumps(records, indent=2))
    with zipfile.ZipFile(target) as archive:
        assert archive.testzip() is None
        for name, digest in records.items():
            assert hashlib.sha256(archive.read(name)).hexdigest() == digest
        assert not any(name.lower().endswith(('.db', '.sqlite', '.env', '.sqlite-wal', '.sqlite-shm'))
                       for name in archive.namelist())
    digest = hashlib.sha256(target.read_bytes()).hexdigest()
    target.with_suffix('.zip.sha256').write_text(digest + '  ' + target.name + '\n', encoding='ascii')
    print(f'{target.name}: {len(records)} code files, {target.stat().st_size} bytes; hashes and CRC OK')


if __name__ == '__main__':
    main()
