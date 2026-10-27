"""Create an original KinoTime banner inspired by the supplied layout.

The banner is a silent 3:1 video intended for the free upper strip of a 9:16 clip.
"""
from __future__ import annotations

import subprocess
import sys
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from services.local_clips import ffmpeg_path


OUTPUT = ROOT / "dist" / "KinoTime-banner-profile.mp4"
AVATAR = ROOT / "assets" / "kinotime-avatar.png"
W, H, FPS, SECONDS = 720, 240, 24, 10


def font(size, bold=False):
    name = "arialbd.ttf" if bold else "arial.ttf"
    return ImageFont.truetype(str(Path("C:/Windows/Fonts") / name), size)


def width(draw, value, face):
    return draw.textbbox((0, 0), value, font=face)[2]


def fit_font(draw, value, size=41, limit=475):
    while size > 18:
        face = font(size, True)
        if width(draw, value, face) <= limit:
            return face
        size -= 1
    return font(size, True)


@lru_cache(maxsize=1)
def background():
    image = Image.new("RGB", (W, H), "#00ff00")
    # Retain the rounded layout, now using KinoTime's sky-blue palette.
    mask = Image.new("L", (W, H))
    ImageDraw.Draw(mask).rounded_rectangle((13, 13, W-13, H-13), radius=56, fill=255)
    gradient = Image.new("RGB", (W, H))
    px = gradient.load()
    for x in range(W):
        r = x / (W-1)
        color = (int(111 - r*76), int(203 - r*92), int(248 - r*51))
        for y in range(H):
            px[x, y] = color
    image.paste(gradient, (0, 0), mask)
    return image


@lru_cache(maxsize=1)
def avatar():
    with Image.open(AVATAR) as source:
        picture = source.convert("RGBA")
        edge = min(picture.size)
        left = (picture.width - edge) // 2
        top = (picture.height - edge) // 2
        picture = picture.crop((left, top, left + edge, top + edge))
        picture = picture.resize((198, 198), Image.Resampling.LANCZOS)
    mask = Image.new("L", (198, 198))
    ImageDraw.Draw(mask).ellipse((1, 1, 197, 197), fill=255)
    picture.putalpha(mask)
    return picture


def frame(number):
    t = number / FPS
    image = background().copy()
    draw = ImageDraw.Draw(image)
    # Soft moving light in the background.
    glint = int((t/SECONDS) * (W+220))-110
    for offset in range(-30, 31, 3):
        x = glint + offset
        if 20 < x < W-20:
            draw.line((x, 24, x-65, H-24), fill=(125, 216, 254), width=2)
    draw.rounded_rectangle((29, 28, 168, 62), radius=17, fill="#0b3560")
    draw.text((47, 32), "KinoTime", font=font(20, True), fill="#ffffff")

    # Use the supplied avatar unchanged inside a clean circular crop.
    draw.ellipse((510, 15, 709, 214), fill="#e8f8ff", outline="#ffffff", width=4)
    image.paste(avatar(), (511, 16), avatar())
    draw = ImageDraw.Draw(image)

    # Two short phases allow the prompt to turn into a direct profile action.
    if t < 4.2:
        first, second = "ХОЧЕШЬ УЗНАТЬ ФИНАЛ?", "СМОТРИ ФИЛЬМ ЦЕЛИКОМ"
    else:
        first, second = "ФИЛЬМ ЦЕЛИКОМ В БОТЕ", "ССЫЛКА — В ПРОФИЛЕ  →"
    line1 = fit_font(draw, first, 35, 467)
    line2 = fit_font(draw, second, 34, 467)
    draw.text((34, 91), first, font=line1, fill="#082b51", stroke_width=0)
    draw.text((34, 143), second, font=line2, fill="#ffffff", stroke_width=2, stroke_fill="#174a77")
    draw.rounded_rectangle((34, 202, 484, 207), radius=2, fill="#e8faff")
    # Keep the key uniform outside the panel, including during the moving glint.
    silhouette = Image.new("L", (W, H))
    mask_draw = ImageDraw.Draw(silhouette)
    mask_draw.rounded_rectangle((13, 13, W-13, H-13), radius=56, fill=255)
    mask_draw.ellipse((510, 15, 709, 214), fill=255)
    image = Image.composite(image, Image.new("RGB", (W, H), "#00ff00"), silhouette)
    return image


def main():
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    process = subprocess.Popen([
        ffmpeg_path(), "-hide_banner", "-loglevel", "error", "-y",
        "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", str(FPS),
        "-i", "-", "-an", "-c:v", "libx264", "-preset", "medium", "-crf", "19",
        "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(OUTPUT),
    ], stdin=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        for n in range(FPS*SECONDS):
            process.stdin.write(frame(n).tobytes())
        process.stdin.close()
        error = process.stderr.read().decode("utf-8", errors="replace")
        if process.wait() != 0:
            raise RuntimeError(error)
    finally:
        if process.poll() is None:
            process.kill()
    frame(24).save(OUTPUT.with_name("KinoTime-banner-opening.png"))
    frame(144).save(OUTPUT.with_suffix(".png"))
    print(OUTPUT)


if __name__ == "__main__":
    main()
