import contextlib
import io
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from database.connection import database
from database.migrations import migrate, MIGRATIONS
from database import roles
from database.people import add_user
from database.movies import add_new_movie, delete_movie_by_numb
from database.categories import add_movie_category, add_movie_subcategory
from database.torrents import add_movie_torrent
from database.movie_queries import get_movies_page
from services.catalog import save_movie_draft


class DatabaseTests(unittest.TestCase):
    def setUp(self):
        database.open(":memory:")
        with contextlib.redirect_stdout(io.StringIO()):
            migrate()

    def tearDown(self):
        database.close()

    def test_empty_database_and_repeat_migration(self):
        migrate()
        names = {r[0] for r in database.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertTrue(
            {
                "Users",
                "Movies",
                "channels_info",
                "Roles",
                "UserRoles",
                "BroadcastJobs",
                "MovieCategories",
                "MovieTorrents",
            }
            <= names
        )
        self.assertEqual(database.execute("PRAGMA user_version").fetchone()[0], len(MIGRATIONS))

    def test_upgrade_legacy_database_keeps_data_and_backs_up(self):
        database.close()
        with tempfile.TemporaryDirectory() as folder:
            database.open(Path(folder) / "legacy.sqlite")
            database.execute("CREATE TABLE Users(id INTEGER)")
            database.execute("CREATE TABLE Movies(movie_title INTEGER,movie_number INTEGER)")
            database.execute("CREATE TABLE channels_info(title TEXT,link TEXT,cod INTEGER)")
            database.execute("INSERT INTO Users VALUES (123)")
            database.execute("INSERT INTO Movies VALUES ('Старый фильм',42)")
            database.commit()
            backup_dir = Path(folder) / "backups"
            with contextlib.redirect_stdout(io.StringIO()):
                migrate(backup_dir)
            self.assertEqual(database.execute("SELECT id,is_active FROM Users").fetchone(), (123, 1))
            self.assertEqual(add_new_movie("Новый"), 43)
            backups = list(backup_dir.glob("*.sqlite"))
            self.assertEqual(len(backups), 1)
            with contextlib.closing(sqlite3.connect(backups[0])) as original:
                self.assertEqual(original.execute("SELECT * FROM Movies").fetchone(), ("Старый фильм", 42))
            database.close()

    def test_role_lifecycle_immediately_changes_access(self):
        add_user(123)
        role = roles.save_role("VIP")
        roles.assign_role(123, role)
        self.assertFalse(roles.bypasses_subscription(123))
        roles.set_bypass(role, True)
        self.assertTrue(roles.bypasses_subscription(123))
        roles.save_role("Премиум", role)
        self.assertEqual(roles.user_role(123)[1], "Премиум")
        roles.assign_role(123, None)
        self.assertFalse(roles.bypasses_subscription(123))
        roles.assign_role(123, role)
        roles.delete_role(role)
        self.assertIsNone(roles.user_role(123))
        self.assertFalse(roles.bypasses_subscription(123))

    def test_role_validation_and_foreign_key(self):
        role = roles.save_role("VIP")
        with self.assertRaises(sqlite3.IntegrityError):
            roles.save_role("VIP")
        with self.assertRaises(ValueError):
            roles.save_role(" ")
        with self.assertRaises(ValueError):
            roles.assign_role(999, role)
        add_user(123)
        with self.assertRaises(ValueError):
            roles.assign_role(123, 999)

    def test_movie_delete_is_complete_and_code_not_reused(self):
        movie = add_new_movie("Фильм")
        category = add_movie_category(movie, "Озвучка")
        add_movie_subcategory(category, "1080p", "fake", "document")
        add_movie_torrent(movie, "torrent", "fake")
        delete_movie_by_numb(movie)
        for table in ("Movies", "MovieCategories", "MovieSubcategories", "MovieTorrents"):
            self.assertEqual(database.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0], 0)
        self.assertGreater(add_new_movie("Другой"), movie)

    def test_movie_delete_rolls_back_all_children_on_failure(self):
        movie = add_new_movie("Фильм")
        category = add_movie_category(movie, "Озвучка")
        add_movie_subcategory(category, "1080p", "fake", "document")
        add_movie_torrent(movie, "torrent", "fake")
        database.execute(
            "CREATE TRIGGER reject_delete BEFORE DELETE ON Movies BEGIN SELECT RAISE(ABORT,'test'); END"
        )
        database.commit()
        with self.assertRaises(sqlite3.IntegrityError):
            delete_movie_by_numb(movie)
        for table in ("Movies", "MovieCategories", "MovieSubcategories", "MovieTorrents"):
            self.assertEqual(database.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0], 1)

    def test_movie_draft_is_atomic(self):
        draft = {
            "movie_title": "Фильм",
            "categories": [
                {
                    "name": "Сезон 1",
                    "subcategories": [{"name": "Серия 1", "file_id": "fake", "file_type": "video"}],
                }
            ],
        }
        with patch("services.catalog.add_movie_subcategory", side_effect=RuntimeError("test")):
            with self.assertRaises(RuntimeError):
                save_movie_draft(draft)
        self.assertEqual(database.execute("SELECT COUNT(*) FROM Movies").fetchone()[0], 0)
        self.assertEqual(database.execute("SELECT COUNT(*) FROM MovieCategories").fetchone()[0], 0)

    def test_catalog_pagination_filters_before_limiting(self):
        for i in range(13):
            add_new_movie(str(i), content_type="series" if i % 2 else "movie")
        first, more = get_movies_page(0, "movie")
        second, last_more = get_movies_page(1, "movie")
        self.assertEqual(len(first), 5)
        self.assertEqual(len(second), 2)
        self.assertTrue(more)
        self.assertFalse(last_more)
        self.assertFalse(set(first) & set(second))

    def test_user_reactivation_preserves_role(self):
        add_user(123, "old")
        role = roles.save_role("VIP")
        roles.assign_role(123, role)
        database.execute("UPDATE Users SET is_active=0 WHERE id=123")
        database.commit()
        add_user(123, "new")
        self.assertEqual(
            database.execute("SELECT username,is_active FROM Users WHERE id=123").fetchone(), ("new", 1)
        )
        self.assertEqual(roles.user_role(123)[0], role)
