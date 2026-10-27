"""Episode merging operates only on isolated in-memory catalog records."""
import contextlib
import io
import sqlite3
import unittest
from unittest.mock import patch

from database.connection import database
from database.migrations import migrate
from database.categories import add_movie_category, add_movie_subcategory
from database.movies import add_new_movie
from database.episode_variants import choices, variants
from webpanel import episode_merge


class EpisodeMergeTests(unittest.TestCase):
    def setUp(self):
        database.open(':memory:')
        self.addCleanup(database.close)
        with contextlib.redirect_stdout(io.StringIO()):
            migrate()
        self.movie = add_new_movie('Series', content_type='series')
        self.folder = add_movie_category(self.movie, 'Сезон 1', 1)
        self.target = self.episode('Серия 1: Название', '1080-DUB')
        self.source = self.episode('Серя 1: Название', '720-DUB')
        self.high = self.variant(self.target, '1080p', 'DUB', '1080-DUB')
        self.low = self.variant(self.source, '720p', 'DUB', '720-DUB')
        self.arguments = (self.movie, self.folder, self.target, [self.target, self.source], 'Серия 1: Название')

    def episode(self, name, file=None, kind='video', folder=None):
        return add_movie_subcategory(folder or self.folder, name, file, kind)

    def variant(self, episode, quality, voice, file, kind='video'):
        return database.execute('INSERT INTO EpisodeVariants(episode_id,quality,voice,file_id,file_type) VALUES (?,?,?,?,?)',
                                (episode, quality, voice, file, kind)).lastrowid

    def watched(self, episode, user, when):
        database.execute('INSERT INTO EpisodeProgress(user_id,episode_id,watched_at) VALUES (?,?,?)', (user, episode, when))

    def preview(self, arguments=None):
        return episode_merge.preview(*(arguments or self.arguments))

    def merge(self, arguments=None):
        args = arguments or self.arguments
        return episode_merge.merge(*args, self.preview(args)['token'])

    def snapshot(self):
        return {table: database.execute(f'SELECT * FROM {table} ORDER BY rowid').fetchall() for table in
                ('Movies', 'MovieCategories', 'MovieSubcategories', 'EpisodeVariants', 'EpisodeProgress',
                 'WebAssets', 'TelegramStorageFiles', 'WatchEvents')}

    def test_preview_is_read_only_and_reports_effective_files(self):
        before = self.snapshot()
        changes = database.connection.total_changes
        result = self.preview()
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(database.connection.total_changes, changes)
        self.assertTrue(result['can_merge'])
        self.assertEqual(result['conflicts'], [])
        self.assertEqual(len(result['token']), 64)
        self.assertEqual(result['counts']['variants_before'], 2)
        self.assertEqual(result['counts']['variants_after'], 2)
        self.assertEqual(result['counts']['duplicates'], 0)
        self.assertEqual(result['counts']['episodes_before'], 2)
        self.assertEqual(result['counts']['episodes_after'], 1)

    def test_merge_keeps_target_id_and_moves_variants_without_changing_ids(self):
        english = self.variant(self.source, '720p', 'ENG', '720-ENG', 'document')
        result = self.merge()
        self.assertTrue(result['merged'])
        self.assertEqual(result['target_id'], self.target)
        self.assertEqual(database.execute('SELECT id,name FROM MovieSubcategories').fetchall(),
                         [(self.target, 'Серия 1: Название')])
        self.assertEqual(database.execute('SELECT id,episode_id FROM EpisodeVariants ORDER BY id').fetchall(),
                         [(self.high, self.target), (self.low, self.target), (english, self.target)])
        self.assertEqual([(row.quality, row.voice, row.file_type) for row in choices(self.target)],
                         [('1080p', 'DUB', 'video'), ('720p', 'DUB', 'video'), ('720p', 'ENG', 'document')])
        self.assertEqual(database.execute('PRAGMA foreign_key_check').fetchall(), [])

    def test_union_watched_marks_keeps_earliest_timestamp_and_unaffected_marks(self):
        self.watched(self.target, 1, '2026-10-07 18:00:00')
        self.watched(self.source, 1, '2026-10-07T16:00:00+00:00')
        self.watched(self.target, 2, '2026-10-07 19:00:00')
        self.watched(self.source, 3, '2026-10-06 01:00:00')
        outside = self.episode('Серия 2', 'other')
        self.watched(outside, 1, '2026-10-08 01:00:00')
        preview = self.preview()
        self.assertEqual(preview['counts']['watched_users'], 3)
        self.assertEqual(preview['counts']['progress_before'], 4)
        self.merge()
        self.assertEqual(database.execute('SELECT user_id,watched_at FROM EpisodeProgress WHERE episode_id=? ORDER BY user_id',
                                         (self.target,)).fetchall(),
                         [(1, '2026-10-07T16:00:00+00:00'), (2, '2026-10-07 19:00:00'), (3, '2026-10-06 01:00:00')])
        self.assertEqual(database.execute('SELECT watched_at FROM EpisodeProgress WHERE episode_id=?', (outside,)).fetchone()[0],
                         '2026-10-08 01:00:00')

    def test_exact_variant_duplicates_deduplicate_and_keep_target_variant_id(self):
        duplicate = self.variant(self.source, '1080p', 'DUB', '1080-DUB')
        preview = self.preview()
        self.assertEqual(preview['counts']['variants_before'], 3)
        self.assertEqual(preview['counts']['variants_after'], 2)
        self.assertEqual(preview['counts']['duplicates'], 1)
        self.merge()
        self.assertEqual([choice.id for choice in choices(self.target)], [self.high, self.low])
        self.assertIsNone(database.execute('SELECT id FROM EpisodeVariants WHERE id=?', (duplicate,)).fetchone())

    def test_source_and_1080p_same_file_remain_distinct_metadata_variants(self):
        original = self.variant(self.source, 'source', 'DUB', '1080-DUB')
        self.merge()
        self.assertEqual({(choice.id, choice.quality) for choice in choices(self.target)},
                         {(self.high, '1080p'), (self.low, '720p'), (original, 'source')})

    def test_all_conflicts_reported_and_apply_makes_no_changes(self):
        first = self.variant(self.source, '1080p', 'DUB', 'different')
        second = self.variant(self.target, '720p', 'DUB', 'different-too')
        before = self.snapshot()
        preview = self.preview()
        self.assertFalse(preview['can_merge'])
        self.assertEqual(len(preview['conflicts']), 2)
        self.assertEqual({conflict['quality'] for conflict in preview['conflicts']}, {'1080p', '720p'})
        self.assertTrue(all(conflict['code'] == 'variant_conflict' for conflict in preview['conflicts']))
        self.assertEqual(preview['counts']['duplicates'], 0)
        with self.assertRaises(episode_merge.EpisodeMergeConflict) as error:
            episode_merge.merge(*self.arguments, preview['token'])
        self.assertEqual(error.exception.conflicts, preview['conflicts'])
        self.assertIn(first, {entry['variant_id'] for conflict in preview['conflicts'] for entry in conflict['episodes']})
        self.assertIn(second, {entry['variant_id'] for conflict in preview['conflicts'] for entry in conflict['episodes']})
        self.assertEqual(self.snapshot(), before)

    def test_same_file_id_but_different_type_is_not_silently_deduplicated(self):
        self.variant(self.source, '1080p', 'DUB', '1080-DUB', 'document')
        self.assertFalse(self.preview()['can_merge'])
        with self.assertRaises(episode_merge.EpisodeMergeConflict):
            self.merge()

    def test_distinct_unknown_legacy_files_require_metadata_without_inventing_source(self):
        database.execute('DELETE FROM EpisodeVariants')
        before = self.snapshot()
        preview = self.preview()
        self.assertEqual(preview['conflicts'][0]['code'], 'legacy_metadata_required')
        self.assertEqual(preview['conflicts'][0]['quality'], '')
        self.assertEqual(preview['conflicts'][0]['voice'], '')
        with self.assertRaises(episode_merge.EpisodeMergeConflict):
            self.merge()
        self.assertEqual(self.snapshot(), before)

    def test_one_legacy_source_is_preserved_as_primary_without_fake_quality_or_type(self):
        database.execute('DELETE FROM EpisodeVariants WHERE id=?', (self.low,))
        database.execute('UPDATE MovieSubcategories SET file_type=NULL WHERE id=?', (self.source,))
        self.merge()
        self.assertEqual(database.execute('SELECT file_id,file_type FROM MovieSubcategories WHERE id=?', (self.target,)).fetchone(),
                         ('720-DUB', None))
        self.assertEqual([(choice.id, choice.quality, choice.voice, choice.file_id, choice.file_type) for choice in choices(self.target)],
                         [(self.high, '1080p', 'DUB', '1080-DUB', 'video'), (0, '', '', '720-DUB', None)])

    def test_legacy_target_primary_is_preserved_with_source_modern_variants(self):
        database.execute('DELETE FROM EpisodeVariants WHERE id=?', (self.high,))
        self.merge()
        self.assertEqual(database.execute('SELECT file_id FROM MovieSubcategories WHERE id=?', (self.target,)).fetchone()[0], '1080-DUB')
        self.assertEqual([(choice.id, choice.file_id) for choice in choices(self.target)], [(self.low, '720-DUB'), (0, '1080-DUB')])

    def test_same_legacy_file_is_deduplicated_without_making_a_quality_variant(self):
        database.execute('DELETE FROM EpisodeVariants')
        database.execute('UPDATE MovieSubcategories SET file_id=? WHERE id=?', ('1080-DUB', self.source))
        preview = self.preview()
        self.assertTrue(preview['can_merge'])
        self.assertEqual(preview['counts']['duplicates'], 1)
        self.merge()
        self.assertEqual(variants(self.target), [(0, 'Исходный файл', '1080-DUB', 'video')])
        self.assertEqual(database.execute('SELECT count(*) FROM EpisodeVariants').fetchone()[0], 0)

    def test_legacy_file_represented_by_other_episode_real_variant_uses_that_metadata(self):
        database.execute('DELETE FROM EpisodeVariants WHERE id=?', (self.high,))
        database.execute('UPDATE MovieSubcategories SET file_id=? WHERE id=?', ('720-DUB', self.target))
        preview = self.preview()
        self.assertTrue(preview['can_merge'])
        self.assertEqual(preview['counts']['duplicates'], 1)
        self.merge()
        self.assertEqual([(choice.id, choice.quality, choice.file_id) for choice in choices(self.target)], [(self.low, '720p', '720-DUB')])

    def test_legacy_unknown_type_matches_modern_file_id_across_episodes(self):
        database.execute('DELETE FROM EpisodeVariants WHERE id=?', (self.high,))
        database.execute('UPDATE MovieSubcategories SET file_id=?,file_type=NULL WHERE id=?', ('720-DUB', self.target))
        preview = self.preview()
        self.assertEqual(preview['counts']['variants_before'], len(choices(self.target)) + len(choices(self.source)))
        self.assertEqual(preview['counts']['variants_after'], 1)
        self.assertEqual(preview['counts']['duplicates'], 1)
        self.merge()
        self.assertEqual([(choice.id, choice.file_type) for choice in choices(self.target)], [(self.low, 'video')])
        self.assertEqual(database.execute('SELECT file_type FROM MovieSubcategories WHERE id=?', (self.target,)).fetchone()[0], 'video')

    def test_legacy_unknown_type_matches_modern_file_id_inside_same_episode(self):
        database.execute('UPDATE MovieSubcategories SET file_type=NULL WHERE id=?', (self.target,))
        preview = self.preview()
        self.assertEqual(preview['counts']['variants_before'], len(choices(self.target)) + len(choices(self.source)))
        self.assertEqual(preview['counts']['variants_before'], 2)
        self.assertEqual(preview['counts']['variants_after'], 2)
        self.assertEqual(preview['counts']['duplicates'], 0)
        self.merge()
        self.assertEqual([choice.id for choice in choices(self.target)], [self.high, self.low])

    def test_three_or_more_typo_episodes_can_merge_and_outside_episode_stays_unchanged(self):
        third = self.episode('Серя1', '480-DUB')
        third_variant = self.variant(third, '480p', 'DUB', '480-DUB')
        outside = self.episode('Серия 2', 'outside')
        row = database.execute('SELECT * FROM MovieSubcategories WHERE id=?', (outside,)).fetchone()
        self.merge((self.movie, self.folder, self.target, [third, self.source, self.target], 'Серия 1: Название'))
        self.assertEqual(database.execute('SELECT * FROM MovieSubcategories WHERE id=?', (outside,)).fetchone(), row)
        self.assertEqual([choice.id for choice in choices(self.target)], [self.high, self.low, third_variant])

    def test_preview_token_invalidated_by_variant_base_name_or_watch_changes(self):
        mutations = [
            ('UPDATE EpisodeVariants SET voice=? WHERE id=?', ('new voice', self.low)),
            ('UPDATE MovieSubcategories SET file_id=? WHERE id=?', ('new base', self.source)),
            ('UPDATE MovieSubcategories SET name=? WHERE id=?', ('Changed name', self.source)),
            ('INSERT INTO EpisodeProgress(user_id,episode_id,watched_at) VALUES (?,?,?)', (42, self.source, '2026-10-07 01:00:00')),
        ]
        for query, arguments in mutations:
            with self.subTest(query=query):
                preview = self.preview()
                database.execute(query, arguments)
                before = self.snapshot()
                with self.assertRaisesRegex(ValueError, 'изменились'):
                    episode_merge.merge(*self.arguments, preview['token'])
                self.assertEqual(self.snapshot(), before)

    def test_preview_token_binds_target_name_and_selection_and_rejects_repeated_apply(self):
        preview = self.preview()
        with self.assertRaisesRegex(ValueError, 'изменились'):
            episode_merge.merge(*self.arguments[:-1], 'Другое название', preview['token'])
        episode_merge.merge(*self.arguments, preview['token'])
        with self.assertRaises(ValueError):
            episode_merge.merge(*self.arguments, preview['token'])

    def test_outside_name_collision_requires_including_existing_episode(self):
        outside = self.episode('  СЕРИЯ 1: НАЗВАНИЕ  ', 'outside')
        preview = self.preview()
        self.assertEqual(preview['conflicts'][0]['code'], 'name_conflict')
        self.assertIn(outside, {entry['id'] for entry in preview['conflicts'][0]['episodes']})
        with self.assertRaises(episode_merge.EpisodeMergeConflict):
            self.merge()

    def test_unrelated_name_edit_since_preview_requires_a_new_preview(self):
        outside = self.episode('Серия 2', 'outside')
        preview = self.preview()
        database.execute('UPDATE MovieSubcategories SET name=? WHERE id=?', ('Renamed', outside))
        with self.assertRaisesRegex(ValueError, 'изменились'):
            episode_merge.merge(*self.arguments, preview['token'])

    def test_rejects_cross_season_cross_movie_and_nonseries_even_if_season_labels_match(self):
        same_label = add_movie_category(self.movie, 'Сезон 1', 1)
        cross_season = self.episode('Episode', 'cross', folder=same_label)
        other_movie = add_new_movie('Other', content_type='series')
        other_folder = add_movie_category(other_movie, 'Сезон 1', 1)
        cross_movie = self.episode('Episode', 'cross', folder=other_folder)
        for foreign in (cross_season, cross_movie):
            with self.subTest(foreign=foreign), self.assertRaisesRegex(ValueError, 'одном'):
                self.preview((self.movie, self.folder, self.target, [self.target, foreign], 'Merged'))
        with self.assertRaisesRegex(ValueError, 'не принадлежит'):
            self.preview((self.movie, other_folder, self.target, [self.target, self.source], 'Merged'))
        database.execute("UPDATE Movies SET content_type='movie' WHERE movie_number=?", (self.movie,))
        with self.assertRaisesRegex(ValueError, 'сериалов'):
            self.preview()

    def test_invalid_ids_selection_names_and_tokens_do_not_write(self):
        before = self.snapshot()
        invalid = [
            (True, self.folder, self.target, [self.target, self.source], 'Name'),
            (self.movie, False, self.target, [self.target, self.source], 'Name'),
            (self.movie, self.folder, True, [self.target, self.source], 'Name'),
            (self.movie, self.folder, self.target, [self.target, True], 'Name'),
            (self.movie, self.folder, self.target, [self.target], 'Name'),
            (self.movie, self.folder, self.target, [self.target, self.target], 'Name'),
            (self.movie, self.folder, self.target, [self.source, 9999], 'Name'),
            (self.movie, self.folder, self.target, [self.target, self.source], ''),
            (self.movie, self.folder, self.target, [self.target, self.source], 'Line\nBreak'),
            (self.movie, self.folder, self.target, [self.target, self.source], 123),
            (self.movie, self.folder, self.target, [self.target, episode_merge.MAX_ID + 1], 'Name'),
            (self.movie, self.folder, self.target, list(range(1, episode_merge.MAX_EPISODES + 2)), 'Name'),
        ]
        for arguments in invalid:
            with self.subTest(arguments=arguments), self.assertRaises(ValueError):
                self.preview(arguments)
        for token in (None, True, {}, 'é' * 64, 'a' * 63):
            with self.subTest(token=token), self.assertRaises(ValueError):
                episode_merge.merge(*self.arguments, token)
        self.assertEqual(self.snapshot(), before)

    def test_storage_assets_watch_history_and_other_catalog_rows_untouched(self):
        database.execute("INSERT INTO TelegramStorageFiles(file_id,file_type,status,storage_chat_id,storage_message_id) VALUES ('720-DUB','video','ready',-1001,42)")
        database.execute("INSERT INTO WebAssets(id,admin_id,source,filename,size,file_type,file_id,status) VALUES ('asset',99,'desktop','episode.mp4',10,'video','720-DUB','ready')")
        database.execute("INSERT INTO WatchEvents(user_id,movie_number,movie_title,created_at) VALUES (1,?,'Series','2026-10-07')", (self.movie,))
        before = {table: self.snapshot()[table] for table in ('TelegramStorageFiles', 'WebAssets', 'WatchEvents', 'Movies', 'MovieCategories')}
        self.merge()
        self.assertEqual({table: self.snapshot()[table] for table in before}, before)
        self.assertIn('720-DUB', {choice.file_id for choice in choices(self.target)})

    def test_late_sql_error_rolls_back_all_moves_progress_and_deletions(self):
        self.watched(self.source, 42, '2026-10-07 01:00:00')
        preview = self.preview()
        before = self.snapshot()
        original = database.execute

        def fail_delete(sql, parameters=()):
            if sql.startswith('DELETE FROM MovieSubcategories'):
                raise sqlite3.OperationalError('Test failure at final deletion')
            return original(sql, parameters)

        with patch.object(database, 'execute', side_effect=fail_delete), self.assertRaises(sqlite3.OperationalError):
            episode_merge.merge(*self.arguments, preview['token'])
        self.assertEqual(self.snapshot(), before)

    def test_nested_transaction_can_roll_back_a_successful_merge(self):
        before = self.snapshot()
        with self.assertRaisesRegex(RuntimeError, 'outer rollback'):
            with database.transaction():
                self.merge()
                raise RuntimeError('outer rollback')
        self.assertEqual(self.snapshot(), before)

    def test_long_names_are_supported_and_selection_order_does_not_change_token(self):
        name = 'Серия 1: ' + 'Название ' * 50
        forward = (self.movie, self.folder, self.target, [self.target, self.source], name)
        reverse = (self.movie, self.folder, self.target, [self.source, self.target], name)
        self.assertEqual(self.preview(forward)['token'], self.preview(reverse)['token'])
        self.merge(forward)
        self.assertEqual(database.execute('SELECT name FROM MovieSubcategories WHERE id=?', (self.target,)).fetchone()[0], name.strip())


if __name__ == '__main__':
    unittest.main()
