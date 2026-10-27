from tests.age_fixtures import reviewed_import
import unittest
import contextlib
import io
from database.connection import database
from database.migrations import migrate
from database.categories import get_subcategories_by_category
from database.episode_variants import variants
from webpanel import repository


class SeriesQualityTests(unittest.TestCase):
    def setUp(self):
        database.open(':memory:')
        self.addCleanup(database.close)
        with contextlib.redirect_stdout(io.StringIO()):
            migrate()

    def tearDown(self):
        database.close()

    def draft(self, admin=99, title='Series'):
        current = reviewed_import(admin, {'version': 1, 'root': 'D:/'+title, 'title': title,
            'files': [{'path': 'One.mp4', 'size': 10, 'mtime': '1'},
                      {'path': 'Two.mp4', 'size': 20, 'mtime': '1'}]})
        for i, item in enumerate(current['body']['items']):
            database.execute("UPDATE WebAssets SET status='ready',file_id=?,file_type='video' WHERE id=?",
                             ('video'+str(i)+title, item['asset_id']))
        body = current['body']
        body.update(content_type='series', catalog_section='series', season_name='Сезон 1', season_number=1)
        for item, quality in zip(body['items'], ['1080p', '720p']):
            item.update(name='Серия 1', quality=quality, voice='DUB')
        return repository.save_draft(admin, body, current['id'])

    def test_two_qualities_create_one_episode_and_cascade_on_delete(self):
        current = self.draft()
        number = repository.publish(current['id'], 99)
        season = database.execute('SELECT id FROM MovieCategories WHERE movie_number=?', (number,)).fetchone()[0]
        episodes = get_subcategories_by_category(season)
        self.assertEqual(len(episodes), 1)
        self.assertEqual([v[1] for v in variants(episodes[0][0])], ['1080p · DUB', '720p · DUB'])
        database.execute('DELETE FROM MovieSubcategories WHERE id=?', (episodes[0][0],))
        self.assertEqual(database.execute('SELECT count(*) FROM EpisodeVariants').fetchone()[0], 0)

    def test_duplicate_quality_rolls_back_publication(self):
        current = self.draft()
        body = current['body']
        body['items'][1]['quality'] = '1080p'
        repository.save_draft(99, body, current['id'])
        with self.assertRaises(ValueError):
            repository.publish(current['id'], 99)
        self.assertEqual(database.execute('SELECT count(*) FROM Movies').fetchone()[0], 0)
        self.assertEqual(database.execute('SELECT count(*) FROM EpisodeVariants').fetchone()[0], 0)

    def test_reject_all_is_scoped_and_does_not_delete_assets(self):
        first = self.draft(title='First')
        self.draft(admin=88, title='Other')
        repository.review(first['id'], 99, 'revision', 'Fix')
        count = database.execute('SELECT count(*) FROM WebAssets').fetchone()[0]
        self.assertEqual(repository.reject_all(99, 1), 1)
        self.assertEqual(repository.draft(first['id'], 99)['status'], 'rejected')
        self.assertEqual(repository.review_queue(88)['total'], 1)
        self.assertEqual(database.execute('SELECT count(*) FROM WebAssets').fetchone()[0], count)

    def test_reject_all_requires_current_count(self):
        current = self.draft()
        with self.assertRaises(ValueError):
            repository.reject_all(99, 2)
        self.assertEqual(repository.draft(current['id'], 99)['status'], 'draft')

    def test_quality_can_be_added_to_existing_season(self):
        current = self.draft(title='Existing')
        number = repository.publish(current['id'], 99)
        season = database.execute('SELECT id FROM MovieCategories WHERE movie_number=?', (number,)).fetchone()[0]
        new = self.draft(title='New')
        body = new['body']
        body.update(movie_number=number, category_id=season)
        body['items'] = [body['items'][0]]
        body['items'][0]['quality'] = '480p'
        repository.save_draft(99, body, new['id'])
        repository.publish(new['id'], 99)
        episodes = get_subcategories_by_category(season)
        self.assertEqual(len(episodes), 1)
        self.assertEqual(len(variants(episodes[0][0])), 3)

    def test_published_draft_is_not_rejected(self):
        current = self.draft()
        repository.publish(current['id'], 99)
        other = self.draft(title='Pending')
        self.assertEqual(repository.reject_all(99, 1), 1)
        self.assertEqual(repository.draft(current['id'], 99)['status'], 'published')
        self.assertEqual(repository.draft(other['id'], 99)['status'], 'rejected')

    def test_episode_number_order_is_shared_by_bot_and_web(self):
        from database.movies import add_new_movie
        from database.categories import add_movie_category, add_movie_subcategory
        code = add_new_movie('Series', content_type='series')
        season = add_movie_category(code, 'Season 1', 1)
        for name in ['Серия 40: Special', 'Серия 1: First', 'Серия 10: Ten', 'Серия 2: Two']:
            add_movie_subcategory(season, name, 'file', 'video')
        expected = ['Серия 1: First', 'Серия 2: Two', 'Серия 10: Ten', 'Серия 40: Special']
        self.assertEqual([r[1] for r in get_subcategories_by_category(season)], expected)
        self.assertEqual([f['name'] for f in repository.catalog_folders(code)['folders'][0]['files']], expected)

    def test_movie_quality_order_is_unchanged(self):
        from database.movies import add_new_movie
        from database.categories import add_movie_category, add_movie_subcategory
        code = add_new_movie('Movie')
        category = add_movie_category(code, 'DUB')
        for name in ['1080p', '720p', '480p']:
            add_movie_subcategory(category, name, 'file', 'video')
        self.assertEqual([r[1] for r in get_subcategories_by_category(category)], ['1080p', '720p', '480p'])
