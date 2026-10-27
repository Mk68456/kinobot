import contextlib
import io
import unittest

from database.connection import database
from database.migrations import migrate
from database.movies import add_new_movie
from database.catalog_sections import all_sections, set_enabled
from database.movie_queries import get_movies_page
from database.search import get_movies_by_title, get_movie_from_numb
from keyboards.users.keyboard import find_movie_markup
from webpanel.repository import normalize_draft


class CatalogSectionsTests(unittest.TestCase):
    def setUp(self):
        database.open(':memory:')
        with contextlib.redirect_stdout(io.StringIO()):
            migrate()

    def tearDown(self):
        database.close()

    def test_only_existing_sections_are_enabled_by_default(self):
        states = {section['key']: section['enabled'] for section in all_sections()}
        self.assertEqual(states, {'movies': True, 'series': True, 'cartoons': False,
                                  'animated_series': False, 'anime': False})
        labels = [button.text for row in find_movie_markup().inline_keyboard for button in row]
        self.assertIn('🎬 Фильмы', labels)
        self.assertIn('📺 Сериалы', labels)
        self.assertNotIn('⛩ Аниме', labels)
        set_enabled('anime', True)
        labels = [button.text for row in find_movie_markup().inline_keyboard for button in row]
        self.assertIn('⛩ Аниме', labels)

    def test_section_and_format_are_independent_and_search_is_scoped(self):
        film = add_new_movie('Общее название', content_type='movie', catalog_section='movies')
        anime = add_new_movie('Общее название', content_type='movie', catalog_section='anime')
        show = add_new_movie('Общее название', content_type='series', catalog_section='anime')
        self.assertEqual([row[1] for row in get_movies_page(0, catalog_section='anime')[0]],
                         [anime, show])
        self.assertEqual([row[1] for row in get_movies_by_title('Общее', 'movie', 'anime')], [anime])
        self.assertEqual(get_movie_from_numb(show)['catalog_section'], 'anime')
        self.assertNotEqual(film, anime)

    def test_draft_rejects_incompatible_section_but_anime_accepts_both_formats(self):
        base = {'title': 'Test', 'description': '', 'poster_url': '', 'items': []}
        with self.assertRaisesRegex(ValueError, 'не поддерживает'):
            normalize_draft({**base, 'content_type': 'series', 'catalog_section': 'cartoons'})
        self.assertEqual(normalize_draft({**base, 'content_type': 'movie',
                                         'catalog_section': 'anime'})['catalog_section'], 'anime')
        self.assertEqual(normalize_draft({**base, 'content_type': 'series',
                                         'catalog_section': 'anime'})['catalog_section'], 'anime')


if __name__ == '__main__':
    unittest.main()
