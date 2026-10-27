"""Vendor selection, fallback, cancellation and installed state isolation."""
import os
from pathlib import Path
import subprocess
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

from tools import desktop_launcher as launcher, launcher_runtime as runtime, video_hardware as hardware


class HardwareSelectionTests(unittest.TestCase):
    def setUp(self):
        hardware.configure()
        with hardware._lock:
            hardware._availability.clear()
            hardware._probe_errors.clear()
        self.addCleanup(hardware.configure)

    def test_probe_requires_successful_encoding_and_caches_result(self):
        with patch.object(hardware, 'media_tool', return_value='ffmpeg.exe'), \
             patch.object(hardware.subprocess, 'run', return_value=Mock(returncode=1)) as run:
            self.assertFalse(hardware.available('nvidia'))
            self.assertFalse(hardware.available('nvidia'))
            self.assertEqual(run.call_count, 1)
            self.assertIn('h264_nvenc', run.call_args.args[0])
            run.return_value.returncode = 0
            self.assertTrue(hardware.available('nvidia', refresh=True))
            hardware.disable('nvidia')
            self.assertFalse(hardware.available('nvidia'))

    def test_timeout_is_an_unavailable_gpu(self):
        with patch.object(hardware, 'media_tool', return_value='ffmpeg.exe'), \
             patch.object(hardware.subprocess, 'run', side_effect=subprocess.TimeoutExpired('ffmpeg', 12)):
            self.assertFalse(hardware.available('amd'))

    def test_auto_skips_unavailable_vendor_and_has_cpu_fallback(self):
        with patch.object(hardware, 'available', side_effect=lambda b: b in ('intel', 'amd')):
            self.assertEqual(hardware.candidates(), ['intel', 'amd', 'cpu'])

    def test_each_manual_vendor_is_used_without_switching_to_other_gpus(self):
        for vendor in ('nvidia', 'intel', 'amd'):
            hardware.configure(vendor)
            with patch.object(hardware, 'available', return_value=True) as probe:
                self.assertEqual(hardware.candidates(), [vendor, 'cpu'])
                probe.assert_called_once_with(vendor)
            with patch.object(hardware, 'available', return_value=False):
                self.assertEqual(hardware.candidates(), ['cpu'])

    def test_cpu_and_two_pass_modes_never_probe_gpu(self):
        with patch.object(hardware, 'available') as probe:
            hardware.configure('cpu')
            self.assertEqual(hardware.candidates(), ['cpu'])
            hardware.configure('auto')
            self.assertEqual(hardware.candidates('exact'), ['cpu'])
            probe.assert_not_called()

    def test_cancel_before_probe_stops_preparation(self):
        stop = threading.Event()
        stop.set()
        with patch.object(hardware, 'available') as probe:
            with self.assertRaises(InterruptedError):
                hardware.candidates(stop=stop)
            probe.assert_not_called()

    def test_resource_budget_and_eco_priority(self):
        self.assertEqual(hardware.cpu_threads('eco', 12), 3)
        self.assertEqual(hardware.cpu_threads('balanced', 12), 6)
        self.assertEqual(hardware.cpu_threads('performance', 12), 12)
        self.assertEqual(hardware.cpu_threads('eco', 1), 1)
        hardware.configure('cpu', 'eco')
        self.assertEqual(hardware.process_flags() & getattr(subprocess, 'BELOW_NORMAL_PRIORITY_CLASS', 0),
                         getattr(subprocess, 'BELOW_NORMAL_PRIORITY_CLASS', 0))
        args = hardware.codec_args('cpu', 800)
        self.assertEqual(args[args.index('-threads') + 1], str(hardware.cpu_threads()))

    def test_invalid_preferences_do_not_mutate_current_settings(self):
        previous = hardware.settings()
        with self.assertRaises(ValueError):
            hardware.configure('unknown', 'balanced')
        self.assertEqual(hardware.settings(), previous)


class EncodingFallbackTests(unittest.TestCase):
    def test_failed_gpu_output_is_removed_then_cpu_encodes(self):
        info = {'format': {'duration': '1'}, 'streams': [{'codec_type': 'video', 'codec_name': 'mpeg4'}]}
        compatible = {'streams': [{'codec_type': 'video', 'codec_name': 'h264', 'pix_fmt': 'yuv420p'}]}
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / 'prepared.mp4'
            partial = target.with_suffix('.partial.mp4')
            def encode(command, *args):
                if 'h264_amf' in command:
                    partial.write_bytes(b'broken')
                    raise ValueError('Driver error')
                self.assertFalse(partial.exists())
                partial.write_bytes(b'good')
            with patch.object(hardware, 'candidates', return_value=['amd', 'cpu']), \
                 patch.object(hardware, 'disable') as disable, \
                 patch.object(launcher, '_run_ffmpeg', side_effect=encode) as run, \
                 patch.object(launcher, 'probe_video', return_value=compatible):
                launcher._prepare_quality_video(Path('source.mkv'), target, '360p',
                    launcher.QUALITY_PROFILES['360p'], info, 'fast', threading.Event(), Mock())
                self.assertEqual(run.call_count, 2)
                disable.assert_called_once_with('amd', reason='Driver error')
                self.assertEqual(target.read_bytes(), b'good')

    def test_stop_does_not_turn_into_a_cpu_retry(self):
        info = {'format': {'duration': '1'}, 'streams': [{'codec_type': 'video', 'codec_name': 'mpeg4'}]}
        with tempfile.TemporaryDirectory() as directory, \
             patch.object(hardware, 'candidates', return_value=['intel', 'cpu']), \
             patch.object(hardware, 'disable') as disable, \
             patch.object(launcher, '_run_ffmpeg', side_effect=InterruptedError('Stopped')) as run:
            with self.assertRaises(InterruptedError):
                launcher._prepare_quality_video(Path('source.mkv'), Path(directory) / 'target.mp4',
                    '360p', launcher.QUALITY_PROFILES['360p'], info, 'fast', threading.Event(), Mock())
            self.assertEqual(run.call_count, 1)
            disable.assert_not_called()

    def test_success_exit_without_video_also_falls_back_to_cpu(self):
        info = {'format': {'duration': '1'}, 'streams': [{'codec_type': 'video', 'codec_name': 'mpeg4'}]}
        compatible = {'streams': [{'codec_type': 'video', 'codec_name': 'h264', 'pix_fmt': 'yuv420p'}]}
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / 'target.mp4'
            partial = target.with_suffix('.partial.mp4')
            with patch.object(hardware, 'candidates', return_value=['nvidia', 'cpu']), \
                 patch.object(hardware, 'disable') as disable, \
                 patch.object(launcher, '_run_ffmpeg', side_effect=lambda *args: partial.write_bytes(b'video')) as run, \
                 patch.object(launcher, 'probe_video', side_effect=[{'streams': []}, compatible, compatible]):
                launcher._prepare_quality_video(Path('source.mkv'), target, '360p',
                    launcher.QUALITY_PROFILES['360p'], info, 'fast', threading.Event(), Mock())
                self.assertEqual(run.call_count, 2)
                disable.assert_called_once_with('nvidia', reason='Кодировщик не вернул совместимый H.264/yuv420p.')


class RuntimePathTests(unittest.TestCase):
    def test_frozen_settings_survive_executable_directory_change(self):
        with tempfile.TemporaryDirectory() as directory, \
             patch.dict(os.environ, {'LOCALAPPDATA': directory}), \
             patch.object(runtime.sys, 'frozen', True, create=True), \
             patch.dict(os.environ) as env:
            env.pop('KINOTIME_STATE_DIR', None)
            first = runtime.state_path('launcher-settings.json', Path(directory) / 'install1' / 'app.py')
            second = runtime.state_path('launcher-settings.json', Path(directory) / 'install2' / 'app.py')
            self.assertEqual(first, second)
            self.assertEqual(first.parent, Path(directory) / 'KinoTime' / 'Launcher')

    def test_bundled_ffmpeg_wins_over_path(self):
        with tempfile.TemporaryDirectory() as directory, \
             patch.object(runtime.sys, '_MEIPASS', directory, create=True), \
             patch.object(runtime.shutil, 'which', return_value='external-ffmpeg'):
            executable = Path(directory) / 'bin' / ('ffmpeg.exe' if os.name == 'nt' else 'ffmpeg')
            executable.parent.mkdir()
            executable.write_bytes(b'bundled')
            self.assertEqual(runtime.media_tool('ffmpeg'), str(executable))


class CPUEncodingIntegrationTests(unittest.TestCase):
    def test_cpu_two_pass_and_legacy_preparation_produce_compatible_video(self):
        try:
            ffmpeg = runtime.media_tool('ffmpeg')
            runtime.media_tool('ffprobe')
        except ValueError:
            self.skipTest('FFmpeg required')
        self.addCleanup(hardware.configure)
        hardware.configure('cpu', 'eco')
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'source.mkv'
            subprocess.run([ffmpeg, '-nostdin', '-v', 'error', '-f', 'lavfi', '-i',
                            'testsrc2=size=640x360:rate=10', '-f', 'lavfi', '-i', 'sine=frequency=440',
                            '-t', '0.5', '-c:v', 'mpeg4', '-c:a', 'aac', str(source)],
                           check=True, capture_output=True, timeout=60,
                           creationflags=hardware.process_flags())
            target = root / 'exact.mp4'
            info = launcher.probe_video(source)
            launcher._prepare_quality_video(source, target, '360p', launcher.QUALITY_PROFILES['360p'],
                info, 'exact', threading.Event(), Mock())
            self.assertTrue(launcher.player_compatible(launcher.probe_video(target)))
            legacy = root / 'legacy'
            legacy.mkdir()
            (legacy / source.name).write_bytes(source.read_bytes())
            manifest = launcher.prepare_media_base(legacy, Mock(), threading.Event())
            self.assertEqual(len(manifest['files']), 1)
            converted = Path(manifest['root']) / manifest['files'][0]['path']
            self.assertTrue(launcher.player_compatible(launcher.probe_video(converted)))


if __name__ == '__main__':
    unittest.main()
