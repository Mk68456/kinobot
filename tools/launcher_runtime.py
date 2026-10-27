"""Paths for source runs, installed Windows apps and one-file portable bundles."""
import os
from pathlib import Path
import shutil
import sys

VERSION = '1.4.10'


def state_path(filename, module_file):
    override = os.environ.get('KINOTIME_STATE_DIR')
    if override:
        directory = Path(override).expanduser().resolve()
    elif getattr(sys, 'frozen', False):
        directory = Path(os.environ.get('LOCALAPPDATA', Path.home() / 'AppData' / 'Local')) / 'KinoTime' / 'Launcher'
    else:
        directory = Path(module_file).resolve().parent
    directory.mkdir(parents=True, exist_ok=True)
    return directory / filename


def resource_root():
    return Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parent))


def media_tool(name):
    bundled = resource_root() / 'bin' / (name + '.exe' if os.name == 'nt' else name)
    if bundled.is_file():
        return str(bundled)
    external = shutil.which(name)
    if external:
        return external
    raise ValueError('Не найден ' + name + '. Используйте полную сборку KinoTime со встроенным FFmpeg.')
