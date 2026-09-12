import sqlite3
import subprocess
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from tools.deploy_storage import initialize, snapshot
from tools import setup_deploy


class DeployTests(unittest.TestCase):
    def test_hash_accepts_surrounding_paste_decorations(self):
        key = "abcdef0123456789" * 2
        with patch.object(setup_deploy.getpass, "getpass", return_value=' \u200b"' + key + '"\u200b '):
            self.assertEqual(setup_deploy.read_api_hash(), key)

    def test_hash_can_retry_with_visible_paste_without_logging_secret(self):
        key = "abcdef0123456789" * 2
        with (
            patch.object(setup_deploy.getpass, "getpass", return_value="\x16"),
            patch("builtins.input", side_effect=["v", key]),
            patch("builtins.print") as output,
        ):
            self.assertEqual(setup_deploy.read_api_hash(), key)
            self.assertNotIn(key, str(output.call_args_list))

    def test_hash_does_not_repair_embedded_invalid_characters(self):
        with (
            patch.object(setup_deploy.getpass, "getpass", return_value="a" * 16 + " " + "b" * 16),
            patch("builtins.input", return_value="q"),
            patch("builtins.print"),
            self.assertRaisesRegex(ValueError, "cancelled"),
        ):
            setup_deploy.read_api_hash()

    def test_unavailable_docker_stops_before_deployment(self):
        for failure in (
            subprocess.CalledProcessError(1, ["docker", "info"]),
            subprocess.TimeoutExpired(["docker", "info"], 30),
        ):
            with (
                self.subTest(failure=type(failure).__name__),
                patch("sys.argv", ["setup"]),
                patch.object(setup_deploy.shutil, "which", return_value="docker"),
                patch.object(setup_deploy.subprocess, "check_output", side_effect=failure),
                patch.object(setup_deploy, "compose") as compose,
                self.assertRaisesRegex(ValueError, "Docker Engine is unavailable"),
            ):
                try:
                    setup_deploy.main()
                finally:
                    compose.assert_not_called()

    def test_settings_reject_compose_injection(self):
        for domain in (
            "https://kino.example.com",
            "x.com\nCOMPOSE_PROFILES=bad",
            "${SECRET}.com",
            "x.com/path",
        ):
            with self.subTest(domain=domain), self.assertRaises(ValueError):
                setup_deploy.settings_text("1234", "a" * 32, domain)
        with self.assertRaises(ValueError):
            setup_deploy.settings_text("1\nOTHER=1", "a" * 32, "")
        with self.assertRaises(ValueError):
            setup_deploy.settings_text("1234", "$" * 32, "")

    def test_https_is_optional_and_local_is_loopback(self):
        local = setup_deploy.settings_text("1234", "a" * 32, "")
        self.assertIn("WEB_PUBLIC_URL=http://127.0.0.1:8088\n", local)
        self.assertIn("COMPOSE_PROFILES=\n", local)
        remote = setup_deploy.settings_text("1234", "a" * 32, "Kino.Example.com")
        self.assertIn("WEB_PUBLIC_URL=https://kino.example.com\n", remote)
        self.assertIn("COMPOSE_PROFILES=https\n", remote)

    def test_snapshot_includes_committed_wal_and_import_never_overwrites(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source.sqlite"
            db = sqlite3.connect(source)
            try:
                db.execute("PRAGMA journal_mode=WAL")
                db.execute("CREATE TABLE Movies (title TEXT)")
                db.execute("INSERT INTO Movies VALUES ('Original')")
                db.commit()
                seed = root / "seed.sqlite"
                snapshot(source, seed)
                volume = root / "volume"
                initialize(volume, seed)
                db.execute("UPDATE Movies SET title='Changed source'")
                db.commit()
                snapshot(source, seed)
                initialize(volume, seed)
                with closing(sqlite3.connect(volume / "bot.sqlite")) as target:
                    self.assertEqual(target.execute("SELECT title FROM Movies").fetchone()[0], "Original")
                self.assertEqual(len(list((volume / "backups").glob("*.sqlite"))), 1)
            finally:
                db.close()

    def test_missing_source_does_not_create_working_database(self):
        with tempfile.TemporaryDirectory() as tmp:
            volume = Path(tmp) / "volume"
            with self.assertRaises(ValueError):
                initialize(volume, Path(tmp) / "missing.sqlite")
            self.assertFalse((volume / "bot.sqlite").exists())

    def test_update_preserves_settings_and_never_logs_out(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            env = root / ".deploy.env"
            env.write_text("saved settings", encoding="utf-8")
            marker = root / "ready"
            marker.touch()
            with (
                patch.object(setup_deploy, "ROOT", root),
                patch.object(setup_deploy, "ENV", env),
                patch.object(setup_deploy, "MARKER", marker),
                patch("sys.argv", ["setup", "--update"]),
                patch.object(setup_deploy.shutil, "which", return_value="/usr/bin/docker"),
                patch.object(setup_deploy.subprocess, "check_output", return_value="linux\n"),
                patch.object(setup_deploy, "run"),
                patch.object(setup_deploy, "compose") as compose,
                patch("builtins.input", side_effect=AssertionError("Update must not prompt")),
            ):
                setup_deploy.main()
            self.assertEqual(env.read_text(), "saved settings")
            commands = [call.args for call in compose.call_args_list]
            self.assertFalse(any("tools/logout_cloud.py" in command for command in commands))
            self.assertIn(("stop", "bot"), commands)
            self.assertIn(("up", "-d", "--wait", "--wait-timeout", "300"), commands)


if __name__ == "__main__":
    unittest.main()
