"""Desktop season manifests must keep one show and require review before publication."""

import contextlib
import copy
import io
import unittest

from database.connection import database
from database.migrations import migrate
from database.movies import add_new_movie
from webpanel import imports, repository


class SeasonImportTests(unittest.TestCase):
    def setUp(self):
        database.open(':memory:')
        with contextlib.redirect_stdout(io.StringIO()):
            migrate()
        self.show = add_new_movie('Example (2024)', content_type='series')
        self.manifest = {'version': 1, 'root': 'D:/Shows/Season', 'content_type': 'series',
                         'movie_number': self.show, 'season_number': 1,
                         'files': [{'path': '01.mp4', 'size': 123, 'name': 'Серия 01'},
                                   {'path': '02.mp4', 'size': 124, 'name': 'Серия 02'}]}

    def tearDown(self):
        database.close()

    def ready(self, current):
        for item in current['body']['items']:
            database.execute("UPDATE WebAssets SET status='ready',file_id=?,file_type='video' WHERE id=?",
                             (item['asset_id'], item['asset_id']))
        database.commit()

    def test_two_seasons_publish_under_one_show_only_after_approval(self):
        for number in (1, 2):
            self.manifest['season_number'] = number
            current = imports.create(99, self.manifest)
            self.assertEqual(current['body']['movie_number'], self.show)
            self.assertEqual(current['body']['title'], 'Example (2024)')
            with self.assertRaises(ValueError):
                repository.publish(current['id'], 99)
            self.ready(current)
            self.assertEqual(database.execute('SELECT count(*) FROM MovieCategories').fetchone()[0], number - 1)
            self.assertEqual(repository.publish(current['id'], 99), self.show)
            self.assertEqual(imports.create(99, self.manifest)['id'], current['id'])
        self.assertEqual(database.execute('SELECT count(*) FROM Movies').fetchone()[0], 1)
        self.assertEqual(database.execute('SELECT count(*) FROM MovieSubcategories').fetchone()[0], 4)

    def test_reject_invalid_target_and_duplicate_episode_before_creating_assets(self):
        movie = add_new_movie('Movie')
        for changes in ({'movie_number': movie}, {'movie_number': 99999},
                        {'season_number': 0}, {'season_number': True}, {'content_type': 'invalid'},
                        {'files': [self.manifest['files'][0],
                                   {'path': 'other.mp4', 'size': 125, 'name': 'Серия 01'}]}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                imports.create(99, {**self.manifest, **changes})
        self.assertEqual(database.execute('SELECT count(*) FROM WebAssets').fetchone()[0], 0)

    def test_resume_keeps_admin_edits_and_rejects_changed_files(self):
        current = imports.create(99, self.manifest)
        edited = copy.deepcopy(current['body'])
        edited['items'][0]['name'] = 'Pilot'
        repository.save_draft(99, edited, current['id'])
        self.assertEqual(imports.create(99, self.manifest)['body']['items'][0]['name'], 'Pilot')
        self.manifest['files'][0]['size'] += 1
        with self.assertRaises(ValueError):
            imports.create(99, self.manifest)

    def test_existing_season_from_different_folder_cannot_be_recreated(self):
        current = imports.create(99, self.manifest)
        self.ready(current)
        repository.publish(current['id'], 99)
        self.manifest['root'] = 'D:/Other copy'
        with self.assertRaises(ValueError):
            imports.create(99, self.manifest)
        self.assertEqual(database.execute('SELECT count(*) FROM WebAssets').fetchone()[0], 2)


if __name__ == '__main__':
    unittest.main()
