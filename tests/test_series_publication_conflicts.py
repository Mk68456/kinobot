"""Separate seasons/voices publish; true repeats identify rows and remain atomic."""
from tests.age_fixtures import reviewed_import
import contextlib
import io
import json
import unittest
from types import SimpleNamespace

from database.connection import database
from database.migrations import migrate
from database.episode_variants import variants
from webpanel import repository, server
from webpanel.series_validation import SeriesVariantConflict


class SeriesPublicationConflictTests(unittest.TestCase):
    def setUp(self):
        database.open(':memory:')
        self.addCleanup(database.close)
        with contextlib.redirect_stdout(io.StringIO()):
            migrate()
        self.serial = 0

    def draft(self, specs, **metadata):
        self.serial += 1
        current = reviewed_import(99, {'version': 1, 'root': f'D:/Series-{self.serial}', 'title': 'Series',
            'files': [{'path': f'file-{index}.mp4', 'size': 20 + index, 'mtime': '1'}
                      for index in range(len(specs))]})
        body = current['body']
        body.update(content_type='series', catalog_section='series', season_number=None, season_name='')
        body.update(metadata)
        for index, (item, spec) in enumerate(zip(body['items'], specs)):
            item.update(name='Серия 7 The Magical Red Jewel', quality='360p', voice='Любительская (RUS)', season_number=2)
            item.update(spec)
            database.execute("UPDATE WebAssets SET status='ready',file_id=?,file_type='video' WHERE id=?",
                             (f'video-{current["id"]}-{index}', item['asset_id']))
        return repository.save_draft(99, body, current['id'])

    def test_screenshot_voices_are_two_choices_of_one_episode(self):
        current = self.draft([{}, {'voice': 'Оригинальный дубляж (ENG)'}])
        code = repository.publish(current['id'], 99)
        season, number = database.execute('SELECT id,season_number FROM MovieCategories WHERE movie_number=?', (code,)).fetchone()
        self.assertEqual(number, 2)
        episodes = database.execute('SELECT id FROM MovieSubcategories WHERE category_id=?', (season,)).fetchall()
        self.assertEqual(len(episodes), 1)
        self.assertEqual([row[1] for row in variants(episodes[0][0])],
                         ['360p · Любительская (RUS)', '360p · Оригинальный дубляж (ENG)'])

    def test_same_episode_quality_voice_in_three_seasons_stays_separate(self):
        current = self.draft([{'season_number': number} for number in (1, 2, 3)])
        code = repository.publish(current['id'], 99)
        self.assertEqual(database.execute('SELECT season_number FROM MovieCategories WHERE movie_number=? ORDER BY season_number',
                                         (code,)).fetchall(), [(1,), (2,), (3,)])
        self.assertEqual(database.execute('SELECT count(*) FROM MovieSubcategories').fetchone()[0], 3)
        self.assertEqual(database.execute('SELECT count(*) FROM EpisodeVariants').fetchone()[0], 3)

    def test_common_season_one_and_per_file_two_and_three(self):
        current = self.draft([{'season_number': None}, {'season_number': 2}, {'season_number': 3}], season_number=1)
        code = repository.publish(current['id'], 99)
        self.assertEqual(database.execute('SELECT season_number FROM MovieCategories WHERE movie_number=? ORDER BY season_number',
                                         (code,)).fetchall(), [(1,), (2,), (3,)])

    def test_duplicate_far_apart_in_280_rows_reports_both_without_publication(self):
        specs = [{'name': f'Серия {index + 1}'} for index in range(280)]
        specs[270]['name'] = specs[16]['name']
        current = self.draft(specs)
        with self.assertRaises(SeriesVariantConflict) as raised:
            repository.publish(current['id'], 99)
        conflict = raised.exception.conflicts[0]
        self.assertEqual(conflict['rows'], [17, 271])
        self.assertEqual(conflict['asset_ids'], [current['body']['items'][i]['asset_id'] for i in (16, 270)])
        self.assertFalse(conflict['existing'])
        self.assertIn('17, 271', str(raised.exception))
        self.assertIn('Серия 17', str(raised.exception))
        self.assertEqual(database.execute('SELECT count(*) FROM Movies').fetchone()[0], 0)
        self.assertEqual(repository.draft(current['id'], 99)['status'], 'draft')
        self.assertEqual(database.execute('SELECT count(*) FROM WebAssets').fetchone()[0], 280)

    def test_multiple_conflicts_all_return_and_names_use_casefold(self):
        current = self.draft([{'name': 'Серия 1'}, {'name': 'серия 1'}, {'name': 'Серия 2'}, {'name': 'Серия 2'}])
        with self.assertRaises(SeriesVariantConflict) as raised:
            repository.publish(current['id'], 99)
        self.assertEqual([group['rows'] for group in raised.exception.conflicts], [[1, 2], [3, 4]])

    def test_existing_variant_is_reported_and_other_new_episode_is_not_partially_added(self):
        first = self.draft([{}])
        code = repository.publish(first['id'], 99)
        second = self.draft([{'name': 'Новая серия'}, {}], movie_number=code)
        with self.assertRaises(SeriesVariantConflict) as raised:
            repository.publish(second['id'], 99)
        self.assertEqual(raised.exception.conflicts[0]['rows'], [2])
        self.assertTrue(raised.exception.conflicts[0]['existing'])
        self.assertIn('уже опубликованным', str(raised.exception))
        self.assertEqual(database.execute('SELECT count(*) FROM MovieSubcategories').fetchone()[0], 1)
        self.assertEqual(repository.draft(second['id'], 99)['status'], 'draft')

    def test_changing_the_wrong_voice_allows_resuming_same_draft(self):
        current = self.draft([{}, {}])
        with self.assertRaises(SeriesVariantConflict):
            repository.publish(current['id'], 99)
        current['body']['items'][1]['voice'] = 'Оригинальный дубляж (ENG)'
        repository.save_draft(99, current['body'], current['id'])
        code = repository.publish(current['id'], 99)
        self.assertEqual(repository.publish(current['id'], 99), code)
        self.assertEqual(database.execute('SELECT count(*) FROM EpisodeVariants').fetchone()[0], 2)

    def test_existing_category_and_new_qualities_are_supported(self):
        first = self.draft([{}])
        code = repository.publish(first['id'], 99)
        category = database.execute('SELECT id FROM MovieCategories WHERE movie_number=?', (code,)).fetchone()[0]
        second = self.draft([{'season_number': None}], movie_number=code, category_id=category)
        with self.assertRaises(SeriesVariantConflict) as raised:
            repository.publish(second['id'], 99)
        self.assertTrue(raised.exception.conflicts[0]['existing'])
        second['body']['items'][0]['quality'] = '720p'
        repository.save_draft(99, second['body'], second['id'])
        repository.publish(second['id'], 99)
        self.assertEqual(database.execute('SELECT count(*) FROM EpisodeVariants').fetchone()[0], 2)


class ConflictAPITests(unittest.IsolatedAsyncioTestCase):
    async def test_error_includes_structured_rows_and_asset_ids(self):
        conflict = {'season': 'Сезон 2', 'name': 'Серия 7', 'quality': '360p', 'voice': 'DUB',
                    'rows': [17, 271], 'asset_ids': ['a' * 32, 'b' * 32], 'existing': False}
        async def handler(request):
            raise SeriesVariantConflict([conflict])
        response = await server.errors(SimpleNamespace(path='/api/drafts/test/publish'), handler)
        self.assertEqual(response.status, 400)
        body = json.loads(response.text)
        self.assertEqual(body['conflicts'], [conflict])
        self.assertIn('17, 271', body['error'])
        self.assertEqual(response.headers['Cache-Control'], 'no-store')
