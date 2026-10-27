import contextlib
import io
import unittest
from database.connection import database
from database.migrations import migrate
from database.movies import add_new_movie
from database.categories import add_movie_category, get_subcategories_by_category
from database.episode_variants import add_series_file, variants
from webpanel import catalog_merge


class CatalogMergeTests(unittest.TestCase):
    def setUp(self):
        database.open(':memory:')
        self.addCleanup(database.close)
        with contextlib.redirect_stdout(io.StringIO()):
            migrate()
        self.target = add_new_movie('Main',content_type='series')
        self.source = add_new_movie('Extra',content_type='series')

    def episode(self,code,season,quality='720p',file='video',name='Серия 1'):
        category = add_movie_category(code,f'Сезон {season}',season)
        with database.transaction():
            episode = add_series_file(category,{'name':name,'quality':quality,'voice':'DUB'}, {'file_id':file,'file_type':'video'})
        return category,episode

    def test_distinct_seasons_keep_episode_ids_and_progress(self):
        self.episode(self.target,1)
        category,episode = self.episode(self.source,2)
        database.execute('INSERT INTO EpisodeProgress(user_id,episode_id) VALUES (99,?)',(episode,))
        preview = catalog_merge.preview(self.source,self.target)
        self.assertTrue(preview['can_merge'])
        catalog_merge.merge(self.source,self.target,preview['token'])
        self.assertEqual(database.execute('SELECT movie_number FROM MovieCategories WHERE id=?',(category,)).fetchone()[0],self.target)
        self.assertIsNotNone(database.execute('SELECT 1 FROM EpisodeProgress WHERE episode_id=?',(episode,)).fetchone())
        self.assertIsNone(database.execute('SELECT 1 FROM Movies WHERE movie_number=?',(self.source,)).fetchone())

    def test_same_episode_merges_qualities_and_personal_marks(self):
        category,target_episode = self.episode(self.target,1,'1080p','full')
        _,source_episode = self.episode(self.source,1,'720p','small')
        database.execute('INSERT INTO EpisodeProgress(user_id,episode_id) VALUES (99,?)',(source_episode,))
        preview = catalog_merge.preview(self.source,self.target)
        catalog_merge.merge(self.source,self.target,preview['token'])
        self.assertEqual(len(get_subcategories_by_category(category)),1)
        self.assertEqual({v[2] for v in variants(target_episode)},{'full','small'})
        self.assertIsNotNone(database.execute('SELECT 1 FROM EpisodeProgress WHERE episode_id=? AND user_id=99',(target_episode,)).fetchone())

    def test_different_files_for_same_quality_block_without_changes(self):
        self.episode(self.target,1,'720p','one')
        self.episode(self.source,1,'720p','two')
        preview = catalog_merge.preview(self.source,self.target)
        self.assertFalse(preview['can_merge'])
        with self.assertRaises(ValueError):
            catalog_merge.merge(self.source,self.target,preview['token'])
        self.assertEqual(database.execute('SELECT count(*) FROM Movies').fetchone()[0],2)
        self.assertEqual(database.execute('SELECT count(*) FROM MovieSubcategories').fetchone()[0],2)

    def test_stale_preview_blocks_merge(self):
        self.episode(self.source,1)
        preview = catalog_merge.preview(self.source,self.target)
        database.execute('UPDATE Movies SET movie_title=? WHERE movie_number=?',('Renamed',self.source))
        with self.assertRaisesRegex(ValueError,'изменились'):
            catalog_merge.merge(self.source,self.target,preview['token'])
        self.assertEqual(database.execute('SELECT count(*) FROM Movies').fetchone()[0],2)

    def test_same_file_quality_is_deduplicated(self):
        _,episode = self.episode(self.target,1,'720p','same')
        self.episode(self.source,1,'720p','same')
        preview = catalog_merge.preview(self.source,self.target)
        catalog_merge.merge(self.source,self.target,preview['token'])
        self.assertEqual(len(variants(episode)),1)
