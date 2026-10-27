import contextlib
import io
import unittest
from unittest.mock import AsyncMock, patch
from database.connection import database
from database.migrations import migrate
from services import poiskkino


class PoiskkinoTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        database.open(':memory:')
        with contextlib.redirect_stdout(io.StringIO()):migrate()

    async def asyncTearDown(self):database.close()

    async def test_search_and_id_reuse_cache_refresh_is_explicit(self):
        item={'id':42,'name':'Example','year':2024,'rating':{'kp':7.5,'imdb':8},'ageRating':0,'isSeries':False}
        with patch.object(poiskkino,'fetch',AsyncMock(return_value={'docs':[item]})) as fetch:
            first=await poiskkino.lookup('Example')
            self.assertEqual(first['items'][0]['age_rating'],'0+')
            await poiskkino.lookup(' example ')
            cached=await poiskkino.lookup(source_id=42)
            self.assertTrue(cached['cached'])
            self.assertEqual(fetch.await_count,1)
            fetch.return_value=item
            await poiskkino.lookup(source_id=42,refresh=True)
            self.assertEqual(fetch.await_count,2)
            fetch.side_effect=ValueError('limit')
            with self.assertRaises(ValueError):await poiskkino.lookup(source_id=42,refresh=True)
            self.assertEqual((await poiskkino.lookup(source_id=42))['items'][0]['rating_kp'],7.5)
        self.assertIsNotNone(poiskkino.provenance({'rating_source_id':42})['ratings_fetched_at'])
        with self.assertRaises(ValueError):poiskkino.provenance({'rating_source_id':999})

    async def test_missing_scores_and_series_filter(self):
        item={'id':7,'isSeries':True,'rating':{'kp':0,'imdb':9},'votes':{'imdb':0},'ageRating':None}
        with patch.object(poiskkino,'fetch',AsyncMock(return_value={'docs':[item]})):
            self.assertEqual((await poiskkino.lookup('Example'))['items'],[])
            record=(await poiskkino.lookup('Example',kind='series'))['items'][0]
            self.assertIsNone(record['rating_kp'])
            self.assertIsNone(record['rating_imdb'])
            self.assertIsNone(record['age_rating'])
