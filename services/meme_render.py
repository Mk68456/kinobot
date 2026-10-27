"""Local MemeFlow compositor. No transcription, bot connection or cloud services."""
from __future__ import annotations

import json
import re
import subprocess
import tempfile
import time
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path

from PIL import Image, ImageDraw

from services.local_clips import check_cancel, ffmpeg_path, run_media


@dataclass
class MediaInfo:
    duration: float
    width: int
    height: int
    has_audio: bool


@dataclass
class MemeSettings:
    shape: str = "circle"
    size: float = .76
    center_y: float = .48
    crop_x: float = .5
    crop_y: float = .5
    resolution: int = 720
    banner_enabled: bool = False
    banner_chromakey: bool = False
    smooth_intro: bool = False

    def validate(self):
        if self.shape not in ("circle", "rounded"):
            raise ValueError("Выберите круг или скруглённый прямоугольник")
        if not .4 <= self.size <= .94 or not .3 <= self.center_y <= .73:
            raise ValueError("Размер или положение окна выходит за допустимые границы")
        if not 0 <= self.crop_x <= 1 or not 0 <= self.crop_y <= 1:
            raise ValueError("Проверьте положение изображения внутри окна")
        if self.resolution not in (720, 1080):
            raise ValueError("Разрешение должно быть 720 или 1080")


def inspect_media(path, cancel):
    path = Path(path)
    if not path.is_file():
        raise ValueError("Файл не найден: " + str(path))
    log = run_media(["-i", str(path.resolve()), "-t", "0", "-map", "0:v:0", "-f", "null", "-"], cancel)
    header = log.split("Stream mapping:")[0]
    duration = re.search(r"Duration: (\d+):(\d+):(\d+(?:\.\d+)?)", header)
    dimensions = re.search(r"Video:[^\n]*?\b(\d{2,5})x(\d{2,5})\b", header)
    if not duration or not dimensions:
        raise ValueError("Нужен обычный видеофайл с определённой длительностью: " + path.name)
    h, m, s = map(float, duration.groups())
    seconds = h*3600 + m*60 + s
    if seconds <= 0:
        raise ValueError("Видео пустое: " + path.name)
    return MediaInfo(seconds, int(dimensions[1]), int(dimensions[2]), "Audio:" in header)


def geometry(settings, width):
    settings.validate()
    height = width*16//9
    box_w = int(width*settings.size)//2*2
    box_h = box_w if settings.shape == "circle" else int(box_w*.65)//2*2
    x = (width-box_w)//2
    y = min(height-box_h, max(0, round(height*settings.center_y-box_h/2)))
    return width, height, box_w, box_h, x, y


def mask_image(width, height, shape, destination):
    # Supersampling retains smooth edges in the exported video.
    scale = 3
    mask = Image.new("L", (width*scale, height*scale), 0)
    drawing = ImageDraw.Draw(mask)
    bounds = (0, 0, width*scale-1, height*scale-1)
    if shape == "circle":
        drawing.ellipse(bounds, fill=255)
    else:
        drawing.rounded_rectangle(bounds, radius=round(min(width, height)*.10*scale), fill=255)
    mask.resize((width, height), Image.Resampling.LANCZOS).save(destination)


def composition(settings, width, has_banner):
    w, h, bw, bh, x, y = geometry(settings, width)
    delay = 1 if settings.smooth_intro else 0
    hold = f"tpad=start_mode=clone:start_duration={delay}," if delay else ""
    fade = "fade=t=in:st=1:d=0.6:alpha=1," if delay else ""
    filters = (
        f"[0:v]setpts=PTS-STARTPTS,scale={w}:{h}:force_original_aspect_ratio=increase,"
        f"crop={w}:{h},setsar=1,fps=30[bg];"
        f"[1:v]setpts=PTS-STARTPTS,{hold}scale={bw}:{bh}:force_original_aspect_ratio=increase,"
        f"crop={bw}:{bh}:(iw-ow)*{settings.crop_x}:(ih-oh)*{settings.crop_y},"
        "setsar=1,format=rgb24[meme];"
        f"[2:v]format=gray[mask];[meme][mask]alphamerge,format=rgba,{fade}null[cut];"
        f"[bg][cut]overlay={x}:{y}:shortest=1:format=auto[placed]"
    )
    if has_banner:
        ad_w = int(w*.84)//2*2
        ad_h = int(ad_w/3)//2*2
        pad = "0x00FF00" if settings.banner_chromakey else "black@0"
        key = "chromakey=0x00FF00:0.24:0.08," if settings.banner_chromakey else ""
        filters += (
            f";[3:v]setpts=PTS-STARTPTS,scale={ad_w}:{ad_h}:force_original_aspect_ratio=decrease,"
            f"format=rgba,pad={ad_w}:{ad_h}:(ow-iw)/2:(oh-ih)/2:color={pad},{key}format=rgba,{fade}null[ad];"
            f"[placed][ad]overlay=(W-w)/2:{round(h*.075)}:shortest=1:format=auto[out]"
        )
    else:
        filters += ";[placed]null[out]"
    return filters


def input_args(background, meme, mask, banner, at=0):
    args = ["-stream_loop", "-1"]
    if at:
        args += ["-ss", str(at)]
    args += ["-i", str(Path(background).resolve())]
    if at:
        args += ["-ss", str(at)]
    args += ["-i", str(Path(meme).resolve()), "-loop", "1", "-i", str(mask)]
    if banner:
        if Path(banner).suffix.lower() in (".png", ".jpg", ".jpeg", ".webp"):
            args += ["-loop", "1"]
        else:
            args += ["-stream_loop", "-1"]
            if at:
                args += ["-ss", str(at)]
        args += ["-i", str(Path(banner).resolve())]
    return args


def validate_inputs(meme, background, banner, settings):
    settings.validate()
    for label, path in (("Мем", meme), ("Фон", background)):
        if not Path(path).is_file():
            raise ValueError(label + ": выберите существующий видеофайл")
    if settings.banner_enabled and (not banner or not Path(banner).is_file()):
        raise ValueError("Выберите баннер или отключите его")


def preview_frame(meme, background, banner, settings, destination, cancel, at=0, width=360):
    validate_inputs(meme, background, banner, settings)
    banner = banner if settings.banner_enabled else None
    with tempfile.TemporaryDirectory(prefix="memeflow-preview-") as folder:
        _, _, bw, bh, _, _ = geometry(settings, width)
        mask = Path(folder) / "mask.png"
        mask_image(bw, bh, settings.shape, mask)
        # Seek after composition so fades and the delayed meme match the export timeline.
        run_media(["-y", *input_args(background, meme, mask, banner),
                   "-filter_complex_threads", "2", "-filter_complex", composition(settings, width, bool(banner)),
                   "-map", "[out]", "-ss", str(at), "-an", "-frames:v", "1", str(Path(destination).resolve())], cancel)
    return Path(destination)


def _render_with_progress(args, duration, cancel, progress):
    with tempfile.TemporaryDirectory(prefix="memeflow-progress-") as folder:
        progress_file = Path(folder) / "progress.txt"
        with tempfile.TemporaryFile() as log:
            process = subprocess.Popen(
                [ffmpeg_path(), "-hide_banner", "-nostdin", "-y", "-progress", str(progress_file), *args],
                stdout=log, stderr=log,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            previous = -1
            try:
                while process.poll() is None:
                    check_cancel(cancel)
                    if progress_file.exists():
                        try:
                            entries = re.findall(r"out_time_us=(\d+)", progress_file.read_text(errors="replace"))
                            percent = min(99, round(int(entries[-1])/1_000_000/duration*100)) if entries else 0
                            if percent != previous:
                                progress(percent)
                                previous = percent
                        except OSError:
                            pass
                    time.sleep(.2)
                check_cancel(cancel)
                if process.returncode:
                    log.seek(0)
                    raise RuntimeError(log.read().decode("utf-8", errors="replace")[-2500:])
                progress(100)
            finally:
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait()


def render_meme(meme, background, banner, settings, output_folder, cancel, progress=lambda p: None):
    validate_inputs(meme, background, banner, settings)
    check_cancel(cancel)
    info = inspect_media(meme, cancel)
    duration = info.duration + (1 if settings.smooth_intro else 0)
    inspect_media(background, cancel)
    banner = banner if settings.banner_enabled else None
    output_folder = Path(output_folder).resolve()
    output_folder.mkdir(parents=True, exist_ok=True)
    safe_name = re.sub(r"[^\w .-]", "_", Path(meme).stem)[:80].strip() or "meme"
    target = output_folder / f"{safe_name}_MemeFlow_{uuid.uuid4().hex[:8]}.mp4"
    partial = target.with_suffix(".partial.mp4")
    try:
        with tempfile.TemporaryDirectory(prefix="memeflow-mask-") as folder:
            _, _, bw, bh, _, _ = geometry(settings, settings.resolution)
            mask = Path(folder) / "mask.png"
            mask_image(bw, bh, settings.shape, mask)
            args = [*input_args(background, meme, mask, banner),
                    "-filter_complex_threads", "2", "-filter_complex", composition(settings, settings.resolution, bool(banner)),
                    "-map", "[out]", "-map", "1:a:0?", "-t", str(duration),
                    "-c:v", "libx264", "-preset", "veryfast", "-crf", "21", "-threads", "4",
                    "-pix_fmt", "yuv420p", "-r", "30", "-c:a", "aac", "-b:a", "160k",
                    "-movflags", "+faststart", str(partial)]
            if settings.smooth_intro and info.has_audio:
                args[-1:-1] = ["-af", "adelay=1000:all=1"]
            _render_with_progress(args, duration, cancel, progress)
        check_cancel(cancel)
        partial.replace(target)
        target.with_suffix(".json").write_text(json.dumps({
            "meme": str(Path(meme).resolve()), "background": str(Path(background).resolve()),
            "banner": str(Path(banner).resolve()) if banner else None,
            "settings": asdict(settings), "duration": duration,
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        return target
    finally:
        partial.unlink(missing_ok=True)
