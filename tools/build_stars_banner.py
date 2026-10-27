"""Build a silent, seamless glowing banner from the original generated artwork."""
from pathlib import Path
import sys
import threading

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from services.local_clips import run_media


def main():
    run_media([
        "-loop", "1", "-i", str(ROOT / "assets" / "telegram-stars-banner.png"),
        "-vf", "scale=1080:360,eq=brightness='0.012*sin(2*PI*t/6)':eval=frame",
        "-t", "6", "-r", "30", "-an", "-c:v", "libx264",
        "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p",
        "-movflags", "+faststart", "-y", str(ROOT / "dist" / "Telegram-Stars-banner.mp4"),
    ], threading.Event())


if __name__ == "__main__":
    main()
