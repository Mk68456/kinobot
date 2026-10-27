"""Read frozen PYZ code without executing it and compare it to current source."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import marshal
from pathlib import Path
import sys
import types

from PyInstaller.archive.readers import CArchiveReader

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MODULES = ('tools.desktop_launcher', 'tools.remote_render', 'tools.launcher_projects',
                   'tools.launcher_projects_ui', 'tools.remote_projects', 'tools.disk_space', 'tools.video_hardware',
                   'tools.release_description', 'tools.release_assistant_ui', 'tools.torrent_panel',
                   'tools.release_search', 'tools.release_search_ui', 'tools.torrent_queue',
                   'tools.jackett_search', 'tools.jackett_sources_ui', 'tools.remote_torrents',
                   'tools.launcher_runtime', 'tools.launcher_entry')


def digest_file(path):
    digest = hashlib.sha256()
    with path.open('rb') as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def code_shape(value):
    """Canonical code data; only co_filename is intentionally ignored."""
    if isinstance(value, types.CodeType):
        attributes = ('co_argcount', 'co_posonlyargcount', 'co_kwonlyargcount', 'co_nlocals',
                      'co_stacksize', 'co_flags', 'co_code', 'co_consts', 'co_names', 'co_varnames',
                      'co_name', 'co_qualname', 'co_firstlineno', 'co_linetable', 'co_exceptiontable',
                      'co_freevars', 'co_cellvars')
        return {'type': 'code', 'fields': {name: code_shape(getattr(value, name))
                                         for name in attributes if hasattr(value, name)}}
    if isinstance(value, bytes):
        return {'type': 'bytes', 'hex': value.hex()}
    if isinstance(value, (tuple, list)):
        return {'type': type(value).__name__, 'items': [code_shape(item) for item in value]}
    if isinstance(value, frozenset):
        items = [code_shape(item) for item in value]
        items.sort(key=lambda item: json.dumps(item, sort_keys=True, ensure_ascii=True))
        return {'type': 'frozenset', 'items': items}
    if isinstance(value, float):
        return {'type': 'float', 'value': value.hex()}
    if isinstance(value, complex):
        return {'type': 'complex', 'real': value.real.hex(), 'imaginary': value.imag.hex()}
    if isinstance(value, slice):
        return {'type': 'slice', 'start': code_shape(value.start),
                'stop': code_shape(value.stop), 'step': code_shape(value.step)}
    if value is Ellipsis:
        return {'type': 'ellipsis'}
    if value is None or isinstance(value, (str, int, bool)):
        return {'type': type(value).__name__, 'value': value}
    raise TypeError('Unsupported code constant type: ' + type(value).__name__)


def code_digest(code):
    encoded = json.dumps(code_shape(code), sort_keys=True, ensure_ascii=True,
                         separators=(',', ':')).encode('ascii')
    return hashlib.sha256(encoded).hexdigest()


def inspect_bundle(executable, modules):
    archive = CArchiveReader(str(executable))
    pyz = [archive.open_embedded_archive(name) for name, record in archive.toc.items()
           if record[-1] == 'z']
    if not pyz:
        raise ValueError('No embedded PYZ archive found.')
    result = {'exe': str(executable.resolve()), 'exe_sha256': digest_file(executable), 'modules': {}}
    for module in modules:
        path = ROOT.joinpath(*module.split('.')).with_suffix('.py')
        source = path.read_bytes()
        frozen = next((item.extract(module) for item in pyz if module in item.toc), None)
        if frozen is None and module == 'tools.launcher_entry':
            # PyInstaller stores the entry script in CArchive, rather than PYZ.
            script = next((name for name, record in archive.toc.items()
                           if name == 'launcher_entry' and record[-1] == 's'), None)
            if script:
                frozen = marshal.loads(archive.extract(script))
        if not isinstance(frozen, types.CodeType):
            result['modules'][module] = {'match': False, 'error': 'Module not found in embedded PYZ.'}
            continue
        current = compile(source, str(path), 'exec', dont_inherit=True, optimize=0)
        source_code_hash, frozen_code_hash = code_digest(current), code_digest(frozen)
        result['modules'][module] = {'match': source_code_hash == frozen_code_hash,
                                    'source_sha256': hashlib.sha256(source).hexdigest(),
                                    'source_code_sha256': source_code_hash,
                                    'frozen_code_sha256': frozen_code_hash}
    result['ok'] = all(module['match'] for module in result['modules'].values())
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--exe', action='append', type=Path, required=True)
    parser.add_argument('--module', action='append', dest='modules')
    parser.add_argument('--report', type=Path, required=True)
    arguments = parser.parse_args()
    modules = arguments.modules or DEFAULT_MODULES
    report = {'checked_at': datetime.now(timezone.utc).isoformat(), 'python': sys.version.split()[0],
              'comparison': 'All Python code fields including nested code, excluding filenames only.',
              'bundles': []}
    for executable in arguments.exe:
        try:
            result = inspect_bundle(executable, modules)
        except Exception as error:
            result = {'exe': str(executable.resolve()), 'ok': False, 'error': str(error)}
        report['bundles'].append(result)
    report['ok'] = all(bundle['ok'] for bundle in report['bundles'])
    arguments.report.parent.mkdir(parents=True, exist_ok=True)
    arguments.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'ok': report['ok'], 'report': str(arguments.report), 'bundles': [
        {'exe': bundle['exe'], 'ok': bundle['ok'],
         'modules': {name: module['match'] for name, module in bundle.get('modules', {}).items()},
         **({'error': bundle['error']} if 'error' in bundle else {})} for bundle in report['bundles']]},
        ensure_ascii=False))
    return 0 if report['ok'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
