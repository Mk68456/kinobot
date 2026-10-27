"""Age policy, persistent parental limits and safe catalog queries."""
import contextlib
import io
import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from database import age_access
from database.connection import database
from database.migrations import MIGRATIONS, migrate
from database.movies import add_new_movie
from database.movie_queries import get_movies_page
from database.search import get_movies_by_title
from database.categories import add_movie_category, add_movie_subcategory
from database.torrents import add_movie_torrent
from services.age_access import callback_allowed
from webpanel import imports, repository


def movie(rating, title='Test', kind='movie'):
    number = add_new_movie(title, content_type=kind, poster_image='poster', card_description='Secret description',
                           movie_file='file', movie_file_type='video', movie_trailer='trailer')
    database.execute('UPDATE Movies SET age_rating=? WHERE movie_number=?', (rating, number))
    database.commit()
    return number


class AgePolicyTests(unittest.TestCase):
    def setUp(self):
        database.open(':memory:')
        self.addCleanup(database.close)
        with contextlib.redirect_stdout(io.StringIO()):
            migrate()

    def test_defaults_apply_to_old_and_new_users_without_overwriting_catalog(self):
        database.execute('INSERT INTO Users(id) VALUES (123)')
        code = movie('18+')
        for user_id in (123, 456):
            self.assertEqual(age_access.preferences(user_id)['max_age'], 16)
            self.assertFalse(age_access.allowed(user_id, {'age_rating': '18+'}))
            self.assertFalse(age_access.allowed(user_id, {'age_rating': None}))
            self.assertTrue(age_access.allowed(user_id, {'age_rating': '16+'}))
        self.assertEqual(database.execute('SELECT age_rating FROM Movies WHERE movie_number=?', (code,)).fetchone()[0], '18+')

    def test_catalog_pagination_and_search_include_restricted_cards(self):
        for _ in range(8):
            movie('18+', 'Friends adult')
            movie(None, 'Friends unmarked')
        codes = [movie('6+', f'Friends family {index}') for index in range(7)]
        rows, more = get_movies_page(user_id=123)
        self.assertEqual(len(rows), 5)
        self.assertTrue(more)
        self.assertEqual(rows[0][0], 'Friends adult')
        rows, more = get_movies_page(page=4, user_id=123)
        self.assertEqual([r[1] for r in rows], codes[4:])
        self.assertFalse(more)
        self.assertEqual(len(get_movies_by_title('friends', user_id=123)), 23)

    def test_confirmation_is_saved_and_safe_mode_can_be_reenabled(self):
        age_access.confirm_adult(123)
        self.assertTrue(age_access.allowed(123, {'age_rating': '18+'}))
        self.assertTrue(age_access.allowed(123, {'age_rating': None}))
        age_access.safe_mode(123)
        self.assertFalse(age_access.allowed(123, {'age_rating': '18+'}))
        self.assertFalse(age_access.allowed(456, {'age_rating': '18+'}))

    def test_parental_pin_hash_and_confirmation_cannot_replace_existing_pin(self):
        age_access.confirm_adult(123)
        age_access.enable_parental(123, 6, '123456')
        row = database.execute('SELECT pin_hash,pin_salt FROM AgePreferences WHERE user_id=123').fetchone()
        self.assertEqual(len(row[0]), 64)
        self.assertEqual(len(row[1]), 32)
        self.assertNotIn('123456', row)
        for action in (lambda: age_access.confirm_adult(123), lambda: age_access.safe_mode(123),
                       lambda: age_access.enable_parental(123, 16, '654321')):
            with self.assertRaises(ValueError):
                action()
        self.assertEqual(age_access.preferences(123)['max_age'], 6)

    def test_only_correct_pin_changes_limit_or_disables_parental(self):
        age_access.enable_parental(123, 0, '123456')
        self.assertFalse(age_access.change_parental(123, '000000', 16)[0])
        self.assertFalse(age_access.change_parental(456, '123456', 16)[0])
        self.assertTrue(age_access.change_parental(123, '123456', 12)[0])
        self.assertEqual(age_access.preferences(123)['max_age'], 12)
        self.assertFalse(age_access.allowed(123, {'age_rating': '16+'}))
        self.assertTrue(age_access.change_parental(123, '123456')[0])
        self.assertEqual(age_access.preferences(123)['max_age'], 16)
        self.assertFalse(age_access.preferences(123)['parental'])

    def test_five_attempts_lock_even_correct_pin_until_expiration(self):
        age_access.enable_parental(123, 6, '123456')
        for _ in range(5):
            self.assertFalse(age_access.change_parental(123, '000000')[0])
        self.assertFalse(age_access.change_parental(123, '123456')[0])
        self.assertTrue(age_access.preferences(123)['parental'])
        with patch('database.age_access.time.time', return_value=time.time() + 901):
            self.assertTrue(age_access.change_parental(123, '123456')[0])

    def test_invalid_pin_and_parental_limits_are_rejected(self):
        for pin in ('', '12345', '1234567', '１２３４５６', 'abcdef', None):
            with self.subTest(pin=pin), self.assertRaises(ValueError):
                age_access.enable_parental(123, 6, pin)
        for limit in (18, True, '6', 7):
            with self.subTest(limit=limit), self.assertRaises(ValueError):
                age_access.enable_parental(123, limit, '123456')

    def test_age_callbacks_cover_all_old_and_new_movie_and_series_paths(self):
        code = movie('18+', kind='series')
        category = add_movie_category(code, 'Season 1', 1)
        episode = add_movie_subcategory(category, 'Episode 1', 'file', 'video')
        target = add_movie_subcategory(category, 'Episode 2', 'second', 'video')
        torrent = add_movie_torrent(code, 'Torrent', 'torrent')
        data = [f'{prefix}_{code}' for prefix in ('dlfile','trailer','movtorrents')]
        data += [f'movcat_{category}', f'movsub_{episode}', f'wpage:{category}:0', f'epseasons:{code}',
                 f'epquality:{episode}:0', f'epvoices:{episode}:0', f'epnext:{episode}:0:{target}',
                 f'wp:f:{code}:1:c:0', f'wp:s:{category}:1:c:0', f'wp:e:{episode}:1:m:0', f'gettorrent_{torrent}']
        for callback in data:
            with self.subTest(callback=callback):
                self.assertFalse(callback_allowed(123, callback))
        age_access.confirm_adult(123)
        self.assertTrue(all(callback_allowed(123, callback) for callback in data))

    def test_next_callback_checks_target_owner_even_when_source_is_allowed(self):
        family = movie('0+', kind='series')
        restricted = movie('18+', kind='series')
        source = add_movie_subcategory(add_movie_category(family, 'Season'), 'Episode 1', 'f', 'video')
        target = add_movie_subcategory(add_movie_category(restricted, 'Season'), 'Episode 2', 'r', 'video')
        self.assertFalse(callback_allowed(123, f'epnext:{source}:0:{target}'))

    def test_unrated_draft_can_save_but_cannot_publish_or_join_package(self):
        current = imports.create(99, {'version': 1, 'root': 'D:/Film', 'title': 'Movie',
                                      'files': [{'path': 'film.mp4', 'size': 123}]})
        asset = current['body']['items'][0]['asset_id']
        database.execute("UPDATE WebAssets SET status='ready',file_id='file' WHERE id=?", (asset,))
        database.commit()
        current = repository.draft(current['id'], 99)
        self.assertIsNone(current['body']['age_rating'])
        for action in (lambda: repository.publish(current['id'], 99),
                       lambda: repository.package_add(99, current['id'], repository.review_token(current))):
            with self.assertRaisesRegex(ValueError, 'возрастную маркировку'):
                action()
        self.assertEqual(database.execute('SELECT COUNT(*) FROM Movies').fetchone()[0], 0)
        self.assertFalse(repository.review_queue(99)['items'][0]['can_publish'])
        current = repository.save_draft(99, {**current['body'], 'age_rating': '12+'}, current['id'])
        number = repository.publish(current['id'], 99)
        self.assertEqual(database.execute('SELECT age_rating FROM Movies WHERE movie_number=?', (number,)).fetchone()[0], '12+')

    def test_default_rejects_unrecognized_ratings_and_sql_column_injection(self):
        for rating in ('', '21+', '18', 0, 'unknown'):
            self.assertFalse(age_access.allowed(123, {'age_rating': rating}))
        with self.assertRaises(ValueError):
            age_access.sql_filter(123, 'age_rating OR 1=1')


class AgePersistenceTests(unittest.TestCase):
    def test_migration_preserves_old_data_and_lockout_survives_reopening(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'test.sqlite'
            database.open(path)
            try:
                with contextlib.redirect_stdout(io.StringIO()):
                    for index, migration in enumerate(MIGRATIONS[:next(i for i, m in enumerate(MIGRATIONS)
                                                                     if m.__name__ == '_age_preferences')], 1):
                        with database.transaction():
                            migration()
                            database.execute(f'PRAGMA user_version={index}')
                database.execute('INSERT INTO Users(id) VALUES (123)')
                code = movie('18+')
                migrate(Path(directory) / 'backups')
                self.assertEqual(age_access.preferences(123)['max_age'], 16)
                age_access.enable_parental(123, 6, '123456')
                for _ in range(5):
                    age_access.change_parental(123, '000000')
                database.close()
                database.open(path)
                self.assertFalse(age_access.change_parental(123, '123456')[0])
                self.assertEqual(database.execute('SELECT movie_number FROM Movies').fetchone()[0], code)
                self.assertEqual(database.execute('SELECT id FROM Users').fetchone()[0], 123)
                self.assertTrue(list((Path(directory) / 'backups').glob('*.sqlite')))
            finally:
                database.close()


class AgeAdminTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        database.open(':memory:')
        self.addCleanup(database.close)
        with contextlib.redirect_stdout(io.StringIO()):
            migrate()

    async def test_unrated_admin_filter_is_paginated_and_retains_marked_catalog(self):
        from types import SimpleNamespace
        from webpanel.server import catalog
        unknown = movie(None, 'Unknown')
        known = movie('18+', 'Adult')
        response = await catalog(SimpleNamespace(query={'unrated': '1'}))
        self.assertEqual([r['id'] for r in json.loads(response.text)['movies']], [unknown])
        response = await catalog(SimpleNamespace(query={}))
        self.assertEqual([r['id'] for r in json.loads(response.text)['movies']], [known, unknown])
