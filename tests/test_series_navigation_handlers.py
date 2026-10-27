"""Offline dispatcher checks for episode quality, voice and retained media."""
import asyncio
from html import unescape
import re
import unittest
from unittest.mock import AsyncMock, patch

from aiogram import types
from aiogram.dispatcher.filters import StateFilter

import loader
import tests.test_handlers as common
from database import progress
from database.categories import add_movie_category, add_movie_subcategory
from database.connection import database
from database.episode_variants import add_series_file
from database.movies import add_new_movie


class SeriesNavigationHandlerTests(unittest.IsolatedAsyncioTestCase):
    # Reuse the setup without inheriting and rerunning the original test class.
    asyncTearDown = common.HandlerTests.asyncTearDown
    send = common.HandlerTests.send

    @classmethod
    def setUpClass(cls):
        if loader.dp is None:
            common.HandlerTests.setUpClass()
        cls.dp = loader.dp

    async def asyncSetUp(self):
        await common.HandlerTests.asyncSetUp(self)
        for name in ('edit_message_media', 'edit_message_caption'):
            self.mocks[name] = AsyncMock()
            patcher = patch.object(loader.bot, name, self.mocks[name])
            patcher.start()
            self.addCleanup(patcher.stop)
        patcher = patch('services.subscriptions.check_subscription', AsyncMock(return_value=True))
        patcher.start()
        self.addCleanup(patcher.stop)

    def series(self, title='Series'):
        movie = add_new_movie(title, content_type='series', poster_image='series-poster')
        return movie, add_movie_category(movie, 'Сезон 1', 1)

    def episode(self, season, name, specs):
        identifiers = {}
        with database.transaction():
            for quality, voice, file_type in specs:
                file_id = f'{name}:{quality}:{voice}'
                episode = add_series_file(season, {'name': name, 'quality': quality, 'voice': voice},
                                          {'file_id': file_id, 'file_type': file_type})
                identifier = database.execute('SELECT id FROM EpisodeVariants WHERE file_id=?',
                                              (file_id,)).fetchone()[0]
                identifiers[(quality, voice)] = identifier
        return episode, identifiers

    async def media_callback(self, callback, kind='video', caption='Episode <caption>', message_id=100,
                             file_id='already-delivered-file', markup=None):
        """A real media update, including its caption, through the middleware."""
        StateFilter.ctx_state.set(await self.dp.storage.get_state(chat=123, user=123))
        attachment = {'file_id': file_id, 'file_unique_id': 'retained-file'}
        if kind == 'video':
            attachment.update(width=1280, height=720, duration=25)
        message = {'message_id': message_id, 'date': 1, 'chat': {'id': 123, 'type': 'private'},
                   'from': {'id': 123, 'is_bot': False, 'first_name': 'Test'},
                   kind: attachment}
        if caption is not None:
            message['caption'] = caption
        if markup is not None:
            message['reply_markup'] = markup.to_python()
        return await self.dp.process_update(types.Update(update_id=message_id, callback_query={
            'id': f'callback-{message_id}', 'from': message['from'], 'chat_instance': 'test',
            'message': message, 'data': callback,
        }))

    async def navigation_callback(self, callback, markup, text=None, caption=None):
        """An actual text or photo menu with the markup returned by the handler."""
        StateFilter.ctx_state.set(await self.dp.storage.get_state(chat=123, user=123))
        message = {'message_id': 200, 'date': 1, 'chat': {'id': 123, 'type': 'private'},
                   'from': {'id': 123, 'is_bot': False, 'first_name': 'Test'},
                   'reply_markup': markup.to_python()}
        if caption is not None:
            message['photo'] = [{'file_id': 'series-poster', 'file_unique_id': 'poster',
                                 'width': 640, 'height': 480}]
            message['caption'] = caption
        else:
            message['text'] = text
        return await self.dp.process_update(types.Update(update_id=200, callback_query={
            'id': 'menu-callback', 'from': message['from'], 'chat_instance': 'test',
            'message': message, 'data': callback,
        }))

    def buttons(self, method='send_photo'):
        markup = self.mocks[method].call_args.kwargs['reply_markup']
        return [button for row in markup.inline_keyboard for button in row]

    def matching_buttons(self, prefix, method='send_photo'):
        return [button for button in self.buttons(method) if (button.callback_data or '').startswith(prefix)]

    def reset_mocks(self):
        for mock in self.mocks.values():
            mock.reset_mock()

    def assert_media_retained(self):
        for method in ('delete_message', 'edit_message_media', 'edit_message_caption', 'edit_message_text'):
            self.mocks[method].assert_not_awaited()

    async def test_twenty_variants_offer_four_qualities_then_only_five_matching_voices(self):
        _, season = self.series()
        voices = ['Braindead & NDRecords', 'BossVasa3211', 'BossVasa3211 18+', 'English', 'Other']
        episode, variants = self.episode(season, 'Серия 1',
            [(quality, voice, 'video') for quality in ('source', '720p', '480p', '360p') for voice in voices])
        await self.send(123, callback=f'movsub_{episode}')
        qualities = self.matching_buttons('epvoices:')
        self.assertEqual([button.text for button in qualities], ['Исходное качество', '720p', '480p', '360p'])
        self.assertEqual(len(qualities), 4)
        self.assertFalse(self.matching_buttons('epquality:'))
        await self.send(123, callback=f'epvoices:{episode}:{variants[("720p", voices[0])]}')
        voice_buttons = self.matching_buttons('epquality:')
        self.assertEqual([button.text for button in voice_buttons], voices)
        self.assertEqual({button.callback_data for button in voice_buttons},
                         {f'epquality:{episode}:{variants[("720p", voice)]}' for voice in voices})
        self.mocks['send_video'].assert_not_awaited()

    async def test_voice_selection_sends_exact_file_from_captioned_media_and_keeps_context(self):
        _, season = self.series()
        episode, variants = self.episode(season, 'Серия 1',
            [('1080p', 'Russian', 'video'), ('720p', 'English', 'video')])
        selected = variants[('720p', 'English')]
        await self.media_callback(f'epquality:{episode}:{selected}')
        call = self.mocks['send_video'].call_args
        self.assertEqual(call.kwargs['video'], 'Серия 1:720p:English')
        self.assertIn('720p', call.kwargs['caption'])
        self.assertIn('English', call.kwargs['caption'])
        self.assertIn(f'wp:e:{episode}:1:m:{selected}', str(call.kwargs['reply_markup']))
        self.assert_media_retained()

    async def test_legacy_direct_quality_callback_remains_usable_from_plain_navigation(self):
        _, season = self.series()
        episode, variants = self.episode(season, 'Серия 1', [('720p', '', 'document')])
        await self.send(123, callback=f'epquality:{episode}:{variants[("720p", "")]}')
        self.assertEqual(self.mocks['send_document'].call_args.kwargs['document'], 'Серия 1:720p:')
        self.mocks['edit_message_media'].assert_not_awaited()

    async def test_original_file_zero_variant_offers_quality_and_voice_and_sends_file(self):
        _, season = self.series()
        episode = add_movie_subcategory(season, 'Серия 1', 'original-video', 'video')
        await self.send(123, callback=f'movsub_{episode}')
        self.assertEqual([(button.text, button.callback_data) for button in self.matching_buttons('epvoices:')],
                         [('Исходный файл', f'epvoices:{episode}:0')])
        await self.send(123, callback=f'epvoices:{episode}:0')
        self.assertEqual([button.callback_data for button in self.matching_buttons('epquality:')],
                         [f'epquality:{episode}:0'])
        await self.media_callback(f'epquality:{episode}:0', caption=None)
        self.assertEqual(self.mocks['send_video'].call_args.kwargs['video'], 'original-video')
        self.assertIn(f'wp:e:{episode}:1:m:0', str(self.mocks['send_video'].call_args.kwargs['reply_markup']))

    async def test_next_and_previous_deliver_new_media_with_same_quality_and_voice(self):
        _, season = self.series()
        specs = [('1080p', 'Russian', 'video'), ('720p', 'English', 'video')]
        first, first_variants = self.episode(season, 'Серия 1', specs)
        last, last_variants = self.episode(season, 'Серия 2',
            [('1080p', 'Russian', 'video'), ('720p', 'English', 'document')])
        current = first_variants[('720p', 'English')]
        selected = last_variants[('720p', 'English')]
        await self.media_callback(f'epnext:{first}:{current}:{last}')
        self.assertEqual(self.mocks['send_document'].call_args.kwargs['document'], 'Серия 2:720p:English')
        markup = str(self.mocks['send_document'].call_args.kwargs['reply_markup'])
        self.assertIn(f'epnext:{last}:{selected}:{first}', markup)
        self.assertIn(f'wp:e:{last}:1:m:{selected}', markup)
        await self.media_callback(f'epnext:{last}:{selected}:{first}', kind='document')
        self.assertEqual(self.mocks['send_video'].call_args.kwargs['video'], 'Серия 1:720p:English')
        self.assertEqual(self.mocks['send_video'].await_count, 1)
        self.assertEqual(self.mocks['send_document'].await_count, 1)
        self.assert_media_retained()

    async def test_missing_voice_offers_only_voices_for_previous_quality(self):
        _, season = self.series()
        first, current = self.episode(season, 'Серия 1', [('720p', 'English', 'video')])
        last, available = self.episode(season, 'Серия 2',
            [('720p', 'Russian', 'video'), ('720p', 'Dub', 'video'), ('360p', 'English', 'video')])
        await self.media_callback(f'epnext:{first}:{current[("720p", "English")]}:{last}')
        self.assertIn('озвучки', self.mocks['send_photo'].call_args.kwargs['caption'])
        self.assertEqual({button.callback_data for button in self.matching_buttons('epquality:')},
                         {f'epquality:{last}:{available[("720p", voice)]}' for voice in ('Russian', 'Dub')})
        self.mocks['send_video'].assert_not_awaited()
        self.mocks['send_document'].assert_not_awaited()
        self.assert_media_retained()

    async def test_missing_quality_returns_quality_picker_and_explanation(self):
        _, season = self.series()
        first, current = self.episode(season, 'Серия 1', [('720p', 'English', 'video')])
        last, available = self.episode(season, 'Серия 2', [('480p', 'English', 'video')])
        await self.media_callback(f'epnext:{first}:{current[("720p", "English")]}:{last}')
        self.assertIn('качества', self.mocks['send_photo'].call_args.kwargs['caption'])
        self.assertEqual([button.callback_data for button in self.matching_buttons('epvoices:')],
                         [f'epvoices:{last}:{available[("480p", "English")]}'])
        self.mocks['send_video'].assert_not_awaited()
        self.assert_media_retained()

    async def test_deleted_previous_variant_recovers_via_target_quality_picker(self):
        _, season = self.series()
        first, current = self.episode(season, 'Серия 1', [('720p', 'English', 'video')])
        last, available = self.episode(season, 'Серия 2', [('720p', 'English', 'video')])
        identifier = current[('720p', 'English')]
        database.execute('DELETE FROM EpisodeVariants WHERE id=?', (identifier,))
        database.commit()
        await self.media_callback(f'epnext:{first}:{identifier}:{last}')
        self.assertIn('удалён', self.mocks['send_photo'].call_args.kwargs['caption'])
        self.assertEqual([button.callback_data for button in self.matching_buttons('epvoices:')],
                         [f'epvoices:{last}:{available[("720p", "English")]}'])
        self.mocks['send_video'].assert_not_awaited()
        self.assert_media_retained()

    async def test_cross_season_and_nonadjacent_navigation_are_rejected(self):
        movie, season = self.series()
        first, current = self.episode(season, 'Серия 1', [('720p', 'English', 'video')])
        self.episode(season, 'Серия 2', [('720p', 'English', 'video')])
        third, _ = self.episode(season, 'Серия 3', [('720p', 'English', 'video')])
        other_season = add_movie_category(movie, 'Сезон 2', 2)
        cross, _ = self.episode(other_season, 'Другой сезон', [('720p', 'English', 'video')])
        for target in (cross, third, first):
            with self.subTest(target=target):
                await self.media_callback(f'epnext:{first}:{current[("720p", "English")]}:{target}')
                self.assertTrue(self.mocks['answer_callback_query'].call_args.kwargs['show_alert'])
        self.mocks['send_video'].assert_not_awaited()
        self.mocks['send_document'].assert_not_awaited()
        self.assert_media_retained()

    async def test_last_episode_has_no_next_button_into_another_season(self):
        movie, season = self.series()
        first = add_movie_subcategory(season, 'Серия 1', 'first', 'video')
        last = add_movie_subcategory(season, 'Серия 2', 'last', 'video')
        next_season = add_movie_category(movie, 'Сезон 2', 2)
        cross = add_movie_subcategory(next_season, 'Серия 1', 'cross', 'video')
        await self.media_callback(f'epquality:{last}:0')
        self.assertEqual([button.callback_data for button in self.matching_buttons('epnext:', 'send_video')],
                         [f'epnext:{last}:0:{first}'])
        self.assertNotIn(f':{cross}', str(self.mocks['send_video'].call_args.kwargs['reply_markup']))

    async def test_watched_mark_only_edits_buttons_and_keeps_variant_context(self):
        _, season = self.series()
        first, current = self.episode(season, 'Серия 1', [('720p', 'English', 'video')])
        last, _ = self.episode(season, 'Серия 2', [('720p', 'English', 'video')])
        variant = current[('720p', 'English')]
        await self.media_callback(f'wp:e:{first}:1:m:{variant}')
        self.assertEqual(progress.episode_marks(123, season), {first})
        self.assertEqual(progress.episode_marks(99, season), set())
        markup = str(self.mocks['edit_message_reply_markup'].call_args.kwargs['reply_markup'])
        self.assertIn(f'wp:e:{first}:0:m:{variant}', markup)
        self.assertIn(f'epnext:{first}:{variant}:{last}', markup)
        await self.media_callback(f'wp:e:{first}:0:m:{variant}')
        self.assertFalse(progress.episode_marks(123, season))
        self.mocks['send_video'].assert_not_awaited()
        self.mocks['send_message'].assert_not_awaited()
        self.mocks['send_photo'].assert_not_awaited()
        self.assert_media_retained()

    async def test_back_to_episodes_seasons_card_and_quality_retains_video_and_document(self):
        movie, season = self.series()
        episode, variants = self.episode(season, 'Серия 1', [('720p', 'English', 'video')])
        for kind, callback in [(kind, callback) for kind in ('video', 'document') for callback in
                              (f'wpage:{season}:0', f'epseasons:{movie}', f'filecard_{movie}',
                               f'movsub_{episode}', f'epvoices:{episode}:{variants[("720p", "English")]}')]:
            with self.subTest(kind=kind, callback=callback):
                self.reset_mocks()
                await self.media_callback(callback, kind=kind)
                self.assert_media_retained()
                self.assertGreater(self.mocks['send_message'].await_count + self.mocks['send_photo'].await_count, 0)

    async def test_foreign_episode_variant_is_not_accepted_for_quality_or_voice(self):
        _, season = self.series()
        first, _ = self.episode(season, 'Серия 1', [('720p', 'English', 'video')])
        _, other = self.episode(season, 'Серия 2', [('720p', 'English', 'video')])
        for prefix in ('epquality', 'epvoices'):
            with self.subTest(prefix=prefix):
                await self.media_callback(f'{prefix}:{first}:{other[("720p", "English")]}')
                self.assertTrue(self.mocks['answer_callback_query'].call_args.kwargs['show_alert'])
        self.mocks['send_video'].assert_not_awaited()
        self.mocks['send_message'].assert_not_awaited()
        self.mocks['send_photo'].assert_not_awaited()
        self.assert_media_retained()

    async def test_new_callback_prefixes_require_subscriptions_and_do_not_send_media(self):
        _, season = self.series()
        first, current = self.episode(season, 'Серия 1', [('720p', 'English', 'video')])
        last, _ = self.episode(season, 'Серия 2', [('720p', 'English', 'video')])
        variant = current[('720p', 'English')]
        with patch('services.subscriptions.check_subscription', AsyncMock(return_value=False)) as check:
            for callback in (f'epvoices:{first}:{variant}', f'epnext:{first}:{variant}:{last}'):
                await self.media_callback(callback)
                self.assertIn('подпишитесь', str(self.mocks['answer_callback_query'].call_args))
            self.assertEqual(check.await_count, 2)
        self.mocks['send_video'].assert_not_awaited()
        self.mocks['send_document'].assert_not_awaited()
        self.mocks['send_message'].assert_not_awaited()
        self.mocks['send_photo'].assert_not_awaited()
        self.assert_media_retained()

    async def test_concurrent_duplicate_next_click_sends_once_without_mutating_previous_media(self):
        _, season = self.series()
        first, current = self.episode(season, 'Серия 1', [('720p', 'English', 'video')])
        last, _ = self.episode(season, 'Серия 2', [('720p', 'English', 'video')])
        entered, release = asyncio.Event(), asyncio.Event()

        async def blocked_send(*args, **kwargs):
            entered.set()
            await release.wait()

        self.mocks['send_video'].side_effect = blocked_send
        callback = f'epnext:{first}:{current[("720p", "English")]}:{last}'
        task = asyncio.create_task(self.media_callback(callback))
        try:
            await asyncio.wait_for(entered.wait(), 2)
            await asyncio.wait_for(self.media_callback(callback), 2)
            self.assertEqual(self.mocks['send_video'].await_count, 1)
            self.assertIn('уже выполняется', str(self.mocks['answer_callback_query'].call_args))
        finally:
            release.set()
            await task
        self.assert_media_retained()

    async def test_failed_episode_send_allows_a_later_retry(self):
        _, season = self.series()
        first, current = self.episode(season, 'Серия 1', [('720p', 'English', 'video')])
        last, _ = self.episode(season, 'Серия 2', [('720p', 'English', 'video')])
        callback = f'epnext:{first}:{current[("720p", "English")]}:{last}'
        self.mocks['send_video'].side_effect = RuntimeError('Temporary send failure')
        with self.assertRaisesRegex(RuntimeError, 'Temporary send failure'):
            await self.media_callback(callback)
        self.mocks['send_video'].side_effect = None
        await self.media_callback(callback)
        self.assertEqual(self.mocks['send_video'].await_count, 2)
        self.assert_media_retained()

    async def test_old_next_button_recovers_exact_quality_and_voice_from_delivered_file(self):
        _, season = self.series()
        specs = [('1080p', 'Russian', 'video'), ('720p', 'English', 'video')]
        first, _ = self.episode(season, 'Серия 1', specs)
        last, target = self.episode(season, 'Серия 2', specs)
        old_markup = types.InlineKeyboardMarkup().add(
            types.InlineKeyboardButton('Следующая серия', callback_data=f'movsub_{last}'),
            types.InlineKeyboardButton('Просмотрена', callback_data=f'wp:e:{first}:1:m:0'))
        await self.media_callback(f'movsub_{last}', file_id='Серия 1:720p:English', markup=old_markup)
        self.assertEqual(self.mocks['send_video'].call_args.kwargs['video'], 'Серия 2:720p:English')
        self.assertIn(f'wp:e:{last}:1:m:{target[("720p", "English")]}',
                      str(self.mocks['send_video'].call_args.kwargs['reply_markup']))
        self.mocks['send_message'].assert_not_awaited()
        self.mocks['send_photo'].assert_not_awaited()
        self.assert_media_retained()

    async def test_old_media_mark_upgrades_zero_context_to_actual_variant(self):
        _, season = self.series()
        first, current = self.episode(season, 'Серия 1', [('720p', 'English', 'document')])
        last, _ = self.episode(season, 'Серия 2', [('720p', 'English', 'document')])
        await self.media_callback(f'wp:e:{first}:1:m:0', kind='document', file_id='Серия 1:720p:English')
        variant = current[('720p', 'English')]
        markup = str(self.mocks['edit_message_reply_markup'].call_args.kwargs['reply_markup'])
        self.assertIn(f'wp:e:{first}:0:m:{variant}', markup)
        self.assertIn(f'epnext:{first}:{variant}:{last}', markup)
        self.assertEqual(progress.episode_marks(123, season), {first})
        self.assert_media_retained()

    async def test_old_next_ambiguous_file_does_not_guess_and_offers_target_qualities(self):
        _, season = self.series()
        first, current = self.episode(season, 'Серия 1',
            [('1080p', 'Russian', 'video'), ('720p', 'English', 'video')])
        last, _ = self.episode(season, 'Серия 2', [('720p', 'English', 'video')])
        database.execute('UPDATE EpisodeVariants SET file_id=? WHERE id=?',
                         ('Серия 1:1080p:Russian', current[('720p', 'English')]))
        database.commit()
        old_markup = types.InlineKeyboardMarkup().add(
            types.InlineKeyboardButton('Просмотрена', callback_data=f'wp:e:{first}:1:m:0'))
        await self.media_callback(f'movsub_{last}', file_id='Серия 1:1080p:Russian', markup=old_markup)
        self.assertEqual(len(self.matching_buttons(f'epvoices:{last}:')), 1)
        self.mocks['send_video'].assert_not_awaited()
        self.assert_media_retained()

    async def test_same_episode_quality_button_does_not_reuse_legacy_media_context(self):
        _, season = self.series()
        first, _ = self.episode(season, 'Серия 1',
            [('1080p', 'Russian', 'video'), ('720p', 'English', 'video')])
        old_markup = types.InlineKeyboardMarkup().add(
            types.InlineKeyboardButton('Просмотрена', callback_data=f'wp:e:{first}:1:m:0'))
        await self.media_callback(f'movsub_{first}', file_id='Серия 1:720p:English', markup=old_markup)
        self.assertEqual(len(self.matching_buttons(f'epvoices:{first}:')), 2)
        self.mocks['send_video'].assert_not_awaited()
        self.assert_media_retained()

    async def test_long_unicode_title_keeps_menu_instructions_caption_variant_and_database_name(self):
        _, season = self.series()
        title = '😀&<>' * 1600 + ' — original ending'
        voice = 'Русская & English 😀'
        episode, variants = self.episode(season, title, [('720p', voice, 'video')])
        self.assertGreaterEqual(len(title), 6000)
        await self.send(123, callback=f'movsub_{episode}')
        text = unescape(re.sub(r'<[^>]*>', '', self.mocks['send_photo'].call_args.kwargs['caption']))
        self.assertLessEqual(len(text.encode('utf-16-le')) // 2, 1024)
        self.assertIn('Выберите качество:', text)
        await self.send(123, callback=f'epvoices:{episode}:{variants[("720p", voice)]}')
        text = unescape(re.sub(r'<[^>]*>', '', self.mocks['send_photo'].call_args.kwargs['caption']))
        self.assertLessEqual(len(text.encode('utf-16-le')) // 2, 1024)
        self.assertIn('Выберите озвучку:', text)
        await self.media_callback(f'epquality:{episode}:{variants[("720p", voice)]}')
        caption = self.mocks['send_video'].call_args.kwargs['caption']
        self.assertLessEqual(len(caption.encode('utf-16-le')) // 2, 1024)
        self.assertTrue(caption.endswith(' · 720p · ' + voice))
        self.assertEqual(database.execute('SELECT name FROM MovieSubcategories WHERE id=?',
                                         (episode,)).fetchone()[0], title)

    async def test_season_selection_sends_fresh_episode_card_with_matching_caption_and_buttons(self):
        movie, season = self.series()
        first = add_movie_subcategory(season, 'Серия 1', 'first-video', 'video')
        last = add_movie_subcategory(season, 'Серия 2', 'last-video', 'video')
        await self.send(123, callback=f'epseasons:{movie}')
        old_card = self.mocks['send_photo'].call_args.kwargs
        self.assertIn('Выберите сезон', old_card['caption'])
        self.reset_mocks()
        await self.navigation_callback(f'movcat_{season}', old_card['reply_markup'],
                                       caption=old_card['caption'])
        fresh_card = self.mocks['send_photo'].call_args.kwargs
        self.assertIn('Выберите серию:', fresh_card['caption'])
        self.assertNotIn('Выберите сезон', fresh_card['caption'])
        self.assertEqual({button.callback_data for button in self.matching_buttons('movsub_', 'send_photo')},
                         {f'movsub_{first}', f'movsub_{last}'})
        self.assertIn(f'epseasons:{movie}', str(fresh_card['reply_markup']))
        self.mocks['edit_message_reply_markup'].assert_not_awaited()
        self.mocks['edit_message_media'].assert_not_awaited()
        self.mocks['edit_message_caption'].assert_not_awaited()

    async def test_back_from_quality_or_voice_menu_sends_card_with_episode_hint_and_buttons(self):
        _, season = self.series()
        episode, variants = self.episode(season, 'Серия 1', [('720p', 'English', 'video')])
        for selector in ('quality', 'voice'):
            with self.subTest(selector=selector):
                await self.send(123, callback=f'movsub_{episode}')
                if selector == 'voice':
                    await self.send(123, callback=f'epvoices:{episode}:{variants[("720p", "English")]}')
                old_menu = self.mocks['send_photo'].call_args.kwargs
                self.assertIn('Выберите качество:' if selector == 'quality' else 'Выберите озвучку:',
                              old_menu['caption'])
                self.reset_mocks()
                await self.navigation_callback(f'wpage:{season}:0', old_menu['reply_markup'],
                                               caption=old_menu['caption'])
                fresh_card = self.mocks['send_photo'].call_args.kwargs
                self.assertIn('Выберите серию:', fresh_card['caption'])
                self.assertNotIn('Выберите озвучку:', fresh_card['caption'])
                self.assertNotIn('Выберите качество:', fresh_card['caption'])
                self.assertEqual([button.callback_data for button in self.matching_buttons('movsub_', 'send_photo')],
                                 [f'movsub_{episode}'])
                self.mocks['edit_message_reply_markup'].assert_not_awaited()
                self.mocks['edit_message_media'].assert_not_awaited()
                self.mocks['edit_message_caption'].assert_not_awaited()


if __name__ == '__main__':
    unittest.main()
