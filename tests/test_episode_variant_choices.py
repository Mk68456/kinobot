"""Published episode menus read exact quality and voice fields in an isolated DB."""
import contextlib
import io
import unittest
from dataclasses import FrozenInstanceError

from database.connection import database
from database.migrations import migrate
from database.categories import add_movie_category, add_movie_subcategory
from database.movies import add_new_movie
from database.episode_variants import EpisodeChoice, choices, quality_groups, variants


class EpisodeVariantChoicesTests(unittest.TestCase):
    def setUp(self):
        database.open(':memory:')
        self.addCleanup(database.close)
        with contextlib.redirect_stdout(io.StringIO()):
            migrate()
        self.movie = add_new_movie('Series', content_type='series')
        self.season = add_movie_category(self.movie, 'Сезон 1', 1)

    def episode(self, name='Серия 1', file='base-file', kind='video', season=None):
        return add_movie_subcategory(season or self.season, name, file, kind)

    def variant(self, episode, quality, voice='', file=None, kind='video'):
        inserted = database.execute('INSERT INTO EpisodeVariants(episode_id,quality,voice,file_id,file_type) VALUES (?,?,?,?,?)',
                                    (episode, quality, voice, file or f'{quality}-{voice}', kind))
        return inserted.lastrowid

    def test_missing_or_deleted_episode_has_no_choices_or_groups(self):
        self.assertEqual(choices(9999), [])
        self.assertEqual(quality_groups(9999), [])
        episode = self.episode()
        self.variant(episode, '720p', 'DUB')
        database.execute('DELETE FROM MovieSubcategories WHERE id=?', (episode,))
        self.assertEqual(choices(episode), [])
        self.assertEqual(quality_groups(episode), [])

    def test_legacy_base_file_is_a_distinct_choice_with_zero_id(self):
        episode = self.episode(file='legacy-document', kind='document')
        self.assertEqual(choices(episode), [EpisodeChoice(0, '', '', 'legacy-document', 'document')])
        group = quality_groups(episode)[0]
        self.assertEqual(group.quality, '')
        self.assertEqual(group.label, 'Исходный файл')
        self.assertEqual(group.choices, tuple(choices(episode)))

    def test_source_and_1080p_are_distinct_even_when_they_reference_same_file(self):
        episode = self.episode(file='same-video')
        original = self.variant(episode, 'source', 'DUB', 'same-video')
        full_hd = self.variant(episode, '1080p', 'DUB', 'same-video')
        groups = quality_groups(episode)
        self.assertEqual([group.quality for group in groups], ['source', '1080p'])
        self.assertEqual([group.label for group in groups], ['Исходное качество', '1080p'])
        self.assertEqual([group.choices[0].id for group in groups], [original, full_hd])
        self.assertNotIn(0, [choice.id for choice in choices(episode)])

    def test_voices_share_one_quality_group_and_preserve_exact_stored_voice(self):
        episode = self.episode(file=None, kind=None)
        voices = ['Braindead Project & NDRecords (RUS)', 'BossVasa3211 (RUS)',
                  'BossVasa3211 18+ (RUS)', 'ENG · Original', '']
        ids = [self.variant(episode, '720p', voice) for voice in voices]
        groups = quality_groups(episode)
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0].label, '720p')
        self.assertEqual([choice.voice for choice in groups[0].choices], voices)
        self.assertEqual([choice.id for choice in groups[0].choices], ids)
        self.assertTrue(all(type(choice.id) is int for choice in groups[0].choices))

    def test_quality_groups_sort_resolutions_descending_instead_of_insertion_order(self):
        episode = self.episode(file=None)
        for quality in ['360p', '1080p', '480p', 'source', '720p', '2160p']:
            self.variant(episode, quality)
        self.assertEqual([group.quality for group in quality_groups(episode)],
                         ['source', '2160p', '1080p', '720p', '480p', '360p'])

    def test_custom_qualities_sort_naturally_and_legacy_base_stays_last(self):
        episode = self.episode()
        for quality in ['Profile 10', '360p', 'Profile 2', 'source']:
            self.variant(episode, quality)
        groups = quality_groups(episode)
        self.assertEqual([group.quality for group in groups], ['source', '360p', 'Profile 2', 'Profile 10', ''])
        self.assertEqual(groups[-1].choices[0].id, 0)

    def test_unrepresented_legacy_file_keeps_original_type_and_label(self):
        episode = self.episode(file='legacy', kind=None)
        modern = self.variant(episode, '720p', 'ENG', 'modern', 'document')
        self.assertEqual(choices(episode), [EpisodeChoice(modern, '720p', 'ENG', 'modern', 'document'),
                                          EpisodeChoice(0, '', '', 'legacy', None)])
        self.assertEqual(quality_groups(episode)[-1].label, 'Исходный файл')

    def test_empty_legacy_episode_does_not_invent_a_file(self):
        episode = self.episode(file=None, kind=None)
        self.assertEqual(choices(episode), [])
        self.assertEqual(quality_groups(episode), [])

    def test_choices_do_not_leak_between_episodes_seasons_or_projects(self):
        first = self.episode(file=None)
        second = self.episode('Серия 2', file=None)
        other_season = add_movie_category(self.movie, 'Сезон 2', 2)
        third = self.episode(file=None, season=other_season)
        other_movie = add_new_movie('Other series', content_type='series')
        other_category = add_movie_category(other_movie, 'Сезон 1', 1)
        fourth = self.episode(file=None, season=other_category)
        for index, episode in enumerate((first, second, third, fourth)):
            self.variant(episode, '720p', 'ENG', f'file-{index}')
        self.assertEqual([choice.file_id for choice in choices(first)], ['file-0'])
        self.assertEqual([choice.file_id for choice in quality_groups(third)[0].choices], ['file-2'])

    def test_structured_queries_do_not_modify_database_or_legacy_contract(self):
        episode = self.episode(file='legacy')
        low = self.variant(episode, '360p', 'DUB', 'low')
        source = self.variant(episode, 'source', 'ENG · Original', 'original', 'document')
        expected = [(low, '360p · DUB', 'low', 'video'),
                    (source, 'Исходное качество · ENG · Original', 'original', 'document'),
                    (0, 'Исходный файл', 'legacy', 'video')]
        before = database.connection.total_changes
        self.assertEqual(variants(episode), expected)
        selected = choices(episode)[0]
        group = quality_groups(episode)[0]
        with self.assertRaises(FrozenInstanceError):
            selected.voice = 'changed'
        with self.assertRaises(FrozenInstanceError):
            group.label = 'changed'
        self.assertEqual(database.connection.total_changes, before)
        self.assertEqual(variants(episode), expected)


if __name__ == '__main__':
    unittest.main()
