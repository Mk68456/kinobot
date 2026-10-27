import http.server
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch
from urllib.parse import parse_qs, urlsplit

from tools.torrent_queue import GIB, QBitClient, TorrentQueue, queue_lock, safe_path, used_bytes


class TorrentQueueTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.queue = TorrentQueue(self.temp.name, quota_gib=100, reserve_gib=0)
        self.stop = threading.Event()
        self.client = Mock()
        self.job = {
            "id": "a" * 32,
            "tag": "kinotime-" + "a" * 32,
            "hash": "b" * 40,
            "title": "Movie",
            "status": "queued",
            "selected": [0],
            "mode": "batch",
            "catalog_section": "movies",
            "error": "",
        }
        self.files = [
            {"index": 0, "name": "release/Movie.mp4", "size": 5, "progress": 1},
            {"index": 1, "name": "release/Other.mp4", "size": 100, "progress": 0},
        ]
        self.job["files"] = self.files
        self.queue.jobs.append(self.job)
        folder = self.queue.folder(self.job) / "download"
        folder.mkdir(parents=True)
        self.client.info.return_value = {
            "hash": self.job["hash"],
            "tags": self.job["tag"],
            "save_path": str(folder),
            "state": "stalledDL",
        }
        self.client.files.return_value = self.files
        safe_path(folder, self.files[0]["name"]).parent.mkdir()
        safe_path(folder, self.files[0]["name"]).write_bytes(b"video")

    def test_download_selected_files_then_prepare_without_telegram(self):
        prepared = Mock(return_value=None)
        self.queue.run(self.client, self.stop, Mock(), Mock(), prepared)
        self.assertEqual(self.job["status"], "prepared")
        self.client.priorities.assert_any_call(self.job["hash"], [0, 1], 0)
        self.client.priorities.assert_any_call(self.job["hash"], [0], 1)
        self.client.stop.assert_called_with(self.job["hash"])
        prepared.assert_called_once()
        stage = Path(self.job["import_folder"])
        self.assertEqual([p.name for p in stage.iterdir()], ["Movie.mp4"])
        self.assertEqual((stage / "Movie.mp4").read_bytes(), b"video")
        self.assertEqual(used_bytes(stage), 5)
        # Successful preparation is durable and not repeated after restart.
        restored = TorrentQueue(self.temp.name, reserve_gib=0)
        restored.run(self.client, self.stop, Mock(), Mock(), prepared)
        prepared.assert_called_once()

    def test_space_limit_blocks_start(self):
        self.queue.quota = 1
        prepare = Mock()
        self.queue.run(self.client, self.stop, Mock(), Mock(), prepare)
        self.client.start.assert_not_called()
        prepare.assert_not_called()
        self.assertEqual(self.job["status"], "error")

    def test_automatic_job_does_not_run_other_queued_downloads(self):
        other = {**self.job, 'id': 'c' * 32, 'title': 'Unrelated', 'status': 'queued'}
        self.queue.jobs.insert(0, other)
        prepared = Mock(return_value=None)
        self.queue.run(self.client, self.stop, Mock(), Mock(), prepared, job_ids={self.job['id']})
        self.assertEqual(self.job['status'], 'prepared')
        self.assertEqual(other['status'], 'queued')
        prepared.assert_called_once()

    def test_reprepare_uses_downloaded_sources_without_redownload_or_deleting_manifest(self):
        self.queue.download(self.client, self.job, self.stop, Mock(), Mock())
        old_manifest = {'root': 'previous-ready', 'files': [{'path': 'original.mp4'}]}
        self.job.update(status='prepared', manifest=old_manifest)
        self.queue.save()
        self.client.start.reset_mock()
        folder = self.queue.reprepare(self.job)
        self.assertEqual(self.job['manifest'], old_manifest)
        self.assertTrue((folder / 'Movie.mp4').exists())
        updated = {'root': 'new-ready', 'files': [{'path': '360p/Movie.mp4'}]}
        prepare = Mock(return_value=updated)
        self.queue.run(self.client, self.stop, Mock(), Mock(), prepare)
        self.client.start.assert_not_called()
        self.assertEqual(self.job['manifest'], updated)
        self.assertEqual(self.job['status'], 'prepared')
        self.assertEqual((folder / 'Movie.mp4').read_bytes(), b'video')

    def test_reprepare_rejects_active_foreign_and_missing_source_jobs(self):
        for status in ('downloading', 'preparing', 'cancelled'):
            self.job['status'] = status
            with self.assertRaises(ValueError):
                self.queue.reprepare(self.job)
        self.job['status'] = 'prepared'
        self.job['import_folder'] = str(Path(self.temp.name) / 'unrelated')
        with self.assertRaises(ValueError):
            self.queue.reprepare(self.job)
        self.job['import_folder'] = str(self.queue.folder(self.job) / 'import')
        with self.assertRaises(ValueError):
            self.queue.reprepare(self.job)
        with self.assertRaises(ValueError):
            self.queue.reprepare({**self.job, 'id': 'f' * 32})

    def test_disk_stop_keeps_reason_when_encoder_raises(self):
        self.queue.download(self.client, self.job, self.stop, Mock(), Mock())
        report = Mock()

        def encode(job, folder):
            self.assertTrue(self.stop.wait(3))
            raise InterruptedError("Generic encoder interruption")

        with patch.object(self.queue, "check_space", side_effect=[None, ValueError("Disk reserve reached")]):
            self.queue.run(self.client, self.stop, report, Mock(), encode)
        self.assertEqual(self.job["status"], "paused")
        self.assertEqual(self.job["error"], "Disk reserve reached")
        self.assertIn("Disk reserve reached", report.call_args.args[0])
        self.assertEqual(self.stop.preparation_error, "")

    def test_disk_scan_tolerates_atomic_journal_rename(self):
        root = Path(self.temp.name)
        stable = root / 'stable.mp4'
        stable.write_bytes(b'video')
        transient = root / '.kinotime-batch.tmp'
        transient.write_bytes(b'journal')
        original = Path.stat

        def concurrent_stat(path, *args, **kwargs):
            if path == transient:
                raise FileNotFoundError(str(path))
            return original(path, *args, **kwargs)

        with patch('tools.torrent_queue.os.walk', return_value=[(str(root), [], [stable.name, transient.name])]), patch.object(Path, 'stat', concurrent_stat):
            self.assertEqual(used_bytes(root), 5)

    def test_disk_scan_does_not_hide_permission_errors(self):
        root = Path(self.temp.name)
        with patch('tools.torrent_queue.os.walk', return_value=[(str(root), [], ['video.mp4'])]), patch.object(Path, 'stat', side_effect=PermissionError('Denied')):
            with self.assertRaises(PermissionError):
                used_bytes(root)

    def test_stop_prevents_processing(self):
        self.stop.set()
        self.queue.run(self.client, self.stop, Mock(), Mock(), Mock())
        self.client.start.assert_not_called()

    def test_downloaded_job_retries_preparation_without_redownload(self):
        self.queue.download(self.client, self.job, self.stop, Mock(), Mock())
        self.client.start.reset_mock()
        callback = Mock(side_effect=ValueError("conversion failed"))
        self.queue.run(self.client, self.stop, Mock(), Mock(), callback)
        self.assertEqual(self.job["status"], "error")
        callback.side_effect = None
        callback.return_value = {"version": 1}
        self.queue.run(self.client, self.stop, Mock(), Mock(), callback)
        self.client.start.assert_not_called()
        self.assertEqual(self.job["status"], "prepared")

    def test_disk_reserve_blocks_before_download(self):
        self.queue.reserve = 20 * GIB
        with patch("tools.torrent_queue.shutil.disk_usage", return_value=Mock(free=10 * GIB)):
            with self.assertRaisesRegex(ValueError, "Недостаточно"):
                self.queue.download(self.client, self.job, self.stop, Mock(), Mock())
        self.client.start.assert_not_called()

    def test_ownership_and_external_paths_are_rejected(self):
        self.client.info.return_value["tags"] = "personal-torrent"
        with self.assertRaisesRegex(ValueError, "не принадлежит"):
            self.queue.download(self.client, self.job, self.stop, Mock(), Mock())
        self.client.start.assert_not_called()
        for relative in (
            "../secret.mp4",
            "/secret.mp4",
            "C:/secret.mp4",
            "video.mp4:stream",
            "a\\..\\secret.mp4",
        ):
            with self.subTest(relative=relative), self.assertRaises(ValueError):
                safe_path(self.temp.name, relative)

    def test_selection_cannot_mutate_existing_import(self):
        self.queue.download(self.client, self.job, self.stop, Mock(), Mock())
        with self.assertRaises(ValueError):
            self.queue.select(self.job, [1], mode="batch", section="movies")

    def test_resumed_in_progress_job_becomes_paused(self):
        self.job["status"] = "downloading"
        self.queue.save()
        recovered = TorrentQueue(self.temp.name, reserve_gib=0)
        self.assertEqual(recovered.jobs[0]["status"], "paused")

    def test_unselected_files_never_enter_import(self):
        folder = self.queue.folder(self.job) / "download"
        safe_path(folder, self.files[1]["name"]).write_bytes(b"not selected")
        destination = self.queue.stage_files(self.job, self.files)
        self.assertFalse((destination / "Other.mp4").exists())

    def test_series_keeps_paths_and_requires_series_code(self):
        with self.assertRaises(ValueError):
            self.queue.select(self.job, [0], mode="season", section="series")
        self.queue.select(self.job, [0], mode="season", section="series", movie_number=3, season_number=2)
        folder = self.queue.stage_files(self.job, self.files)
        self.assertTrue((folder / "release" / "Movie.mp4").exists())

    def test_queue_lock_rejects_second_writer(self):
        with queue_lock(self.temp.name):
            with self.assertRaises(ValueError), queue_lock(self.temp.name):
                pass

    def test_magnet_metadata_does_not_start_content(self):
        self.client.query.return_value = [{"hash": "c" * 40, "name": "New movie"}]

        def info(_):
            job = self.queue.jobs[-1]
            return {"tags": job["tag"], "save_path": str(self.queue.folder(job) / "download")}

        self.client.info.side_effect = info
        job = self.queue.add(self.client, "magnet:?xt=urn:btih:" + "c" * 40, self.stop, Mock())
        self.client.start.assert_not_called()
        self.client.stop.assert_any_call(job["hash"])
        self.assertEqual(job["status"], "choose_files")


class QBitHTTPTests(unittest.TestCase):
    def setUp(self):
        self.calls = []
        calls = self.calls
        owner = self
        self.version = b'v5.1.4'
        self.expected_key = ''
        self.deny_status = 403

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                calls.append(("GET", self.path, None, dict(self.headers)))
                if owner.expected_key and self.headers.get('Authorization') != 'Bearer ' + owner.expected_key:
                    self.send_response(owner.deny_status)
                    self.end_headers()
                    return
                body = owner.version if self.path.endswith("app/version") else b"[]"
                self.send_response(200)
                self.end_headers()
                self.wfile.write(body)

            def do_POST(self):
                data = self.rfile.read(int(self.headers["Content-Length"]))
                calls.append(("POST", self.path, data, dict(self.headers)))
                if owner.expected_key and self.headers.get('Authorization') != 'Bearer ' + owner.expected_key:
                    self.send_response(owner.deny_status)
                    self.end_headers()
                    return
                self.send_response(200)
                self.send_header("Set-Cookie", "SID=test; Path=/")
                self.end_headers()
                self.wfile.write(b"Ok.")

            def log_message(self, *args):
                pass

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.client = QBitClient(f"http://127.0.0.1:{self.server.server_port}", "admin", "password")

    def test_authenticated_api_and_stopped_magnet_add(self):
        self.assertEqual(self.client.login(), "v5.1.4")
        self.client.add("magnet:?xt=urn:btih:" + "a" * 40, "C:/downloads", "kinotime-test")
        method, endpoint, body, headers = self.calls[-1]
        fields = parse_qs(body.decode())
        self.assertEqual(method, "POST")
        self.assertEqual(fields["stopped"], ["true"])
        self.assertEqual(fields["stopCondition"], ["MetadataReceived"])
        self.assertEqual(headers["Cookie"], "SID=test")
        self.client.files("a" * 40)
        self.assertEqual(self.calls[-1][0], "GET")
        self.assertEqual(parse_qs(urlsplit(self.calls[-1][1]).query)["hash"], ["a" * 40])

    def test_torrent_upload_is_multipart(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "test.torrent"
            path.write_bytes(b"d4:infodee")
            self.client.add(str(path), folder, "kinotime-test")
        body, headers = self.calls[-1][2:]
        self.assertIn("multipart/form-data", headers["Content-Type"])
        self.assertIn(b'name="torrents"', body)
        self.assertIn(b"d4:infodee", body)

    def test_remote_api_urls_are_rejected(self):
        for url in (
            "http://example.com:8080",
            "http://127.0.0.1:8080@evil.com",
            "http://127.0.0.1:8080/api",
            "https://127.0.0.1:8080",
        ):
            with self.assertRaises(ValueError):
                QBitClient(url)

    def test_api_key_authenticates_get_post_and_multipart_without_login(self):
        self.version = b'v5.2.4'
        self.expected_key = 'qbt_' + 'A1' * 14
        client = QBitClient(self.client.url, api_key=self.expected_key)
        self.assertEqual(client.login(), 'v5.2.4')
        client.files('a' * 40)
        client.stop('a' * 40)
        client.add('magnet:?xt=urn:btih:' + 'a' * 40, 'C:/downloads', 'test')
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'test.torrent'
            path.write_bytes(b'd4:infodee')
            client.add(str(path), folder, 'test')
        self.assertEqual(len(self.calls), 5)
        for method, url, body, headers in self.calls:
            self.assertNotIn('/auth/', url)
            self.assertEqual(headers['Authorization'], 'Bearer ' + self.expected_key)
            self.assertNotIn(self.expected_key, url)
            if body:
                self.assertNotIn(self.expected_key.encode(), body)
        with self.assertRaises(ValueError):
            client.request('auth/login', {})

    def test_wrong_api_key_and_unauthorized_get_have_specific_errors(self):
        self.expected_key = 'qbt_' + 'A' * 28
        client = QBitClient(self.client.url, api_key='qbt_' + 'B' * 28)
        for status in (401, 403):
            self.deny_status = status
            with self.assertRaisesRegex(ValueError, 'не принял API-ключ'):
                client.login()
            with self.assertRaisesRegex(ValueError, 'не принял API-ключ'):
                client.files('a' * 40)

    def test_invalid_api_keys_do_not_make_requests(self):
        for key in ('qbt_short', 'a' * 32, 'qbt_' + 'a' * 27 + '\nX', 'qbt_' + 'a' * 29):
            with self.subTest(key=key), self.assertRaises(ValueError):
                QBitClient(self.client.url, api_key=key)
        self.assertEqual(self.calls, [])

    def test_api_key_requires_supported_qbittorrent_version(self):
        client = QBitClient(self.client.url, api_key='qbt_' + 'A' * 28)
        with self.assertRaisesRegex(ValueError, '5.2'):
            client.login()

    def test_password_whitespace_is_preserved(self):
        client = QBitClient(self.client.url, 'admin', ' password ')
        client.login()
        self.assertEqual(parse_qs(self.calls[0][2].decode())['password'], [' password '])


if __name__ == "__main__":
    unittest.main()
