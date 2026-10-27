"""Large compatible sources must be compressed without changing frame dimensions."""
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from tools import desktop_launcher as launcher


class SourceSizeLimitTests(unittest.TestCase):
    def info(self, rate=None):
        video = {'codec_type': 'video', 'codec_name': 'h264', 'pix_fmt': 'yuv420p',
                 'width': 1920, 'height': 800}
        if rate is not None:
            video['bit_rate'] = rate
        return {'streams': [video], 'format': {'duration': '3600'}}

    def test_cropped_quality_choices_and_source_deduplication(self):
        for size, expected in [((1920, 800), '1080p'), ((1280, 536), '720p'),
                               ((640, 268), '360p')]:
            with self.subTest(size=size), tempfile.TemporaryDirectory() as directory:
                source = Path(directory) / 'episode.mkv'
                source.write_bytes(b'video')
                info = self.info()
                info['streams'][0].update(width=size[0], height=size[1])
                with patch.object(launcher, 'probe_video', return_value=info):
                    media = launcher.quality_choices(source)
                self.assertEqual(media['selected'][0], expected)
                if expected != '1080p':
                    self.assertNotIn('1080p', media['eligible'])
                self.assertNotIn(expected, launcher.resolve_quality_selection(media, ['source', expected]))

    def test_copy_decision_reserves_audio_and_handles_missing_bitrate(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'episode.mkv'
            source.write_bytes(b'video')
            profile = launcher._quality_profile('source', self.info())
            self.assertTrue(launcher._can_copy_source(source, profile, self.info()))
            self.assertTrue(launcher._can_copy_source(source, profile, self.info('4000000')))
            self.assertFalse(launcher._can_copy_source(source, profile, self.info('12000000')))
            # 3.6 GiB of video fits alone, but not with an hour of AAC and overhead.
            rate = str(int(3.6 * 1024**3 * 8 / 3600))
            self.assertFalse(launcher._can_copy_source(source, profile, self.info(rate)))
            with patch.object(Path, 'stat', return_value=Mock(st_size=4 * 1024**3)):
                self.assertFalse(launcher._can_copy_source(source, profile, self.info()))

    def test_underestimated_copy_is_reencoded(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'episode.mkv'
            source.write_bytes(b'video')
            target = Path(directory) / 'ready.mp4'
            info = self.info('100')
            info['format']['duration'] = '1'
            profile = {**launcher._quality_profile('source', info), 'limit': .0001}
            commands = []

            def run(command, *args):
                commands.append(command)
                Path(command[-1]).write_bytes(b'x' * (120000 if command[command.index('-c:v') + 1] == 'copy' else 100))

            with patch.object(launcher, '_run_ffmpeg', side_effect=run), \
                 patch.object(launcher, 'probe_video', return_value=info), \
                 patch.object(launcher.video_hardware, 'candidates', return_value=['cpu']):
                launcher._prepare_quality_video(source, target, 'source', profile, info,
                                                'fast', threading.Event(), Mock())
            self.assertEqual(len(commands), 2)
            self.assertEqual(commands[0][commands[0].index('-c:v') + 1], 'copy')
            self.assertEqual(commands[1][commands[1].index('-c:v') + 1], 'libx264')
            self.assertEqual(target.stat().st_size, 100)

    def test_large_cropped_source_and_stale_cache_prepare_all_voices(self):
        try:
            ffmpeg = launcher.media_tool('ffmpeg')
            launcher.media_tool('ffprobe')
        except ValueError:
            self.skipTest('FFmpeg required')
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'episode.mkv'
            subprocess.run([ffmpeg, '-v', 'error', '-f', 'lavfi', '-i',
                            'testsrc2=size=1920x800:rate=10', '-f', 'lavfi', '-i', 'sine',
                            '-t', '3', '-c:v', 'libx264', '-preset', 'ultrafast', '-crf', '0',
                            '-pix_fmt', 'yuv420p', '-c:a', 'aac', str(source)],
                           check=True, capture_output=True, timeout=60)
            profile = {**launcher._quality_profile('source', launcher.probe_video(source)), 'limit': .001}
            self.assertGreater(source.stat().st_size, profile['limit'] * 1024**3)
            plan = {'qualities': ['source'], 'mode': 'exact', 'audio': [
                {'index': 0, 'name': name, 'enabled': True} for name in ('AlexFilm', 'LostFilm', 'KvK', 'English')]}
            prepare = launcher._prepare_quality_video

            # A first interrupted attempt leaves an oversized video intermediate.
            def leave_cache(source, target, *args):
                target.write_bytes(source.read_bytes())
                raise InterruptedError('Stopped')

            with patch.object(launcher, '_quality_profile', return_value=profile), \
                 patch.object(launcher, '_prepare_quality_video', side_effect=leave_cache):
                with self.assertRaises(InterruptedError):
                    launcher.prepare_media(source, Mock(), threading.Event(), media_plan=plan, cache_root=root / 'cache')
            with patch.object(launcher, '_quality_profile', return_value=profile), \
                 patch.object(launcher, '_prepare_quality_video', wraps=prepare) as encoder:
                result = launcher.prepare_media(source, Mock(), threading.Event(), media_plan=plan, cache_root=root / 'cache')
            encoder.assert_called_once()
            self.assertEqual(len(result['files']), 4)
            for entry in result['files']:
                output = Path(result['root']) / entry['path']
                self.assertLessEqual(output.stat().st_size, profile['limit'] * 1024**3)
                info = launcher.probe_video(output)
                video = next(s for s in info['streams'] if s['codec_type'] == 'video')
                self.assertEqual((video['width'], video['height']), (1920, 800))
                self.assertTrue(launcher.player_compatible(info))


if __name__ == '__main__':
    unittest.main()
