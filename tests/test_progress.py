import contextlib
import io
import unittest
from database.connection import database
from database.migrations import migrate
from database.movies import add_new_movie, delete_movie_by_numb
from database.categories import add_movie_category, add_movie_subcategory, delete_subcategory, delete_category
from database import progress


class ProgressTests(unittest.TestCase):
    def setUp(self):
        database.open(":memory:")
        with contextlib.redirect_stdout(io.StringIO()):
            migrate()
        self.movie = add_new_movie("Фильм")
        self.series = add_new_movie("Сериал", content_type="series")
        self.season = add_movie_category(self.series, "Сезон 1", 1)
        self.episodes = [
            add_movie_subcategory(self.season, f"Серия {i + 1}", f"file{i}", "document") for i in range(12)
        ]

    def tearDown(self):
        database.close()

    def test_episode_marks_are_personal_and_idempotent(self):
        for _ in range(2):
            progress.set_watched(123, "e", self.episodes[0], True)
        self.assertEqual(progress.episode_marks(123, self.season), {self.episodes[0]})
        self.assertEqual(progress.episode_marks(124, self.season), set())
        self.assertEqual(progress.totals(123), (0, 1))
        progress.set_watched(123, "e", self.episodes[0], False)
        self.assertEqual(progress.totals(123), (0, 0))

    def test_season_bulk_mark_and_episode_unmark(self):
        progress.set_watched(123, "s", self.season, True)
        self.assertTrue(progress.is_watched(123, self.series, "series"))
        self.assertEqual(progress.season_progress(123, self.series)[self.season], (12, 12))
        progress.set_watched(123, "e", self.episodes[0], False)
        self.assertFalse(progress.is_watched(123, self.series, "series"))
        self.assertEqual(progress.season_progress(123, self.series)[self.season], (11, 12))
        progress.set_watched(123, "s", self.season, False)
        self.assertEqual(progress.episode_marks(123, self.season), set())

    def test_new_episode_is_not_automatically_watched(self):
        progress.set_watched(123, "s", self.season, True)
        add_movie_subcategory(self.season, "Новая серия", "new", "video")
        self.assertFalse(progress.is_watched(123, self.series, "series"))
        self.assertEqual(progress.season_progress(123, self.series)[self.season], (12, 13))

    def test_film_marks_do_not_change_another_user(self):
        progress.set_watched(123, "f", self.movie, True)
        self.assertTrue(progress.is_watched(123, self.movie, "movie"))
        self.assertFalse(progress.is_watched(124, self.movie, "movie"))
        self.assertEqual(progress.user_films(123)[0][0], self.movie)
        progress.set_watched(123, "f", self.movie, False)
        self.assertEqual(progress.user_films(123), [])

    def test_statistics_count_user_episode_pairs_and_list_exact_episodes(self):
        progress.set_watched(123, "e", self.episodes[0], True)
        progress.set_watched(123, "e", self.episodes[1], True)
        progress.set_watched(124, "e", self.episodes[0], True)
        self.assertEqual(progress.series_stats()[0][2:], (12, 3, 2))
        self.assertEqual(progress.series_stats(user_id=123)[0][2:], (12, 2, 1))
        details = progress.user_episodes(123, self.series)
        self.assertEqual([(r[0], r[1]) for r in details], [("Сезон 1", "Серия 1"), ("Сезон 1", "Серия 2")])

    def test_admin_episode_pagination_does_not_skip_rows(self):
        progress.set_watched(123, "s", self.season, True)
        page1 = progress.user_episodes(123, self.series, 0)
        page2 = progress.user_episodes(123, self.series, 1)
        self.assertEqual(len(page1), 11)
        self.assertEqual(len(page2), 2)
        self.assertEqual(page1[-1], page2[0])

    def test_deleting_episode_season_and_movie_cleans_marks(self):
        progress.set_watched(123, "s", self.season, True)
        progress.set_watched(123, "f", self.movie, True)
        delete_subcategory(self.episodes[0])
        self.assertEqual(progress.totals(123), (1, 11))
        delete_category(self.season)
        self.assertEqual(progress.totals(123), (1, 0))
        delete_movie_by_numb(self.movie)
        self.assertEqual(progress.totals(123), (0, 0))
        self.assertEqual(database.execute("PRAGMA foreign_key_check").fetchall(), [])

    def test_quality_variants_are_not_episodes_and_missing_items_are_rejected(self):
        quality = add_movie_category(self.movie, "Озвучка")
        variant = add_movie_subcategory(quality, "1080p", "file", "video")
        for kind, item in [("e", variant), ("s", quality), ("f", self.series), ("e", 999999)]:
            with self.subTest(kind=kind, item=item), self.assertRaises(ValueError):
                progress.set_watched(123, kind, item, True)
        self.assertEqual(progress.totals(), (0, 0))

    def test_migration_from_v2_preserves_roles(self):
        database.close()
        database.open(":memory:")
        from database.migrations import _initial_schema, _roles_and_jobs
        from database import roles
        from database.people import add_user

        with database.transaction(), contextlib.redirect_stdout(io.StringIO()):
            _initial_schema()
            _roles_and_jobs()
            database.execute("PRAGMA user_version=2")
        add_user(123)
        role_id = roles.save_role("VIP")
        roles.assign_role(123, role_id)
        roles.set_bypass(role_id, True)
        migrate()
        migrate()
        self.assertTrue(roles.bypasses_subscription(123))
        self.assertEqual(progress.totals(123), (0, 0))
