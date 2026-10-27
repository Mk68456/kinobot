import contextlib
import io
import unittest
from database.connection import database
from database.migrations import migrate
from database.search import get_movie_from_numb
from webpanel import repository, imports
from services.ratings import normalize, line


class RatingTests(unittest.TestCase):
    def test_validation_and_hidden_empty_values(self):
        self.assertEqual(line({}), '')
        self.assertEqual(normalize({'rating_kp':'0','rating_imdb':'7,2','age_rating':'12+'}), {'rating_kp':0.0,'rating_imdb':7.2,'age_rating':'12+'})
        self.assertIn('IMDb: 7,2',line({'rating_imdb':'7.2'}))
        for invalid in ('nan','inf',-1,11,True,'wrong'):
            with self.assertRaises(ValueError):normalize({'rating_kp':invalid})
        with self.assertRaises(ValueError):normalize({'age_rating':'21+'})

    def test_ratings_survive_draft_publication(self):
        database.open(':memory:')
        try:
            with contextlib.redirect_stdout(io.StringIO()):migrate()
            draft=imports.create(99,{'version':1,'root':'D:/Movie','title':'Movie','files':[{'path':'film.mp4','size':10}]})
            asset=draft['body']['items'][0]['asset_id']
            database.execute("UPDATE WebAssets SET status='ready',file_id='file' WHERE id=?",(asset,))
            database.commit()
            body=dict(draft['body'],rating_kp='8,1',rating_imdb=0,age_rating='6+')
            saved=repository.save_draft(99,body,draft['id'])
            self.assertEqual(saved['body']['rating_kp'],8.1)
            number=repository.publish(draft['id'],99)
            info=get_movie_from_numb(number)
            self.assertEqual((info['rating_kp'],info['rating_imdb'],info['age_rating']),(8.1,0,'6+'))
        finally:database.close()
