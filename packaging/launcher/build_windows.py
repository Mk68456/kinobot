"""Build two independent Windows x64 applications and their per-user installer.

Run with the isolated build venv and local verified FFmpeg/Inno Setup files.
No runtime database, media, credentials or saved settings are collected.
"""
import argparse
import hashlib
from importlib import metadata
import json
from pathlib import Path
import shutil
import subprocess
import sys

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from tools.launcher_runtime import VERSION


def copy_licenses(target, ffmpeg_root):
    target.mkdir(parents=True, exist_ok=True)
    shutil.copy2(Path(__file__).with_name('THIRD_PARTY.md'), target / 'THIRD_PARTY.md')
    shutil.copy2(ffmpeg_root / 'LICENSE', target / 'FFmpeg-LICENSE.txt')
    shutil.copy2(ffmpeg_root / 'README.txt', target / 'FFmpeg-README.txt')
    shutil.copy2(Path(sys.base_prefix) / 'LICENSE.txt', target / 'Python-LICENSE.txt')
    for package in ('pyinstaller', 'pywinauto', 'pywin32', 'comtypes', 'Pillow', 'six'):
        distribution = metadata.distribution(package)
        for file in distribution.files or []:
            if 'license' in file.name.lower() or 'copying' in file.name.lower():
                path = distribution.locate_file(file)
                if path.is_file():
                    shutil.copy2(path, target / (package + '-' + file.name))
    for name in ('tcl8.6', 'tk8.6'):
        source = Path(sys.base_prefix) / 'tcl' / name / 'license.terms'
        if source.exists():
            shutil.copy2(source, target / (name + '-license.txt'))
    process = subprocess.run([str(ffmpeg_root / 'bin' / 'ffmpeg.exe'), '-version', '-buildconf'],
                             capture_output=True, text=True, check=True)
    (target / 'FFmpeg-build.txt').write_text(process.stdout + process.stderr, encoding='utf-8')


def run(command, log):
    print('Building: ' + ' '.join(map(str, command[:5])), flush=True)
    with log.open('w', encoding='utf-8') as output:
        subprocess.run(list(map(str, command)), cwd=ROOT, stdout=output, stderr=subprocess.STDOUT, check=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--ffmpeg-root', type=Path, required=True)
    parser.add_argument('--iscc', type=Path, required=True)
    parser.add_argument('--jackett-root', type=Path,
                        default=ROOT / 'build/launcher-vendor/jackett-0.24.2910/Jackett')
    parser.add_argument('--reuse', action='store_true', help='Reuse PyInstaller analysis for a local incremental build.')
    args = parser.parse_args()
    ffmpeg_root, iscc = args.ffmpeg_root.resolve(), args.iscc.resolve()
    if not iscc.is_file() or not all((ffmpeg_root / 'bin' / f'{tool}.exe').is_file() for tool in ('ffmpeg', 'ffprobe')):
        parser.error('Verified local FFmpeg, FFprobe and Inno Setup ISCC are required.')
    jackett_root = args.jackett_root.resolve()
    if not all((jackett_root / name).is_file() for name in ('JackettConsole.exe', 'Jackett.Common.dll', 'LICENSE')):
        parser.error('Verified Jackett Windows portable binaries and license are required.')
    work = ROOT / 'build' / 'launcher-package'
    work.mkdir(parents=True, exist_ok=True)
    release = ROOT / 'dist' / f'KinoTime-Windows-{VERSION}'
    release.mkdir(parents=True, exist_ok=True)
    licenses = work / 'licenses'
    copy_licenses(licenses, ffmpeg_root)
    shutil.copy2(jackett_root / 'LICENSE', licenses / 'Jackett-LICENSE.txt')
    (licenses / 'Jackett-SOURCE.txt').write_text(
        'Jackett 0.24.2910 (separate bundled executable), GPL-2.0.\n'
        'Corresponding source: https://github.com/Jackett/Jackett/archive/refs/tags/v0.24.2910.zip\n'
        'Original binary: https://github.com/Jackett/Jackett/releases/tag/v0.24.2910\n'
        'No modifications to Jackett. Tracker credentials stay in the local Jackett data directory.\n', 'utf-8')
    icon = Image.new('RGBA', (256, 256), '#3b82f6')
    drawing = ImageDraw.Draw(icon)
    font = ImageFont.truetype('C:/Windows/Fonts/segoeuib.ttf', 182)
    drawing.text((128, 130), 'K', font=font, anchor='mm', fill='white')
    icon_path = work / 'KinoTime.ico'
    icon.save(icon_path, sizes=[(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    version_file = work / 'version.txt'
    version_numbers = ','.join(VERSION.split('.') + ['0'])
    version_file.write_text(f'''VSVersionInfo(
  ffi=FixedFileInfo(filevers=({version_numbers}), prodvers=({version_numbers}), mask=0x3f,
    flags=0, OS=0x40004, fileType=0x1, subtype=0, date=(0,0)),
  kids=[StringFileInfo([StringTable('040904B0', [
    StringStruct('CompanyName','KinoTime'), StringStruct('FileDescription','KinoTime Launcher'),
    StringStruct('FileVersion','{VERSION}'), StringStruct('ProductVersion','{VERSION}'),
    StringStruct('ProductName','KinoTime'), StringStruct('OriginalFilename','KinoTime.exe')])]),
    VarFileInfo([VarStruct('Translation',[1033,1200])])])
''', encoding='utf-8')
    common = [sys.executable, '-m', 'PyInstaller', '--noconfirm', '--windowed',
              '--name', 'KinoTime', '--paths', ROOT, '--icon', icon_path,
              '--version-file', version_file,
              '--hidden-import', 'win32timezone', '--collect-submodules', 'pywinauto',
              '--collect-submodules', 'comtypes', '--exclude-module', 'pytest',
              '--exclude-module', 'pywinauto.tests', '--exclude-module', 'comtypes.test',
              '--add-data', f'{licenses};licenses',
              '--add-data', f'{jackett_root};jackett',
              '--add-binary', f'{ffmpeg_root / "bin" / "ffmpeg.exe"};bin',
              '--add-binary', f'{ffmpeg_root / "bin" / "ffprobe.exe"};bin']
    if not args.reuse:
        common.append('--clean')
    entry = ROOT / 'tools' / 'launcher_entry.py'
    run(common + ['--onedir', '--specpath', work / 'spec-installed', '--distpath', work / 'installed',
                  '--workpath', work / 'work-installed', entry],
        work / 'build-installed.log')
    run(common + ['--onefile', '--specpath', work / 'spec-portable', '--distpath', release,
                  '--workpath', work / 'work-portable', entry],
        work / 'build-portable.log')
    shutil.copy2(Path(__file__).with_name('README.txt'), release / 'README.txt')
    run([iscc, '/Qp', f'/DAppFiles={work / "installed" / "KinoTime"}',
         f'/DReleaseDirectory={release}', f'/DIconFile={icon_path}', f'/DAppVersion={VERSION}',
         Path(__file__).with_name('KinoTime.iss')], work / 'build-installer.log')
    files = [release / 'KinoTime.exe', release / f'KinoTime-Setup-{VERSION}.exe']
    hashes = {}
    for file in files:
        with file.open('rb') as binary:
            hashes[file.name] = hashlib.file_digest(binary, 'sha256').hexdigest()
    (release / 'SHA256.txt').write_text('\n'.join(f'{value}  {name}' for name, value in hashes.items()) + '\n')
    (release / 'build-manifest.json').write_text(json.dumps({
        'version': VERSION, 'architecture': 'Windows x64', 'python': sys.version,
        'packages': {d.metadata['Name']: d.version for d in metadata.distributions()},
        'files': hashes}, ensure_ascii=False, indent=2), encoding='utf-8')
    print('Built release: ' + str(release), flush=True)


if __name__ == '__main__':
    main()
