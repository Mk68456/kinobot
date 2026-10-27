"""Offline clip preparation. Scores are editorial heuristics, never reach predictions."""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path


class Cancelled(Exception):
    pass


@dataclass
class Cue:
    start: float
    end: float
    text: str
    words: list[dict] = field(default_factory=list)


@dataclass
class Caption:
    start: float
    end: float
    text: str
    accent: str = ""
    color: str = "green"


@dataclass
class Candidate:
    start: float
    end: float
    score: int
    reason: str
    text: str


def check_cancel(cancel):
    if cancel.is_set():
        raise Cancelled("Операция отменена")


def ffmpeg_path():
    binary = shutil.which("ffmpeg")
    if binary:
        return binary
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except ImportError as exc:
        raise RuntimeError("Установите зависимости Clips по инструкции docs/LOCAL_CLIPS.md") from exc


def run_media(args, cancel, cwd=None):
    """Drain stderr to disk, avoiding pipe deadlocks during long renders."""
    import tempfile
    check_cancel(cancel)
    with tempfile.TemporaryFile() as log:
        process = subprocess.Popen(
            [ffmpeg_path(), "-hide_banner", "-nostdin", *args],
            stdout=log, stderr=log, cwd=cwd,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        try:
            while process.poll() is None:
                check_cancel(cancel)
                time.sleep(0.15)
            log.seek(0)
            output = log.read().decode("utf-8", errors="replace")
            if process.returncode:
                raise RuntimeError(output[-3500:])
            return output
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()


def parse_srt(text):
    cues = []
    pattern = r"(\d{1,3}):(\d{2}):(\d{2})[,.](\d{3})"
    for block in re.split(r"\n\s*\n", text.replace("\r", "").lstrip("\ufeff").strip()):
        lines = block.splitlines()
        for index, line in enumerate(lines):
            if "-->" not in line:
                continue
            stamps = re.findall(pattern, line)
            if len(stamps) != 2:
                raise ValueError("Некорректный таймкод SRT: " + line)
            start, end = [int(h)*3600 + int(m)*60 + int(s) + int(ms)/1000
                          for h, m, s, ms in stamps]
            body = re.sub(r"<[^>]*>", "", " ".join(lines[index + 1:])).strip()
            if end <= start:
                raise ValueError("Конец реплики должен быть позже начала")
            if body:
                cues.append(Cue(start, end, body))
            break
    if not cues:
        raise ValueError("В SRT не найдено реплик. Нужен файл UTF-8 с временными метками.")
    return sorted(cues, key=lambda cue: cue.start)


def media_duration(source, cancel):
    output = run_media(["-i", str(source), "-t", "0", "-map", "0:v:0", "-f", "null", "-"], cancel)
    match = re.search(r"Duration: (\d+):(\d+):(\d+(?:\.\d+)?)", output)
    if not match:
        raise ValueError("Не удалось определить длительность видео")
    h, m, s = map(float, match.groups())
    return h*3600 + m*60 + s


def refresh_project_srt(project, path):
    """Return an updated copy only when the chosen SRT contents change."""
    import copy
    path = Path(path).resolve()
    data = path.read_bytes()
    fingerprint = hashlib.sha256(data).hexdigest()
    identity = {"path": str(path), "sha256": fingerprint}
    if project.get("subtitle_source") == identity:
        return project
    cues = parse_srt(data.decode("utf-8-sig"))
    updated = copy.deepcopy(project)
    updated["cues"] = [asdict(c) for c in cues]
    updated["caption_edits"] = {}
    updated["subtitle_source"] = identity
    for candidate in updated["candidates"]:
        candidate["text"] = " ".join(c.text for c in cues
                                     if c.end > candidate["start"] and c.start < candidate["end"])
    return updated


def audio_tracks(source):
    import av
    with av.open(str(source)) as container:
        return [{"index": i, "label": f"{i}: {stream.metadata.get('language', '?')} — "
                 + stream.metadata.get('title', f'Дорожка {i + 1}')}
                for i, stream in enumerate(container.streams.audio)]


def transcribe(source, model_dir, cache_dir, cancel, progress, *, language="ru",
               audio_index=0, start=0, end=None, force=False):
    """A model directory is mandatory: inference never silently downloads a model."""
    if not model_dir or not (Path(model_dir) / "model.bin").is_file():
        raise ValueError("Выберите SRT или папку скачанной модели faster-whisper (с model.bin).")
    from faster_whisper import WhisperModel
    import tempfile
    if audio_index < 0 or start < 0 or (end is not None and end <= start):
        raise ValueError("Проверьте аудиодорожку и границы распознавания")
    stat = source.stat()
    model_stat = (Path(model_dir) / "model.bin").stat()
    identity = f"{source.resolve()}:{stat.st_size}:{stat.st_mtime_ns}:{Path(model_dir).resolve()}"
    identity += f":{model_stat.st_size}:{model_stat.st_mtime_ns}:{language}:{audio_index}:{start}:{end}:speech-v2"
    key = hashlib.sha256(identity.encode()).hexdigest()
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_file = cache_dir / (key + ".json")
    if cache_file.exists() and not force:
        progress("Используется сохранённая расшифровка")
        return [Cue(**item) for item in json.loads(cache_file.read_text(encoding="utf-8"))]
    cues = []
    # Explicit stream selection avoids silently transcribing the wrong dub.
    # Leave a small context margin around a selected scene, then clip timestamps.
    offset = max(0, start - 1)
    with tempfile.TemporaryDirectory(prefix="speech-", dir=cache_dir) as temporary_audio:
        wav = Path(temporary_audio) / "speech.wav"
        args = ["-y", "-ss", str(offset), "-i", str(source.resolve())]
        if end is not None:
            args += ["-t", str(end + 1 - offset)]
        progress(f"Подготовка звука: дорожка {audio_index + 1}…")
        run_media([*args, "-map", f"0:a:{audio_index}", "-vn", "-ac", "1", "-ar", "16000",
                   "-c:a", "pcm_s16le", str(wav)], cancel)
        check_cancel(cancel)
        progress(f"Загрузка {Path(model_dir).name}. Распознавание на процессоре…")
        model = WhisperModel(str(model_dir), device="cpu", compute_type="int8",
                             cpu_threads=max(1, min(6, (os.cpu_count() or 2) - 2)),
                             local_files_only=True)
        check_cancel(cancel)
        segments, info = model.transcribe(str(wav), language=None if language == "auto" else language,
                                          task="transcribe", beam_size=5, temperature=0,
                                          condition_on_previous_text=False, vad_filter=True,
                                          vad_parameters={"min_silence_duration_ms": 500,
                                                          "speech_pad_ms": 400},
                                          word_timestamps=True, hallucination_silence_threshold=2)
        for segment in segments:
            check_cancel(cancel)
            left, right = max(start, segment.start + offset), segment.end + offset
            if end is not None:
                right = min(end, right)
            if segment.text.strip() and right > left:
                words = [{"start": max(left, word.start + offset),
                          "end": min(right, word.end + offset), "word": word.word.strip()}
                         for word in (segment.words or []) if word.word.strip()
                         and word.end + offset > left and word.start + offset < right]
                text = " ".join(w["word"] for w in words) if words else segment.text.strip()
                cues.append(Cue(left, right, text, words))
            progress(f"Распознано {segment.end / 60:.1f} из {info.duration / 60:.1f} минут")
    check_cancel(cancel)
    if not cues:
        raise ValueError("Речь не найдена. Попробуйте SRT или другую модель.")
    temporary = cache_file.with_suffix(".tmp")
    temporary.write_text(json.dumps([asdict(cue) for cue in cues], ensure_ascii=False), encoding="utf-8")
    temporary.replace(cache_file)
    return cues


def select_candidates(cues, duration, minimum=25, maximum=55, limit=15):
    if not 5 <= minimum <= maximum <= 180:
        raise ValueError("Длительность: 5 ≤ минимум ≤ максимум ≤ 180 секунд")
    candidates = []
    for index, first in enumerate(cues):
        if first.start >= duration:
            break
        group = []
        for cue in cues[index:]:
            if cue.end - first.start > maximum - 0.6 or cue.end > duration:
                break
            if group and cue.start - group[-1].end > 5:
                break
            group.append(cue)
        if not group or group[-1].end - first.start < minimum:
            continue
        text = " ".join(c.text for c in group)
        seconds = group[-1].end - first.start
        speech = sum(c.end - c.start for c in group)
        score = 35
        reasons = []
        if "?" in " ".join(c.text for c in group[:2]):
            score += 15
            reasons.append("вопрос в начале")
        if re.search(r"\b(почему|зачем|тайна|секрет|правда|невозможно|опасно|убь|нельзя|стой|why|secret|truth|wait)\w*", text, re.I):
            score += 15
            reasons.append("слова, связанные с интригой или конфликтом")
        if 1.0 <= len(text.split()) / seconds <= 3.5:
            score += 10
            reasons.append("умеренный темп речи")
        if min(speech / seconds, 1) > 0.65:
            score += 10
            reasons.append("мало пауз между репликами")
        if re.search(r"[.!?…][\"»']?$", group[-1].text):
            score += 5
            reasons.append("завершённая реплика в конце")
        if first.start > duration * 0.8:
            score -= 20
            reasons.append("ближе к финалу: проверьте спойлеры")
        candidates.append(Candidate(max(0, first.start-0.2), min(duration, group[-1].end+0.3),
                                    score, "; ".join(reasons) or "связный блок реплик", text))
    chosen = []
    for candidate in sorted(candidates, key=lambda c: (-c.score, c.start)):
        if all(candidate.end <= other.start or candidate.start >= other.end for other in chosen):
            chosen.append(candidate)
        if len(chosen) >= limit:
            break
    return chosen


def ass_time(seconds):
    ticks = round(max(0, seconds)*100)
    return f"{ticks//360000}:{ticks//6000%60:02}:{ticks//100%60:02}.{ticks%100:02}"


def ass_text(text):
    # Remove ASS override syntax supplied by subtitles or user-entered titles.
    return text.replace("\\", "＼").replace("{", "(").replace("}", ")").replace("\n", r"\N")


def caption_words(cue):
    """Use measured word times, or estimate positions within an SRT line."""
    if cue.words:
        return [(float(w["start"]), float(w["end"]), w["word"])
                for w in cue.words if w["word"].strip()]
    tokens = cue.text.split()
    if not tokens:
        return []
    lengths = [max(1, len(re.sub(r"\W", "", token))) for token in tokens]
    total = sum(lengths)
    elapsed = cue.start
    result = []
    for token, length in zip(tokens, lengths):
        next_time = min(cue.end, elapsed + (cue.end-cue.start)*length/total)
        result.append((elapsed, next_time, token))
        elapsed = next_time
    return result


def make_captions(cues, start, end):
    """Break dialogue into short timed phrases; SRT word positions are estimates."""
    words = [item for cue in cues for item in caption_words(cue)
             if item[1] > start and item[0] < end]
    words.sort(key=lambda item: item[0])
    groups, group = [], []
    for item in words:
        if group and (len(group) >= 5 or len(" ".join(w[2] for w in group+[item])) > 28
                      or item[0] - group[-1][1] > .7):
            groups.append(group)
            group = []
        group.append(item)
        if re.search(r"[.!?…]$", item[2]) and len(group) >= 2:
            groups.append(group)
            group = []
    if group:
        groups.append(group)
    captions = []
    for group in groups:
        text = " ".join(w[2] for w in group).strip()
        if not text:
            continue
        begin = max(start, group[0][0])
        finish = min(end, group[-1][1])
        if finish <= begin:
            continue
        notable = [re.sub(r"^[^\w]+|[^\w]+$", "", w[2]) for w in group]
        accent = next((w for w in reversed(notable) if len(w) >= 4), "")
        captions.append(Caption(begin, finish, text, accent, "green"))
    return captions


def accented_ass_text(caption):
    plain = caption.text.upper()
    accent = caption.accent.upper().strip()
    if not accent:
        return ass_text(plain)
    match = re.search(r"(?<!\w)" + re.escape(accent) + r"(?!\w)", plain)
    if not match:
        return ass_text(plain)
    tint = r"{\c&H00FFFF&}" if caption.color == "yellow" else r"{\c&H55F870&}"
    return (ass_text(plain[:match.start()]) + tint + ass_text(plain[match.start():match.end()])
            + r"{\c&HFFFFFF&}" + ass_text(plain[match.end():]))


def subtitles_ass(cues, start, end, title, cta, style="classic", captions=None):
    header = """[Script Info]
ScriptType: v4.00+
PlayResX: 720
PlayResY: 1280
WrapStyle: 0
[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Sub,Arial,34,&H00FFFFFF,&H00FFFFFF,&H00101010,&H90000000,0,0,0,0,100,100,0,0,1,2,1,2,55,80,260,1
Style: Brand,Arial,32,&H00FFFFFF,&H00FFFFFF,&H00101010,&H90000000,-1,0,0,0,100,100,0,0,1,2,1,8,50,65,90,1
Style: CTA,Arial,30,&H00FFFFFF,&H00FFFFFF,&H00101010,&H90000000,-1,0,0,0,100,100,0,0,1,2,1,2,55,80,150,1
Style: SubRef,Arial,45,&H00FFFFFF,&H00FFFFFF,&H00000000,&H90000000,-1,0,0,0,100,100,0,0,1,3,0,2,70,70,275,1
[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    rows = []
    if title:
        rows.append(f"Dialogue: 1,0:00:00.00,{ass_time(end-start)},Brand,,0,0,0,,{ass_text(title)}")
    if cta:
        rows.append(f"Dialogue: 1,{ass_time(max(0, end-start-7))},{ass_time(end-start)},CTA,,0,0,0,,{ass_text(cta)}")
    if style == "reference":
        for caption in captions if captions is not None else make_captions(cues, start, end):
            left, right = max(caption.start, start), min(caption.end, end)
            if right > left and caption.text.strip():
                rows.append(f"Dialogue: 0,{ass_time(left-start)},{ass_time(right-start)},SubRef,,0,0,0,,"
                            + accented_ass_text(caption))
    else:
        for cue in captions if captions is not None else cues:
            left, right = max(cue.start, start), min(cue.end, end)
            if right > left:
                rows.append(f"Dialogue: 0,{ass_time(left-start)},{ass_time(right-start)},Sub,,0,0,0,,{ass_text(cue.text)}")
    return header + "\n".join(rows) + "\n"


def export_clip(source, cues, candidate, output_dir, title, cta, cancel, crop=False, banner=None,
                style="classic", captions=None, audio_index=0):
    import uuid
    if not source.is_file() or not 0 <= candidate.start < candidate.end:
        raise ValueError("Проверьте исходный файл и границы фрагмента")
    output_dir.mkdir(parents=True, exist_ok=True)
    name = "clip_" + uuid.uuid4().hex[:12]
    ass = output_dir / (name + ".ass")
    partial = output_dir / (name + ".partial.mp4")
    target = output_dir / (name + ".mp4")
    banner = Path(banner) if banner else None
    if banner and not banner.is_file():
        raise ValueError("Файл рекламного баннера не найден")
    if style not in ("classic", "reference"):
        raise ValueError("Неизвестный стиль монтажа")
    ass.write_text(subtitles_ass(cues, candidate.start, candidate.end,
                                 "" if banner else title, "" if banner else cta,
                                 style, captions), encoding="utf-8-sig")
    if style == "reference":
        layout = ("[0:v]split=2[bg][fg];"
                  "[bg]scale=360:640:force_original_aspect_ratio=increase,crop=360:640,"
                  "boxblur=12:2,scale=720:1280,setsar=1[b];"
                  "[fg]scale=720:410:force_original_aspect_ratio=decrease,setsar=1[f];"
                  "[b][f]overlay=(W-w)/2:430+(410-h)/2[v]")
    elif crop:
        layout = "[0:v]scale=720:1280:force_original_aspect_ratio=increase,crop=720:1280,setsar=1[v]"
    else:
        layout = ("[0:v]split=2[bg][fg];"
                  "[bg]scale=360:640:force_original_aspect_ratio=increase,crop=360:640,"
                  "boxblur=12:2,scale=720:1280,setsar=1[b];"
                  "[fg]scale=720:900:force_original_aspect_ratio=decrease,setsar=1[f];"
                  "[b][f]overlay=(W-w)/2:(H-h)/2[v]")
    if banner:
        filters = (layout + ";[1:v]scale=720:240:force_original_aspect_ratio=decrease,"
                   "pad=720:240:(ow-iw)/2:(oh-ih)/2:color=0x00FF00,"
                   "chromakey=0x00FF00:0.24:0.08,format=rgba[ad];"
                   "[v][ad]overlay=0:140:shortest=1:format=auto[with_ad];"
                   f"[with_ad]ass={ass.name}[out]")
    else:
        filters = layout + f";[v]ass={ass.name}[out]"
    try:
        inputs = ["-ss", str(candidate.start), "-i", str(source.resolve())]
        if banner:
            inputs.extend(["-stream_loop", "-1", "-i", str(banner.resolve())])
        run_media(["-y", *inputs,
                   "-t", str(candidate.end-candidate.start), "-filter_complex_threads", "2",
                   "-filter_complex", filters, "-map", "[out]", "-map", f"0:a:{audio_index}?",
                   "-c:v", "libx264", "-preset", "veryfast", "-crf", "22", "-threads", "4",
                   "-pix_fmt", "yuv420p", "-r", "30", "-c:a", "aac", "-b:a", "160k",
                   "-movflags", "+faststart", str(partial.resolve())], cancel, cwd=output_dir)
        check_cancel(cancel)
        partial.replace(target)
        target.with_suffix(".json").write_text(json.dumps({
            "source": str(source.resolve()), "candidate": asdict(candidate),
            "title": title, "cta": cta, "crop": crop,
            "banner": str(banner.resolve()) if banner else None,
            "style": style, "captions": [asdict(c) for c in captions] if captions is not None else None,
            "audio_index": audio_index,
            "score_kind": "text_heuristic_not_view_prediction",
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        return target
    finally:
        partial.unlink(missing_ok=True)
