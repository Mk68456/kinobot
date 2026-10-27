"""Diagnose unsupported GPUs and exercise the actual launcher selector/persistence."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch

from tools import desktop_launcher as launcher, video_hardware as hardware


def fixture(selected='nvidia', supported=False, adapters=None):
    return {'selected': selected, 'resource_mode': 'balanced', 'cpu_threads': 4,
            'logical_processors': 8, 'encoders': {'nvidia': supported, 'intel': False, 'amd': False},
            'errors': {'nvidia': 'No NVENC capable devices found', 'intel': 'No device', 'amd': 'No device'},
            'devices': adapters or [{'name': 'NVIDIA P106-100', 'driver': 'test', 'vendor': 'nvidia'}]}


class DiagnosticsTests(unittest.TestCase):
    def setUp(self):
        hardware.configure()
        hardware._availability.clear()
        hardware._probe_errors.clear()
        self.addCleanup(hardware.configure)
        self.addCleanup(hardware._availability.clear)
        self.addCleanup(hardware._probe_errors.clear)

    def test_driver_error_is_retained_and_refresh_success_clears_it(self):
        response = Mock(returncode=1, stderr=b'No NVENC capable devices found')
        with patch.object(hardware.subprocess, 'run', return_value=response), \
                patch.object(hardware, 'media_tool', return_value='ffmpeg'):
            self.assertFalse(hardware.available('nvidia'))
            self.assertIn('No NVENC', hardware._probe_errors['nvidia'])
            report = Mock()
            hardware.configure('nvidia')
            self.assertEqual(hardware.candidates(report=report), ['cpu'])
            self.assertIn('NVENC', report.call_args.args[0])
            self.assertIn('процессор', report.call_args.args[0])
            response.returncode = 0
            self.assertTrue(hardware.available('nvidia', refresh=True))
            self.assertEqual(hardware._probe_errors['nvidia'], '')

    def test_timeout_is_in_detailed_diagnostics(self):
        with patch.object(hardware.subprocess, 'run', side_effect=subprocess.TimeoutExpired('ffmpeg', 12)), \
                patch.object(hardware, 'media_tool', return_value='ffmpeg'), \
                patch.object(hardware, 'devices', return_value=[]):
            info = hardware.diagnostics(refresh=True)
        self.assertIn('12 секунд', hardware.diagnostics_text(info))

    def test_p106_reports_missing_nvenc_and_falls_back(self):
        summary = hardware.diagnostics_summary(fixture())
        self.assertIn('P106-100 не имеет блока NVENC', summary)
        self.assertIn('Используется процессор', summary)
        self.assertIn('No NVENC capable', hardware.diagnostics_text(fixture()))

    def test_p106_does_not_mask_working_second_nvidia_gpu(self):
        adapters = [{'name': 'P106-100', 'vendor': 'nvidia', 'driver': 'test'},
                    {'name': 'NVIDIA RTX 3060', 'vendor': 'nvidia', 'driver': 'test'}]
        info = fixture(supported=True, adapters=adapters)
        self.assertIn('проверка H.264 пройдена', hardware.diagnostics_summary(info))
        self.assertNotIn('Используется процессор', hardware.diagnostics_summary(info))
        info['encoders']['nvidia'] = False
        self.assertNotIn('не имеет блока NVENC', hardware.diagnostics_summary(info))

    def test_actual_success_takes_precedence_over_adapter_name(self):
        self.assertIn('проверка H.264 пройдена', hardware.diagnostics_summary(fixture(supported=True)))

    def test_auto_can_select_intel_alongside_unsupported_p106(self):
        info = fixture('auto')
        info['encoders']['intel'] = True
        self.assertIn('Автовыбор: Intel Quick Sync', hardware.diagnostics_summary(info))

    def test_driver_version_error_has_actionable_reason(self):
        info = fixture(adapters=[{'name': 'RTX 3060', 'vendor': 'nvidia', 'driver': 'old'}])
        info['errors']['nvidia'] = 'Driver does not support the required nvenc API version'
        self.assertIn('драйвер NVIDIA не поддерживает нужную версию', hardware.diagnostics_summary(info))

    def test_encode_failure_replaces_earlier_success_without_another_probe(self):
        hardware._availability['nvidia'] = True
        hardware.configure('nvidia')
        hardware.disable('nvidia', 'No NVENC capable devices found')
        with patch.object(hardware.subprocess, 'run') as run:
            info = hardware.diagnostics_snapshot()
            text = hardware.diagnostics_text(info)
            run.assert_not_called()
        self.assertIn('Используется процессор', text)
        self.assertIn('No NVENC capable', text)
        self.assertIn('Intel Quick Sync: ещё не проверен', text)

    def test_windows_adapters_are_read_hidden_and_cached(self):
        if os.name != 'nt':
            self.skipTest('Windows adapter inventory')
        records = [{'Name': 'P106-100', 'DriverVersion': '1', 'PNPDeviceID': 'PCI\\VEN_10DE&DEV_1C07'},
                   {'Name': 'Intel Graphics', 'DriverVersion': '2', 'PNPDeviceID': 'PCI\\VEN_8086'}]
        with patch.object(hardware.subprocess, 'run', return_value=Mock(returncode=0,
                           stdout=json.dumps(records).encode('utf-8'))) as run, \
                patch.object(hardware, '_devices', None):
            devices = hardware.devices()
            self.assertEqual([d['vendor'] for d in devices], ['nvidia', 'intel'])
            self.assertEqual(hardware.devices(), devices)
            run.assert_called_once()
            self.assertEqual(run.call_args.kwargs['creationflags'], subprocess.CREATE_NO_WINDOW)


class LauncherHardwareUITests(unittest.TestCase):
    def exercise(self, saved=None, test_busy=False):
        import tkinter as tk
        root = tk.Tk()
        root.withdraw()
        errors = []
        findings = {}
        root.report_callback_exception = lambda *args: errors.append(args[1])
        def all_widgets(parent):
            for child in parent.winfo_children():
                yield child
                yield from all_widgets(child)
        def interact():
            try:
                selectors = [w for w in all_widgets(root) if w.winfo_name() == 'hardware_picker']
                loads = [w for w in all_widgets(root) if w.winfo_name() == 'load_picker']
                self.assertEqual(len(selectors), 2)
                self.assertEqual(len(loads), 2)
                if saved:
                    self.assertEqual(selectors[0].get(), 'AMD AMF')
                    self.assertEqual(loads[1].get(), 'Экономный')
                selectors[0].set('Процессор')
                selectors[0].event_generate('<<ComboboxSelected>>')
                self.assertEqual(selectors[1].get(), 'Процессор')
                loads[1].set('Максимальная скорость')
                loads[1].event_generate('<<ComboboxSelected>>')
                self.assertEqual(loads[0].get(), 'Максимальная скорость')
                if test_busy:
                    checks = [w for w in all_widgets(root) if w.winfo_name() == 'hardware_check']
                    with patch.object(launcher.threading, 'Thread'):
                        checks[0].invoke()
                    for selector in (*selectors, *loads):
                        self.assertEqual(str(selector.cget('state')), 'disabled')
                    # Even a queued selection event cannot change an active worker's configuration.
                    selectors[1].set('AMD AMF')
                    selectors[1].event_generate('<<ComboboxSelected>>')
                    self.assertEqual(selectors[0].get(), 'Процессор')
                findings.update(json.loads((Path(directory) / 'launcher-settings.json').read_text(encoding='utf-8')))
            except Exception as error:
                errors.append(error)
            finally:
                root.destroy()
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {'KINOTIME_STATE_DIR': directory}), \
                patch.object(tk, 'Tk', return_value=root):
            if saved:
                (Path(directory) / 'launcher-settings.json').write_text(json.dumps(saved), encoding='utf-8')
            root.after(25, interact)
            launcher._main(smoke_test=True)
        if errors:
            raise errors[0]
        self.assertEqual(findings['hardware'], 'cpu')
        self.assertEqual(findings['resource_mode'], 'performance')
        self.assertNotIn('token', findings)
        hardware.configure()

    def test_queue_and_processing_share_selection_and_save_without_button(self):
        self.exercise()

    def test_saved_hardware_preferences_restore_in_both_pages(self):
        self.exercise({'hardware': 'amd', 'resource_mode': 'eco'})

    def test_active_preparation_disables_both_selectors_and_rejects_pending_change(self):
        self.exercise(test_busy=True)


if __name__ == '__main__':
    unittest.main()
