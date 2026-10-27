"""Executable entry point, startup diagnostics and isolated offline bundle check."""
import argparse
import json
import logging
import os
import shutil
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import traceback

from tools.launcher_runtime import VERSION, media_tool, state_path


def self_test(report_path):
    result = {'version': VERSION, 'ok': False, 'frozen': bool(getattr(sys, 'frozen', False))}
    try:
        with tempfile.TemporaryDirectory(prefix='KinoTime-check-') as temporary:
            root = Path(temporary)
            os.environ['KINOTIME_STATE_DIR'] = str(root / 'state')
            os.environ['KINOTIME_DOWNLOAD_DIR'] = str(root / 'downloads')
            from pywinauto import Desktop
            from tools import desktop_launcher as launcher, video_hardware
            from tools.release_description import parse_description
            from tools.release_assistant_ui import open_assistant
            from tools.release_search import search_rows, required_issues
            from tools.release_search_ui import open_search
            from tools.jackett_sources_ui import open_sources, load_sources
            from tools.launcher_runtime import resource_root
            import tkinter as tk
            sample = parse_description('Название: Проверка\nКачество: WEBRip 1080p\n'
                                       'Перевод: Студия А\nРазмер: 2 GiB\nРеклама: нет')
            if sample.value('quality') != 'WEBRIP' or sample.size_bytes != 2 * 1024 ** 3:
                raise ValueError('Release description parsing failed.')
            sample_candidate = {'title': 'Проверка', 'description': sample}
            if required_issues(sample_candidate, 'Проверка') or not required_issues(sample_candidate, 'Проверка', height=720):
                raise ValueError('Mandatory search filters failed.')
            assistant_root = tk.Tk()
            assistant_root.withdraw()
            try:
                assistant = open_assistant(assistant_root, lambda source: None)
                assistant.withdraw()
                assistant_root.update_idletasks()
                assistant.destroy()
                search_window = open_search(assistant_root, lambda source: None, lambda candidate, options: None)
                search_window.withdraw()
                assistant_root.update_idletasks()
                search_window.destroy()
                sources_window = open_sources(assistant_root, load_sources(), lambda: None)
                sources_window.withdraw()
                assistant_root.update_idletasks()
                sources_window.destroy()
                found = search_rows('<div id="index"><table><tr><td><a href="/torrent/1/test">'
                                    'Test WEBRip 1080p</a></td></tr></table></div>', 'https://rutor.info')
                if len(found) != 1 or found[0]['id'] != '1':
                    raise ValueError('Release search extraction failed.')
            finally:
                assistant_root.destroy()
            result['release_assistant'] = {'ok': True, 'parser': 'offline', 'gui': 'built and closed'}
            result['release_search'] = {'ok': True, 'checks': 'offline search parsing and GUI construction',
                                        'live_site_tested': False}
            if getattr(sys, 'frozen', False):
                jackett = resource_root() / 'jackett' / 'JackettConsole.exe'
                version = subprocess.run([str(jackett), '--version'], capture_output=True, text=True,
                                         timeout=20, creationflags=video_hardware.process_flags())
                # Jackett's command-line parser exits 1 for both --help and --version.
                if version.returncode not in (0, 1) or version.stdout.strip() != 'Jackett v0.24.2910':
                    raise ValueError('Bundled Jackett version check failed.')
                result['jackett'] = {'ok': True, 'version': '0.24.2910', 'sources_gui': True,
                                     'live_trackers_tested': False}
            result['desktop_backend'] = Desktop.__module__
            language_source = root / 'Language check.mkv'
            subprocess.run([media_tool('ffmpeg'), '-nostdin', '-v', 'error', '-f', 'lavfi', '-i',
                            'color=size=64x64:rate=5', '-f', 'lavfi', '-i', 'sine=frequency=440',
                            '-f', 'lavfi', '-i', 'sine=frequency=880', '-t', '0.4',
                            '-map', '0:v', '-map', '1:a', '-map', '2:a', '-c:v', 'libx264',
                            '-pix_fmt', 'yuv420p', '-c:a', 'aac',
                            '-metadata:s:a:0', 'language=rus', '-metadata:s:a:1', 'language=eng',
                            str(language_source)], check=True, capture_output=True, timeout=60,
                           creationflags=video_hardware.process_flags())
            audio = launcher.audio_choices(language_source)
            if [a['language'] for a in audio] != ['rus', 'eng']:
                raise ValueError('Audio language detection failed.')
            for choice in audio:
                choice['enabled'] = choice['language'] == 'rus'
            launcher.save_audio_choices(language_source, audio)
            selected = launcher.prepare_media(language_source, lambda *args: None, threading.Event())
            if len(selected['files']) != 1 or selected['files'][0]['voice'] != 'rus':
                raise ValueError('Unselected foreign audio was rendered.')
            result['audio_language_selection'] = {'ok': True, 'selected': 'rus', 'outputs': 1}
            result['ffmpeg'] = media_tool('ffmpeg')
            result['ffprobe'] = media_tool('ffprobe')
            result['hardware'] = video_hardware.diagnostics(refresh=True)
            source = root / 'Видео для проверки' / 'S01E01.mkv'
            source.parent.mkdir()
            command = [media_tool('ffmpeg'), '-nostdin', '-v', 'error', '-f', 'lavfi', '-i',
                       'testsrc2=size=1280x720:rate=10', '-f', 'lavfi', '-i', 'sine=frequency=440',
                       '-t', '0.4', '-c:v', 'mpeg4', '-q:v', '4', '-c:a', 'aac', str(source)]
            subprocess.run(command, check=True, capture_output=True, timeout=60,
                           creationflags=video_hardware.process_flags())
            messages = []
            manifest = launcher.prepare_media(source, messages.append, threading.Event(),
                                              qualities=['source', '360p'])
            outputs = []
            for item in manifest['files']:
                path = Path(manifest['root']) / item['path']
                info = launcher.probe_video(path)
                if not launcher.player_compatible(info):
                    raise ValueError('Prepared video is not Telegram-compatible.')
                video = next(s for s in info['streams'] if s.get('codec_type') == 'video')
                outputs.append({'quality': item['quality'], 'height': video['height'],
                                'codec': video['codec_name'], 'pixel_format': video['pix_fmt']})
            if {o['quality']: o['height'] for o in outputs} != {'source': 720, '360p': 360}:
                raise ValueError('Selected qualities are incomplete.')
            result.update(outputs=outputs, encoding_messages=messages)
            episode = root / 'Сезон с точками' / '2x01 - Мужик приходит к психиатру....mp4'
            episode.parent.mkdir()
            subprocess.run([media_tool('ffmpeg'), '-nostdin', '-v', 'error', '-f', 'lavfi',
                            '-i', 'testsrc2=size=1920x1080:rate=25', '-f', 'lavfi', '-i',
                            'sine=frequency=440', '-t', '3', '-c:v', 'libx264', '-preset',
                            'ultrafast', '-pix_fmt', 'yuv420p', '-c:a', 'aac', str(episode)],
                           check=True, capture_output=True, timeout=60,
                           creationflags=video_hardware.process_flags())
            folder = launcher.prepare_folder_qualities(
                episode.parent, ['1080p', '720p', '480p', '360p'], messages.append, threading.Event())
            if {entry['quality'] for entry in folder['files']} != {'1080p', '720p', '480p', '360p'}:
                raise ValueError('Folder preparation did not save all four qualities.')
            for entry in folder['files']:
                path = Path(folder['root']) / entry['path']
                if not path.is_file() or not launcher.player_compatible(launcher.probe_video(path)):
                    raise ValueError('Episode output is missing or incompatible: ' + str(path))
                stream_copy = (root / 'stream-path-check' / entry['quality'] /
                               launcher.episode_output_directory(episode.name) / path.name)
                stream_copy.parent.mkdir(parents=True, exist_ok=True)
                os.link(path, stream_copy)
                if not launcher.player_compatible(launcher.probe_video(stream_copy)):
                    raise ValueError('Streaming episode path is invalid: ' + str(stream_copy))
            repeated = launcher.prepare_folder_qualities(
                episode.parent, ['1080p', '720p', '480p', '360p'], messages.append, threading.Event())
            if repeated != folder:
                raise ValueError('Repeated folder preparation changed the manifest.')
            result['trailing_dots_folder'] = {'ok': True, 'qualities': ['1080p', '720p', '480p', '360p'],
                                            'repeat_ok': True, 'stream_copy_paths_ok': True}
            from tools.launcher_projects import ProjectManager, build_project
            import time
            manager = ProjectManager(root / 'projects.sqlite')
            try:
                for name in ('Первый сериал', 'Второй сериал'):
                    project_source = root / name / 'S01E01.mkv'
                    project_source.parent.mkdir()
                    shutil.copy2(source, project_source)
                    manager.add(build_project(project_source.parent, ['360p']))
                deadline = time.monotonic() + 90
                while manager.busy and time.monotonic() < deadline:
                    time.sleep(.1)
                states = manager.snapshots()
                if manager.busy or len(states) != 2 or any(p['state'] != 'prepared' for p in states):
                    raise ValueError('Multi-project rendering failed: ' + str(states))
                for project in states:
                    for manifest in manager.manifests(project['id']).values():
                        for entry in manifest['files']:
                            if not launcher.player_compatible(launcher.probe_video(Path(manifest['root']) / entry['path'])):
                                raise ValueError('Project output is incompatible.')
                result['projects'] = {'ok': True, 'parallelism': manager.parallelism,
                                      'projects': len(states), 'outputs': sum(p['ready'] for p in states)}
            finally:
                manager.close()
                if not manager.wait_closed(10):
                    raise ValueError('Project workers did not stop.')
            # Build all pages and tear down without a window, network or user data.
            launcher.main(smoke_test=True)
            result['gui'] = 'built and closed'
            result['ok'] = True
    except Exception:
        result['error'] = traceback.format_exc()
    report_path = Path(report_path).resolve()
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    return 0 if result['ok'] else 1


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--self-test', metavar='REPORT_JSON')
    arguments = parser.parse_args()
    if arguments.self_test:
        return self_test(arguments.self_test)
    log = state_path('launcher.log', __file__)
    from logging.handlers import RotatingFileHandler
    logging.basicConfig(handlers=[RotatingFileHandler(log, maxBytes=1_000_000, backupCount=2,
                                                    encoding='utf-8')], level=logging.WARNING)
    try:
        from tools.desktop_launcher import main as launch
        launch()
    except Exception:
        logging.exception('Launcher startup failed')
        from tkinter import messagebox
        messagebox.showerror('KinoTime', 'Не удалось запустить лаунчер. Подробности сохранены в:\n' + str(log))
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
