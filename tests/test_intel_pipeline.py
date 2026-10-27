import tempfile
import threading
from pathlib import Path
import unittest
from unittest.mock import Mock, patch
from tools import desktop_launcher as launcher, video_hardware as hardware


class IntelPipelineTests(unittest.TestCase):
    def test_sizes_even_aspect_and_no_upscale(self):
        stream = {'codec_name': 'hevc', 'pix_fmt': 'yuv420p10le', 'width': 1920, 'height': 1080}
        args, filter_text = hardware.intel_pipeline(stream, {'width': 854, 'height': 480})
        self.assertIn('qsv', args)
        self.assertEqual(filter_text, 'scale_qsv=w=852:h=480:format=nv12')
        stream.update(width=320, height=180)
        self.assertIn('w=320:h=180', hardware.intel_pipeline(stream, {'width': 640, 'height': 360})[1])

    def test_unsupported_formats_stay_on_existing_path(self):
        stream = {'codec_name': 'h264', 'pix_fmt': 'yuv444p', 'width': 1920, 'height': 1080}
        self.assertIsNone(hardware.intel_pipeline(stream, {'width': 640, 'height': 360}))
        stream.update(pix_fmt='yuv420p', field_order='tt')
        self.assertIsNone(hardware.intel_pipeline(stream, {'width': 640, 'height': 360}))

    def test_success_failure_and_cancel(self):
        info = {'format': {'duration': '10'}, 'streams': [
            {'codec_type': 'video', 'codec_name': 'h264', 'pix_fmt': 'yuv420p', 'width': 1920, 'height': 1080}]}
        for outcome in ('success', 'fallback', 'cancel'):
            with self.subTest(outcome=outcome), tempfile.TemporaryDirectory() as folder:
                target = Path(folder) / 'video.mp4'
                calls = []
                def run(command, *args):
                    calls.append(command)
                    if len(calls) == 1:
                        if outcome == 'fallback':
                            raise ValueError('unsupported decoder')
                        if outcome == 'cancel':
                            raise InterruptedError('Stopped')
                    Path(command[-1]).write_bytes(b'video')
                with patch.object(hardware, 'candidates', return_value=['intel', 'cpu']), \
                     patch.object(launcher, '_run_ffmpeg', side_effect=run), \
                     patch.object(launcher, 'probe_video', return_value=info), \
                     patch.object(hardware, 'disable') as disable:
                    if outcome == 'cancel':
                        with self.assertRaises(InterruptedError):
                            launcher._prepare_quality_video(Path(folder)/'source.mkv', target, '360p',
                                launcher.QUALITY_PROFILES['360p'], info, 'fast', threading.Event(), Mock())
                    else:
                        launcher._prepare_quality_video(Path(folder)/'source.mkv', target, '360p',
                            launcher.QUALITY_PROFILES['360p'], info, 'fast', threading.Event(), Mock())
                        self.assertTrue(target.exists())
                    self.assertIn('-hwaccel', calls[0])
                    self.assertNotIn('-pix_fmt', calls[0])
                    self.assertEqual(len(calls), 2 if outcome == 'fallback' else 1)
                    if outcome == 'fallback':
                        self.assertNotIn('-hwaccel', calls[1])
                        self.assertIn('h264_qsv', calls[1])
                    disable.assert_not_called()
