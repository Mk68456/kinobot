"""Probe real H.264 encoding, choose a working vendor and bound CPU usage."""
import os
import json
from pathlib import Path
import subprocess
import threading
from contextlib import contextmanager

try:
    from .launcher_runtime import media_tool
except ImportError:
    from launcher_runtime import media_tool

ENCODERS = {'nvidia': 'h264_nvenc', 'intel': 'h264_qsv', 'amd': 'h264_amf', 'cpu': 'libx264'}
LABELS = {'nvidia': 'NVIDIA NVENC', 'intel': 'Intel Quick Sync', 'amd': 'AMD AMF', 'cpu': 'Процессор (H.264)'}
POLICIES = {'auto', *ENCODERS}
LOADS = {'eco', 'balanced', 'performance'}
_policy, _load = 'auto', 'balanced'
_availability = {}
_probe_errors = {}
_devices = None
_lock = threading.RLock()
_worker = threading.local()


@contextmanager
def worker_budget(slots):
    """Share the selected CPU budget across simultaneous project workers."""
    previous = getattr(_worker, 'slots', 1)
    _worker.slots = max(1, slots)
    try:
        yield
    finally:
        _worker.slots = previous


def configure(policy='auto', load='balanced'):
    global _policy, _load
    if policy not in POLICIES or load not in LOADS:
        raise ValueError('Неизвестный режим оборудования или нагрузки.')
    _policy, _load = policy, load


def settings():
    return {'hardware': _policy, 'resource_mode': _load}


def cpu_threads(load=None, logical=None):
    logical = max(1, logical or os.cpu_count() or 1)
    fraction = {'eco': .25, 'balanced': .5, 'performance': 1}[load or _load]
    return max(1, int(logical * fraction) // getattr(_worker, 'slots', 1))


def process_flags():
    flags = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
    if _load == 'eco':
        flags |= getattr(subprocess, 'BELOW_NORMAL_PRIORITY_CLASS', 0)
    return flags


def codec_args(backend, bitrate=None, mode='fast'):
    rate = ['-b:v', f'{bitrate}k', '-maxrate', f'{bitrate}k', '-bufsize', f'{bitrate * 2}k'] if bitrate else []
    if backend == 'nvidia':
        return ['-c:v', 'h264_nvenc', '-preset', 'p4', '-tune', 'hq', '-rc', 'vbr',
                *(rate or ['-cq', '20', '-b:v', '0']), '-pix_fmt', 'yuv420p']
    if backend == 'intel':
        return ['-c:v', 'h264_qsv', '-preset', 'veryfast',
                *(rate or ['-global_quality', '20']), '-pix_fmt', 'nv12']
    if backend == 'amd':
        return ['-c:v', 'h264_amf', '-usage', 'transcoding', '-quality', 'balanced',
                '-rc', 'vbr_peak' if bitrate else 'cqp',
                *(rate or ['-qp_i', '20', '-qp_p', '20']), '-pix_fmt', 'nv12']
    if backend != 'cpu':
        raise ValueError('Неизвестный кодировщик.')
    return ['-c:v', 'libx264', '-preset', 'slow' if mode == 'exact' else 'fast',
            *(rate or ['-crf', '20']), '-threads', str(cpu_threads()), '-pix_fmt', 'yuv420p']


def intel_pipeline(source_video, profile):
    """Keep supported decoding and scaling on QSV; conservative CPU fallback otherwise."""
    if (source_video.get('codec_name') not in ('h264', 'hevc', 'mpeg2video', 'vp9', 'av1') or
            source_video.get('pix_fmt') not in ('yuv420p', 'yuv420p10le', 'nv12', 'p010le') or
            source_video.get('field_order', 'progressive') not in ('progressive', 'unknown')):
        return None
    width, height = int(source_video.get('width', 0)), int(source_video.get('height', 0))
    if width <= 0 or height <= 0:
        return None
    ratio = min(1, profile['width'] / width, profile['height'] / height)
    output_width, output_height = max(2, int(width * ratio) // 2 * 2), max(2, int(height * ratio) // 2 * 2)
    return (['-hwaccel', 'qsv', '-hwaccel_output_format', 'qsv'],
            f'scale_qsv=w={output_width}:h={output_height}:format=nv12')


def available(backend, refresh=False):
    if backend == 'cpu':
        return True
    if backend not in ENCODERS:
        raise ValueError('Неизвестный кодировщик.')
    with _lock:
        if refresh or backend not in _availability:
            try:
                # Listing an encoder alone does not prove that the GPU/driver supports it.
                result = subprocess.run([media_tool('ffmpeg'), '-nostdin', '-v', 'error',
                    '-f', 'lavfi', '-i', 'color=size=320x180:rate=10', '-frames:v', '3',
                    *codec_args(backend, 800), '-f', 'null', '-'], capture_output=True,
                    timeout=12, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
                _availability[backend] = result.returncode == 0
                error = result.stderr if isinstance(result.stderr, (str, bytes)) else ''
                if isinstance(error, bytes):
                    error = error.decode('utf-8', errors='replace')
                _probe_errors[backend] = '' if result.returncode == 0 else error.strip()[-8000:]
            except subprocess.TimeoutExpired:
                _availability[backend] = False
                _probe_errors[backend] = 'Проверка кодировщика не завершилась за 12 секунд.'
            except (OSError, ValueError) as error:
                _availability[backend] = False
                _probe_errors[backend] = str(error)
        return _availability[backend]


def disable(backend, reason=''):
    with _lock:
        _availability[backend] = False
        _probe_errors[backend] = reason or 'Кодировщик не смог обработать исходное видео.'


def failure_reason(backend, error=None):
    error = _probe_errors.get(backend, '') if error is None else error
    lower = error.casefold()
    if 'nvenc capable' in lower or 'unsupported device' in lower or 'no capable devices' in lower:
        return 'драйвер не предоставляет устройство с поддержкой NVENC'
    if 'required nvenc api' in lower or 'required nvenc version' in lower or 'minimum required nvidia driver' in lower:
        return 'драйвер NVIDIA не поддерживает нужную версию NVENC'
    if 'cannot load' in lower or 'library' in lower and 'not found' in lower:
        return 'не удалось загрузить библиотеку драйвера'
    if 'no device' in lower or 'device not found' in lower:
        return 'подходящее устройство не найдено'
    return 'проверка кодирования не прошла; подробности доступны в диагностике'


def candidates(mode='fast', stop=None, report=None):
    def notify(message):
        if report:
            report('Оборудование: ' + message)
    if mode == 'exact' or _policy == 'cpu':
        notify('Процессор: точный режим использует два прохода на CPU.' if mode == 'exact'
               else 'Процессор выбран вручную.')
        return ['cpu']
    result = []
    requested = ('nvidia', 'intel', 'amd') if _policy == 'auto' else (_policy,)
    for backend in requested:
        if stop is not None and stop.is_set():
            raise InterruptedError('Подготовка остановлена.')
        if available(backend):
            result.append(backend)
    if not result:
        reason = failure_reason(_policy) if _policy != 'auto' else 'рабочий аппаратный кодировщик не найден'
        label = LABELS[_policy] if _policy != 'auto' else 'Автовыбор'
        notify(f'{label}: {reason}. Используется процессор.')
    else:
        notify('Кодирование: ' + LABELS[result[0]] + '. При ошибке используется процессор.')
    return [*result, 'cpu']


def devices(refresh=False):
    """Read Windows adapter names; availability still requires a real encode test."""
    global _devices
    with _lock:
        if not refresh and _devices is not None:
            return [dict(device) for device in _devices]
        _devices = []
        if os.name != 'nt':
            return []
        executable = Path(os.environ.get('SYSTEMROOT', 'C:/Windows')) / 'System32/WindowsPowerShell/v1.0/powershell.exe'
        command = ('[Console]::OutputEncoding = [System.Text.Encoding]::UTF8; '
                   'Get-CimInstance Win32_VideoController | Select-Object Name,DriverVersion,PNPDeviceID '
                   '| ConvertTo-Json -Compress')
        try:
            result = subprocess.run([str(executable), '-NoProfile', '-NonInteractive', '-Command', command],
                capture_output=True, timeout=8, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            if result.returncode:
                return []
            records = json.loads(result.stdout.decode('utf-8-sig'))
            if isinstance(records, dict):
                records = [records]
            for record in records if isinstance(records, list) else []:
                if not isinstance(record, dict) or not isinstance(record.get('Name'), str):
                    continue
                pnp = str(record.get('PNPDeviceID') or '').upper()
                vendor = next((key for key, identifier in [('nvidia', 'VEN_10DE'), ('intel', 'VEN_8086'),
                                                          ('amd', 'VEN_1002')] if identifier in pnp), '')
                _devices.append({'name': record['Name'][:200], 'driver': str(record.get('DriverVersion') or '')[:80],
                                 'vendor': vendor})
        except (OSError, ValueError, subprocess.TimeoutExpired):
            pass
        return [dict(device) for device in _devices]


def diagnostics(refresh=False):
    for backend in ENCODERS:
        if backend != 'cpu':
            available(backend, refresh)
    adapters = devices(refresh)
    return {**diagnostics_snapshot(), 'devices': adapters}


def diagnostics_snapshot():
    """Return the latest probe/encode result without starting another GPU test."""
    with _lock:
        return {'selected': _policy, 'resource_mode': _load,
                'cpu_threads': cpu_threads(), 'logical_processors': os.cpu_count() or 1,
                'encoders': {backend: _availability.get(backend) for backend in ENCODERS if backend != 'cpu'},
                'errors': {backend: _probe_errors.get(backend, '') for backend in ENCODERS if backend != 'cpu'},
                'devices': [dict(device) for device in _devices or []]}


def diagnostics_summary(info):
    selected = info['selected']
    enabled = [backend for backend, working in info['encoders'].items() if working]
    if selected == 'cpu':
        summary = 'Процессор выбран вручную.'
    elif selected != 'auto':
        if info['encoders'].get(selected):
            summary = LABELS[selected] + ': проверка H.264 пройдена.'
        else:
            nvidia = [d for d in info.get('devices', []) if d['vendor'] == 'nvidia']
            p106_only = nvidia and all('P106-100' in d['name'].upper() or 'GP106-100' in d['name'].upper()
                                      for d in nvidia)
            reason = ('P106-100 не имеет блока NVENC' if selected == 'nvidia' and p106_only else
                      failure_reason(selected, info.get('errors', {}).get(selected, '')))
            summary = LABELS[selected] + ': ' + reason + '. Используется процессор.'
    elif enabled:
        summary = 'Автовыбор: ' + LABELS[enabled[0]] + '. Проверка H.264 пройдена.'
    else:
        summary = 'Рабочее аппаратное кодирование не найдено. Используется процессор.'
        if any('P106-100' in d['name'].upper() for d in info.get('devices', [])):
            summary += ' P106-100 не имеет блока NVENC.'
    adapters = ', '.join(d['name'] for d in info.get('devices', []))
    if adapters:
        summary += ' Видеоадаптеры: ' + adapters + '.'
    return summary


def diagnostics_text(info):
    lines = [diagnostics_summary(info), '',
             f"Потоки CPU: {info['cpu_threads']} из {info['logical_processors']}.",
             'Точный режим использует два прохода на CPU. Совместимый исходный H.264 копируется.',
             'Выбор GPU ускоряет кодирование; декодирование и масштабирование выполняются на CPU.', '',
             'Видеоадаптеры:']
    for device in info.get('devices', []):
        lines.append(f"• {device['name']} · драйвер {device['driver'] or 'не определён'}")
    if not info.get('devices'):
        lines.append('Названия адаптеров не удалось получить.')
    lines.extend(['', 'Результаты реального теста H.264:'])
    for backend, enabled in info['encoders'].items():
        if enabled is None:
            lines.append(LABELS[backend] + ': ещё не проверен\n')
            continue
        lines.append(LABELS[backend] + ': ' + ('проверка пройдена' if enabled else 'недоступен'))
        if not enabled:
            lines.append(info.get('errors', {}).get(backend) or 'Драйвер не вернул описание ошибки.')
        lines.append('')
    return '\n'.join(lines)
