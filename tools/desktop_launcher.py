"""Windows launcher: manifests over HTTPS, videos only through Telegram Desktop UI."""

import json
import logging
from contextlib import contextmanager
import hashlib
import http.client
import os
import re
import subprocess
from pathlib import Path
import threading
import time
import unicodedata
import uuid
import urllib.error
import urllib.request
from urllib.parse import urlsplit

try:
    from . import video_hardware, video_hardware_ui, launcher_device, remote_render, disk_space, launcher_projects
    from .launcher_runtime import VERSION, media_tool, state_path
except ImportError:
    import video_hardware
    import video_hardware_ui
    import launcher_device
    import remote_render
    import disk_space
    import launcher_projects
    from launcher_runtime import VERSION, media_tool, state_path

EXTENSIONS = {'.mp4', '.mkv', '.avi', '.mov', '.m4v', '.webm', '.ts'}
MAX_IMPORT_FILES = 1000
MAX_JSON_BYTES = 16 * 1024**2


def movie_title(filename):
    stem = re.sub(r'[._]+', ' ', Path(filename).stem).strip()
    for year in re.finditer(r'(?<!\d)(?:19|20)\d{2}(?!\d)', stem):
        title = stem[:year.start()].strip(' ()[]-')
        if title:
            return f'{title} ({year.group()})'[:200]
    return re.split(r'\b(?:2160p|1080p|720p|BDRip|WEB[ -]?DL|HDRip|DVDRip)\b',
                    stem, flags=re.I)[0].strip(' ()[]-')[:200] or Path(filename).stem[:200]


def probe_video(path):
    result = subprocess.run([media_tool('ffprobe'), '-v', 'error', '-show_streams', '-show_format',
                             '-of', 'json', str(path)], capture_output=True, timeout=60,
                            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    if result.returncode:
        raise ValueError(f'{Path(path).name}: не удалось прочитать видео.')
    return json.loads(result.stdout)


def player_compatible(info):
    streams = info.get('streams', [])
    videos = [s for s in streams if s.get('codec_type') == 'video']
    audio = [s for s in streams if s.get('codec_type') == 'audio']
    return (bool(videos) and 'mp4' in info.get('format', {}).get('format_name', '').split(',')
            and all(s.get('codec_name') == 'h264' and s.get('pix_fmt') == 'yuv420p' for s in videos)
            and all(s.get('codec_name') == 'aac' for s in audio))


def video_options(info, hardware=False):
    """Options used by the classic folder conversion path."""
    video = next((s for s in info.get('streams', []) if s.get('codec_type') == 'video'), {})
    if video.get('codec_name') == 'h264' and video.get('pix_fmt') == 'yuv420p':
        return ['-c:v', 'copy']
    if hardware:
        return ['-c:v', 'h264_qsv', '-preset', 'veryfast', '-global_quality', '20',
                '-pix_fmt', 'nv12', '-vf', 'pad=ceil(iw/2)*2:ceil(ih/2)*2']
    return ['-c:v', 'libx264', '-preset', 'fast', '-crf', '20', '-pix_fmt', 'yuv420p',
            '-vf', 'pad=ceil(iw/2)*2:ceil(ih/2)*2']


def audio_options(info):
    """Copy AAC and convert only incompatible audio tracks."""
    args = ['-c:a', 'copy']
    audio = [s for s in info.get('streams', []) if s.get('codec_type') == 'audio']
    for index, stream in enumerate(audio):
        if stream.get('codec_name') != 'aac':
            args += [f'-c:a:{index}', 'aac', f'-b:a:{index}', '192k']
    return args


def qsv_available():
    return video_hardware.available('intel')


def prepare_media_base(folder, report, stop, *, enforce_upload_limit=True):
    """Create a separate, reusable player-compatible copy; never rename originals."""
    original = scan(folder, enforce_upload_limit=False)
    stop.disk_report = report
    root = Path(original['root'])
    checked = []
    for entry in original['files']:
        if stop.is_set():
            raise InterruptedError('Подготовка остановлена.')
        report('Проверяю формат: ' + entry['path'])
        info = probe_video(root / entry['path'])
        checked.append((entry, player_compatible(info) and Path(entry['path']).suffix.lower() == '.mp4'))
    if all(compatible for _, compatible in checked):
        return scan(folder, enforce_upload_limit=enforce_upload_limit)
    media_tool('ffmpeg')
    key = hashlib.sha256(json.dumps(original, sort_keys=True).encode()).hexdigest()[:24]
    cache = root.parent / 'KinoTime-Ready' / key
    target_root = cache / (root.name if not original.get('selection') else 'movie')
    disk_space.protect(target_root)
    targets = [str(Path(e['path']).with_suffix('.mp4')).casefold() for e, _ in checked]
    if len(set(targets)) != len(targets):
        raise ValueError('После преобразования совпадут имена MP4. Дайте исходным видео разные имена.')
    for index, (entry, compatible) in enumerate(checked, 1):
        if stop.is_set():
            raise InterruptedError('Подготовка остановлена.')
        source = root / entry['path']
        target = target_root / Path(entry['path']).with_suffix('.mp4')
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists() and player_compatible(probe_video(target)):
            continue
        partial = target.with_suffix('.partial.mp4')
        report(f'{index}/{len(checked)}: готовлю MP4 — {source.name}. Это может занять несколько минут.')
        if compatible:
            disk_space.copy(source, partial, stop, report)
        else:
            log_path = target.with_suffix('.conversion.log')
            duration = float(probe_video(source).get('format', {}).get('duration', 0))
            info = probe_video(source)
            video = next(s for s in info['streams'] if s.get('codec_type') == 'video')
            backends = ['copy'] if video.get('codec_name') == 'h264' and video.get('pix_fmt') == 'yuv420p' else video_hardware.candidates(stop=stop, report=report)
            for backend in backends:
                options = ['-c:v', 'copy'] if backend == 'copy' else video_hardware.codec_args(backend)
                if backend != 'copy':
                    options += ['-vf', 'pad=ceil(iw/2)*2:ceil(ih/2)*2']
                report(f'{source.name}: ' + ('Копирование видео' if backend == 'copy' else video_hardware.LABELS[backend]))
                try:
                    _run_ffmpeg(['ffmpeg', '-nostdin', '-y', '-v', 'error', '-i', str(source),
                        '-map', '0:v:0', '-map', '0:a?', *options, *audio_options(info),
                        '-movflags', '+faststart', str(partial)], log_path, stop,
                        lambda percent: report(f'{index}/{len(checked)}: подготовка MP4 {percent}% — {source.name}'), duration)
                    if not player_compatible(probe_video(partial)):
                        raise ValueError('Кодировщик не вернул совместимый MP4.')
                    break
                except ValueError as error:
                    if backend in ('copy', 'cpu'):
                        raise
                    video_hardware.disable(backend, reason=str(error))
                    partial.unlink(missing_ok=True)
                    report('Оборудование: ' + video_hardware.LABELS[backend] + ': '
                           + video_hardware.failure_reason(backend) + '. Пробую следующий кодировщик.')
        stat = source.stat()
        if stat.st_size != entry['size'] or str(stat.st_mtime_ns) != entry['mtime']:
            raise ValueError('Исходное видео изменилось во время подготовки. Повторите сканирование.')
        if not player_compatible(probe_video(partial)):
            raise ValueError(f'{source.name}: подготовленная копия не прошла проверку MP4.')
        partial.replace(target)
        disk_space.register(target)
    manifest = scan(target_root, enforce_upload_limit=enforce_upload_limit)
    manifest['title'] = original['title']
    if original.get('selection'):
        manifest['selection'] = manifest['files'][0]['path']
    return manifest



def audio_language(stream):
    """Use declared metadata only; a studio title does not identify a language."""
    tags = {str(key).lower(): value for key, value in stream.get('tags', {}).items()}
    code = str(tags.get('language') or '').strip().lower().replace('_', '-').split('-')[0]
    aliases = {'ru': 'rus', 'en': 'eng', 'ja': 'jpn', 'uk': 'ukr',
               'de': 'deu', 'ger': 'deu', 'fr': 'fra', 'fre': 'fra',
               'es': 'spa', 'it': 'ita', 'ko': 'kor', 'zh': 'zho', 'chi': 'zho',
               'pt': 'por', 'pl': 'pol', 'tr': 'tur'}
    return aliases.get(code, code) if code not in ('', 'und', 'unknown', 'mul', 'zxx') else ''


def audio_language_label(code):
    return {'rus': 'Русский', 'eng': 'Английский', 'jpn': 'Японский',
            'ukr': 'Украинский', 'deu': 'Немецкий', 'fra': 'Французский',
            'spa': 'Испанский', 'ita': 'Итальянский', 'kor': 'Корейский',
            'zho': 'Китайский', 'por': 'Португальский', 'pol': 'Польский',
            'tur': 'Турецкий'}.get(code, code.upper() if code else 'Язык не указан')


def audio_choices(source):
    source = Path(source)
    streams = [s for s in probe_video(source).get('streams', []) if s.get('codec_type') == 'audio']
    saved_path = source.parent / '.kinotime-audio.json'
    saved = json.loads(saved_path.read_text(encoding='utf-8')) if saved_path.exists() else {}
    stat = source.stat()
    record = saved.get(source.name, {})
    choices = []
    for index, stream in enumerate(streams):
        tags = stream.get('tags', {})
        name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', str(tags.get('title') or tags.get('language') or f'Дорожка {index+1}'))[:100].strip(' .') or f'Дорожка {index+1}'
        choices.append({'index': index, 'name': name, 'enabled': True,
                        'language': audio_language(stream)})
    if record.get('size') == stat.st_size and record.get('mtime') == str(stat.st_mtime_ns):
        old = {c['index']: c for c in record.get('choices', [])}
        for choice in choices:
            if choice['index'] in old:
                choice.update(name=old[choice['index']]['name'], enabled=old[choice['index']]['enabled'])
    counts = {}
    for choice in choices:
        counts[choice['name']] = counts.get(choice['name'],0)+1
    for choice in choices:
        if counts[choice['name']]>1:
            choice['name'] = choice['name'][:80]+f" (дорожка {choice['index']+1})"
    return choices


def save_audio_choices(source, choices):
    source = Path(source)
    enabled = [c for c in choices if c['enabled']]
    if not enabled:
        raise ValueError('Выберите хотя бы одну дорожку.')
    for choice in choices:
        if re.search(r'[<>:"/\\|?*\x00-\x1f]', choice['name']):
            raise ValueError('Название озвучки содержит недопустимые символы имени файла.')
        if not choice['name'].strip() or len(choice['name']) > 100:
            raise ValueError('Название озвучки: от 1 до 100 символов.')
    if len({c['name'].casefold() for c in enabled}) != len(enabled):
        raise ValueError('Задайте разные названия выбранных озвучек.')
    stat = source.stat()
    path = source.parent / '.kinotime-audio.json'
    data = json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}
    data[source.name] = {'size': stat.st_size, 'mtime': str(stat.st_mtime_ns), 'choices': choices}
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    temp.replace(path)


QUALITY_PROFILES = {
    '1080p': {'width': 1920, 'height': 1080, 'audio': 192, 'video': 6000, 'limit': 3.75},
    '720p': {'width': 1280, 'height': 720, 'audio': 160, 'video': 3000, 'limit': 1.85},
    '480p': {'width': 854, 'height': 480, 'audio': 128, 'video': 1400, 'limit': .95},
    '360p': {'width': 640, 'height': 360, 'audio': 96, 'video': 800, 'limit': .55},
}
QUALITY_NAMES = ('source', '1080p', '720p', '480p', '360p')
LAUNCHER_SECTIONS = {'Фильмы': 'movies', 'Мультфильмы': 'cartoons',
                     'Аниме · полнометражное': 'anime'}


def resolve_quality_selection(media, requested):
    """Filter a requested profile list for one source without upscaling or duplicates."""
    requested = list(dict.fromkeys(name for name in requested if name in QUALITY_NAMES))
    selected = [name for name in requested if name in media['eligible']]
    if 'source' in selected:
        for name in tuple(selected):
            if name == 'source':
                continue
            profile = QUALITY_PROFILES[name]
            if (media['width'] <= profile['width'] * 1.05
                    and media['height'] <= profile['height'] * 1.05):
                selected.remove(name)
    return selected


def quality_choices(source):
    """Return saved choices, or sensible phone-oriented defaults for a new movie."""
    source = Path(source)
    info = probe_video(source)
    video = next((stream for stream in info.get('streams', [])
                  if stream.get('codec_type') == 'video'), {})
    width, height = int(video.get('width', 0)), int(video.get('height', 0))
    # Cropped widescreen releases can have full 1920/1280 width but less height.
    eligible = ['source'] + [name for name, profile in QUALITY_PROFILES.items()
                if width >= profile['width'] * .95
                or (width >= profile['width'] * .85 and height >= profile['height'] * .85)]
    stat = source.stat()
    path = source.parent / '.kinotime-media.json'
    saved = json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}
    record = saved.get(source.name, {})
    selected = record.get('qualities') if (record.get('size') == stat.st_size
                                            and record.get('mtime') == str(stat.st_mtime_ns)) else None
    if not selected:
        selected = [name for name in eligible if name != 'source'][:2] or ['source']
    selected = [name for name in selected if name in eligible]
    return {'width': width, 'height': height, 'duration': float(info.get('format', {}).get('duration', 0)),
            'eligible': eligible, 'selected': selected or ['source'],
            'mode': record.get('mode', 'fast')}


def has_saved_media_choices(source):
    source = Path(source)
    path = source.parent / '.kinotime-media.json'
    if not path.exists():
        return False
    record = json.loads(path.read_text(encoding='utf-8')).get(source.name, {})
    stat = source.stat()
    return (record.get('size') == stat.st_size and record.get('mtime') == str(stat.st_mtime_ns)
            and bool(record.get('qualities')))


def save_media_choices(source, qualities, mode='fast'):
    source = Path(source)
    available = quality_choices(source)['eligible']
    qualities = [name for name in qualities if name in available]
    if not qualities:
        raise ValueError('Выберите хотя бы одно качество.')
    if mode not in ('fast', 'exact'):
        raise ValueError('Неизвестный режим кодирования.')
    path = source.parent / '.kinotime-media.json'
    data = json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}
    stat = source.stat()
    data[source.name] = {'size': stat.st_size, 'mtime': str(stat.st_mtime_ns),
                         'qualities': qualities, 'mode': mode}
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    temp.replace(path)


class DiskPressureError(OSError):
    """Stop writing before the disk is full; retry after safe reclamation."""


class EncodingProgress(int):
    def __new__(cls, percent, values, duration):
        result = int.__new__(cls, percent)
        result.details = ''
        try:
            speed = float(values.get('speed', '0').rstrip('x'))
            fps = float(values.get('fps', '0'))
            elapsed = int(values.get('out_time_us') or values.get('out_time_ms') or 0) / 1_000_000
            if 0 < speed < 100000:
                remaining = max(0, int((duration - elapsed) / speed))
                result.details = f' · {speed:.2f}×' + (f' · {fps:.0f} кадр/с' if fps > 0 else '')
                result.details += f' · осталось ~{remaining // 60}:{remaining % 60:02d}'
        except (ValueError, OverflowError, AttributeError):
            pass
        return result


def progress_text(value):
    return f'{int(value)}%' + getattr(value, 'details', '')


def _run_ffmpeg(command, log_path, stop, progress=None, duration=0):
    inputs = [Path(command[i + 1]) for i, arg in enumerate(command[:-1]) if arg == '-i']
    output_dir = Path(log_path).parent
    # Include room for faststart rewriting and the audio track. This is an estimate,
    # backed by the live free-space check during encoding.
    expected = inputs[0].stat().st_size if inputs and inputs[0].is_file() else 0
    if '-b:v' in command and duration > 0:
        expected = int(float(command[command.index('-b:v') + 1].rstrip('k')) * 1000 * duration / 8)
    if '-b:a' in command and duration > 0:
        expected += int(float(command[command.index('-b:a') + 1].rstrip('k')) * 1000 * duration / 8)
    while True:
        try:
            with disk_space.reservation(output_dir, expected * 2, stop,
                                        getattr(stop, 'disk_report', lambda text: None), inputs):
                return _run_ffmpeg_once(command, log_path, stop, progress, duration)
        except DiskPressureError:
            partial = Path(command[-1])
            if partial.name.endswith('.partial.mp4') and partial.parent == output_dir:
                partial.unlink(missing_ok=True)
            getattr(stop, 'disk_report', lambda text: None)('Мало места: текущий незавершённый файл будет создан заново после освобождения диска.')
            if stop.wait(1):
                raise InterruptedError('Подготовка остановлена во время ожидания места.')
            # The partially written output is restarted, not accepted as a ready MP4.


def _run_ffmpeg_once(command, log_path, stop, progress=None, duration=0):
    command = [media_tool('ffmpeg'), '-threads', str(video_hardware.cpu_threads()),
               '-filter_threads', str(min(4, video_hardware.cpu_threads())), *command[1:]]
    progress_path = Path(log_path).with_suffix('.progress')
    progress_path.unlink(missing_ok=True)
    if progress and duration > 0:
        command = command[:1] + ['-progress', str(progress_path), '-nostats'] + command[1:]
    with Path(log_path).open('wb') as log:
        process = subprocess.Popen(command, stdout=log, stderr=log,
                                   creationflags=video_hardware.process_flags())
        try:
            while process.poll() is None:
                if progress and progress_path.exists():
                    try:
                        values = {}
                        for line in progress_path.read_text(encoding='utf-8', errors='ignore').splitlines():
                            if '=' in line:
                                key, value = line.split('=', 1); values[key] = value
                        elapsed = int(values.get('out_time_us') or values.get('out_time_ms') or 0) / 1_000_000
                        progress(EncodingProgress(max(0, min(99, int(elapsed / duration * 100))), values, duration))
                    except (OSError, ValueError):
                        pass
                if disk_space.low(Path(log_path).parent):
                    raise DiskPressureError('Недостаточно места для продолжения записи.')
                if stop.wait(.35):
                    raise InterruptedError(getattr(stop, 'preparation_error', '') or 'Подготовка остановлена. Готовые копии сохранены.')
        finally:
            if process.poll() is None:
                process.terminate()
                process.wait()
    if process.returncode:
        if 'no space left on device' in Path(log_path).read_text(encoding='utf-8', errors='ignore').lower():
            raise DiskPressureError('На диске закончилось место.')
        raise ValueError('FFmpeg завершился с ошибкой. Подробности: ' + str(log_path))
    progress_path.unlink(missing_ok=True)
    if progress:
        progress(100)


def _quality_profile(name, source_info):
    if name != 'source':
        return dict(QUALITY_PROFILES[name])
    video = next(s for s in source_info['streams'] if s.get('codec_type') == 'video')
    width, height = int(video['width']), int(video['height'])
    # Source keeps its original resolution and may legitimately contain a large
    # compatible video stream. 3.75 GiB is only a safe Telegram ceiling; the
    # bitrate cap below still prevents low-resolution sources from being bloated.
    pixels = max(1, width * height)
    video_rate = max(450, min(2200, int(1200 * pixels / (854 * 480))))
    return {'width': width, 'height': height, 'audio': 128, 'video': video_rate,
            'limit': 3.75}


def _video_size_budget(profile, duration):
    """Reserve room for AAC and container overhead before copying a video stream."""
    return max(0, int(profile['limit'] * 1024**3 * .96
                      - profile['audio'] * 1000 * duration / 8))


def _can_copy_source(source, profile, source_info):
    video = next((s for s in source_info.get('streams', []) if s.get('codec_type') == 'video'), {})
    if video.get('codec_name') != 'h264' or video.get('pix_fmt') != 'yuv420p':
        return False
    duration = float(source_info.get('format', {}).get('duration', 0))
    if duration <= 0:
        return False
    try:
        rate = float(video.get('bit_rate', 0))
    except (ValueError, TypeError):
        rate = 0
    # Matroska often omits the stream bitrate. The whole source is a safe upper bound.
    estimate = rate * duration / 8 if rate > 0 else source.stat().st_size
    return estimate <= _video_size_budget(profile, duration)


def _prepare_quality_video(source, target, name, profile, source_info, mode, stop, report, progress=None):
    duration = float(source_info.get('format', {}).get('duration', 0))
    if duration <= 0:
        raise ValueError(f'{source.name}: не удалось определить длительность.')
    # Keep a safety margin for one audio track and MP4 metadata.
    size_rate = int(profile['limit'] * 1024**3 * 8 / duration / 1000 * .96 - profile['audio'])
    bitrate = max(250, min(profile['video'], size_rate))
    scale = (f"scale=w='min(iw,{profile['width']})':h='min(ih,{profile['height']})':"
             "force_original_aspect_ratio=decrease:force_divisible_by=2")
    partial = target.with_suffix('.partial.mp4')
    log_path = target.with_suffix('.conversion.log')
    base = ['ffmpeg', '-nostdin', '-y', '-v', 'error', '-i', str(source), '-map', '0:v:0',
            '-an', '-vf', scale]
    source_video = next((s for s in source_info.get('streams', []) if s.get('codec_type') == 'video'), {})
    if name == 'source' and _can_copy_source(source, profile, source_info):
        report(f'{name}: Оборудование: исходный видеоряд совместим; копирование без кодирования.')
        copy_base = ['ffmpeg', '-nostdin', '-y', '-v', 'error', '-i', str(source), '-map', '0:v:0',
                     '-an', '-c:v', 'copy', '-movflags', '+faststart', str(partial)]
        _run_ffmpeg(copy_base, log_path, stop, progress, duration)
        if partial.stat().st_size <= _video_size_budget(profile, duration):
            partial.replace(target)
            return
        partial.unlink()
        report(f'{name}: копия видео слишком большая; сжимаю с сохранением разрешения.')
    report(f'{name}: целевой битрейт видео · около {bitrate} Кбит/с (это не скорость обработки)')
    if mode == 'fast':
        for backend in video_hardware.candidates(mode, stop, report=report):
            codec = video_hardware.codec_args(backend, bitrate)
            report(f'{name}: {video_hardware.LABELS[backend]} · целевой битрейт {bitrate} Кбит/с')
            try:
                pipeline = video_hardware.intel_pipeline(source_video, profile) if backend == 'intel' else None
                if pipeline:
                    accelerated = ['ffmpeg', '-nostdin', '-y', '-v', 'error', *pipeline[0],
                                   '-i', str(source), '-map', '0:v:0', '-an', '-vf', pipeline[1]]
                    try:
                        report(f'{name}: Intel Quick Sync — декодирование, масштабирование и кодирование на GPU.')
                        # Hardware frames carry their format; do not request software conversion.
                        hardware_codec = [v for i, v in enumerate(codec)
                                          if v != '-pix_fmt' and (i == 0 or codec[i-1] != '-pix_fmt')]
                        _run_ffmpeg(accelerated + hardware_codec + ['-movflags', '+faststart', str(partial)],
                                    log_path, stop, progress, duration)
                        info = probe_video(partial)
                        stream = next((s for s in info.get('streams', []) if s.get('codec_type') == 'video'), {})
                        if stream.get('codec_name') != 'h264' or stream.get('pix_fmt') != 'yuv420p':
                            raise ValueError('Аппаратный конвейер вернул несовместимое видео.')
                        break
                    except ValueError as error:
                        partial.unlink(missing_ok=True)
                        report(f'{name}: аппаратное декодирование/масштабирование недоступно: {error}. '
                               'Продолжаю с обычным декодированием и кодированием Intel Quick Sync.')
                _run_ffmpeg(base + codec + ['-movflags', '+faststart', str(partial)], log_path, stop, progress, duration)
                prepared = probe_video(partial)
                stream = next((s for s in prepared.get('streams', []) if s.get('codec_type') == 'video'), None)
                if not stream or stream.get('codec_name') != 'h264' or stream.get('pix_fmt') != 'yuv420p':
                    raise ValueError('Кодировщик не вернул совместимый H.264/yuv420p.')
                break
            except ValueError as error:
                if backend == 'cpu':
                    raise
                video_hardware.disable(backend, reason=str(error))
                partial.unlink(missing_ok=True)
                report(f'{name}: Оборудование: {video_hardware.LABELS[backend]}: '
                       + video_hardware.failure_reason(backend) + '. Пробую следующий кодировщик.')
    else:
        codec = video_hardware.codec_args('cpu', bitrate, mode)
        if mode == 'exact':
            report('Оборудование: точный режим использует два прохода на процессоре.')
            passlog = str(target.with_suffix('.pass'))
            report(f'{name}: точный режим · анализ 1 из 2')
            _run_ffmpeg(base + codec + ['-pass', '1', '-passlogfile', passlog,
                        '-f', 'null', 'NUL' if os.name == 'nt' else '/dev/null'],
                        target.with_suffix('.pass1.log'), stop,
                        (lambda value: progress(value // 2)) if progress else None, duration)
            report(f'{name}: точный режим · кодирование 2 из 2')
            _run_ffmpeg(base + codec + ['-pass', '2', '-passlogfile', passlog,
                        '-movflags', '+faststart', str(partial)], log_path, stop,
                        (lambda value: progress(50 + value // 2)) if progress else None, duration)
            for suffix in ('-0.log', '-0.log.mbtree'):
                Path(passlog + suffix).unlink(missing_ok=True)
        else:
            _run_ffmpeg(base + codec + ['-movflags', '+faststart', str(partial)], log_path, stop, progress, duration)
    prepared = probe_video(partial)
    video = next((s for s in prepared.get('streams', []) if s.get('codec_type') == 'video'), None)
    if not video or video.get('codec_name') != 'h264' or video.get('pix_fmt') != 'yuv420p':
        raise ValueError(f'{name}: подготовленное видео не прошло проверку.')
    partial.replace(target)


def plan_folder_qualities(folder, qualities, stop):
    """Count the exact eligible quality/audio combinations before encoding."""
    original = scan(folder, enforce_upload_limit=False)
    root = Path(original['root'])
    plans = []
    for entry in original['files']:
        if stop.is_set():
            raise InterruptedError('Подготовка остановлена. Готовые копии сохранены.')
        source = root / entry['path']
        media = quality_choices(source)
        selected = resolve_quality_selection(media, qualities)
        if not selected:
            raise ValueError('Для выбранных качеств недостаточно разрешения исходника: ' + source.name
                             + '. Добавьте исходное разрешение или более низкое качество.')
        audio = audio_choices(source)
        if audio and not any(a['enabled'] for a in audio):
            raise ValueError('Выберите хотя бы одну озвучку: ' + source.name)
        plans.append({'entry': entry, 'qualities': selected, 'mode': media['mode'], 'audio': audio})
    variants = sum(len(plan['qualities']) * max(1, sum(a['enabled'] for a in plan['audio'])) for plan in plans)
    if variants > MAX_IMPORT_FILES:
        raise ValueError(f'Выбрано {variants} MP4 из {len(plans)} исходников. '
                         f'Один импорт допускает до {MAX_IMPORT_FILES}; уменьшите число озвучек или качеств.')
    return original, plans


def folder_queue_item(original, plans):
    children = []
    seasons = set()
    for plan in plans:
        source = plan['entry']['path']
        match = re.search(r'(?<![a-z0-9])s(\d{1,3})[ ._-]*e\d{1,3}(?!\d)', source, re.I)
        if match:
            seasons.add(int(match[1]))
        audio = [a['name'] for a in plan['audio'] if a['enabled']] if plan['audio'] else ['Без звука']
        for quality in plan['qualities']:
            for voice in audio:
                children.append({'name': f'{source} / {quality} / {voice}', 'source': source,
                                 'quality': quality, 'voice': voice, 'status': 'Ожидает', 'detail': ''})
    warning = ('По именам файлов найдены сезоны: ' + ', '.join(map(str, sorted(seasons)))
               + '. Для сезонного импорта выбирайте один сезон.' if len(seasons) > 1 else '')
    return {'file': original['root'], 'title': original['title'], 'status': 'pending', 'error': '',
            'warning': warning, 'source_count': len(plans), 'variants': children}


class FolderProgress:
    """Connect folder preparation events to the same queue used by movie batches."""
    def __init__(self, report, on_change):
        self.report, self.on_change = report, on_change
        self.items, self.by_name = [], {}
        self.last_update = 0

    def __call__(self, text):
        self.report(text)

    def plan(self, original, plans):
        item = folder_queue_item(original, plans)
        item['status'] = 'preparing'
        self.items = [item]
        self.by_name = {v['name']: v for v in item['variants']}
        self.on_change(self.items)
        self.report(f"{item['source_count']} исходников → {len(item['variants'])} MP4. "
                    'Каждое качество создаётся отдельно для каждой выбранной озвучки.')

    def variant(self, name, status, detail):
        child = self.by_name.get(name)
        if child is not None:
            child.update(status=status, detail=detail)
            if time.monotonic() - self.last_update >= .2:
                self.on_change(self.items)
                self.last_update = time.monotonic()

    def phase(self, status, error=''):
        if self.items:
            self.items[0].update(status=status, error=error)
            if status in ('paused', 'error'):
                for child in self.items[0]['variants']:
                    previous = child['status']
                    if previous not in ('Ожидает', 'Подготовлен', 'Принят', 'Ошибка'):
                        child.update(status='Пауза' if status == 'paused' else 'Ошибка',
                                     detail=f'Последний этап: {previous}. {error}')
            self.on_change(self.items)


def preparation_queue_rows(items, status_names):
    """Stable tree rows; sources, resolutions and audio tracks are distinct levels."""
    rows = []
    ready_states = ('Подготовлен', 'Принят')

    def summary(children):
        ready = sum(c.get('status') in ready_states for c in children)
        active = next((c.get('status') for c in children if c.get('status', 'Ожидает')
                       not in (*ready_states, 'Ожидает')), '')
        return f'{ready}/{len(children)} готово' + (f' · {active}' if active else '')

    for item in items:
        parent = item['file']
        prefix = 'queue:' + hashlib.sha256(parent.encode()).hexdigest()
        children = item.get('variants', [])
        status = status_names.get(item['status'], item['status'])
        if children and item['status'] in ('preparing', 'sending'):
            status += ' · ' + summary(children)
        details = (f"Исходников: {item.get('source_count', 1)} → MP4: {len(children)} · "
                   if children else '') + (item.get('error') or
                    ((item['warning'] + ' · ') if item.get('warning') else '') + item['file'])
        rows.append((parent, '', (item['title'], status, details), True, None))
        groups = {}
        for index, child in enumerate(children):
            source = child.get('source', '')
            if child.get('quality'):
                quality, voice = child['quality'], child.get('voice') or 'Без звука'
            else:
                parts = child['name'].split(' / ', 1)
                quality, voice = parts if len(parts) == 2 else ('Варианты', parts[0])
            groups.setdefault(source, {}).setdefault(quality, []).append((index, voice, child))
        for source_index, (source, qualities) in enumerate(groups.items()):
            source_id = parent
            source_path = str(Path(item['file']) / source) if source else None
            if source:
                source_id = f'{prefix}:source:{source_index}'
                variants = [child for entries in qualities.values() for _, _, child in entries]
                voices = {child.get('voice', '') for child in variants}
                rows.append((source_id, parent, (Path(source).name, summary(variants),
                    f'Качества: {len(qualities)} × озвучки: {len(voices)} = {len(variants)} MP4 · {source}'),
                    False, source_path))
            for quality_index, (quality, entries) in enumerate(qualities.items()):
                quality_id = f'{prefix}:quality:{source_index}:{quality_index}'
                label = 'Исходное разрешение' if quality == 'source' else quality
                rows.append((quality_id, source_id, (label, summary([c for _, _, c in entries]), ''),
                             True, source_path))
                for index, voice, child in entries:
                    rows.append((f'{prefix}:variant:{index}', quality_id,
                                 (voice, child.get('status', 'Ожидает'), child.get('detail', '')),
                                 False, source_path))
    return rows


class PreparationQueueView:
    def __init__(self, tree, status_names):
        self.tree, self.status_names = tree, status_names
        self.layout, self.values, self.sources = (), {}, {}

    def render(self, items):
        rows = preparation_queue_rows(items, self.status_names)
        layout = tuple((row[0], row[1]) for row in rows)
        if layout != self.layout:
            self.tree.delete(*self.tree.get_children())
            self.values.clear()
            self.sources = {key: source for key, _, _, _, source in rows if source}
            for key, parent, values, opened, _ in rows:
                self.tree.insert(parent, 'end', iid=key, text='', open=opened, values=values)
                self.values[key] = values
            self.layout = layout
        else:
            # Keep expanded sources, selection and scroll position during progress updates.
            for key, _, values, _, _ in rows:
                if self.values.get(key) != values:
                    self.tree.item(key, values=values)
                    self.values[key] = values


def episode_output_directory(source_path):
    """Stable episode directory that Win32 does not silently rename."""
    relative = Path(source_path).with_suffix('')
    name = relative.name
    if name != name.rstrip(' .'):
        identity = hashlib.sha256(str(source_path).encode('utf-8')).hexdigest()[:12]
        relative = relative.with_name((name.rstrip(' .') or 'episode') + '--' + identity)
    return relative


def prepare_folder_qualities(folder, qualities, report, stop):
    """Prepare every source and keep its episode identity across quality/voice variants."""
    original, plans = plan_folder_qualities(folder, qualities, stop)
    root = Path(original['root'])
    getattr(report, 'plan', lambda *args: None)(original, plans)
    signature = {'version': 'folder-qualities-1', 'source': original, 'plans': plans}
    key = hashlib.sha256(json.dumps(signature, sort_keys=True).encode()).hexdigest()[:24]
    cache_root = root.parent / 'KinoTime-Ready'
    if cache_root.resolve() == root:
        cache_root = root.parent / 'KinoTime-Prepared'
    destination = cache_root / ('folder-' + key)
    disk_space.protect(destination)
    entries = []
    variant_update = getattr(report, 'variant', lambda *args: None)
    for index, plan in enumerate(plans, 1):
        entry = plan['entry']
        relative = Path(entry['path'])
        source = root / relative

        def file_report(text):
            report(f'{index}/{len(plans)} · {source.name}: {text}')

        defer_ready = not bool(stop.__dict__.get('variant_ready'))

        def file_variant(name, status, detail):
            # A cached encode is not complete until its durable episode copy exists.
            if defer_ready and status in ('Подготовлен', 'Принят'):
                status = 'Сохранение в папку'
            variant_update(f'{entry["path"]} / {name}', status, detail)

        file_report.variant = file_variant
        report(f'{index}/{len(plans)} · {source.name}: качества — ' + ', '.join(plan['qualities']))
        result = prepare_media(source, file_report, stop, qualities=plan['qualities'], cache_root=cache_root)
        if stop.__dict__.get('variant_ready'):
            continue  # The streaming runner owns the stable aggregate and receipts.
        for variant in result['files']:
            if stop.is_set():
                raise InterruptedError('Подготовка остановлена. Готовые копии сохранены.')
            prepared = Path(result['root']) / variant['path']
            # A separate episode directory prevents identical voice names from
            # replacing files belonging to another episode.
            episode_directory = episode_output_directory(entry['path'])
            target = destination / variant['quality'] / episode_directory / prepared.name
            target.parent.mkdir(parents=True, exist_ok=True)
            if not target.exists():
                # Each process gets its own partial; fixed names race when two launchers
                # prepare the same destination and one can remove the other's file.
                partial = target.with_name(f'{target.stem}.{uuid.uuid4().hex}.partial.mp4')
                try:
                    os.link(prepared, partial)
                except OSError:
                    disk_space.copy(prepared, partial, stop, report)
                if not partial.is_file():
                    raise FileNotFoundError(f'{source.name}: временная копия не создана: {partial}')
                partial.replace(target)
            if not target.is_file():
                raise FileNotFoundError(f'{source.name}: итоговая копия не создана: {target}')
            stat = target.stat()
            expected = prepared.stat()
            if stat.st_size != expected.st_size or stat.st_mtime_ns != expected.st_mtime_ns:
                raise ValueError(f'{source.name}: готовая копия изменена. Сохраните файлы для проверки.')
            disk_space.register(target, alias=prepared)
            variant_update(
                f'{entry["path"]} / {variant["quality"]} / {variant.get("voice") or "Без звука"}',
                'Подготовлен', f'{stat.st_size / 1024**3:.2f} ГиБ')
            entries.append({'path': target.relative_to(destination).as_posix(), 'size': stat.st_size,
                            'mtime': str(stat.st_mtime_ns), 'name': relative.stem,
                            'quality': variant['quality'], 'voice': variant['voice'][:80]})
        stat = source.stat()
        if stat.st_size != entry['size'] or str(stat.st_mtime_ns) != entry['mtime']:
            raise ValueError('Исходный файл изменился во время подготовки: ' + source.name)
    return {'version': 1, 'title': original['title'], 'root': str(destination), 'files': entries}


def prepare_media(folder, report, stop, *, qualities=None, cache_root=None, media_plan=None):
    with disk_space.session():
        return _prepare_media(folder, report, stop, qualities=qualities, cache_root=cache_root, media_plan=media_plan)


def _prepare_media(folder, report, stop, *, qualities=None, cache_root=None, media_plan=None):
    stop.disk_report = report
    source = Path(folder)
    if stop.is_set():
        raise InterruptedError('Подготовка остановлена.')
    # Legacy folders keep their layout unless a profile selection was requested.
    if not source.is_file():
        if qualities is not None:
            return prepare_folder_qualities(folder, qualities, report, stop)
        return prepare_media_base(folder, report, stop)
    choices = [dict(a) for a in media_plan['audio']] if media_plan is not None else audio_choices(source)
    selected_audio = [choice for choice in choices if choice['enabled']]
    if not selected_audio:
        if choices:
            raise ValueError('Выберите хотя бы одну озвучку: ' + source.name)
        if qualities is None and not has_saved_media_choices(source):
            return prepare_media_base(source, report, stop)
        selected_audio = [{'index': None, 'name': 'Без звука', 'enabled': True}]
    if choices and media_plan is None:
        save_audio_choices(source, choices)
    media = ({'selected': list(media_plan['qualities']), 'mode': media_plan['mode']}
             if media_plan is not None else quality_choices(source))
    if media_plan is None:
        media['selected'] = resolve_quality_selection(media, qualities if qualities is not None else media['selected'])
    if not media['selected']:
        raise ValueError('Выбранные качества выше разрешения исходника: ' + source.name)
    if media_plan is None:
        save_media_choices(source, media['selected'], media['mode'])
    count = len(media['selected']) * len(selected_audio)
    if count > MAX_IMPORT_FILES:
        raise ValueError(f'Выбрано {count} MP4. Один импорт допускает до {MAX_IMPORT_FILES}.')
    if hasattr(report, 'plan'):
        original = scan(source, enforce_upload_limit=False)
        report.plan(original, [{'entry': original['files'][0], 'qualities': media['selected'],
                                'audio': choices, 'mode': media['mode']}])
        original_update = report.variant
        update = lambda name, status, detail: original_update(f'{source.name} / {name}', status, detail)
    else:
        update = getattr(report, 'variant', lambda *args: None)
    source_info = probe_video(source)
    signature = {'source': str(source.resolve()), 'size': source.stat().st_size,
                 'mtime': str(source.stat().st_mtime_ns), 'audio': selected_audio,
                 'qualities': media['selected'], 'mode': media['mode']}
    key = hashlib.sha256(json.dumps(signature, sort_keys=True).encode()).hexdigest()[:24]
    destination = (Path(cache_root) if cache_root is not None else source.parent.parent / 'KinoTime-Ready') / ('mobile-' + key)
    destination.mkdir(parents=True, exist_ok=True)
    disk_space.protect(destination)
    entries, failures = [], []
    ready_hook = stop.__dict__.get('variant_ready')
    skip_hook = stop.__dict__.get('variant_skip')

    def completed(target, quality, audio):
        disk_space.register(target)
        stat = target.stat()
        entries.append({'path': target.relative_to(destination).as_posix(), 'size': stat.st_size,
                        'mtime': str(stat.st_mtime_ns), 'quality': quality,
                        'voice': audio['name'] if audio['index'] is not None else ''})
        update(f"{quality} / {audio['name']}", 'Подготовлен', f'{stat.st_size / 1024**3:.2f} ГиБ')
        if ready_hook:
            ready_hook(source, destination, dict(entries[-1]))

    for quality in media['selected']:
        profile = _quality_profile(quality, source_info)
        quality_root = destination / quality
        quality_root.mkdir(parents=True, exist_ok=True)
        video_target = destination / f'.video-{quality}.mp4'
        def audio_target(audio):
            name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', audio['name']).strip(' .') or 'Озвучка'
            index = audio['index'] + 1 if audio['index'] is not None else 1
            return quality_root / f'{index:02d} - {name}.mp4'
        try:
            skipped = {str(audio_target(a)) for a in selected_audio
                       if skip_hook and skip_hook(source, quality, audio_target(a).name)}
            if ready_hook:
                # Drain completed outputs first, even when the remaining voices
                # need a new video intermediate and the disk is already full.
                for audio in selected_audio:
                    target = audio_target(audio)
                    if str(target) not in skipped and target.is_file():
                        completed(target, quality, audio)
                        skipped.add(str(target))
            if quality == 'source' and video_target.is_file() and video_target.stat().st_size > _video_size_budget(
                    profile, float(source_info.get('format', {}).get('duration', 0))):
                report('Исходное разрешение: промежуточное видео превышает лимит; создаю сжатую копию.')
                disk_space.discard_intermediate(video_target)
                video_target.unlink(missing_ok=True)
            if not video_target.exists() and not all(str(audio_target(a)) in skipped or audio_target(a).is_file()
                                                     for a in selected_audio):
                for audio in selected_audio:
                    update(f"{quality} / {audio['name']}", 'Подготовка видео', '')
                copying = quality == 'source' and _can_copy_source(source, profile, source_info)
                def video_progress(value):
                    for audio in selected_audio:
                        phase = 'Копирование видео' if copying else 'Кодирование'
                        update(f"{quality} / {audio['name']}", f'{phase} · {progress_text(value)}', '')
                _prepare_quality_video(source, video_target, quality, profile, source_info,
                                       media['mode'], stop, report, video_progress)
            for audio in selected_audio:
                label = f"{quality} / {audio['name']}"
                target = audio_target(audio)
                if str(target) in skipped:
                    update(label, 'Принят', 'Подтверждён ботом; повторная обработка не нужна')
                    continue
                partial = target.with_suffix('.partial.mp4')
                if not target.exists():
                    update(label, 'Добавление озвучки', '')
                    command = ['ffmpeg', '-nostdin', '-y', '-v', 'error', '-i', str(video_target),
                               '-i', str(source), '-map', '0:v:0', '-map', f"1:a:{audio['index']}",
                               '-c:v', 'copy', '-c:a', 'aac', '-b:a', f"{profile['audio']}k",
                               '-metadata:s:a:0', 'title=' + audio['name'], '-movflags', '+faststart',
                               str(partial)]
                    if audio['index'] is None:
                        disk_space.copy(video_target, partial, stop, report)
                    else:
                        _run_ffmpeg(command, target.with_suffix('.mux.log'), stop,
                                    lambda value, current=label: update(current, f'Озвучка · {progress_text(value)}', ''),
                                    float(source_info.get('format', {}).get('duration', 0)))
                    # A disk pressure cleanup or a failed mux can remove the
                    # partial output. Never probe or publish it until it exists.
                    if not partial.is_file():
                        raise ValueError(f'{label}: временный MP4 не создан: {partial}')
                    info = probe_video(partial)
                    if not player_compatible(info) or sum(s.get('codec_type') == 'audio'
                                                           for s in info['streams']) != int(audio['index'] is not None):
                        raise ValueError(f'{label}: проверка MP4 не пройдена.')
                    if partial.stat().st_size > profile['limit'] * 1024**3:
                        raise ValueError(f'{label}: итоговый файл превысил предел {profile["limit"]:.2f} ГиБ.')
                    partial.replace(target)
                completed(target, quality, audio)
            # Reusable only while creating this quality's voices. Finished MP4s remain.
            disk_space.discard_intermediate(video_target)
        except InterruptedError:
            raise
        except Exception as error:
            if ready_hook:
                raise  # An uncertain send must pause rendering and preserve its files.
            for audio in selected_audio:
                update(f"{quality} / {audio['name']}", 'Ошибка', str(error))
            failures.append(quality + ': ' + str(error))
    if failures:
        raise ValueError('Не все озвучки подготовлены. Готовые копии сохранены. '+ '; '.join(failures))
    # Unique root identifies one movie; no single-file selection for multiple variants.
    return {'version': 1, 'title': movie_title(source.name), 'root': str(destination), 'files': entries}


class FileRejectedError(ValueError):
    """Explicit server rejection; continue with the next queue entry."""


class BeforeSendError(ValueError):
    """Preparation failed before invoking Send; retry does not duplicate a message."""


def normalized_label(value):
    value = unicodedata.normalize('NFKC', str(value or ''))
    return ' '.join(''.join(c for c in value if unicodedata.category(c) != 'Cf').split()).casefold()


def matches_chat_label(value, bot_name, username):
    # Qt can expose a name and a status on separate lines. Never accept substring
    # matches: another chat may mention this bot in its title or recent message.
    lines = [line for line in str(value or '').splitlines() if line.strip()]
    return bool(lines) and normalized_label(lines[0]) in (
        normalized_label(bot_name), normalized_label('@' + username))


def matches_window_title(value, bot_name, username):
    # Desktop exposes the current chat as "<chat> @ <account> (unread)" in
    # the native window title even when the visual top bar has no UIA name.
    # Match the entire chat part, never a mention in the account name or suffix.
    title = normalized_label(value)
    if '\n' in str(value or '') or '\r' in str(value or ''):
        return False
    expected = (normalized_label(bot_name), normalized_label('@' + username))
    if title in expected:
        return True
    return any(name and title.startswith(name + ' @ ') and title[len(name) + 3:].strip()
               for name in expected)


def control_labels(control):
    labels = []
    try:
        labels.append(control.window_text())
    except Exception:
        pass
    try:
        labels.append(control.element_info.name)
    except Exception:
        pass
    try:
        labels.append(control.legacy_properties().get('Name', ''))
    except Exception:
        pass
    return [label for label in labels if label]


def scan(folder, *, enforce_upload_limit=True):
    chosen = Path(folder)
    if chosen.is_symlink():
        raise ValueError('Выберите исходный файл или папку, а не ссылку.')
    chosen = chosen.resolve()
    single = chosen.is_file()
    root = chosen.parent if single else chosen
    files = []
    if not root.is_dir():
        raise ValueError('Выберите папку фильма.')
    walk = [(root, [], [chosen.name])] if single else os.walk(root, followlinks=False)
    for directory, dirs, names in walk:
        dirs[:] = sorted(d for d in dirs if not (Path(directory) / d).is_symlink())
        for name in sorted(names):
            path = Path(directory) / name
            if path.suffix.lower() not in EXTENSIONS:
                continue
            if path.is_symlink() or root not in path.resolve().parents:
                raise ValueError('Ссылки на видео вне папки не поддерживаются.')
            stat = path.stat()
            if stat.st_size <= 0:
                raise ValueError(f'{name}: пустой файл.')
            if enforce_upload_limit and stat.st_size > 4 * 1024**3:
                raise ValueError(f'{name}: файл для отправки больше 4 ГиБ ({stat.st_size / 1024**3:.2f} ГиБ). Нужно уменьшить размер готового видео.')
            files.append({'path': path.relative_to(root).as_posix(), 'size': stat.st_size,
                          'mtime': str(stat.st_mtime_ns)})
            if len(files) > MAX_IMPORT_FILES:
                raise ValueError(f'В одном импорте может быть не более {MAX_IMPORT_FILES} видео.')
    if not files:
        raise ValueError('В папке нет видео.')
    manifest = {'version': 1, 'title': movie_title(chosen.name) if single else root.name,
                'root': str(root), 'files': files}
    if single:
        manifest['selection'] = chosen.name
    return manifest


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError('Перенаправление запрещено. Укажите основной адрес панели.')


class ConnectionExpired(ValueError):
    pass


class TemporaryConnectionError(OSError):
    def __init__(self, message='', retry_after=None):
        super().__init__(message)
        self.retry_after = retry_after


class Client:
    def __init__(self, url, token, *, instance_id='', report=lambda message: None):
        url = url.strip()
        parsed = urlsplit(url)
        if (parsed.scheme != 'https' and not (parsed.scheme == 'http' and parsed.hostname in ('127.0.0.1', 'localhost'))
                or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment
                or parsed.path not in ('', '/')):
            raise ValueError('Нужен HTTPS-адрес панели без пути (localhost допускает HTTP).')
        self.url, self.token = url.rstrip('/'), token.strip()
        if not re.fullmatch(r'[A-Za-z0-9_-]{30,100}', self.token):
            raise ValueError('В поле «Код подключения» вставьте только сам код из ответа на /desktop. '
                             'Без слова «Код:», адреса панели и остального сообщения.')
        self.opener = urllib.request.build_opener(NoRedirect)
        if instance_id and not launcher_device.valid_instance(instance_id):
            raise ValueError('Некорректный идентификатор компьютера.')
        self.instance_id, self.report = instance_id, report
        self.negotiated = False

    def request(self, path, body=None):
        if self.instance_id and path != 'session' and not self.negotiated:
            self.request('session')
        if path in ('import', 'stream') and self.instance_id:
            body = launcher_device.hash_manifest(body, self.report)
        result = self._request(path, body)
        if path == 'session' and self.instance_id:
            if result.get('capabilities', {}).get('multi_launcher') != 1:
                raise ValueError('Сначала обновите серверную часть до версии с поддержкой нескольких компьютеров и перезапустите бота.')
            self.negotiated = True
        return result

    def _request(self, path, body=None):
        data = None if body is None else json.dumps(body, ensure_ascii=False).encode()
        if data and len(data) > MAX_JSON_BYTES:
            raise ValueError('Список файлов слишком большой.')
        headers = {'Authorization': 'Bearer ' + self.token, 'Content-Type': 'application/json'}
        if self.instance_id:
            headers['X-KinoTime-Instance'] = self.instance_id
        request = urllib.request.Request(self.url + '/api/desktop/' + path, data=data, headers=headers)
        try:
            with self.opener.open(request, timeout=30) as response:
                try:
                    return json.load(response)
                except (ValueError, UnicodeError) as error:
                    raise TemporaryConnectionError('API панели вернул неполный или некорректный ответ.') from error
        except urllib.error.HTTPError as error:
            try:
                message = json.load(error)['error']
            except (ValueError, KeyError):
                message = f'Ошибка соединения: {error.code}'
            if error.code == 404:
                if path.endswith('/reopen'):
                    message = ('На сервере недоступен метод восстановления карточки (HTTP 404). '
                               'Обновите webpanel/server.py и webpanel/imports.py на хостинге и полностью перезапустите бот. '
                               'Обновления одного лаунчера недостаточно.')
                elif path.startswith('import/'):
                    message = ('Черновик не найден или недоступен текущему коду подключения (HTTP 404). '
                               'Проверьте, что подключён тот же бот и администратор, а карточка ещё есть в веб-панели. '
                               'Не удаляйте локальные файлы и журналы.')
                else:
                    message = f'Метод API {path} не найден (HTTP 404). Проверьте версию серверной части.'
            if error.code == 401:
                raise ConnectionExpired(message) from error
            if error.code == 429 or error.code >= 500:
                try:
                    retry_after = max(0, min(300, int(error.headers.get('Retry-After', '0'))))
                except (ValueError, TypeError):
                    retry_after = 0
                raise TemporaryConnectionError(message, retry_after) from error
            raise ValueError(message) from error
        except (urllib.error.URLError, OSError, EOFError, http.client.HTTPException) as error:
            reason = getattr(error, 'reason', error)
            raise TemporaryConnectionError('Нет связи с API панели: ' + str(reason)) from error


def panel_connection_error(error):
    if isinstance(error, ConnectionExpired):
        return ('● Нужен новый код /desktop',
                'Код подключения истёк или недействителен. Получите новый через /desktop и вставьте в Настройки.')
    if isinstance(error, TemporaryConnectionError):
        return ('● Панель недоступна — повтор подключения', str(error))
    return ('● Ошибка подключения — откройте Настройки', str(error))


class RetryingClient:
    def __init__(self, client, stop, report):
        self.client, self.stop, self.report = client, stop, report

    @property
    def instance_id(self):
        value = getattr(self.client, 'instance_id', '')
        return value if launcher_device.valid_instance(value) else ''

    def request(self, path, body=None):
        for attempt in range(120):
            if self.stop.is_set():
                raise InterruptedError('Очередь остановлена.')
            try:
                result = self.client.request(path, body)
                if attempt:
                    self.report('Связь с API панели восстановлена. Работа продолжается.')
                return result
            except TemporaryConnectionError as error:
                delay = max(min(60, 5 * (attempt + 1)), error.retry_after or 0)
                self.report(f'{error} Повтор через {delay} с; прогресс сохранён.')
                if self.stop.wait(delay):
                    raise InterruptedError('Очередь остановлена.')
        raise TemporaryConnectionError('Связь не восстановлена. Запустите очередь повторно.')


@contextmanager
def queue_lock(folder):
    with (Path(folder) / '.kinotime-batch.lock').open('a+b') as lock:
        lock.seek(0, 2)
        if not lock.tell():
            lock.write(b'0')
            lock.flush()
        lock.seek(0)
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            raise ValueError('Эта очередь уже запущена в другом окне лаунчера.') from error
        try:
            yield
        finally:
            lock.seek(0)
            if os.name == 'nt':
                msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(lock, fcntl.LOCK_UN)


@contextmanager
def keep_awake():
    # A temporary execution request, not a change to sleep or lock settings.
    if os.name == 'nt':
        import ctypes
        ctypes.windll.kernel32.SetThreadExecutionState(0x80000003)
    try:
        yield
    finally:
        if os.name == 'nt':
            ctypes.windll.kernel32.SetThreadExecutionState(0x80000000)


def recover_batch_cards(folder, client, kind, changed):
    if kind not in ('rejected', 'published'):
        raise ValueError('Некорректный тип восстановления.')
    root = Path(folder).resolve()
    with queue_lock(root):
        items = batch_plan(root)
        count, errors = 0, []
        for item in items:
            if not item.get('draft_id'):
                continue
            try:
                actual = client.request('import/' + item['draft_id'])['draft_status']
                if actual == kind:
                    client.request('import/' + item['draft_id'] + '/reopen', {'kind': kind})
                    item.update(status='prepared', error='Карточка возвращена на проверку в WEB.')
                    item.pop('retry_rejected', None)
                    item.pop('retry_published', None)
                    count += 1
                elif actual in ('published', 'rejected'):
                    item.update(status=actual)
            except (ConnectionExpired, TemporaryConnectionError, InterruptedError):
                raise
            except ValueError as error:
                errors.append(item['title'] + ': ' + str(error))
            finally:
                journal = root / '.kinotime-batch.json'
                temp = journal.with_suffix('.tmp')
                temp.write_text(json.dumps({i['file']: i for i in items}, ensure_ascii=False, indent=2), encoding='utf-8')
                temp.replace(journal)
                changed(items)
        if not count and not errors:
            errors.append('На сервере нет подходящих карточек в выбранной папке.')
        return count, errors


def batch_plan(folder):
    root = Path(folder).resolve()
    if not root.is_dir():
        raise ValueError('Выберите общую папку с видеофайлами.')
    path = root / '.kinotime-batch.json'
    saved = json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}
    if not isinstance(saved, dict):
        raise ValueError('Повреждён журнал очереди. Сохраните его для проверки.')
    items = []
    for source in sorted(root.iterdir()):
        if not source.is_file() or source.is_symlink() or source.suffix.lower() not in EXTENSIONS:
            continue
        stat = source.stat()
        old = saved.get(source.name, {})
        if not isinstance(old, dict):
            raise ValueError('Повреждена запись журнала очереди. Сохраните журнал для проверки.')
        item = {'file': source.name, 'title': movie_title(source.name), 'size': stat.st_size,
                'mtime': str(stat.st_mtime_ns), 'status': 'pending', 'error': ''}
        if old.get('size') == item['size'] and old.get('mtime') == item['mtime']:
            item.update({k: old[k] for k in ('status', 'error', 'draft_id', 'variants', 'scope', 'retry_rejected', 'retry_published') if k in old})
        audio_path = root / '.kinotime-audio.json'
        media_path = root / '.kinotime-media.json'
        if not item.get('variants') and audio_path.exists() and media_path.exists():
            audio_config = json.loads(audio_path.read_text(encoding='utf-8')).get(source.name, {})
            media_config = json.loads(media_path.read_text(encoding='utf-8')).get(source.name, {})
            valid_audio = (audio_config.get('size') == item['size']
                           and audio_config.get('mtime') == item['mtime'])
            valid_media = (media_config.get('size') == item['size']
                           and media_config.get('mtime') == item['mtime'])
            if valid_audio and valid_media:
                item['variants'] = [
                    {'name': f"{quality} / {choice['name']}", 'status': 'Ожидает', 'detail': ''}
                    for quality in media_config.get('qualities', [])
                    for choice in audio_config.get('choices', []) if choice['enabled']
                ]
        items.append(item)
    if not items or len(items) > 500:
        raise ValueError('Нужно от 1 до 500 видеофайлов непосредственно в выбранной папке.')
    return items



def send_with_recovery(client, prepared, manifest, desktop, stop, report, connect, send):
    """Retry transient chat verification; transfer journals prevent duplicate sends."""
    while not stop.is_set():
        state = client.request('import/' + prepared['draft']['id'])
        if state['files'] and all(f['status']=='ready' for f in state['files']):
            return desktop
        try:
            if desktop is None:
                desktop = connect(prepared)
            send(client, prepared, manifest, desktop, stop, report)
            confirmed = client.request('import/' + prepared['draft']['id'])
            if isinstance(desktop, TelegramDesktop):
                desktop._confirmed_receipts = {f['id'] for f in confirmed['files'] if f['status']=='ready'}
            return desktop
        except BeforeSendError as error:
            if not any(text in str(error) for text in ('заголовок чата', 'потерял фокус', 'На переднем плане')):
                raise
            report('Временно не удалось проверить чат Telegram. Повтор через 8 секунд; принятые файлы будут пропущены.')
            if stop.wait(8):
                raise InterruptedError('Очередь остановлена.')
            desktop = None
    raise InterruptedError('Очередь остановлена.')

def run_batch(folder, client, stop, report, changed, connect, media=prepare_media, send=None, *, prepare_only=False, require_prepared=False):
    """One file is one movie; local failures skip, uncertain UI outcomes pause."""
    send = send or transfer
    root = Path(folder).resolve()
    with queue_lock(root), keep_awake():
        items = batch_plan(root)
        journal = root / '.kinotime-batch.json'

        def persist():
            temp = journal.with_suffix('.tmp')
            temp.write_text(json.dumps({i['file']: i for i in items}, ensure_ascii=False, indent=2), encoding='utf-8')
            temp.replace(journal)
            changed(items)

        safe_client = RetryingClient(client, stop, report)
        try:
            session = safe_client.request('session')
        except ValueError as error:
            if isinstance(error, ConnectionExpired):
                raise
            raise ValueError('Не удалось проверить поддержку очереди. Обновите сервер из пакета массового импорта. '
                             + str(error)) from error
        if session.get('protocol') != 2:
            raise ValueError('Сервер не поддерживает отдельные фильмы из общей папки. Установите обновление сервера.')
        scope = hashlib.sha256(f'{client.url}|{session["admin_id"]}|{session["bot_username"]}'.encode()).hexdigest()
        for item in items:
            instance = getattr(client, 'instance_id', '')
            if launcher_device.valid_instance(instance):
                if item.get('instance_id') not in (None, instance):
                    item.pop('draft_id', None)
                    item.update(status='pending', error='', scope='')
                item['instance_id'] = instance
            if item.get('scope') != scope:
                item.update(status='pending', error='', scope=scope)
        if require_prepared and any(not i.get('draft_id') or i['status'] == 'pending' for i in items):
            raise ValueError('Сначала выполните шаг 1: создайте отдельные карточки и настройте их в WEB-панели.')
        persist()
        desktop = None
        for item in items:
            if stop.is_set():
                break
            if item['status'] == 'complete':
                continue
            if item['status'] == 'published' and not item.get('retry_published'):
                continue
            if prepare_only and item['status'] == 'rejected' and not item.get('retry_rejected'):
                continue
            item.update(status='preparing', error='')
            persist()
            try:
                def item_report(text):
                    report(text)
                def variant_report(name, status, detail):
                    variants = item.setdefault('variants', [])
                    variant = next((v for v in variants if v['name']==name), None)
                    if variant is None:
                        variant = {'name':name}; variants.append(variant)
                    variant.update(status=status, detail=detail)
                    persist()
                item_report.variant = variant_report
                selected_media = media
                if (media is prepare_media and item.get('draft_id')
                        and not has_saved_media_choices(root / item['file'])
                        and item['file'] not in (json.loads((root / '.kinotime-audio.json').read_text(encoding='utf-8'))
                                               if (root / '.kinotime-audio.json').exists() else {})):
                    selected_media = prepare_media_base
                manifest = selected_media(root / item['file'], item_report, stop)
                manifest['catalog_section'] = getattr(report, 'catalog_section', 'movies')
                if item.get('retry_rejected'):
                    manifest['retry_rejected'] = True
                if item.get('retry_published'):
                    manifest['retry_published'] = True
            except Exception as error:
                reason = getattr(stop, 'preparation_error', '') or str(error)
                item.update(status='paused' if stop.is_set() else 'error', error=reason)
                for variant in item.get('variants', []):
                    if variant.get('status', '').startswith(('Кодирование', 'Подготовка', 'Обработка')):
                        variant.update(status='Пауза' if stop.is_set() else 'Ошибка', detail=reason)
                persist()
                continue
            try:
                prepared = safe_client.request('import', manifest)
            except (ConnectionExpired, TemporaryConnectionError, InterruptedError) as error:
                item.update(status='paused', error=str(error))
                persist()
                raise
            except ValueError as error:
                item.update(status='error', error=str(error))
                persist()
                continue
            try:
                item['draft_id'] = prepared['draft']['id']
                server_status = prepared['draft']['status']
                requested_rejected = bool(item.pop('retry_rejected', False))
                requested_published = bool(item.pop('retry_published', False))
                backend_patch = prepared['draft'].get('backend_patch')

                if (requested_rejected or requested_published) and backend_patch != 'draft-reopen-v4':
                    item.update(
                        status='published' if requested_published else 'rejected',
                        error='Backend не загрузил Draft-Reopen v4 (маркер отсутствует). '
                              'Замените рабочий webpanel/imports.py и полностью перезапустите backend.'
                    )
                    persist()
                    continue

                if server_status == 'rejected' and not prepare_only:
                    reopened = safe_client.request('import/' + prepared['draft']['id'] + '/reopen', {'kind': 'rejected'})
                    prepared['draft'] = reopened['draft']
                    server_status = prepared['draft']['status']
                    if server_status != 'draft':
                        raise ValueError('Не удалось вернуть отклонённую карточку на проверку.')
                    report('Отклонённая карточка возвращена на проверку; продолжаю отправку.')
                if server_status == 'rejected':
                    error = ('Backend v4 загружен, но БД оставила черновик rejected.'
                             if requested_rejected else
                             'Отклонено в WEB. Можно пересоздать черновик.')
                    item.update(status='rejected', error=error)
                    persist()
                    continue

                if server_status == 'published':
                    error = ('Сервер не сбросил published. Проверьте, что фильм действительно удалён из каталога.'
                             if requested_published else
                             'Уже опубликовано сервером.')
                    item.update(status='published', error=error)
                    persist()
                    continue
                if prepare_only:
                    item.update(status='prepared', error='Настройте этот фильм в WEB-панели; видео ещё не отправлено.')
                    persist()
                    continue
                if prepared['draft']['status'] != 'published':
                    item['status'] = 'sending'
                    persist()
                    state = safe_client.request('import/' + prepared['draft']['id'])
                    if not state['files'] or any(f['status'] != 'ready' for f in state['files']):
                        desktop = send_with_recovery(safe_client, prepared, manifest, desktop, stop, item_report, connect, send)
                    if stop.is_set():
                        raise InterruptedError('Очередь остановлена.')
                for variant in item.get('variants', []):
                    variant.update(status='Принят')
                item.update(status='complete', error='')
                persist()
            except FileRejectedError as error:
                item.update(status='error', error=str(error))
                persist()
                continue
            except (ConnectionExpired, TemporaryConnectionError, InterruptedError, BeforeSendError) as error:
                item.update(status='paused', error=str(error))
                persist()
                raise
            except Exception as error:
                # Includes ambiguous Send errors: never continue by blindly pasting another video.
                item.update(status='paused', error=str(error))
                persist()
                raise
        changed(items)
        if stop.is_set():
            reason = next((i.get('error') for i in items if i['status'] == 'paused' and i.get('error')), 'Остановка запрошена.')
            report('Очередь приостановлена: ' + reason)
            return
        if prepare_only:
            report(f'Созданы отдельные карточки: {sum(i["status"] == "prepared" for i in items)}. '
                   'Откройте /web → «Проверка и пакет», настройте каждый фильм и сохраните. Затем запустите шаг 2 в лаунчере.')
            return
        report(f'Очередь: готово {sum(i["status"] == "complete" for i in items)} из {len(items)}. '
               'Откройте /web → «Проверка и пакет» для одобрения.')


class TelegramDesktop:
    """Fail closed if the installed Desktop does not expose accessible controls.

    No coordinate clicks and no user-session API credentials are used.
    The user must leave Telegram foreground during transfer preparation.
    """

    def __init__(self, username, bot_name):
        self.username, self.bot_name = username, bot_name
        self.window = None

    def open(self):
        import re
        if not re.fullmatch('[A-Za-z0-9_]{5,32}', self.username):
            raise ValueError('Некорректное имя бота.')
        os.startfile('tg://resolve?domain=' + self.username)

    def bind(self, verify_chat=True):
        import ctypes
        from pywinauto import Desktop
        foreground = ctypes.windll.user32.GetForegroundWindow()
        window = Desktop(backend='uia').window(handle=foreground).wrapper_object()
        process_id = window.element_info.process_id
        kernel = ctypes.windll.kernel32
        kernel.OpenProcess.restype = ctypes.c_void_p
        kernel.CloseHandle.argtypes = [ctypes.c_void_p]
        kernel.QueryFullProcessImageNameW.argtypes = [ctypes.c_void_p, ctypes.c_ulong,
                                                     ctypes.c_wchar_p, ctypes.POINTER(ctypes.c_ulong)]
        handle = kernel.OpenProcess(0x1000, False, process_id)
        try:
            size = ctypes.c_ulong(32768)
            path = ctypes.create_unicode_buffer(size.value)
            if not handle or not kernel.QueryFullProcessImageNameW(handle, 0, path, ctypes.byref(size)):
                raise ValueError('Не удалось проверить окно Telegram.')
            if Path(path.value).name.lower() != 'telegram.exe':
                raise ValueError('На переднем плане должен быть Telegram Desktop.')
        finally:
            if handle:
                kernel.CloseHandle(handle)
        self.window = window
        if verify_chat:
            self.guard(chat=True)

    def confirmed_message_matches(self):
        """Use only a visible bot receipt for a server-confirmed file in this run."""
        ids = getattr(self, '_confirmed_receipts', set())
        if not ids:
            return False
        bounds = self.window.rectangle()
        for control in self.window.descendants():
            if control.element_info.control_type != 'ListItem' or not control.is_visible():
                continue
            rect = control.rectangle()
            if not (rect.left > bounds.left + bounds.width() // 4
                    and rect.top >= bounds.top + 79 and rect.bottom <= bounds.bottom
                    and rect.height() > 0):
                continue
            for label in control_labels(control):
                if (matches_chat_label(label, self.bot_name, self.username)
                        and 'Файл принят:' in label
                        and any(asset_id in label for asset_id in ids)):
                    return True
        return False

    def native_chat_title_matches(self):
        # Qt UIA Name can differ from the actual Windows window title.
        import ctypes
        try:
            if not isinstance(self.window.handle, int):
                return False
            user32 = ctypes.windll.user32
            user32.GetWindowTextLengthW.argtypes = [ctypes.c_void_p]
            user32.GetWindowTextW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_int]
            size = user32.GetWindowTextLengthW(self.window.handle) + 1
            title = ctypes.create_unicode_buffer(size)
            user32.GetWindowTextW(self.window.handle, title, size)
            return bool(matches_window_title(title.value, self.bot_name, self.username))
        except Exception:
            return False

    def inspect_header(self):
        bounds = self.window.rectangle()
        title_matched = (matches_window_title(self.window.window_text(), self.bot_name, self.username)
                         or self.native_chat_title_matches())
        counts = {}
        geometry = []
        matched = False
        right_header_matched = False
        matching_elsewhere = 0
        header_controls = 0
        right_header_controls = 0

        # Telegram Desktop layouts vary between builds. Keep the old strict
        # header zone, but also accept the expected chat label in a constrained
        # upper-right pane. This deliberately excludes the left chat list and
        # most of the message history, so a bot-name mention elsewhere is not
        # enough to pass verification.
        strict_top = bounds.top + min(160, bounds.height() // 4)
        strict_bottom = bounds.top + min(200, bounds.height() // 3)
        right_x = bounds.left + int(bounds.width() * 0.30)
        right_bottom = bounds.top + int(bounds.height() * 0.25)

        for control in self.window.descendants():
            if not control.is_visible():
                continue
            kind = control.element_info.control_type
            counts[kind] = counts.get(kind, 0) + 1
            rect = control.rectangle()

            in_header = (bounds.top <= rect.top < strict_top
                         and rect.left > bounds.left + bounds.width() // 4
                         and rect.bottom <= strict_bottom)

            center_x = (rect.left + rect.right) // 2
            center_y = (rect.top + rect.bottom) // 2
            in_right_header = (
                center_x >= right_x
                and bounds.top <= center_y <= right_bottom
                and rect.right > rect.left and rect.bottom > rect.top
            )

            if in_header:
                header_controls += 1
            if in_right_header:
                right_header_controls += 1

            same_name = any(matches_chat_label(label, self.bot_name, self.username)
                            for label in control_labels(control))
            if in_header or same_name:
                geometry.append({'kind':kind,'expected_name':same_name,'rect':[rect.left-bounds.left,rect.top-bounds.top,rect.right-bounds.left,rect.bottom-bounds.top]})
            if same_name:
                if in_header:
                    matched = True
                elif in_right_header:
                    right_header_matched = True
                else:
                    matching_elsewhere += 1

        verified = bool(matched or right_header_matched or title_matched)
        source = ('window_title' if title_matched else
                  'header' if matched else
                  'right_header_region' if right_header_matched else
                  'none')

        # Do not record messages, titles, account names, tokens, or window contents.
        return {'format': 3,
                'header_matched': bool(matched or right_header_matched),
                'strict_header_matched': bool(matched),
                'right_header_matched': bool(right_header_matched),
                'window_title_matched': bool(title_matched),
                'chat_verified': verified,
                'match_source': source,
                'matching_elements_outside_header': matching_elsewhere,
                'header_element_count': header_controls,
                'right_header_element_count': right_header_controls,
                'visible_control_types': counts,
                'control_geometry': geometry[:250], 'native_title_matched': self.native_chat_title_matches(),
                'window_size': [bounds.width(), bounds.height()]}

    def save_diagnostics(self, report):
        path = state_path('desktop-diagnostics.json', __file__)
        path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        return path

    def guard(self, chat=False):
        import ctypes
        from pywinauto import Desktop
        foreground = Desktop(backend='uia').window(handle=ctypes.windll.user32.GetForegroundWindow()).wrapper_object()
        if (foreground.element_info.process_id != self.window.element_info.process_id
                or foreground.handle != self.window.handle):
            raise ValueError('Telegram потерял фокус. Отправка остановлена.')
        if chat:
            if (matches_window_title(self.window.window_text(), self.bot_name, self.username)
                    or self.native_chat_title_matches()):
                return
            if self.confirmed_message_matches():
                return
            report = self.inspect_header()
            if not report['chat_verified']:
                # Telegram sometimes keeps its accessibility tree stale after a
                # successful media send. A real click in the identified composer
                # refreshes it, just like the harmless manual click users found.
                try:
                    self.refresh_chat_surface()
                    report = self.inspect_header()
                    report['recovered_after_composer_click'] = bool(report['chat_verified'])
                except Exception as error:
                    report['composer_refresh_error'] = str(error)[:240]
                if report['chat_verified']:
                    return
                path = self.save_diagnostics(report)
                raise ValueError('Лаунчер не смог распознать заголовок чата. Отправка остановлена. '
                                 f'Диагностика сохранена: {path}. Передайте этот файл для проверки совместимости.')

    @staticmethod
    def is_class(control, name):
        return str(control.element_info.class_name).removeprefix('class ') == name

    def telegram_surfaces(self):
        """Return visible top-level UIA surfaces owned by this Telegram process.

        Newer Telegram Desktop builds may expose the attachment preview as a
        separate top-level surface instead of a descendant of the main chat window.
        """
        result = [self.window]
        seen = set()
        try:
            main_handle = int(self.window.handle)
            if main_handle:
                seen.add(main_handle)
        except Exception:
            pass

        def add(control):
            try:
                handle = int(control.handle)
                if handle in seen or not control.is_visible():
                    return
                if control.element_info.process_id != self.window.element_info.process_id:
                    return
                seen.add(handle)
                result.append(control)
            except Exception:
                pass

        try:
            from pywinauto import Desktop
            for top in Desktop(backend='uia').windows():
                add(top)
        except Exception:
            pass
        return result

    @staticmethod
    def control_identity(control):
        """Return a stable UIA identity even when pywinauto creates two wrappers."""
        try:
            handle = int(control.handle)
            if handle:
                return ('handle', handle)
        except Exception:
            pass
        try:
            runtime_id = control.element_info.runtime_id
            if runtime_id:
                return ('runtime', tuple(runtime_id))
        except Exception:
            pass
        try:
            rect = control.rectangle()
            return ('visual', str(control.element_info.control_type),
                    str(control.element_info.class_name),
                    int(rect.left), int(rect.top), int(rect.right), int(rect.bottom))
        except Exception:
            return ('object', id(control))

    def attachment_controls(self):
        """Locate Telegram's attachment preview safely across all Telegram surfaces.

        Prefer Telegram's exact SendFilesBox class.  If a newer build exposes the
        preview as a separate top-level window, accept it only when it has a strong
        attachment-preview signature; never fall back to the ordinary composer.
        """
        surfaces = self.telegram_surfaces()
        try:
            main_handle = int(self.window.handle)
        except (TypeError, ValueError):
            main_handle = 0
        visible = []
        owners = {}
        seen_controls = set()

        for surface in surfaces:
            try:
                controls = [c for c in surface.descendants() if c.is_visible()]
            except Exception:
                controls = []
            for c in controls:
                identity = self.control_identity(c)
                if identity in seen_controls:
                    continue
                seen_controls.add(identity)
                visible.append(c)
                owners[identity] = surface

        boxes = [c for c in visible if self.is_class(c, 'SendFilesBox')]
        if len(boxes) > 1:
            raise ValueError('Telegram открыл несколько окон предпросмотра. Закройте лишние и повторите.')
        box = boxes[0] if boxes else None

        preview_surface = owners.get(self.control_identity(box)) if box is not None else None
        scope = [c for c in box.descendants() if c.is_visible()] if box else []

        # Older Telegram versions expose a real labelled Send button. Prefer
        # that unambiguous control before using the coordinate fallback below.
        if box is not None:
            labelled_edits = []
            for control in scope:
                try:
                    if (control.element_info.control_type == 'Edit'
                            and not any(e.is_visible() for e in control.descendants())):
                        labels = {normalized_label(v) for v in control_labels(control) if v}
                        if labels & {'caption', 'подпись', 'add a caption', 'добавить подпись'}:
                            labelled_edits.append(control)
                except Exception:
                    pass
            try:
                parent_controls = [c for c in box.parent().descendants() if c.is_visible()]
            except Exception:
                parent_controls = []
            real_send = [c for c in parent_controls
                         if c.element_info.control_type == 'Button'
                         and normalized_label(c.window_text()) in ('send', 'отправить')]
            documents = [c for c in scope if c.element_info.control_type == 'CheckBox'
                         and normalized_label(c.window_text()) in
                         ('send as a document', 'отправить как документ')]
            previews = [c for c in scope if self.is_class(c, 'Ui::SingleMediaPreview')]
            file_previews = [c for c in scope if self.is_class(c, 'Ui::SingleFilePreview')]
            if len(labelled_edits) == 1 and len(real_send) == 1:
                if documents and len(previews) + len(file_previews) == 1:
                    if len(previews) != 1 and documents[0].get_toggle_state() != 1:
                        raise ValueError('Telegram не распознал вложение как видео. Закройте предпросмотр '
                                         'и повторите подготовку MP4 в лаунчере.')
                return labelled_edits[0], real_send[0], documents[0] if documents else None

        preview_titles = (
            'send a video file', 'send video', 'send file', 'send files',
            'отправить видео', 'отправить видеофайл', 'отправить файл', 'отправить файлы',
        )

        # New Telegram builds can put the preview into a separate top-level
        # Telegram-owned window with no SendFilesBox class.  Such a surface is much
        # safer to inspect than the main chat because it cannot be the normal composer.
        if box is None:
            candidates = []
            for surface in surfaces:
                if int(surface.handle) == main_handle:
                    continue
                try:
                    controls = [c for c in surface.descendants() if c.is_visible()]
                except Exception:
                    continue
                labels = []
                for c in controls:
                    try:
                        labels.extend(normalized_label(v) for v in control_labels(c) if v)
                    except Exception:
                        pass
                has_title = any(any(title in label for title in preview_titles) for label in labels)
                has_document_toggle = any(
                    c.element_info.control_type == 'CheckBox'
                    and normalized_label(c.window_text()) in ('send as a document', 'отправить как документ')
                    for c in controls
                )
                edits = [c for c in controls
                         if c.element_info.control_type == 'Edit'
                         and c.is_visible() and c.is_enabled()
                         and not any(e.is_visible() for e in c.descendants(control_type='Edit'))]
                buttons = [c for c in controls
                           if c.element_info.control_type == 'Button'
                           and c.is_visible() and c.is_enabled()]
                # Require a modal-like signature.  An Edit+Button alone is not enough.
                if edits and buttons and (has_title or has_document_toggle):
                    candidates.append((surface, controls))
            if len(candidates) == 1:
                preview_surface, scope = candidates[0]
            elif len(candidates) > 1:
                raise ValueError('Не удалось однозначно выбрать окно предпросмотра Telegram.')

        if box is None and preview_surface is None:
            raise ValueError('Предпросмотр вложения Telegram пока не найден.')

        bounds = (box.rectangle() if box is not None else preview_surface.rectangle())

        def inside(c):
            try:
                r = c.rectangle()
                return (r.width() > 0 and r.height() > 0
                        and r.left >= bounds.left and r.right <= bounds.right
                        and r.top >= bounds.top and r.bottom <= bounds.bottom)
            except Exception:
                return False

        edits = [c for c in scope
                 if c.element_info.control_type == 'Edit'
                 and c.is_enabled() and inside(c)
                 and not any(e.is_visible() for e in c.descendants(control_type='Edit'))]

        # Prefer an edit explicitly labelled Caption/Подпись.  Otherwise, in a
        # dedicated preview surface the lowest Edit is the caption field.
        caption_edits = []
        for c in edits:
            try:
                labels = {normalized_label(v) for v in control_labels(c)}
            except Exception:
                labels = set()
            if labels & {'caption', 'подпись', 'добавить подпись', 'add a caption'}:
                caption_edits.append(c)
        if len(caption_edits) == 1:
            edit = caption_edits[0]
        elif len(edits) == 1:
            edit = edits[0]
        elif edits and preview_surface is not None and int(preview_surface.handle) != main_handle:
            edits.sort(key=lambda c: c.rectangle().bottom, reverse=True)
            edit = edits[0]
        else:
            raise ValueError('Не удалось однозначно найти поле подписи в предпросмотре Telegram.')

        documents = [c for c in scope
                     if c.element_info.control_type == 'CheckBox'
                     and normalized_label(c.window_text()) in
                     ('send as a document', 'отправить как документ')]

        # IMPORTANT: do not click a UIA "Send" control directly.  In this Telegram
        # build parent/wrapper controls can report the label "Send" while their
        # clickable rectangle is actually the emoji button inside the caption row.
        #
        # Instead, resolve the real action ROW geometrically from the caption field:
        # Send is below the caption, at the same right edge.  If an exact visible
        # Send/Cancel text is exposed below the caption, use its baseline only.
        edit_rect = edit.rectangle()

        def exact_label(control, accepted):
            try:
                return bool({normalized_label(v) for v in control_labels(control) if v} & accepted)
            except Exception:
                return False

        below = []
        for c in scope:
            try:
                if not c.is_visible():
                    continue
                r = c.rectangle()
                if r.width() <= 0 or r.height() <= 0:
                    continue
                # The action row MUST be below the caption edit.  This excludes
                # the emoji icon even when a wrapper around it inherits "Send".
                if r.top < edit_rect.bottom + 3:
                    continue
                below.append(c)
            except Exception:
                pass

        send_labels = [c for c in below if exact_label(c, {'send', 'отправить'})]
        cancel_labels = [c for c in below if exact_label(c, {'cancel', 'отмена'})]

        send_x = int(edit_rect.right - 12)
        send_y = int(edit_rect.bottom + 30)
        source = 'caption_geometry'

        # If Telegram exposes the actual Send text below the caption, use the
        # CENTER OF THAT TEXT RECTANGLE as coordinates (not click_input()).
        compact_sends = []
        seen_rects = set()
        for c in send_labels:
            try:
                r = c.rectangle()
                key = (r.left, r.top, r.right, r.bottom)
                if key in seen_rects:
                    continue
                seen_rects.add(key)
                # Reject huge wrappers; visible action text is small.
                if r.height() <= 60 and r.width() <= 180:
                    compact_sends.append(c)
            except Exception:
                pass

        if compact_sends:
            compact_sends.sort(
                key=lambda c: (c.rectangle().top, c.rectangle().left),
                reverse=True
            )
            r = compact_sends[0].rectangle()
            send_x = int((r.left + r.right) // 2)
            send_y = int((r.top + r.bottom) // 2)
            source = 'send_text_rect'
        elif cancel_labels:
            # Cancel and Send are on the same baseline.  Telegram reliably exposes
            # Cancel more often than Send, so use its vertical center and keep the
            # horizontal position aligned with the caption's right edge.
            valid_cancel = []
            for c in cancel_labels:
                try:
                    r = c.rectangle()
                    if r.height() <= 60 and r.width() <= 180:
                        valid_cancel.append(c)
                except Exception:
                    pass
            if valid_cancel:
                valid_cancel.sort(key=lambda c: c.rectangle().top, reverse=True)
                r = valid_cancel[0].rectangle()
                send_y = int((r.top + r.bottom) // 2)
                source = 'cancel_baseline'

        send_target = {
            'kind': 'point',
            'x': send_x,
            'y': send_y,
            'source': source,
            'caption_rect': [
                edit_rect.left, edit_rect.top, edit_rect.right, edit_rect.bottom
            ],
        }

        previews = [c for c in scope if self.is_class(c, 'Ui::SingleMediaPreview')]
        file_previews = [c for c in scope if self.is_class(c, 'Ui::SingleFilePreview')]
        if documents and len(previews) + len(file_previews) == 1:
            if len(previews) != 1 and documents[0].get_toggle_state() != 1:
                raise ValueError('Telegram не распознал вложение как видео. Закройте предпросмотр '
                                 'и повторите подготовку MP4 в лаунчере.')

        return edit, send_target, documents[0] if documents else None

    def focus_composer(self):
        """Focus only a uniquely identified message editor in the verified chat."""
        labels = ('message', 'write a message...', 'write a message…', 'write a message',
                  'сообщение', 'написать сообщение...', 'написать сообщение…', 'написать сообщение')
        candidates = []
        bounds = self.window.rectangle()
        for control in self.window.descendants(control_type='Edit'):
            if not control.is_visible() or not control.is_enabled():
                continue
            rect = control.rectangle()
            if rect.top < bounds.top + bounds.height() // 2:
                continue
            if any(e.is_visible() for e in control.descendants(control_type='Edit')):
                continue
            if any(normalized_label(label) in labels for label in control_labels(control)):
                candidates.append(control)
        if len(candidates) != 1:
            raise ValueError('Не удалось однозначно найти поле сообщения в чате бота. '
                             'Откройте чат бота и закройте поиск или предпросмотр вложения.')
        candidates[0].set_focus()
        return candidates[0]

    def refresh_chat_surface(self):
        """Dismiss Telegram's post-send focus state without arbitrary coordinates."""
        self.window.set_focus()
        editor = self.focus_composer()
        try:
            editor.click_input(button='left')
        except Exception:
            # set_focus() above still triggers an accessibility refresh in older builds.
            editor.set_focus()
        time.sleep(0.25)
        return editor

    def prepare_attachment(self, path, caption):
        """Attach a file using Telegram Desktop's built-in Ctrl+O shortcut.

        Ctrl+O is Telegram Desktop's native "Send File" command, so this avoids
        depending on the paperclip button being exposed through Windows UIA.
        """
        import ctypes
        from pywinauto import Desktop

        self.guard(chat=True)
        path = Path(path).resolve()
        if not path.is_file():
            raise ValueError('Подготовленный видеофайл не найден: ' + str(path))
        try:
            self.attachment_controls()
        except ValueError as error:
            if 'пока не найден' not in str(error):
                raise
        else:
            raise ValueError('Закройте старое окно вложения в Telegram перед повтором.')

        self.window.set_focus()
        self.focus_composer()

        # Native Telegram Desktop shortcut: Send File.
        # Send Ctrl+O at the Win32 level to the actual Telegram HWND.
        # This is intentionally independent of pywinauto's type_keys/send_keys.
        user32 = ctypes.windll.user32
        hwnd = int(self.window.handle)

        # Restore Telegram if minimized and force it to foreground.
        user32.ShowWindow(hwnd, 9)  # SW_RESTORE
        user32.SetForegroundWindow(hwnd)
        time.sleep(0.35)

        VK_CONTROL = 0x11
        VK_O = 0x4F
        KEYEVENTF_KEYUP = 0x0002
        user32.keybd_event(VK_CONTROL, 0, 0, 0)
        user32.keybd_event(VK_O, 0, 0, 0)
        user32.keybd_event(VK_O, 0, KEYEVENTF_KEYUP, 0)
        user32.keybd_event(VK_CONTROL, 0, KEYEVENTF_KEYUP, 0)
        time.sleep(0.6)
        time.sleep(0.5)

        desktop = Desktop(backend='uia')
        dialog = None
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            try:
                # Only accept the real Windows common file dialog.
                # v6_4 was too broad and mistook an already-open Explorer window
                # for Telegram's file picker, then typed the path into Rename.
                fg_hwnd = ctypes.windll.user32.GetForegroundWindow()
                candidates = []
                try:
                    candidates.append(desktop.window(handle=fg_hwnd).wrapper_object())
                except Exception:
                    pass
                candidates.extend(desktop.windows())

                seen = set()
                for top in candidates:
                    try:
                        if top.handle in seen or top.handle == self.window.handle or not top.is_visible():
                            continue
                        seen.add(top.handle)
                        if (top.element_info.class_name or '') != '#32770':
                            continue
                        edits = [e for e in top.descendants(control_type='Edit')
                                 if e.is_visible() and e.is_enabled()]
                        if edits:
                            dialog = top
                            break
                    except Exception:
                        continue
                if dialog is not None:
                    break
            except Exception:
                pass
            time.sleep(0.2)

        if dialog is None:
            raise BeforeSendError(
                'Ctrl+O сработал, но системный диалог выбора файла #32770 не найден.')

        edits = [e for e in dialog.descendants(control_type='Edit')
                 if e.is_visible() and e.is_enabled()]
        if not edits:
            raise BeforeSendError('В окне выбора файла не найдено поле имени файла.')

        filename = None
        for e in edits:
            try:
                labels = {normalized_label(v) for v in control_labels(e)}
                if labels & {'file name', 'filename', 'имя файла'}:
                    filename = e
                    break
            except Exception:
                pass
        if filename is None:
            # In the standard Open dialog the filename edit is the lowest Edit.
            edits.sort(key=lambda e: e.rectangle().bottom, reverse=True)
            filename = edits[0]

        filename.set_focus()
        filename.set_edit_text(str(path))
        filename.type_keys('{ENTER}', set_foreground=False)

        # Wait for Telegram's attachment preview.  New Telegram Desktop builds may
        # expose it as a separate top-level UIA surface instead of SendFilesBox.
        deadline = time.monotonic() + 30
        last_preview_error = ''
        while time.monotonic() < deadline:
            # Do NOT call guard() here: Telegram's own modal preview can own focus.
            try:
                edit, send, document = self.attachment_controls()
            except ValueError as error:
                last_preview_error = str(error)
                time.sleep(0.25)
                continue

            if document is not None and document.get_toggle_state():
                document.toggle()
                time.sleep(0.3)
                edit, send, document = self.attachment_controls()
            if document is not None and document.get_toggle_state() != 0:
                raise ValueError('Не удалось выключить отправку документом.')

            edit.set_edit_text(caption)
            try:
                value = edit.get_value()
            except Exception:
                value = edit.window_text()
            if value != caption:
                raise ValueError('Не удалось проверить метку в подписи. Файл не отправлен.')

            # A verified attachment preview surface is the safety proof that this
            # is an attachment caption, not a standalone KTFILE message.
            return send

        report = self.inspect_header()
        report['stage'] = 'attachment_preview_timeout_after_ctrl_o'
        report['preview_error'] = last_preview_error[:240]
        try:
            report['telegram_top_level_surfaces'] = len(self.telegram_surfaces())
        except Exception:
            pass
        diagnostics = self.save_diagnostics(report)
        raise BeforeSendError(
            'Файл выбран через Ctrl+O, но Telegram не открыл распознаваемый предпросмотр. '
            'Диагностика: ' + str(diagnostics))

    def send_file(self, path, caption, before_send=None, on_not_sent=None):
        try:
            send = self.prepare_attachment(path, caption)
            # The verified attachment preview is the safety boundary.
        except Exception as error:
            raise BeforeSendError(str(error)) from error

        if before_send:
            before_send()

        # Telegram UIA differs between builds.  Prefer a real click on the exact
        # labelled Send control; if Telegram does not expose it, attachment_controls()
        # returns a safe bottom-right point inside the VERIFIED preview.
        click_error = None
        try:
            if isinstance(send, dict) and send.get('kind') == 'point':
                from pywinauto import mouse
                mouse.click(button='left', coords=(send['x'], send['y']))
            else:
                send.invoke()
        except Exception as error:
            if not isinstance(send, dict):
                raise
            click_error = error

        # Verify the PREVIEW itself disappeared.  This is stronger than checking the
        # clicked UIA element, because text wrappers/buttons can stay "visible" or go
        # stale independently of whether Telegram actually accepted Send.
        deadline = time.monotonic() + 5
        preview_still_open = True
        current_send = send
        while time.monotonic() < deadline:
            try:
                current_edit, current_send, _document = self.attachment_controls()
                try:
                    value = current_edit.get_value()
                except Exception:
                    value = current_edit.window_text()
                if value != caption:
                    preview_still_open = False
                    break
            except Exception:
                # Verified attachment preview no longer exists -> Send succeeded.
                preview_still_open = False
                break
            time.sleep(0.20)

        if preview_still_open:
            if on_not_sent:
                try:
                    on_not_sent()
                except Exception:
                    pass
            report = self.inspect_header()
            report['stage'] = 'send_button_not_activated'
            report['click_input_error'] = str(click_error)[:240] if click_error else ''
            try:
                if isinstance(current_send, dict):
                    report['send_click_point'] = [current_send.get('x'), current_send.get('y')]
                    report['send_click_source'] = current_send.get('source', '')
                    if current_send.get('caption_rect'):
                        report['caption_rect'] = current_send.get('caption_rect')
                else:
                    rect = current_send.rectangle()
                    report['send_button_rect'] = [rect.left, rect.top, rect.right, rect.bottom]
            except Exception:
                pass
            diagnostics = self.save_diagnostics(report)
            raise BeforeSendError(
                'Кнопка Send в предпросмотре Telegram не сработала. '
                'Файл НЕ помечен как отправленный; можно повторить. '
                'Диагностика: ' + str(diagnostics)
            )

        # We cannot prove the preview is still open, so do not risk a second click.
        # Leave sent_unconfirmed: the next run will check the bot before retrying.
        if click_error:
            raise BeforeSendError(
                'Telegram изменил состояние после нажатия Send, но подтверждение клика '
                'не удалось получить. Проверьте чат перед повтором: ' + str(click_error)
            )

        # Telegram Desktop can leave the last media bubble/modal surface as the
        # active accessibility element. Normalize focus now so the next file can
        # be sent continuously without a manual click in the chat.
        try:
            self.refresh_chat_surface()
        except Exception:
            # The server acknowledgement remains the source of truth. If the
            # composer is briefly unavailable, guard() retries the same refresh
            # before the next attachment.
            pass


def resume_rejected_before_send(client, prepared, report):
    draft_id = prepared['draft']['id']
    current = client.request('import/' + draft_id)
    if current['draft_status'] == 'published':
        raise ValueError('Карточка опубликована. Для удалённого произведения сначала используйте безопасный сброс.')
    if current['draft_status'] == 'rejected':
        result = client.request('import/' + draft_id + '/reopen', {'kind': 'rejected'})
        if result['draft']['status'] != 'draft':
            raise ValueError('Не удалось вернуть отклонённую карточку на проверку.')
        prepared['draft'] = result['draft']
        report('Отклонённая карточка возвращена на проверку. Продолжаю отправку непринятых файлов.')
    return prepared


def transfer(client, prepared, manifest, desktop, stop, report, *, resume_timeout=120, upload_timeout=6 * 3600):
    """Send one file at a time; only server acknowledgements count as completion."""
    draft = prepared['draft']
    root = Path(manifest['root'])
    journal_path = root / '.kinotime-transfer.json'
    journal = json.loads(journal_path.read_text(encoding='utf-8')) if journal_path.exists() else {}
    sources = {e['path']: e for e in manifest['files']}
    protected = launcher_device.valid_instance(getattr(client, 'instance_id', ''))

    def persist():
        temp = journal_path.with_suffix('.tmp')
        temp.write_text(json.dumps(journal, ensure_ascii=False), encoding='utf-8')
        temp.replace(journal_path)

    rejected_files = []
    for index, item in enumerate(draft['body']['items'], 1):
        if stop.is_set():
            return
        state = client.request('import/' + draft['id'])
        if state['draft_status'] in ('rejected', 'published'):
            raise ValueError('Статус карточки изменился во время передачи. Она остановлена; для отклонённой карточки запустите шаг 2 повторно.')
        asset_id = item['asset_id']
        variant_name = Path(item.get('relative_path', item.get('name',''))).stem
        variant_name = re.sub(r'^\d+ - ', '', variant_name)
        metadata = sources.get(item.get('relative_path'), {})
        if metadata.get('quality'):
            variant_name = ' / '.join(filter(None, (metadata.get('name'), metadata['quality'],
                                                    metadata.get('voice') or 'Без звука')))
        variant_update = getattr(report, 'variant', lambda *args: None)
        def current_file(response):
            file = next((f for f in response['files'] if f['id'] == asset_id), None)
            if file is None:
                raise ValueError(f'{variant_name}: панель не вернула состояние этого файла. Передача приостановлена.')
            return file

        if current_file(state)['status'] == 'ready':
            if metadata:
                disk_space.confirm(root / metadata['path'], metadata)
            if journal.get(asset_id) != 'confirmed':
                journal[asset_id] = 'confirmed'
                persist()
            variant_update(variant_name, 'Принят', '')
            report(f'{index}: уже принято')
            continue
        rejected = next((f.get('error') for f in state['files'] if f['id']==asset_id), None)
        if rejected:
            journal.pop(asset_id, None)
            persist()
            client.request('import/' + draft['id'], {})
        source = sources[item['relative_path']]
        path = root / source['path']
        if path.is_symlink() or root.resolve() not in path.resolve().parents:
            raise ValueError('Исходный файл перемещён за пределы папки.')
        stat = path.stat()
        if stat.st_size != source['size'] or str(stat.st_mtime_ns) != source['mtime']:
            raise ValueError(f'{path.name}: файл изменился после сканирования.')
        resuming = journal.get(asset_id) == 'sent_unconfirmed'
        transfer_path = f'import/{draft["id"]}/files/{asset_id}/transfer'
        if protected:
            reservation = client.request(transfer_path, {'action': 'claim'})
            if reservation['status'] == 'ready':
                disk_space.confirm(path, source)
                journal[asset_id] = 'confirmed'
                persist()
                variant_update(variant_name, 'Принят', 'Файл уже есть в Telegram')
                continue
            if reservation['status'] == 'sent':
                resuming = True
                journal[asset_id] = 'sent_unconfirmed'
                persist()
        if not resuming:
            report(f'{index}/{len(draft["body"]["items"])}: {path.name}')
            def mark_sending():
                # Persist immediately before Send. Preparation failures remain retryable.
                journal[asset_id] = 'sent_unconfirmed'
                persist()
                if protected:
                    result = client.request(transfer_path, {'action': 'sent'})
                    if result['status'] != 'sent':
                        raise ValueError('Файл уже принят. Запустите передачу снова для обновления очереди.')
            def clear_not_sent():
                if protected:
                    client.request(transfer_path, {'action': 'release', 'verified_not_sent': True})
                if journal.get(asset_id) == 'sent_unconfirmed':
                    journal.pop(asset_id, None)
                    persist()

            variant_update(variant_name, 'Отправляется', '')
            import inspect
            callback = getattr(desktop.send_file, 'side_effect', None)
            callable_to_check = callback if callable(callback) else desktop.send_file
            supports_not_sent = ('on_not_sent' in inspect.signature(callable_to_check).parameters
                                 or any(p.kind == inspect.Parameter.VAR_KEYWORD
                                        for p in inspect.signature(callable_to_check).parameters.values()))
            if supports_not_sent:
                desktop.send_file(
                    path,
                    'KTFILE:' + asset_id,
                    before_send=mark_sending,
                    on_not_sent=clear_not_sent,
                )
            else:
                desktop.send_file(path, 'KTFILE:' + asset_id, before_send=mark_sending)
        else:
            report(f'{path.name}: проверяю подтверждение предыдущей отправки')
        waiting_started = time.monotonic()
        deadline = waiting_started + (resume_timeout if resuming else upload_timeout)
        while True:
            if stop.wait(3):
                raise InterruptedError('Передача остановлена. Неподтверждённая отправка сохранена для проверки.')
            state = client.request('import/' + draft['id'])
            file = current_file(state)
            rejection = file.get('error')
            if rejection:
                journal.pop(asset_id, None)
                persist()
                variant_update(variant_name, 'Ошибка', rejection)
                rejected_files.append(rejection)
                break
            if file['status'] == 'ready':
                disk_space.confirm(path, source)
                variant_update(variant_name, 'Принят', '')
                journal[asset_id] = 'confirmed'
                persist()
                break
            if state['draft_status'] in ('rejected', 'published'):
                raise ValueError('Черновик закрыт. Передача остановлена.')
            now = time.monotonic()
            elapsed = max(0, int(now - waiting_started))
            confirmed = sum(f['status'] == 'ready' for f in state['files'])
            detail = f'Принято {confirmed}/{len(state["files"])} · ожидание {elapsed // 60:02d}:{elapsed % 60:02d}'
            variant_update(variant_name, 'Ожидание подтверждения бота', detail)
            report(f'{index}/{len(draft["body"]["items"])} · {path.name}: {detail}. '
                   + ('Проверяю предыдущую отправку; файл повторно не отправляется.' if resuming
                      else 'Ожидаю завершения отправки в Telegram и ответа бота.'))
            if now >= deadline:
                raise ValueError(
                    f'{path.name}: подтверждение бота не получено. Передача приостановлена. '
                    'Проверьте Telegram: если файл ещё загружается, дождитесь окончания и запустите шаг 2 снова. '
                    'Если файла нет или загрузка отменена, нажмите «Разрешить безопасный повтор», затем шаг 2. '
                    'Неподтверждённый файл автоматически повторно не отправляется.'
                )
        if stop.is_set():
            return
    if rejected_files:
        raise FileRejectedError('; '.join(rejected_files))
    report('Все файлы приняты. Откройте /web для проверки карточки и одобрения.')


def bind_entry_shortcuts(entry):
    """Windows virtual key codes stay the same with Russian and English layouts."""
    def shortcut(event):
        if os.name != 'nt' or event.state & 0x20000:  # Leave AltGr combinations alone.
            return None
        action = {65: '<<SelectAll>>', 67: '<<Copy>>', 86: '<<Paste>>', 88: '<<Cut>>'}.get(event.keycode)
        if action:
            entry.event_generate(action)
            return 'break'  # Avoid a second paste from the default Tk binding.
        return None
    entry.bind('<Control-KeyPress>', shortcut)


def _main(*, smoke_test=False):
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk
    app = tk.Tk()
    if smoke_test:
        app.withdraw()
    project_temporary = None
    if smoke_test:
        import tempfile
        project_temporary = tempfile.TemporaryDirectory()
    projects = launcher_projects.ProjectManager(
        Path(project_temporary.name) / 'projects.sqlite' if project_temporary else
        state_path('launcher-projects.sqlite', __file__))
    callback_errors = []
    def callback_error(error_type, error, trace):
        callback_errors.append(str(error))
        logging.error('Launcher UI callback failed', exc_info=(error_type, error, trace))
    app.report_callback_exception = callback_error
    app.title(f'KinoTime {VERSION} · Telegram Desktop')
    app.geometry('1240x820')
    app.minsize(1040, 680)
    colors = {'window': '#091321', 'sidebar': '#0f1b29', 'card': '#121f2f',
              'card2': '#162840', 'line': '#29445f', 'text': '#f3f7fc',
              'muted': '#91a9c3', 'blue': '#3b82f6', 'blue2': '#24466f'}
    app.configure(bg=colors['window'])
    style = ttk.Style(app)
    style.theme_use('clam')
    style.configure('.', background=colors['window'], foreground=colors['text'],
                    fieldbackground=colors['card'], bordercolor=colors['line'], font=('Segoe UI', 10))
    style.configure('TFrame', background=colors['window'])
    style.configure('Sidebar.TFrame', background=colors['sidebar'])
    style.configure('Card.TFrame', background=colors['card'])
    style.configure('TLabel', background=colors['window'], foreground=colors['text'])
    style.configure('Card.TLabel', background=colors['card'], foreground=colors['text'])
    style.configure('Muted.TLabel', background=colors['window'], foreground=colors['muted'])
    style.configure('CardMuted.TLabel', background=colors['card'], foreground=colors['muted'])
    style.configure('Title.TLabel', font=('Segoe UI Semibold', 22))
    style.configure('Section.TLabel', font=('Segoe UI Semibold', 13))
    style.configure('Nav.TButton', anchor='w', padding=(16, 12), background=colors['sidebar'],
                    foreground=colors['muted'], borderwidth=0)
    style.map('Nav.TButton', background=[('active', colors['card2'])], foreground=[('active', colors['text'])])
    style.configure('Primary.TButton', padding=(14, 9), background=colors['blue'],
                    foreground='white', borderwidth=0)
    style.map('Primary.TButton', background=[('active', '#5594fa'), ('disabled', colors['line'])])
    style.configure('TButton', padding=(12, 8), background=colors['card2'], foreground=colors['text'])
    style.map('TButton', background=[('active', colors['blue2'])])
    style.configure('TEntry', padding=8, insertcolor='white')
    style.configure('TRadiobutton', background=colors['card'], foreground=colors['text'])
    style.configure('TCheckbutton', background=colors['card'], foreground=colors['text'])
    style.map('TRadiobutton', background=[('active', colors['card'])])
    style.map('TCheckbutton', background=[('active', colors['card'])])
    style.configure('Treeview', background=colors['card'], fieldbackground=colors['card'],
                    foreground=colors['text'], rowheight=30, borderwidth=0)
    style.configure('Treeview.Heading', background=colors['card2'], foreground=colors['muted'],
                    relief='flat', padding=7)
    style.map('Treeview', background=[('selected', colors['blue2'])])
    values = {key: tk.StringVar() for key in ('url', 'token', 'folder')}
    prepared = {}
    stop = threading.Event()
    busy = False
    render_job = remote_render.JobState()
    remote_browse_enabled = tk.BooleanVar(value=True)
    cleanup_enabled = tk.BooleanVar(value=True)
    reserve_gib = tk.StringVar(value='2')
    mode = tk.StringVar(value='batch')
    quiet_preparation = tk.BooleanVar(value=False)
    hardware_labels = video_hardware_ui.HARDWARE_LABELS
    load_labels = video_hardware_ui.LOAD_LABELS
    hardware_value = tk.StringVar(value='Автоматически')
    load_value = tk.StringVar(value='Сбалансированный')
    hardware_reason = tk.StringVar(value='Выберите кодировщик и нажмите «Проверить». '
        'Точный режим — два прохода на CPU; совместимое исходное видео копируется.')
    hardware_controls = []
    hardware_info = {}
    catalog_section = tk.StringVar(value='Фильмы')

    def selected_catalog_section():
        return LAUNCHER_SECTIONS.get(catalog_section.get(), 'movies')
    quality_vars = {
        name: tk.BooleanVar(value=name in ('1080p', '720p'))
        for name in QUALITY_NAMES
    }
    quality_selection_dirty = True
    status = tk.StringVar(value='Сначала выберите папку и откройте раздел «Очередь».')
    shell = ttk.Frame(app)
    shell.pack(fill='both', expand=True)
    sidebar = ttk.Frame(shell, style='Sidebar.TFrame', width=218)
    sidebar.pack(side='left', fill='y')
    sidebar.pack_propagate(False)
    brand = ttk.Frame(sidebar, style='Sidebar.TFrame')
    brand.pack(fill='x', padx=20, pady=(24, 30))
    tk.Label(brand, text='K', bg=colors['blue'], fg='white', font=('Segoe UI Semibold', 14),
             width=2).pack(side='left')
    tk.Label(brand, text='  KinoTime', bg=colors['sidebar'], fg='white',
             font=('Segoe UI Semibold', 14)).pack(side='left')
    content_shell = ttk.Frame(shell)
    content_shell.pack(side='left', fill='both', expand=True)
    topbar = ttk.Frame(content_shell)
    topbar.pack(fill='x', padx=28, pady=(18, 8))
    breadcrumb = tk.StringVar(value='Лаунчер / Обзор')
    ttk.Label(topbar, textvariable=breadcrumb, style='Muted.TLabel').pack(side='left')
    connection_label = ttk.Label(topbar, text='● Готов к настройке', style='Muted.TLabel')
    connection_label.pack(side='right')
    pages_host = ttk.Frame(content_shell)
    pages_host.pack(fill='both', expand=True, padx=28, pady=8)
    pages = {name: ttk.Frame(pages_host) for name in
             ('overview', 'source', 'torrents', 'processing', 'queue', 'projects', 'telegram', 'settings')}
    page_titles = {'overview': 'Обзор', 'source': 'Источник', 'processing': 'Качество и звук',
                   'queue': 'Очередь файлов', 'projects': 'Очередь проектов', 'telegram': 'Telegram',
                   'settings': 'Настройки', 'torrents': 'Торренты'}
    nav_buttons = {}

    def show_page(name):
        for page in pages.values():
            page.pack_forget()
        pages[name].pack(fill='both', expand=True)
        breadcrumb.set('Лаунчер / ' + page_titles[name])
        for key, button in nav_buttons.items():
            button.configure(style='Primary.TButton' if key == name else 'Nav.TButton')

    ttk.Label(content_shell, textvariable=status, style='Muted.TLabel', wraplength=920).pack(
        side='bottom', fill='x', padx=30, pady=(4, 14))

    def report(text):
        render_job.report(text)
        app.after(0, status.set, text)
        if 'Оборудование: ' in text:
            hardware_info.update(video_hardware.diagnostics_snapshot())
            app.after(0, hardware_reason.set, text.partition('Оборудование: ')[2])

    def hardware_busy(value):
        for control in hardware_controls:
            control.set_busy(value)

    def stop_all():
        stop.set()
        projects.pause()

    def project_conflict():
        if projects.busy:
            report('Работают проекты. Используйте раздел «Проекты» или приостановите их перед обычной очередью.')
            return True
        return False

    def background(fn, *, job_kind=None, allow_projects=False):
        nonlocal busy
        if busy:
            return False
        if projects.busy and not allow_projects:
            report('Работают проекты. Приостановите их в разделе «Проекты» перед запуском этой операции.')
            return False
        busy = True
        if job_kind:
            render_job.start(job_kind, values['folder'].get(), mode.get())
        hardware_busy(True)
        def run():
            nonlocal busy
            error = None
            try:
                if job_kind:
                    with keep_awake():
                        fn()
                else:
                    fn()
            except Exception as failure:
                error = failure
                report(str(failure))
            finally:
                if job_kind:
                    render_job.finish(error, stop.is_set())
                busy = False
                app.after(0, hardware_busy, projects.busy)
        threading.Thread(target=run, daemon=True).start()
        return True

    def prepare(remote=False):
        if project_conflict():
            if remote:
                raise ValueError('Работают проекты; сначала приостановите их.')
            return False
        if busy:
            if remote:
                raise ValueError('Лаунчер занят.')
            return
        if mode.get() == 'batch':
            return start_batch(prepare_only=True, remote=remote)
        url, token, folder = (values[k].get() for k in ('url', 'token', 'folder'))
        try:
            requested = selected_global_qualities()
        except ValueError as error:
            if remote:
                raise
            report(str(error))
            return
        section = selected_catalog_section()
        def run():
            prepared.clear()
            client = make_client(url, token)
            client.request('session')
            progress = FolderProgress(report, show_batch)
            try:
                manifest = prepare_media(folder, progress, stop, qualities=requested)
                manifest['catalog_section'] = section
                progress.phase('registering')
                data = client.request('import', manifest)
                prepared.update(manifest=manifest, client=client, data=data, source_folder=str(Path(folder).resolve()))
                progress.phase('prepared')
            except Exception as error:
                progress.phase('paused' if isinstance(error, InterruptedError) else 'error', str(error))
                raise
            report(f'Найдено {len(manifest["files"])} видео. Черновик: {manifest["title"]}. '
                   f'Откройте /web: проверьте название, год, категории и подкатегории. '
                   f'Затем нажмите шаг 2. Получатель: @{data["bot_username"]}.')
        stop.clear()
        started = background(run, job_kind='prepare')
        if quiet_preparation.get():
            app.iconify()
        return started

    def start(remote=False):
        if project_conflict():
            if remote:
                raise ValueError('Работают проекты; сначала приостановите их.')
            return False
        if quiet_preparation.get():
            if remote:
                raise ValueError('Выключите тихую подготовку в лаунчере перед отправкой.')
            report('Выключите «Тихая подготовка», когда будете готовы оставить компьютер для отправки.')
            return
        if mode.get() == 'batch':
            return start_batch(remote=remote)
        if not prepared or busy:
            if remote:
                raise ValueError('Сначала подготовьте выбранный проект в этом окне лаунчера.')
            return
        if not remote and not messagebox.askokcancel('Начать передачу', 'Лаунчер откроет чат бота в Telegram Desktop. '
            'У вас будет 8 секунд, чтобы проверить получателя. Не переключайте чат и не используйте '
            'клавиатуру во время подготовки вложения. Для остановки вернитесь сюда и нажмите «Стоп».'):
            return
        stop.clear()
        def run():
            safe = RetryingClient(prepared['client'], stop, report)
            resume_rejected_before_send(safe, prepared['data'], report)
            desktop = TelegramDesktop(prepared['data']['bot_username'], prepared['data']['bot_name'])
            desktop.open()
            for seconds in range(8, 0, -1):
                report(f'Проверьте чат @{desktop.username}. Начало через {seconds} с.')
                if stop.wait(1):
                    return
            desktop.bind()
            transfer(safe, prepared['data'], prepared['manifest'], desktop, stop, report)
        return background(run, job_kind='send')

    def reset_uncertain():
        if busy:
            return

        folder_text = values['folder'].get().strip()
        source_root = Path(folder_text).resolve() if folder_text else None
        stream_journals = []
        if source_root and source_root.is_dir():
            for journal in (source_root.parent / 'KinoTime-Ready').glob('stream-*/.pipeline-state.json'):
                try:
                    state = json.loads(journal.read_text(encoding='utf-8'))
                    signature = state['signature']
                    if (Path(signature['source']['root']).resolve() == source_root
                            and signature['instance'] == instance_id
                            and signature['server'].rstrip('/') == values['url'].get().strip().rstrip('/')):
                        stream_journals.append((journal, state))
                except (OSError, ValueError, KeyError, TypeError):
                    continue
        if stream_journals:
            if not messagebox.askyesno('Повторная отправка',
                    'Подтвердите: неподтверждённые файлы этой очереди не отправлены и не загружаются в Telegram. '
                    'После сброса повторите «Подготовка → отправка → очистка».'):
                return
            reset_client = make_client(values['url'].get().strip(), values['token'].get().strip())
            def reset_stream():
                released = 0
                for journal, state in stream_journals:
                    if not state.get('draft_id'):
                        continue
                    current = reset_client.request('import/' + state['draft_id'])
                    confirmed = {f['id'] for f in current['files'] if f['status'] == 'ready'}
                    pending = sorted(set(state.get('assets', {}).values()) - confirmed)
                    if not pending:
                        continue
                    answer = reset_client.request('transfers/reset',
                        {'asset_ids': pending, 'verified_not_sent': True})
                    released += answer.get('released', 0)
                    transfer_journal = journal.parent / '.kinotime-transfer.json'
                    if transfer_journal.exists():
                        data = json.loads(transfer_journal.read_text(encoding='utf-8'))
                        for asset in pending:
                            if data.get(asset) == 'sent_unconfirmed':
                                data.pop(asset)
                        temp = transfer_journal.with_suffix('.tmp')
                        temp.write_text(json.dumps(data), encoding='utf-8')
                        temp.replace(transfer_journal)
                report(f'Снято отметок отправки: {released}. Продолжите «Подготовка → отправка → очистка». '
                       'Подтверждённые варианты пропускаются.')
            background(reset_stream)
            return

        # Batch mode has no `prepared` object, so the old implementation silently
        # returned and the button appeared to do nothing.
        if mode.get() == 'batch':
            folder_text = values['folder'].get().strip()
            if not folder_text:
                report('Сначала выберите папку с видео.')
                return
            root = Path(folder_text).resolve()
            if not root.is_dir():
                report('Выбранная папка с видео не найдена.')
                return
            if not messagebox.askyesno(
                    'Повторная отправка',
                    'Проверьте Telegram: видео действительно не отправлено и не загружается?\n\n'
                    'Будут сброшены отметки неопределённой отправки этого компьютера. '
                    'Карточки и настройки WEB-панели сохранятся.'):
                return

            reset_client = make_client(values['url'].get().strip(), values['token'].get().strip())
            def work():
                cleared = 0

                # Prepared MP4 copies are stored next to the selected source folder.
                # Clear only "sent_unconfirmed"; keep confirmed entries intact.
                search_roots = [root]
                ready = root.parent / 'KinoTime-Ready'
                if ready.is_dir():
                    search_roots.append(ready)

                seen = set()
                for base in search_roots:
                    for journal_path in base.rglob('.kinotime-transfer.json'):
                        try:
                            key = str(journal_path.resolve())
                            if key in seen:
                                continue
                            seen.add(key)
                            data = json.loads(journal_path.read_text(encoding='utf-8'))
                            if not isinstance(data, dict):
                                continue
                            pending_ids = [key for key, value in data.items() if value == 'sent_unconfirmed']
                            if pending_ids:
                                reset_client.request('transfers/reset', {'asset_ids': pending_ids, 'verified_not_sent': True})
                            changed_journal = False
                            for asset_id in list(data):
                                if data.get(asset_id) == 'sent_unconfirmed':
                                    del data[asset_id]
                                    changed_journal = True
                                    cleared += 1
                            if changed_journal:
                                temp = journal_path.with_suffix('.tmp')
                                temp.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
                                temp.replace(journal_path)
                        except (OSError, json.JSONDecodeError):
                            continue

                # Preserve draft_id/scope/WEB preparation, but move a locally stuck
                # "sending" item back to prepared so the batch runner can retry it.
                batch_journal = root / '.kinotime-batch.json'
                if batch_journal.exists():
                    try:
                        data = json.loads(batch_journal.read_text(encoding='utf-8'))
                        if isinstance(data, dict):
                            changed_batch = False
                            for item in data.values():
                                if isinstance(item, dict) and item.get('status') in ('sending', 'paused'):
                                    item['status'] = 'prepared' if item.get('draft_id') else 'pending'
                                    item['error'] = ''
                                    changed_batch = True
                            if changed_batch:
                                temp = batch_journal.with_suffix('.tmp')
                                temp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
                                temp.replace(batch_journal)
                    except Exception as error:
                        report('Не удалось обновить журнал очереди: ' + str(error))
                        return

                try:
                    refreshed = batch_plan(root)
                    show_batch(refreshed)
                except Exception:
                    pass
                report(f'Повтор разрешён. Сброшено неподтверждённых отправок: {cleared}. '
                       'WEB-карточки и подтверждённые файлы сохранены.')
            background(work)
            return

        # Single-movie mode.
        if not prepared:
            report('Сначала подготовьте фильм.')
            return
        if messagebox.askyesno('Повторная отправка', 'Проверьте в Telegram: файлы не отправлены и не загружаются? '
                               'Сброс разрешит повторную отправку неподтверждённых файлов.'):
            def work():
                path = Path(prepared['manifest']['root']) / '.kinotime-transfer.json'
                if path.exists():
                    try:
                        data = json.loads(path.read_text(encoding='utf-8'))
                    except Exception:
                        data = {}
                    if isinstance(data, dict):
                        pending_ids = [k for k, v in data.items() if v == 'sent_unconfirmed']
                        if pending_ids:
                            prepared['client'].request('transfers/reset', {'asset_ids': pending_ids, 'verified_not_sent': True})
                        data = {k: v for k, v in data.items() if v != 'sent_unconfirmed'}
                    else:
                        data = {}
                    path.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
                report('Повтор разрешён. Уже подтверждённые ботом файлы повторно отправлены не будут.')
            background(work)

    def recover_cards(kind):
        if busy:
            report('Дождитесь завершения текущей операции или остановите очередь.')
            return
        if not messagebox.askyesno('Восстановить карточки',
                'Проверить карточки на сервере и вернуть подходящие на проверку?\n'
                'Файлы и оформление сохраняются. Существующие публикации не сбрасываются.'):
            return
        try:
            client = make_client(values['url'].get().strip(), values['token'].get().strip())
            folder = values['folder'].get().strip()
            single = mode.get() != 'batch'
            draft_id = prepared.get('data', {}).get('draft', {}).get('id') if single else None
            if single and not draft_id:
                raise ValueError('Сначала откройте подготовленную карточку выбранного фильма.')
        except Exception as error:
            report(str(error))
            return
        stop.clear()
        def work():
            safe = RetryingClient(client, stop, report)
            if single:
                result = safe.request(f'import/{draft_id}/reopen', {'kind': kind})
                prepared['data']['draft'] = result['draft']
                report('Карточка возвращена на проверку. Откройте /web; повторная отправка принятых файлов не нужна.')
            else:
                restored, errors = recover_batch_cards(folder, safe, kind, show_batch)
                report(f'Возвращено на проверку: {restored}. ' + ('; '.join(errors) if errors else 'Откройте /web для настройки карточек.'))
        background(work)

    def recreate_rejected():
        recover_cards('rejected')

    def reset_deleted_published():
        recover_cards('published')

    def diagnose():
        if not prepared or busy:
            report('Сначала нажмите «Найти видео и создать черновик».')
            return
        stop.clear()
        def run():
            desktop = TelegramDesktop(prepared['data']['bot_username'], prepared['data']['bot_name'])
            desktop.open()
            report('Диагностика: оставьте Telegram на переднем плане. Проверка через 8 секунд; файлы не отправляются.')
            if stop.wait(8):
                return
            desktop.bind(verify_chat=False)
            result = desktop.inspect_header()
            path = desktop.save_diagnostics(result)
            report(('Чат бота распознан. ' if result['chat_verified'] else 'Чат бота не распознан. ')
                   + f'Диагностика: {path}. Видео не отправлялись.')
        background(run)

    batch_items = []
    batch_names = {'pending': 'Ожидает', 'preparing': 'Подготовка MP4', 'sending': 'Отправка',
                   'registering': 'Создание черновика в WEB',
                   'prepared': 'Настройка в WEB', 'complete': 'Готов к проверке',
                   'published': 'Опубликовано', 'rejected': 'Отклонено в WEB',
                   'paused': 'Пауза', 'error': 'Ошибка подготовки'}

    queue_update = {'items': [], 'scheduled': False}
    def show_batch(items):
        render_job.queue(items)
        queue_update['items'] = json.loads(json.dumps(items))
        if queue_update['scheduled']:
            return
        queue_update['scheduled'] = True
        def update():
            queue_update['scheduled'] = False
            queue_view.render(queue_update['items'])
        app.after(100, update)

    def selected_global_qualities():
        selected = [name for name in QUALITY_NAMES if quality_vars[name].get()]
        if not selected:
            raise ValueError('Выберите хотя бы одно качество: исходник, 1080p, 720p, 480p или 360p.')
        return selected

    def mark_quality_selection_changed(*_):
        nonlocal quality_selection_dirty
        quality_selection_dirty = True

    for variable in quality_vars.values():
        variable.trace_add('write', mark_quality_selection_changed)

    def apply_quality_selection(folder):
        requested = selected_global_qualities()
        root = Path(folder).resolve()
        if not root.is_dir():
            raise ValueError('Выберите существующую папку с видео.')
        plans = []
        unavailable = []
        for source in sorted(root.iterdir(), key=lambda path: path.name.casefold()):
            if not source.is_file() or source.suffix.lower() not in EXTENSIONS:
                continue
            media = quality_choices(source)
            selected = resolve_quality_selection(media, requested)
            if not selected:
                unavailable.append(source.name)
            else:
                plans.append((source, selected, media['mode']))
        if unavailable:
            preview = ', '.join(unavailable[:3]) + ('…' if len(unavailable) > 3 else '')
            raise ValueError('Для выбранных качеств недостаточно разрешения исходника: ' + preview
                             + '. Добавьте «Исходное разрешение» или более низкое качество.')
        for source, selected, encoding_mode in plans:
            save_media_choices(source, selected, encoding_mode)
        return plans

    def set_remote_qualities(payload):
        nonlocal quality_selection_dirty
        requested = payload.get('qualities')
        if requested is None and payload.get('quality_policy') in ('adaptive', 'source_only'):
            requested = ['source'] if payload['quality_policy'] == 'source_only' else ['1080p', '720p']
        if not isinstance(requested, list) or not requested or any(name not in QUALITY_NAMES for name in requested):
            raise ValueError('С телефона не передан корректный выбор качеств.')
        for name, variable in quality_vars.items():
            variable.set(name in requested)
        quality_selection_dirty = True


    def configure_media():
        if busy:
            return
        folder_mode = mode.get() == 'single'
        selected = queue.selection()
        if not selected:
            report('Выберите фильм в очереди, затем откройте «Качество и звук».')
            return
        row = selected[0]
        if folder_mode:
            path = queue_view.sources.get(row)
            if not path:
                report('Раскройте проект и выберите исходник серии, качество или озвучку под ним.')
                return
            source = Path(path)
            try:
                requested = selected_global_qualities()
            except ValueError as error:
                report(str(error))
                return
        else:
            while queue.parent(row):
                row = queue.parent(row)
            source = Path(values['folder'].get()) / row
            try:
                current = next(i for i in batch_plan(source.parent) if i['file']==source.name)
                if current.get('draft_id'):
                    raise ValueError('Карточка уже создана. Качество и звук выбираются до шага 1.')
            except Exception as error:
                report(str(error)); return
        def probe():
            audio = audio_choices(source)
            media = quality_choices(source)
            if folder_mode:
                media['selected'] = resolve_quality_selection(media, requested)
            def show():
                dialog = tk.Toplevel(app)
                dialog.title('Качество и звук — ' + source.name)
                dialog.configure(bg=colors['window'])
                dialog.geometry('790x760')
                dialog.transient(app)
                body = ttk.Frame(dialog, style='Card.TFrame')
                body.pack(fill='both', expand=True, padx=18, pady=18)
                ttk.Label(body, text=source.name, style='Section.TLabel').pack(anchor='w', padx=18, pady=(16, 4))
                ttk.Label(body, text=f"Исходник: {media['width']}×{media['height']} · "
                          f"{media['duration']/60:.0f} мин", style='CardMuted.TLabel').pack(anchor='w', padx=18)
                ttk.Label(body, text='Качество для телефона и Telegram', style='Card.TLabel').pack(
                    anchor='w', padx=18, pady=(18, 6))
                if folder_mode:
                    ttk.Label(body, text='Для папки качества выбираются в разделе «Источник». '
                              'Здесь можно изменить озвучки этой серии.', style='CardMuted.TLabel',
                              wraplength=620).pack(anchor='w', padx=18)
                quality_vars = {}
                for quality in media['eligible']:
                    profile = _quality_profile(quality, probe_video(source))
                    estimated = min(profile['limit'], media['duration'] *
                                    (profile['video'] + profile['audio']) * 1000 / 8 / 1024**3)
                    value = tk.BooleanVar(value=quality in media['selected'])
                    quality_vars[quality] = value
                    label = ('Исходное разрешение' if quality == 'source' else quality)
                    ttk.Checkbutton(body, variable=value, state='disabled' if folder_mode else 'normal',
                        text=f'{label:<10}  примерно {estimated:.2f} ГиБ · предел {profile["limit"]:.2f} ГиБ').pack(
                            anchor='w', padx=24, pady=2)
                ttk.Label(body, text='Озвучки', style='Card.TLabel').pack(anchor='w', padx=18, pady=(18, 6))
                ttk.Label(body, text='Обрабатываются только отмеченные дорожки. Язык берётся из метаданных; '
                          'дорожки без языка проверьте вручную.', style='CardMuted.TLabel',
                          wraplength=700).pack(anchor='w', padx=18)
                language_row = ttk.Frame(body, style='Card.TFrame')
                language_row.pack(fill='x', padx=18, pady=6)
                languages = list(dict.fromkeys(c['language'] for c in audio))
                language_value = tk.StringVar(value=audio_language_label(languages[0]) if languages else '')
                ttk.Combobox(language_row, textvariable=language_value, state='readonly', width=23,
                             values=[audio_language_label(code) for code in languages]).pack(side='left')
                def select_language():
                    for choice, enabled, _ in fields:
                        enabled.set(audio_language_label(choice['language']) == language_value.get())
                ttk.Button(language_row, text='Выбрать только этот язык', command=select_language).pack(side='left', padx=6)
                def select_all_audio(value):
                    for _, enabled, _ in fields:
                        enabled.set(value)
                ttk.Button(language_row, text='Все', command=lambda: select_all_audio(True)).pack(side='left')
                ttk.Button(language_row, text='Снять все', command=lambda: select_all_audio(False)).pack(side='left', padx=6)
                # Keep the save controls reachable even for sources with many tracks.
                audio_container = ttk.Frame(body, style='Card.TFrame')
                audio_container.pack(fill='both', expand=True, padx=18, pady=4)
                audio_canvas = tk.Canvas(audio_container, height=140, bg=colors['window'], highlightthickness=0)
                audio_scroll = ttk.Scrollbar(audio_container, orient='vertical', command=audio_canvas.yview)
                audio_scroll.pack(side='right', fill='y')
                audio_canvas.pack(side='left', fill='both', expand=True)
                audio_canvas.configure(yscrollcommand=audio_scroll.set)
                audio_body = ttk.Frame(audio_canvas, style='Card.TFrame')
                audio_window = audio_canvas.create_window((0, 0), window=audio_body, anchor='nw')
                audio_body.bind('<Configure>', lambda _: audio_canvas.configure(scrollregion=audio_canvas.bbox('all')))
                audio_canvas.bind('<Configure>', lambda event: audio_canvas.itemconfigure(audio_window, width=event.width))
                fields=[]
                for choice in audio:
                    row_frame=ttk.Frame(audio_body, style='Card.TFrame');row_frame.pack(fill='x',padx=4,pady=3)
                    enabled=tk.BooleanVar(value=choice['enabled']);name=tk.StringVar(value=choice['name'])
                    ttk.Checkbutton(row_frame,text=str(choice['index']+1),variable=enabled).pack(side='left')
                    ttk.Label(row_frame, text=audio_language_label(choice['language']), width=18,
                              style='CardMuted.TLabel').pack(side='left', padx=6)
                    ttk.Entry(row_frame,textvariable=name,width=55).pack(side='left',fill='x',expand=True,padx=(8,0))
                    fields.append((choice,enabled,name))
                mode_value = tk.StringVar(value=media['mode'])
                mode_row = ttk.Frame(body, style='Card.TFrame');mode_row.pack(fill='x', padx=18, pady=(18, 4))
                ttk.Label(mode_row, text='Кодирование:', style='Card.TLabel').pack(side='left')
                ttk.Radiobutton(mode_row, text='Быстро', variable=mode_value, value='fast').pack(side='left', padx=8)
                ttk.Radiobutton(mode_row, text='Точнее', variable=mode_value, value='exact').pack(side='left', padx=8)
                summary = ttk.Label(body, style='CardMuted.TLabel')
                summary.pack(anchor='w', padx=18, pady=8)
                def update_summary(*_):
                    q = sum(v.get() for v in quality_vars.values())
                    a = sum(enabled.get() for _, enabled, _ in fields) if fields else 1
                    summary.configure(text=f'Итоговых файлов: {q} качества × {a} озвучки = {q*a}')
                for value in list(quality_vars.values()) + [enabled for _, enabled, _ in fields]:
                    value.trace_add('write', update_summary)
                update_summary()
                def save():
                    try:
                        result=[dict(c,enabled=e.get(),name=n.get().strip()) for c,e,n in fields]
                        if result:
                            save_audio_choices(source,result)
                        selected_quality = [name for name, value in quality_vars.items() if value.get()]
                        save_media_choices(source, selected_quality, mode_value.get())
                        dialog.destroy();show_page('queue')
                        if folder_mode:
                            # Probe on a worker after the current probe releases the UI busy flag.
                            app.after(0, inspect_batch)
                        else:
                            show_batch(batch_plan(source.parent))
                            report('Профили сохранены. Теперь можно создавать WEB-карточки.')
                    except Exception as error:
                        messagebox.showerror('Качество и звук',str(error),parent=dialog)
                ttk.Button(body,text='Сохранить выбор',style='Primary.TButton',command=save).pack(
                    anchor='e', padx=18, pady=(4,16))
            app.after(0,show)
        background(probe, allow_projects=True)

    def inspect_batch():
        nonlocal batch_items, quality_selection_dirty
        if busy:
            return
        try:
            if mode.get() == 'single':
                folder, requested = values['folder'].get(), selected_global_qualities()
                report('Проверяю разрешение и озвучки исходников, рассчитываю число MP4…')
                def preview():
                    nonlocal batch_items
                    manifest, plans = plan_folder_qualities(folder, requested, threading.Event())
                    batch_items = [folder_queue_item(manifest, plans)]
                    show_batch(batch_items)
                    report(f"Исходников: {len(plans)} → MP4: {len(batch_items[0]['variants'])}. "
                           'Раскройте серию: качества × озвучки. Лимит одного импорта — 1000 MP4.')
                background(preview, allow_projects=True)
                return
            if quality_selection_dirty:
                apply_quality_selection(values['folder'].get())
                quality_selection_dirty = False
            batch_items = batch_plan(values['folder'].get())
            show_batch(batch_items)
            report(f'В очереди {len(batch_items)} фильмов. Каждый файл — отдельная карточка. '
                   'Проверьте названия; год лучше указывать в имени файла.')
        except Exception as error:
            report(str(error))

    def start_batch(prepare_only=False, remote=False):
        nonlocal quality_selection_dirty
        if project_conflict():
            if remote:
                raise ValueError('Работают проекты; сначала приостановите их.')
            return False
        if busy:
            if remote:
                raise ValueError('Лаунчер занят.')
            return
        if not prepare_only and quiet_preparation.get():
            message = 'Тихая подготовка включена. Выключите её в лаунчере перед отправкой через Telegram.'
            if remote:
                raise ValueError(message)
            report(message)
            return
        folder = values['folder'].get()
        try:
            if quality_selection_dirty:
                apply_quality_selection(folder)
                quality_selection_dirty = False
            items = batch_plan(folder)
            client = make_client(values['url'].get(), values['token'].get())
        except Exception as error:
            if remote:
                raise
            report(str(error))
            return
        if not prepare_only and not remote and not messagebox.askokcancel('Отправить настроенные фильмы',
            'Вы сохранили оформление каждого фильма в WEB-панели?\n\n'
            'Сейчас начнётся отправка файлов через Telegram Desktop. Публикацию вы подтвердите отдельно. '
            'Оставьте Windows разблокированной и Telegram открытым. Продолжить?'):
            return
        stop.clear()
        show_batch(items)

        def connect(data):
            desktop = TelegramDesktop(data['bot_username'], data['bot_name'])
            desktop.open()
            report(f'Открываю @{desktop.username}. Подготовка отправки через 8 секунд.')
            if stop.wait(8):
                raise InterruptedError('Очередь остановлена.')
            try:
                desktop.bind()
            except Exception as error:
                raise BeforeSendError(str(error)) from error
            return desktop

        report.catalog_section = selected_catalog_section()
        started = background(lambda: run_batch(folder, client, stop, report, show_batch, connect,
            prepare_only=prepare_only, require_prepared=not prepare_only), job_kind='prepare' if prepare_only else 'send')
        if prepare_only and quiet_preparation.get():
            app.iconify()
        return started

    def stream():
        if project_conflict():
            return
        if busy:
            return
        if quiet_preparation.get():
            report('Выключите тихую подготовку перед запуском режима с отправкой в Telegram.')
            return
        try:
            folder = values['folder'].get()
            qualities = selected_global_qualities()
            client = make_client(values['url'].get(), values['token'].get())
            section = selected_catalog_section()
            sources = ([str(Path(folder) / item['file']) for item in batch_plan(folder)]
                       if mode.get() == 'batch' else [folder])
        except Exception as error:
            report(str(error))
            return
        if not messagebox.askokcancel('Подготовка → отправка → очистка',
                'Каждый готовый MP4 будет отправлен боту и удалён с компьютера только после подтверждения. '
                'Исходники сохраняются. Все варианты проекта попадут в одну WEB-карточку.\n\n'
                'Оставьте Windows разблокированной; Telegram будет управляться лаунчером. '
                'Публикацию нужно подтвердить отдельно в WEB. Запустить?'):
            return
        stop.clear()
        prepared.clear()

        def connect(data):
            desktop = TelegramDesktop(data['bot_username'], data['bot_name'])
            desktop.open()
            report(f'Открываю @{desktop.username}. Отправка через 8 секунд.')
            if stop.wait(8):
                raise InterruptedError('Поточная подготовка остановлена.')
            try:
                desktop.bind()
            except Exception as error:
                raise BeforeSendError(str(error)) from error
            return desktop

        def work():
            try:
                from . import stream_pipeline
            except ImportError:
                import stream_pipeline
            for source in sources:
                progress = FolderProgress(report, show_batch)
                try:
                    stream_pipeline.run(source, client, stop, progress, connect,
                                        qualities=qualities, section=section)
                    progress.phase('prepared')
                except Exception as error:
                    progress.phase('paused' if isinstance(error, InterruptedError) else 'error', str(error))
                    raise
        return background(work, job_kind='send')

    def page_heading(page, eyebrow, title, description):
        ttk.Label(page, text=eyebrow.upper(), style='Muted.TLabel').pack(anchor='w', pady=(6, 8))
        ttk.Label(page, text=title, style='Title.TLabel').pack(anchor='w')
        ttk.Label(page, text=description, style='Muted.TLabel').pack(anchor='w', pady=(4, 18))

    def card(parent):
        result = ttk.Frame(parent, style='Card.TFrame')
        result.pack(fill='x', pady=7)
        return result

    @staticmethod
    def control_identity(control):
        """Return a stable UIA identity even when pywinauto creates two wrappers."""
        try:
            handle = int(control.handle)
            if handle:
                return ('handle', handle)
        except Exception:
            pass
        try:
            runtime_id = control.element_info.runtime_id
            if runtime_id:
                return ('runtime', tuple(runtime_id))
        except Exception:
            pass
        try:
            rect = control.rectangle()
            return ('visual', str(control.element_info.control_type),
                    str(control.element_info.class_name),
                    int(rect.left), int(rect.top), int(rect.right), int(rect.bottom))
        except Exception:
            return ('object', id(control))

    # Overview
    page_heading(pages['overview'], 'Состояние', 'Управление каталогом',
                 'Подготовка на компьютере, оформление в WEB и отправка через Telegram Desktop.')
    overview_card = card(pages['overview'])
    ttk.Label(overview_card, text='Рабочий процесс', style='Section.TLabel').pack(anchor='w', padx=20, pady=(18, 8))
    ttk.Label(overview_card, text='1  Выберите папку   →   2  Настройте качество и звук   →   '
              '3  Создайте карточки   →   4  Отправьте после проверки в WEB',
              style='CardMuted.TLabel').pack(anchor='w', padx=20, pady=(0, 18))
    overview_actions = ttk.Frame(pages['overview'])
    overview_actions.pack(fill='x', pady=8)
    ttk.Button(overview_actions, text='Выбрать источник', style='Primary.TButton',
               command=lambda: show_page('source')).pack(side='left')
    ttk.Button(overview_actions, text='Открыть очередь',
               command=lambda: (show_page('queue'), inspect_batch())).pack(side='left', padx=8)

    # Source
    page_heading(pages['source'], 'Шаг 1', 'Источник видео',
                 'Выберите папку. В массовом режиме каждый файл создаёт отдельную WEB-карточку.')
    source_card = card(pages['source'])
    ttk.Label(source_card, text='Папка с видео', style='Card.TLabel').pack(anchor='w', padx=20, pady=(18, 6))
    source_row = ttk.Frame(source_card, style='Card.TFrame'); source_row.pack(fill='x', padx=20)
    folder_entry = ttk.Entry(source_row, textvariable=values['folder'])
    folder_entry.pack(side='left', fill='x', expand=True); bind_entry_shortcuts(folder_entry)
    ttk.Button(source_row, text='Выбрать папку', command=lambda: values['folder'].set(
        filedialog.askdirectory() or values['folder'].get())).pack(side='left', padx=(8, 0))
    ttk.Label(source_card, text='Режим импорта', style='Card.TLabel').pack(anchor='w', padx=20, pady=(18, 5))
    ttk.Radiobutton(source_card, text='Несколько фильмов: каждый файл — отдельная карточка',
                    variable=mode, value='batch').pack(anchor='w', padx=24, pady=2)
    ttk.Radiobutton(source_card, text='Один проект / сериал: содержимое папки — одна карточка',
                    variable=mode, value='single').pack(anchor='w', padx=24, pady=2)
    ttk.Label(source_card, text='Раздел будущих карточек', style='Card.TLabel').pack(
        anchor='w', padx=20, pady=(18, 5))
    section_picker = ttk.Combobox(source_card, textvariable=catalog_section, state='readonly',
                                  values=tuple(LAUNCHER_SECTIONS))
    section_picker.pack(anchor='w', fill='x', padx=24)
    section_picker.set(catalog_section.get())
    ttk.Label(source_card, text='Для сериалов и мультсериалов используйте сезонный импорт через WEB-панель.',
              style='CardMuted.TLabel').pack(anchor='w', padx=24, pady=(3, 2))
    ttk.Label(source_card, text='Качества для массовой подготовки', style='Card.TLabel').pack(
        anchor='w', padx=20, pady=(18, 5))
    quality_labels = {'source': 'Исходное разрешение', '1080p': '1080p', '720p': '720p',
                      '480p': '480p', '360p': '360p'}
    quality_row = ttk.Frame(source_card, style='Card.TFrame');quality_row.pack(fill='x', padx=20)
    for name in ('source', '1080p', '720p', '480p', '360p'):
        ttk.Checkbutton(quality_row, text=quality_labels[name], variable=quality_vars[name]).pack(
            side='left', padx=(4, 14), pady=3)
    ttk.Label(source_card, text='Создаются только отмеченные варианты. Качество выше исходника пропускается.',
              style='CardMuted.TLabel').pack(anchor='w', padx=24, pady=(3, 2))
    source_actions = ttk.Frame(source_card, style='Card.TFrame')
    source_actions.pack(fill='x', padx=20, pady=18)
    ttk.Button(source_actions, text='Просканировать и открыть очередь', style='Primary.TButton',
               command=lambda: (show_page('queue'), inspect_batch())).pack(side='left')
    ttk.Button(source_actions, text='Добавить в проекты и начать рендер',
               command=lambda: (show_page('projects'), add_project())).pack(side='right')

    # Processing
    page_heading(pages['processing'], 'Шаг 2', 'Качество и звук',
                 'Настройки применяются к выбранному фильму до создания WEB-карточки.')
    processing_card = card(pages['processing'])
    ttk.Label(processing_card, text='Выберите фильм в очереди', style='Section.TLabel').pack(
        anchor='w', padx=20, pady=(18, 6))
    ttk.Label(processing_card, text='Доступные профили зависят от разрешения исходника. '
              'Лаунчер не увеличивает разрешение и показывает ожидаемый размер.',
              style='CardMuted.TLabel', wraplength=780).pack(anchor='w', padx=20)
    ttk.Button(processing_card, text='Настроить выбранный фильм', style='Primary.TButton',
               command=configure_media).pack(anchor='w', padx=20, pady=18)

    # Queue
    page_heading(pages['queue'], 'Шаги 1–2', 'Очередь подготовки',
                 'Раскройте проект и исходник: внутри показаны качества, озвучки и состояние каждого MP4.')
    batch_frame = ttk.Frame(pages['queue'], style='Card.TFrame')
    batch_frame.pack(fill='both', expand=True)
    controls = ttk.Frame(batch_frame, style='Card.TFrame')
    controls.pack(fill='x', padx=14, pady=12)
    ttk.Button(controls, text='Обновить', command=inspect_batch).pack(side='left', padx=3)
    ttk.Button(controls, text='Качество и звук', command=lambda: (show_page('processing'),
               configure_media())).pack(side='left', padx=3)
    ttk.Button(controls, text='1. Подготовить без отправки', style='Primary.TButton',
               command=prepare).pack(side='left', padx=3)
    ttk.Button(controls, text='Стоп', command=stop_all).pack(side='right', padx=3)
    stream_controls = ttk.Frame(batch_frame, style='Card.TFrame')
    stream_controls.pack(fill='x', padx=17, pady=(0, 8))
    ttk.Button(stream_controls, text='Подготовка → отправка → очистка',
               command=stream).pack(side='left')
    ttk.Label(stream_controls, text='Очистка только после подтверждения бота',
              style='CardMuted.TLabel', wraplength=360).pack(side='left', padx=12)
    hardware_controls.append(video_hardware_ui.HardwareControls(batch_frame, hardware_value, load_value,
        hardware_reason, lambda: hardware_changed(), lambda: check_hardware(), lambda: hardware_details()))
    quiet_controls = ttk.Frame(batch_frame, style='Card.TFrame')
    quiet_controls.pack(fill='x', padx=17, pady=(0, 10))
    ttk.Checkbutton(quiet_controls, text='Тихая подготовка — свернуть лаунчер, отправку запускать позже',
                    variable=quiet_preparation).pack(anchor='w')
    ttk.Label(quiet_controls, text='Окно остаётся на панели задач. Подготовка продолжается; Telegram не открывается. '
              'Для остановки разверните лаунчер и нажмите «Стоп».', style='CardMuted.TLabel',
              wraplength=850).pack(anchor='w', pady=(4, 0))
    queue = ttk.Treeview(batch_frame, columns=('title', 'status', 'details'), show='tree headings', height=13)
    queue.column('#0', width=36, stretch=False)
    for column, title, width in [('title', 'Фильм', 230), ('status', 'Состояние', 150), ('details', 'Файл / причина', 440)]:
        queue.heading(column, text=title)
        queue.column(column, width=width)
    scroll = ttk.Scrollbar(batch_frame, orient='vertical', command=queue.yview)
    queue.configure(yscrollcommand=scroll.set)
    scroll.pack(side='right', fill='y', pady=(0, 12))
    queue.pack(fill='both', expand=True, padx=(14, 0), pady=(0, 12))
    queue_view = PreparationQueueView(queue, batch_names)

    # Telegram
    page_heading(pages['telegram'], 'Шаг 4', 'Отправка через Telegram Desktop',
                 'Запускайте после настройки карточек в WEB. Компьютер должен оставаться разблокированным.')
    telegram_card = card(pages['telegram'])
    ttk.Label(telegram_card, text='Передача настроенных файлов', style='Section.TLabel').pack(
        anchor='w', padx=20, pady=(18, 6))
    ttk.Label(telegram_card, text='Принятые ботом файлы пропускаются. При временной ошибке '
              'лаунчер повторяет проверку через 8 секунд.', style='CardMuted.TLabel',
              wraplength=800).pack(anchor='w', padx=20)
    ttk.Checkbutton(telegram_card, text='Тихая подготовка (выключите перед отправкой)',
                    variable=quiet_preparation).pack(anchor='w', padx=20, pady=(8, 0))
    telegram_actions = ttk.Frame(telegram_card, style='Card.TFrame'); telegram_actions.pack(fill='x', padx=18, pady=18)
    ttk.Button(telegram_actions, text='2. Отправить настроенные фильмы', style='Primary.TButton',
               command=start).pack(side='left', padx=3)
    ttk.Button(telegram_actions, text='Остановить', command=stop_all).pack(side='left', padx=3)
    ttk.Button(telegram_actions, text='Разрешить безопасный повтор', command=reset_uncertain).pack(side='left', padx=3)
    service_card = card(pages['telegram'])
    ttk.Label(service_card, text='Обслуживание очереди', style='Section.TLabel').pack(anchor='w', padx=20, pady=(16, 8))
    service_actions = ttk.Frame(service_card, style='Card.TFrame'); service_actions.pack(fill='x', padx=18, pady=(0, 16))
    ttk.Button(service_actions, text='Проверить Telegram без отправки', command=diagnose).pack(side='left', padx=3)
    ttk.Button(service_actions, text='Пересоздать отклонённые', command=recreate_rejected).pack(side='left', padx=3)
    ttk.Button(service_actions, text='Сбросить удалённые опубликованные',
               command=reset_deleted_published).pack(side='left', padx=3)

    # Settings
    page_heading(pages['settings'], 'Подключение', 'Настройки панели',
                 'Получите адрес и код командой /desktop. Код действует 48 часов и хранится только до закрытия лаунчера.')
    settings_card = card(pages['settings'])
    entries = {}
    for key, label in [('url', 'Адрес панели'), ('token', 'Код подключения')]:
        ttk.Label(settings_card, text=label, style='Card.TLabel').pack(anchor='w', padx=20, pady=(16, 5))
        row = ttk.Frame(settings_card, style='Card.TFrame'); row.pack(fill='x', padx=20)
        entry = ttk.Entry(row, textvariable=values[key], show='*' if key == 'token' else '')
        entry.pack(side='left', fill='x', expand=True); bind_entry_shortcuts(entry); entries[key] = entry
        if key == 'token':
            ttk.Button(row, text='Вставить код', command=lambda field=entry: (
                field.focus_set(), field.event_generate('<<Paste>>'))).pack(side='left', padx=(8, 0))

    connection_reason = tk.StringVar(value='Нажмите «Проверить связь» для проверки адреса и кода.')
    ttk.Label(settings_card, textvariable=connection_reason, wraplength=800,
              style='Card.TLabel').pack(fill='x', padx=20, pady=12)

    def check_panel_connection():
        url, token = values['url'].get().strip(), values['token'].get().strip()
        def check():
            try:
                make_client(url, token).request('session')
                app.after(0, connection_reason.set, 'Связь с API установлена. Код подключения действителен.')
                app.after(0, connection_label.configure, {'text': '● Панель подключена'})
            except Exception as error:
                label, reason = panel_connection_error(error)
                app.after(0, connection_reason.set, reason)
                app.after(0, connection_label.configure, {'text': label})
        connection_reason.set('Проверка связи с панелью…')
        background(check)

    ttk.Button(settings_card, text='Проверить связь', command=check_panel_connection).pack(anchor='w', padx=20, pady=8)

    settings_path = state_path('launcher-settings.json', __file__)
    try:
        identity_settings = json.loads(settings_path.read_text(encoding='utf-8'))
        if not isinstance(identity_settings, dict):
            identity_settings = {}
    except (OSError, ValueError):
        identity_settings = {}
    device_info = launcher_device.identity(identity_settings)
    instance_id = device_info['instance_id']
    computer_name = tk.StringVar(value=device_info['name'])
    ttk.Label(settings_card, text='Имя компьютера в веб-панели', style='Card.TLabel').pack(anchor='w', padx=20, pady=(12, 5))
    name_entry = ttk.Entry(settings_card, textvariable=computer_name)
    name_entry.pack(fill='x', padx=20)
    bind_entry_shortcuts(name_entry)

    def make_client(url, token):
        return Client(url, token, instance_id=instance_id, report=report)
    hardware_card = card(pages['processing'])
    ttk.Label(hardware_card, text='Оборудование и нагрузка', style='Section.TLabel').pack(
        anchor='w', padx=20, pady=(14, 6))
    hardware_controls.append(video_hardware_ui.HardwareControls(hardware_card, hardware_value, load_value,
        hardware_reason, lambda: hardware_changed(), lambda: check_hardware(), lambda: hardware_details()))

    def apply_hardware_settings():
        if busy or projects.busy:
            raise ValueError('Сначала остановите очередь, затем измените настройки оборудования.')
        video_hardware.configure(hardware_labels[hardware_value.get()], load_labels[load_value.get()])

    def check_hardware():
        try:
            apply_hardware_settings()
        except ValueError as error:
            report(str(error));return
        hardware_reason.set('Проверяю Intel, AMD и NVIDIA на коротком тестовом видео…')
        def check():
            info = video_hardware.diagnostics(refresh=True)
            hardware_info.clear()
            hardware_info.update(info)
            state_path('hardware-diagnostics.json', __file__).write_text(
                json.dumps(info, ensure_ascii=False, indent=2), encoding='utf-8')
            report('Оборудование: ' + video_hardware.diagnostics_summary(info))
        background(check)

    def hardware_details():
        if not hardware_info:
            report('Сначала нажмите «Проверить» рядом с выбором кодировщика.')
            return
        dialog = tk.Toplevel(app)
        dialog.title('Диагностика оборудования')
        dialog.geometry('780x560')
        body = tk.Text(dialog, wrap='word', bg=colors['card'], fg=colors['text'],
                       font=('Segoe UI', 10), padx=16, pady=16)
        body.pack(fill='both', expand=True)
        body.insert('1.0', video_hardware.diagnostics_text(hardware_info))
        body.configure(state='disabled')
        ttk.Button(dialog, text='Закрыть', command=dialog.destroy).pack(pady=8)

    def hardware_changed():
        previous = video_hardware.settings()
        if busy or projects.busy:
            hardware_value.set(next(label for label, key in hardware_labels.items() if key == previous['hardware']))
            load_value.set(next(label for label, key in load_labels.items() if key == previous['resource_mode']))
            report('Сначала остановите очередь, затем измените настройки оборудования.')
            return
        if save_settings():
            hardware_info.clear()
            hardware_reason.set('Выбрано: ' + hardware_value.get() + '. Нагрузка: ' + load_value.get()
                                + '. Настройка сохранена. Нажмите «Проверить» для теста кодирования.')
    remote_folders = []
    ttk.Checkbutton(settings_card, text='Разрешить выбор любых папок этого ПК через веб-панель',
                    variable=remote_browse_enabled, command=lambda: save_settings()).pack(
                        anchor='w', padx=20, pady=(8, 0))
    ttk.Checkbutton(settings_card, text='Автоочистка: промежуточные файлы и MP4, подтверждённые ботом',
                    variable=cleanup_enabled, command=lambda: save_settings()).pack(anchor='w', padx=20, pady=8)
    disk_row = ttk.Frame(settings_card, style='Card.TFrame')
    disk_row.pack(fill='x', padx=20, pady=4)
    ttk.Label(disk_row, text='Резерв свободного места, ГиБ:', style='Card.TLabel').pack(side='left')
    ttk.Entry(disk_row, textvariable=reserve_gib, width=8).pack(side='left', padx=10)
    ttk.Label(settings_card, text='Исходники и непринятые MP4 сохраняются. При нехватке места очередь ждёт '
              'и продолжает автоматически. После очистки принятые копии нужно создавать заново для повторной отправки.',
              style='CardMuted.TLabel', wraplength=720).pack(anchor='w', padx=20, pady=4)
    def save_settings():
        try:
            qualities = selected_global_qualities()
            requested_hardware = {'hardware': hardware_labels[hardware_value.get()],
                                  'resource_mode': load_labels[load_value.get()]}
            if requested_hardware != video_hardware.settings():
                apply_hardware_settings()
            disk_space.configure(cleanup_enabled.get(), float(reserve_gib.get().replace(',', '.')))
        except ValueError as error:
            report(str(error))
            return
        folder = values['folder'].get().strip()
        if folder and folder not in remote_folders:
            remote_folders.append(folder)
        settings_path.write_text(json.dumps({'url': values['url'].get().strip(),
            'folder': folder, 'folders': remote_folders[-30:], 'instance_id': instance_id,
            'machine_key': device_info['machine_key'], 'name': computer_name.get().strip()[:80] or device_info['name'],
            'qualities': qualities, 'catalog_section': selected_catalog_section(),
            'remote_browse_enabled': remote_browse_enabled.get(), **video_hardware.settings(), **disk_space.settings()},
            ensure_ascii=False, indent=2), encoding='utf-8')
        connection_label.configure(text='● Настройки сохранены')
        report('Настройки сохранены. Код подключения останется только до закрытия лаунчера.')
        return True
    ttk.Button(settings_card, text='Сохранить настройки', style='Primary.TButton',
               command=save_settings).pack(anchor='e', padx=20, pady=18)
    if settings_path.exists():
        try:
            saved_settings = json.loads(settings_path.read_text(encoding='utf-8'))
            remote_browse_enabled.set(saved_settings.get('remote_browse_enabled', True) is True)
            cleanup_enabled.set(saved_settings.get('disk_cleanup', True) is True)
            reserve_gib.set(str(saved_settings.get('disk_reserve_gib', 2)))
            disk_space.configure(cleanup_enabled.get(), float(reserve_gib.get()))
            policy = saved_settings.get('hardware', 'auto')
            load = saved_settings.get('resource_mode', 'balanced')
            video_hardware.configure(policy, load)
            hardware_value.set(next(label for label, key in hardware_labels.items() if key == policy))
            load_value.set(next(label for label, key in load_labels.items() if key == load))
            saved_qualities = saved_settings.get('qualities')
            if not isinstance(saved_qualities, list):
                saved_qualities = (['source'] if saved_settings.get('quality_policy') == 'source_only'
                                   else ['1080p', '720p'])
            for name, variable in quality_vars.items():
                variable.set(name in saved_qualities)
            saved_section = saved_settings.get('catalog_section', 'movies')
            catalog_section.set(next((label for label,key in LAUNCHER_SECTIONS.items()
                                      if key == saved_section), 'Фильмы'))
            remote_folders.extend(path for path in saved_settings.get('folders', [])
                                  if isinstance(path, str) and path.strip())
            for key in ('url', 'folder'):
                values[key].set(saved_settings.get(key, ''))
            if values['folder'].get() and values['folder'].get() not in remote_folders:
                remote_folders.append(values['folder'].get())
            values['token'].set('')
            if 'token' in saved_settings:
                settings_path.write_text(json.dumps({
                    'url': saved_settings.get('url', ''),
                    'folder': saved_settings.get('folder', ''),
                    'folders': remote_folders,
                    'instance_id': instance_id,
                    'machine_key': device_info['machine_key'], 'name': computer_name.get(),
                    'qualities': selected_global_qualities(),
                    'catalog_section': selected_catalog_section(),
                    'remote_browse_enabled': remote_browse_enabled.get(),
                    **video_hardware.settings(),
                    **disk_space.settings(),
                }, ensure_ascii=False, indent=2), encoding='utf-8')
            connection_label.configure(text='● Настройки загружены')
        except Exception:
            report('Сохранённые настройки повреждены; заполните их заново.')

    # Persist the identity even if the user never presses Save; omit the pairing code.
    save_settings()

    def import_launcher_settings():
        nonlocal instance_id, device_info
        if busy or projects.busy or remote_request_running:
            report('Дождитесь завершения текущей операции и проверки связи перед переносом настроек.')
            return
        filename = filedialog.askopenfilename(title='Настройки старого лаунчера на этом компьютере',
            filetypes=[('Настройки лаунчера', 'launcher-settings.json'), ('JSON', '*.json')])
        if not filename:
            return
        try:
            saved = json.loads(Path(filename).read_text(encoding='utf-8'))
            if not isinstance(saved, dict) or not launcher_device.valid_instance(saved.get('instance_id')):
                raise ValueError('В этом файле нет идентификатора старого лаунчера.')
            device_info = launcher_device.identity(saved)
            instance_id = device_info['instance_id']
            computer_name.set(device_info['name'])
            for key in ('url', 'folder'):
                values[key].set(saved.get(key, ''))
            values['token'].set('')
            remote_folders[:] = [p for p in saved.get('folders', []) if isinstance(p, str) and p.strip()][:30]
            remote_browse_enabled.set(saved.get('remote_browse_enabled', True) is True)
            requested = saved.get('qualities', ['1080p', '720p'])
            for name, variable in quality_vars.items():
                variable.set(name in requested)
            save_settings()
            report('Настройки перенесены. Получите свежий код /desktop для этого компьютера; старые очереди сохранены.')
        except (OSError, ValueError) as error:
            report('Не удалось перенести настройки: ' + str(error))

    ttk.Button(settings_card, text='Перенести настройки старого лаунчера',
               command=import_launcher_settings).pack(anchor='w', padx=20, pady=(0, 14))

    remote_request_running = False
    remote_results = {}
    remote_results_lock = threading.Lock()
    torrent_remote = {}
    def finish_remote(command, ok, message, data=None):
        url, token = values['url'].get().strip(), values['token'].get().strip()
        if not url or not token:return
        body = {'ok': ok, 'result': message}
        if data is not None:
            body['data'] = data
        with remote_results_lock:
            remote_results[command['id']] = (make_client(url, token), body)

    def choose_remote_folder(folder):
        path = remote_render.existing_folder(folder, remote_folders, remote_browse_enabled.get())
        if prepared and prepared.get('source_folder') != path:
            prepared.clear()
        if values['folder'].get() != path:
            queue_update['items'] = []
        values['folder'].set(path)
        if path not in remote_folders:
            remote_folders.append(path)
        return path

    def remote_settings(payload):
        if 'folder' in payload:
            choose_remote_folder(payload['folder'])
        selected_mode = payload.get('mode', 'batch')
        if selected_mode not in ('single', 'batch'):
            raise ValueError('Выберите режим одного проекта или нескольких фильмов.')
        policy = payload.get('hardware', hardware_labels[hardware_value.get()])
        load = payload.get('load', load_labels[load_value.get()])
        if policy not in hardware_labels.values() or load not in load_labels.values():
            raise ValueError('Некорректные настройки оборудования.')
        section = payload.get('catalog_section', selected_catalog_section())
        if section not in LAUNCHER_SECTIONS.values():
            raise ValueError('Некорректный раздел каталога.')
        set_remote_qualities(payload)
        mode.set(selected_mode)
        hardware_value.set(next(k for k,v in hardware_labels.items() if v == policy))
        load_value.set(next(k for k,v in load_labels.items() if v == load))
        catalog_section.set(next(k for k,v in LAUNCHER_SECTIONS.items() if v == section))
        apply_hardware_settings()
        if not save_settings():
            raise ValueError('Не удалось сохранить настройки подготовки.')

    def handle_remote(command):
        nonlocal quality_selection_dirty
        if not command:return
        action,payload=command.get('action'),command.get('payload') or {}
        try:
            if command.get('instance_id', instance_id) != instance_id:
                raise ValueError('Команда предназначена другому компьютеру.')
            if action and action.startswith('torrent_'):
                if action in ('torrent_add', 'torrent_start', 'torrent_files') and busy:
                    raise ValueError('Лаунчер занят другой операцией. Дождитесь завершения или приостановите её.')
                if 'command' not in torrent_remote:
                    raise ValueError('Торрент-очередь ещё не готова.')
                message = torrent_remote['command'](action, payload)
                finish_remote(command, True, message)
                return
            if action and action.startswith('project_'):
                try:
                    from . import remote_projects
                except ImportError:
                    import remote_projects
                if action in ('project_add', 'project_cards', 'project_send', 'project_resume') and busy:
                    raise ValueError('Дождитесь завершения текущей операции лаунчера.')
                if action in ('project_add', 'project_cards', 'project_send'):
                    if action == 'project_add':
                        folder = remote_render.existing_folder(payload.get('folder', ''), remote_folders, remote_browse_enabled.get())
                        # Active projects retain their frozen encoding settings.
                        if not projects.busy:
                            remote_settings(payload)
                        elif (payload.get('hardware', hardware_labels[hardware_value.get()]) != hardware_labels[hardware_value.get()]
                              or payload.get('load', load_labels[load_value.get()]) != load_labels[load_value.get()]):
                            raise ValueError('Во время обработки используйте текущие настройки оборудования и нагрузки.')
                        def work():
                            project = launcher_projects.build_project(folder, payload['qualities'],
                                payload.get('mode', 'single'), payload.get('catalog_section', 'movies'),
                                stop, series=payload.get('series'))
                            if stop.is_set():
                                raise InterruptedError('Добавление проекта остановлено.')
                            projects.add(project)
                    else:
                        if action == 'project_send' and payload.get('confirmed') is not True:
                            raise ValueError('Подтвердите готовность карточек перед отправкой.')
                        client = make_client(values['url'].get().strip(), values['token'].get().strip())
                        def work():
                            remote_projects.transfer(projects, payload['project_id'], client, stop, report,
                                                     action == 'project_send')
                    stop.clear()
                    def run_project_command():
                        try:
                            work()
                            app.after(0, finish_remote, command, True, 'Операция проекта выполнена.')
                        except Exception as error:
                            report(str(error))
                            app.after(0, finish_remote, command, False, str(error))
                    if not background(run_project_command, allow_projects=True):
                        raise ValueError('Операция проекта не запущена.')
                    return
                remote_projects.control(projects, action, payload, stop)
                finish_remote(command, True, 'Команда очереди проектов выполнена.')
                return
            if action=='stop':stop_all();message='Остановка всех проектов и отправки запрошена.'
            elif action=='browse_folder':
                enabled = remote_browse_enabled.get()
                def browse():
                    try:
                        data = remote_render.browse_folder(payload.get('folder', ''), payload.get('offset', 0), enabled=enabled)
                        app.after(0, finish_remote, command, True, 'Список папок получен.', data)
                    except Exception as error:
                        app.after(0, finish_remote, command, False, str(error))
                threading.Thread(target=browse, daemon=True).start()
                return
            elif action=='refresh' and (busy or projects.busy):message='Задачи выполняются. Прогресс обновляется автоматически.'
            elif busy or projects.busy:raise ValueError('Лаунчер занят. Повторите команду после завершения операции.')
            elif action=='switch_folder':
                choose_remote_folder(payload.get('folder',''))
                save_settings();message='Папка выбрана. Подготовка ещё не запущена.'
            elif action=='refresh':inspect_batch();message='Очередь обновлена.'
            elif action=='prepare':
                remote_settings(payload)
                if not prepare(remote=True):raise ValueError('Подготовка не запущена.')
                message='Подготовка MP4 запущена. Ход и итог смотрите в состоянии задачи.'
            elif action=='send':
                remote_settings(payload)
                if prepared and prepared.get('source_folder') != str(Path(values['folder'].get()).resolve()):
                    raise ValueError('Подготовленный проект относится к другой папке.')
                if not start(remote=True):raise ValueError('Отправка не запущена.')
                message='Отправка через Telegram Desktop запущена.'
            else:raise ValueError('Неизвестная команда.')
            finish_remote(command,True,message)
        except Exception as error:
            if action in ('prepare', 'send') and not busy:
                render_job.start(action, payload.get('folder', values['folder'].get()), payload.get('mode', mode.get()))
                render_job.finish(error)
            finish_remote(command,False,str(error))

    def remote_poll():
        nonlocal remote_request_running
        try:
            from . import remote_projects
        except ImportError:
            import remote_projects
        if remote_request_running:
            app.after(5000,remote_poll);return
        url,token=values['url'].get().strip(),values['token'].get().strip()
        if not url or not token:
            connection_label.configure(text='● Введите адрес и код /desktop в Настройках')
            app.after(5000,remote_poll);return
        remote_request_running=True
        snapshot={'instance_id':instance_id,'name':computer_name.get().strip()[:80] or device_info['name'],
                  'protocol':3,'folders':list(dict.fromkeys(remote_folders)),
                  'status':{'busy':busy or projects.busy,'folder':values['folder'].get().strip(),'message':status.get(),
                            'qualities':[name for name in QUALITY_NAMES if quality_vars[name].get()],
                            'catalog_section':selected_catalog_section(), 'mode':mode.get(),
                            'hardware':hardware_labels[hardware_value.get()], 'load':load_labels[load_value.get()],
                            'capabilities':{'folder_browser':1, 'remote_render':1, 'project_control':1, 'torrent_control':1},
                            'remote_browse_enabled':remote_browse_enabled.get(), 'job':render_job.snapshot(),
                            'projects': remote_projects.snapshot(projects),
                            'project_parallelism': projects.parallelism, 'operation_busy': busy,
                            'torrents': torrent_remote['snapshot']() if 'snapshot' in torrent_remote else {},
                            'prepared_count': (1 if prepared else sum(i.get('status') in ('prepared','complete','published')
                                for i in queue_update['items']))}}
        def poll():
            nonlocal remote_request_running
            try:
                with remote_results_lock:
                    pending = list(remote_results.items())
                for command_id, (client, answer) in pending:
                    try:
                        client.request(f'control/commands/{command_id}/complete', answer)
                    except ValueError as error:
                        if 'уже завершена' not in str(error):
                            raise
                    with remote_results_lock:
                        remote_results.pop(command_id, None)
                result=make_client(url,token).request('control/heartbeat',snapshot)
                app.after(0,handle_remote,result.get('command'))
                app.after(0,connection_label.configure,{'text':'● Панель подключена'})
                app.after(0,connection_reason.set,'Связь с панелью установлена.')
            except Exception as error:
                label, reason = panel_connection_error(error)
                app.after(0,connection_label.configure,{'text':label})
                app.after(0,connection_reason.set,reason)
            finally:remote_request_running=False
        threading.Thread(target=poll,daemon=True).start();app.after(5000,remote_poll)

    try:
        from .launcher_projects_ui import mount as mount_projects
    except ImportError:
        from launcher_projects_ui import mount as mount_projects

    add_project = mount_projects(pages['projects'], app, projects, background, report, stop,
        lambda: (values['folder'].get(), selected_global_qualities(), mode.get(), selected_catalog_section()),
        lambda: make_client(values['url'].get().strip(), values['token'].get().strip()),
        show_page, lambda: busy)

    def update_project_controls():
        hardware_busy(busy or projects.busy)
        app.after(500, update_project_controls)
    app.after(100, update_project_controls)

    def build_torrent_preparer(qualities=None):
        requested = list(qualities) if qualities else selected_global_qualities()
        panel_url, panel_token = values['url'].get().strip(), values['token'].get().strip()
        def prepare_job(job, folder):
            if job['mode'] == 'project':
                project_id = launcher_projects.handoff_download(projects, job, folder, requested, stop)
                app.after(0, show_page, 'projects')
                report('Скачано. Исходники переданы в очередь проектов; подготовка выполняется обычным способом.')
                return {'queued_project_id': project_id}
            client = make_client(panel_url, panel_token)
            job_report = FolderProgress(report, show_batch)
            job_report.catalog_section = job['catalog_section']
            if job['mode'] == 'batch':
                for source in folder.iterdir():
                    if source.is_file() and source.suffix.lower() in EXTENSIONS:
                        media = quality_choices(source)
                        selected = resolve_quality_selection(media, requested)
                        if not selected:
                            raise ValueError('Выбранное качество выше исходника: ' + source.name)
                        save_media_choices(source, selected, media['mode'])
                run_batch(folder, client, stop, job_report, show_batch,
                          lambda _: None, prepare_only=True)
                items = batch_plan(folder)
                failures = [item.get('error') or item['status'] for item in items
                            if item['status'] not in ('prepared', 'complete', 'published')]
                if failures:
                    raise ValueError('Подготовка не завершена: ' + '; '.join(failures[:3]))
                return None
            with keep_awake():
                try:
                    manifest = prepare_media(folder, job_report, stop, qualities=requested)
                    manifest['title'] = movie_title(job['title'])
                    manifest['catalog_section'] = job['catalog_section']
                    if job['mode'] == 'season':
                        manifest.update(content_type='series', movie_number=job['movie_number'],
                                        season_number=job['season_number'])
                    job_report.phase('registering')
                    result = RetryingClient(client, stop, job_report).request('import', manifest)
                    if result['draft']['status'] in ('rejected', 'published'):
                        raise ValueError('Черновик закрыт. Проверьте его в WEB-панели.')
                    job_report.phase('prepared')
                    return manifest
                except Exception as error:
                    job_report.phase('paused' if isinstance(error, InterruptedError) else 'error', str(error))
                    raise
        return prepare_job

    def open_torrent_job(job):
        nonlocal quality_selection_dirty
        if job.get('project_id'):
            show_page('projects')
            return
        if busy:
            report('Лаунчер занят. Дождитесь завершения операции.')
            return
        values['folder'].set(job['import_folder'])
        mode.set('batch' if job['mode'] == 'batch' else 'single')
        quality_selection_dirty = False
        catalog_section.set(next((name for name, key in LAUNCHER_SECTIONS.items()
                                  if key == job['catalog_section']), 'Фильмы'))
        if job['mode'] == 'batch':
            show_page('queue')
            inspect_batch()
        else:
            client = make_client(values['url'].get().strip(), values['token'].get().strip())
            manifest = job['manifest']
            def restore():
                data = client.request('import', manifest)
                prepared.clear()
                prepared.update(manifest=manifest, client=client, data=data, source_folder=str(Path(job['import_folder']).resolve()))
                app.after(0, show_page, 'telegram')
                report('Подготовлено. Проверьте карточку в WEB, выключите тихий режим и запустите отправку.')
            background(restore)

    try:
        from tools.torrent_panel import mount as mount_torrents
    except ModuleNotFoundError:
        from torrent_panel import mount as mount_torrents
    shutdown_torrents = mount_torrents(pages['torrents'], app, background, report, stop,
        build_torrent_preparer, open_torrent_job, quiet_preparation.get,
        dict(LAUNCHER_SECTIONS, **{'Сериалы': 'series', 'Мультсериалы': 'animated_series'}),
        bind_entry_shortcuts, ingest_background=lambda work: background(work, allow_projects=True), remote=torrent_remote)

    for key, icon in [('overview', '⌂  Обзор'), ('source', '↑  Источник'), ('torrents', '⇣  Торренты'),
                      ('processing', '◇  Качество и звук'), ('queue', '☷  Очередь файлов'), ('projects', '▤  Очередь проектов'),
                      ('telegram', '↗  Telegram'), ('settings', '⚙  Настройки')]:
        button = ttk.Button(sidebar, text=icon, style='Nav.TButton', command=lambda name=key: show_page(name))
        button.pack(fill='x', padx=12, pady=2); nav_buttons[key] = button
    ttk.Label(sidebar, text='Подготовка и отправка\nбез публикации', style='Muted.TLabel').pack(
        side='bottom', anchor='w', padx=22, pady=20)
    show_page('overview')
    def cancel_callbacks(event):
        if event.widget is app:
            for callback in app.tk.splitlist(app.tk.call('after', 'info')):
                app.after_cancel(callback)
    app.bind('<Destroy>', cancel_callbacks, add='+')
    def close():
        stop.set()
        projects.close()
        shutdown_torrents()
        status.set('Закрываю лаунчер: останавливаю обработку и сохраняю проекты…')
        def finish_close():
            if busy or not projects.wait_closed(0):
                app.after(100, finish_close)
                return
            app.destroy()
            if project_temporary:
                project_temporary.cleanup()
        finish_close()
    if smoke_test:
        app.after(100, close)
    else:
        app.after(1000, remote_poll)
    app.protocol('WM_DELETE_WINDOW', close)
    app.mainloop()
    if smoke_test and callback_errors:
        raise RuntimeError('; '.join(callback_errors))


def main(*, smoke_test=False):
    profile = state_path('launcher-settings.json', __file__).parent
    with queue_lock(profile):
        return _main(smoke_test=smoke_test)


if __name__ == '__main__':
    main()
