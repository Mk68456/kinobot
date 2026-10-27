import os
from pathlib import Path
from types import SimpleNamespace
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

from tools import disk_space, desktop_launcher as launcher


class DiskSpaceTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.cache = self.root / 'KinoTime-Ready'
        self.cache.mkdir()
        env = patch.dict(os.environ, KINOTIME_STATE_DIR=str(self.root / 'state'))
        env.start()
        self.addCleanup(env.stop)
        disk_space.configure()
        self.addCleanup(disk_space.configure)

    def file(self, name, data=b'video'):
        path = self.cache / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return path

    def metadata(self, path):
        info = path.stat()
        return {'size': info.st_size, 'mtime': str(info.st_mtime_ns)}

    def clean(self, protected=()):
        with patch.object(disk_space.shutil, 'disk_usage', return_value=SimpleNamespace(free=0)):
            return disk_space.reclaim(self.cache, 1000, protected)

    def test_unconfirmed_and_untracked_files_never_deleted(self):
        original = self.file('source.mkv')
        output = self.file('mobile-test/720p/Dub.mp4')
        disk_space.register(output)
        self.assertEqual(self.clean(), 0)
        self.assertTrue(original.exists())
        self.assertTrue(output.exists())

    def test_stream_cleanup_only_confirmed_group_even_in_active_project(self):
        output = self.file('mobile-test/720p/Dub.mp4')
        alias = self.file('stream-test/720p/Episode/Dub.mp4')
        other = self.file('mobile-test/720p/Other.mp4')
        intermediate = self.file('mobile-test/.video-720p.mp4')
        disk_space.register(output)
        disk_space.register(alias, alias=output)
        disk_space.register(other)
        disk_space.register(intermediate, kind='intermediate')
        self.assertEqual(disk_space.release_confirmed(alias), 0)
        disk_space.confirm(alias, self.metadata(alias))
        with disk_space.session():
            disk_space.protect(output.parent.parent)
            self.assertGreater(disk_space.release_confirmed(alias), 0)
        self.assertFalse(output.exists())
        self.assertFalse(alias.exists())
        self.assertTrue(other.exists())
        self.assertTrue(intermediate.exists())

    def test_stream_cleanup_preserves_replaced_files_and_source(self):
        output = self.file('mobile-test/Dub.mp4')
        disk_space.register(output)
        disk_space.confirm(output, self.metadata(output))
        output.write_bytes(b'replaced file')
        self.assertEqual(disk_space.release_confirmed(output), 0)
        self.assertTrue(output.exists())
        source = self.root / 'source.mkv'
        source.write_bytes(b'source')
        disk_space.register(source)
        disk_space.confirm(source, self.metadata(source))
        self.assertEqual(disk_space.release_confirmed(source), 0)
        self.assertTrue(source.exists())

    def test_confirmation_must_match_recorded_metadata(self):
        output = self.file('mobile-test/720p/Dub.mp4')
        disk_space.register(output)
        disk_space.confirm(output, {'size': 123, 'mtime': '0'})
        self.assertEqual(self.clean(), 0)
        disk_space.confirm(output, self.metadata(output))
        self.assertGreater(self.clean(), 0)
        self.assertFalse(output.exists())

    def test_alias_confirmation_reclaims_both_copies(self):
        first = self.file('mobile-test/720p/Dub.mp4')
        second = self.file('folder-test/720p/episode/Dub.mp4')
        disk_space.register(first)
        disk_space.register(second, alias=first)
        disk_space.confirm(second, self.metadata(second))
        self.assertGreater(self.clean(), 0)
        self.assertFalse(first.exists())
        self.assertFalse(second.exists())

    def test_replaced_confirmed_file_is_preserved(self):
        output = self.file('mobile-test/Dub.mp4')
        disk_space.register(output)
        disk_space.confirm(output, self.metadata(output))
        output.write_bytes(b'replacement data')
        self.assertEqual(self.clean(), 0)
        self.assertTrue(output.exists())

    def test_active_input_is_protected_even_if_confirmed(self):
        output = self.file('mobile-test/Dub.mp4')
        disk_space.register(output)
        disk_space.confirm(output, self.metadata(output))
        self.assertEqual(self.clean([output]), 0)
        self.assertTrue(output.exists())

    def test_external_paths_are_not_registered(self):
        original = self.root / 'source.mp4'
        original.write_bytes(b'original')
        disk_space.register(original)
        disk_space.confirm(original, self.metadata(original))
        self.assertFalse(disk_space.safe_path(original, self.cache))
        self.assertEqual(self.clean(), 0)
        self.assertTrue(original.exists())

    def test_active_project_keeps_all_its_variants_until_preparation_finishes(self):
        output = self.file('mobile-test/Dub.mp4')
        disk_space.register(output)
        disk_space.confirm(output, self.metadata(output))
        with disk_space.session():
            disk_space.protect(output.parent)
            with disk_space.session():
                self.assertEqual(self.clean(), 0)
            self.assertEqual(self.clean(), 0)
        self.assertGreater(self.clean(), 0)

    def test_reparse_redirect_is_rejected(self):
        output = self.file('mobile-test/Dub.mp4')
        info = output.lstat()
        fake = SimpleNamespace(st_mode=info.st_mode, st_file_attributes=0x400)
        with patch.object(Path, 'lstat', return_value=fake):
            self.assertFalse(disk_space.safe_path(output, self.cache))

    def test_cleanup_disabled_preserves_intermediate_and_ready_output(self):
        output = self.file('mobile-test/Dub.mp4')
        intermediate = self.file('mobile-test/.video-720p.mp4')
        disk_space.register(output)
        disk_space.confirm(output, self.metadata(output))
        disk_space.configure(False)
        disk_space.discard_intermediate(intermediate)
        self.assertEqual(self.clean(), 0)
        self.assertTrue(output.exists())
        self.assertTrue(intermediate.exists())

    def test_completed_intermediate_is_removed_without_deleting_voices(self):
        intermediate = self.file('mobile-test/.video-720p.mp4')
        output = self.file('mobile-test/720p/Dub.mp4')
        disk_space.register(output)
        disk_space.discard_intermediate(intermediate)
        self.assertFalse(intermediate.exists())
        self.assertTrue(output.exists())

    def test_low_space_waits_and_automatically_resumes(self):
        stop, report = Mock(), Mock()
        stop.is_set.return_value = False
        stop.wait.return_value = False
        low, high = SimpleNamespace(free=0), SimpleNamespace(free=10 * disk_space.GIB)
        with patch.object(disk_space.shutil, 'disk_usage', side_effect=[low, low, high]), \
             patch.object(disk_space, 'reclaim', return_value=0):
            disk_space.ensure(self.cache, 100, stop, report)
        stop.wait.assert_called_once_with(5)
        self.assertIn('Ожидание места', report.call_args_list[0].args[0])

    def test_cancel_while_waiting_preserves_files(self):
        output = self.file('mobile-test/Dub.mp4')
        stop = threading.Event()
        stop.set()
        with patch.object(disk_space.shutil, 'disk_usage', return_value=SimpleNamespace(free=0)):
            with self.assertRaises(InterruptedError):
                disk_space.ensure(self.cache, 100, stop, Mock())
        self.assertTrue(output.exists())

    def test_ffmpeg_retries_only_unfinished_output_after_disk_pressure(self):
        source = self.root / 'original.mkv'
        source.write_bytes(b'original')
        output = self.file('mobile-test/.video-720p.partial.mp4')
        stop = Mock()
        stop.wait.return_value = False
        stop.disk_report = Mock()
        with patch.object(disk_space, 'ensure') as ensure, \
             patch.object(launcher, '_run_ffmpeg_once', side_effect=[launcher.DiskPressureError(), None]) as run:
            launcher._run_ffmpeg(['ffmpeg', '-i', str(source), str(output)], output.with_suffix('.log'), stop)
        self.assertEqual(run.call_count, 2)
        self.assertEqual(ensure.call_count, 2)
        self.assertFalse(output.exists())
        self.assertEqual(source.read_bytes(), b'original')

    def test_locked_file_does_not_block_other_candidates(self):
        first = self.file('mobile-test/first.mp4')
        second = self.file('mobile-test/second.mp4')
        for output in (first, second):
            disk_space.register(output)
            disk_space.confirm(output, self.metadata(output))
        original_unlink = Path.unlink
        def unlink(path, *args, **kwargs):
            if path == first:
                raise PermissionError('Telegram holds this file')
            return original_unlink(path, *args, **kwargs)
        with patch.object(Path, 'unlink', unlink):
            self.assertGreater(self.clean(), 0)
        self.assertTrue(first.exists())
        self.assertFalse(second.exists())

    def transfer_fixture(self, status):
        output = self.file('mobile-test/Dub.mp4')
        metadata = {'path': output.name, **self.metadata(output)}
        disk_space.register(output)
        manifest = {'root': str(output.parent), 'files': [metadata]}
        prepared = {'draft': {'id': 'draft', 'body': {'items': [
            {'asset_id': 'asset', 'relative_path': output.name}]}}}
        client = Mock(instance_id='')
        client.request.return_value = {'draft_status': 'draft', 'files': [{'id': 'asset', 'status': status}]}
        stop = Mock()
        stop.is_set.return_value = False
        stop.wait.return_value = True
        return output, manifest, prepared, client, stop

    def test_server_receipt_enables_reclamation_without_resend(self):
        output, manifest, prepared, client, stop = self.transfer_fixture('ready')
        desktop = Mock()
        launcher.transfer(client, prepared, manifest, desktop, stop, Mock())
        desktop.send_file.assert_not_called()
        self.assertGreater(self.clean(), 0)
        self.assertFalse(output.exists())

    def test_uncertain_transfer_journal_never_enables_reclamation(self):
        output, manifest, prepared, client, stop = self.transfer_fixture('waiting_telegram')
        (output.parent / '.kinotime-transfer.json').write_text('{"asset":"sent_unconfirmed"}', encoding='utf-8')
        with self.assertRaises(InterruptedError):
            launcher.transfer(client, prepared, manifest, Mock(), stop, Mock())
        self.assertEqual(self.clean(), 0)
        self.assertTrue(output.exists())

    def test_copy_retries_enospc_and_preserves_source(self):
        import errno
        source = self.root / 'source.mp4'
        source.write_bytes(b'original')
        target = self.file('mobile-test/output.partial.mp4')
        stop = Mock()
        stop.wait.return_value = False
        with patch.object(disk_space, 'ensure') as ensure, \
             patch.object(disk_space.shutil, 'copy2', side_effect=[OSError(errno.ENOSPC, 'full'), None]) as copy:
            disk_space.copy(source, target, stop, Mock())
        self.assertEqual(copy.call_count, 2)
        self.assertEqual(ensure.call_count, 2)
        self.assertEqual(source.read_bytes(), b'original')

    def test_reserve_settings_validated(self):
        for value in (0, -1, float('nan'), 101, '2', True):
            with self.assertRaises(ValueError):
                disk_space.configure(True, value)
        disk_space.configure(True, 4)
        self.assertEqual(disk_space.settings()['disk_reserve_gib'], 4)

    def test_real_speed_is_distinct_from_bitrate(self):
        value = launcher.EncodingProgress(50, {'speed': '2.00x', 'fps': '60', 'out_time_us': '30000000'}, 60)
        self.assertEqual(value, 50)
        self.assertEqual(launcher.progress_text(value), '50% · 2.00× · 60 кадр/с · осталось ~0:15')
        self.assertEqual(launcher.progress_text(launcher.EncodingProgress(3, {'speed': 'N/A'}, 60)), '3%')


if __name__ == '__main__':
    unittest.main()
