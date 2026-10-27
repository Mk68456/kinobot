import math
from pathlib import Path
import struct
import tempfile
import threading
import unittest

from PIL import Image

from services.local_clips import Cancelled, ffmpeg_path, run_media
from services.meme_render import MemeSettings, geometry, inspect_media, preview_frame, render_meme


class MemeRenderTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory(prefix="MemeFlow тест ")
        self.root = Path(self.folder.name)
        self.cancel = threading.Event()
        self.background = self.root / "фон.mp4"
        self.meme = self.root / "мем.mp4"
        self.banner = self.root / "баннер.mp4"
        for path, color, duration, frequency in [
            (self.background, "blue", .7, 900), (self.meme, "red", 1.8, 440),
            (self.banner, "green", .4, 1200),
        ]:
            run_media(["-y", "-f", "lavfi", "-i", f"color=c={color}:size=160x100:rate=30",
                       "-f", "lavfi", "-i", f"sine=frequency={frequency}:sample_rate=44100",
                       "-t", str(duration), "-c:v", "libx264", "-c:a", "aac", str(path)], self.cancel)

    def tearDown(self):
        self.folder.cleanup()

    def test_shapes_loop_background_and_keep_only_meme_sound(self):
        import subprocess
        for shape in ("circle", "rounded"):
            settings = MemeSettings(shape=shape, banner_enabled=True)
            original = self.meme.read_bytes()
            result = render_meme(self.meme, self.background, self.banner, settings,
                                 self.root / "готовые", self.cancel)
            info = inspect_media(result, self.cancel)
            self.assertAlmostEqual(info.duration, 1.8, delta=.15)
            self.assertEqual((info.width, info.height), (720, 1280))
            self.assertTrue(info.has_audio)
            self.assertEqual(original, self.meme.read_bytes())
            still = self.root / f"{shape}.png"
            run_media(["-y", "-ss", "1.4", "-i", str(result), "-frames:v", "1", str(still)], self.cancel)
            _, _, bw, bh, x, y = geometry(settings, 720)
            with Image.open(still) as image:
                rgb = image.convert("RGB")
                center = rgb.getpixel((360, round(1280*settings.center_y)))
                corner = rgb.getpixel((x+2, y+2))
                background = rgb.getpixel((30, 1100))
                banner = rgb.getpixel((360, 160))
            self.assertGreater(center[0], 180)
            self.assertGreater(corner[2], 180)  # rounded mask exposes the background
            self.assertGreater(background[2], 180)  # still present after .7 s source ends
            self.assertGreater(banner[1], 80)
            audio = subprocess.run([ffmpeg_path(), "-v", "error", "-i", str(result), "-t", "1",
                                    "-vn", "-ac", "1", "-ar", "8000", "-f", "s16le", "-"],
                                   capture_output=True, check=True).stdout
            samples = struct.unpack(f"<{len(audio)//2}h", audio)
            def amplitude(hz):
                return abs(sum(value*math.sin(2*math.pi*hz*n/8000) for n, value in enumerate(samples)))
            self.assertGreater(amplitude(440), 10*amplitude(900))
            self.assertGreater(amplitude(440), 10*amplitude(1200))
            self.assertFalse(list((self.root / "готовые").glob("*.partial.mp4")))

    def test_preview_late_in_video_with_looped_background(self):
        settings = MemeSettings(banner_enabled=True)
        output = preview_frame(self.meme, self.background, self.banner, settings,
                               self.root / "preview.png", self.cancel, at=1.4)
        with Image.open(output) as image:
            self.assertEqual(image.size, (360, 640))

    def test_intro_fades_both_layers_and_delays_audio_without_truncating_meme(self):
        import subprocess
        settings = MemeSettings(banner_enabled=True, smooth_intro=True)
        result = render_meme(self.meme, self.background, self.banner, settings, self.root, self.cancel)
        self.assertAlmostEqual(inspect_media(result, self.cancel).duration, 2.8, delta=.15)
        for moment, low, high in ((.5, 0, 20), (1.3, 80, 170), (1.8, 220, 255)):
            still = self.root / f"intro-{moment}.png"
            run_media(["-y", "-ss", str(moment), "-i", str(result), "-frames:v", "1", str(still)], self.cancel)
            preview = preview_frame(self.meme, self.background, self.banner, settings,
                                    self.root / 'intro-preview.png', self.cancel, at=moment, width=720)
            with Image.open(still) as frame, Image.open(preview) as live:
                center = frame.convert('RGB').getpixel((360, 615))
                ad = frame.convert('RGB').getpixel((360, 160))
                self.assertTrue(low <= center[0] <= high, center)
                self.assertTrue(low*.45 <= ad[1] <= high*.65+15, ad)
                for a, b in zip(center, live.convert('RGB').getpixel((360, 615))):
                    self.assertAlmostEqual(a, b, delta=10)
        levels = []
        for moment in (.2, 1.7, 2.5):
            audio = subprocess.run([ffmpeg_path(), '-v', 'error', '-ss', str(moment), '-i', str(result),
                                    '-t', '0.2', '-vn', '-ac', '1', '-ar', '8000', '-f', 's16le', '-'],
                                   capture_output=True, check=True).stdout
            samples = struct.unpack(f'<{len(audio)//2}h', audio)
            levels.append(sum(abs(v) for v in samples)/max(1, len(samples)))
        self.assertLess(levels[0], 10)
        self.assertGreater(min(levels[1:]), 500)

    def test_silent_meme_and_cancellation(self):
        silent = self.root / "silent.mp4"
        run_media(["-y", "-i", str(self.meme), "-an", "-c:v", "copy", str(silent)], self.cancel)
        result = render_meme(silent, self.background, None, MemeSettings(), self.root, self.cancel)
        self.assertFalse(inspect_media(result, self.cancel).has_audio)
        self.cancel.set()
        with self.assertRaises(Cancelled):
            render_meme(silent, self.background, None, MemeSettings(), self.root, self.cancel)
        self.assertFalse(list(self.root.glob("*.partial.mp4")))

    def test_green_background_on_banner_is_removed(self):
        banner = self.root / "keyed.png"
        image = Image.new("RGB", (300, 100), "#00ff00")
        from PIL import ImageDraw
        ImageDraw.Draw(image).rectangle((80, 20, 220, 80), fill="white")
        image.save(banner)
        settings = MemeSettings(banner_enabled=True, banner_chromakey=True)
        output = preview_frame(self.meme, self.background, banner, settings,
                               self.root / "keyed-preview.png", self.cancel)
        with Image.open(output) as frame:
            rgb = frame.convert('RGB')
            outside = rgb.getpixel((45, 60))
            inside = rgb.getpixel((180, 90))
        self.assertGreater(outside[2], 180)
        self.assertLess(outside[1], 80)
        self.assertGreater(min(inside), 200)

    def test_cancel_during_export_does_not_leave_partial_video(self):
        def stop(percent):
            self.cancel.set()
        with self.assertRaises(Cancelled):
            render_meme(self.meme, self.background, None, MemeSettings(), self.root / 'cancelled',
                        self.cancel, stop)
        self.assertFalse(list((self.root / 'cancelled').glob('*.mp4')))


if __name__ == "__main__":
    unittest.main()
