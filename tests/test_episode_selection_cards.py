"""Offline dispatcher checks for informative episode quality and voice cards."""
from html.parser import HTMLParser
import unittest
from unittest.mock import patch

from aiogram.utils.exceptions import BadRequest

import tests.test_series_navigation_handlers as navigation
from database.categories import add_movie_category, add_movie_subcategory
from database.connection import database
from database.movies import add_new_movie


class CaptionText(HTMLParser):
    def __init__(self, markup):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.tags = []
        self.feed(markup)
        self.close()

    def handle_data(self, data):
        self.parts.append(data)

    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, attrs))

    @property
    def text(self):
        return ''.join(self.parts)


def utf16_length(value):
    return len(value.encode('utf-16-le')) // 2


class EpisodeSelectionCardTests(unittest.IsolatedAsyncioTestCase):
    # Reuse the isolated dispatcher/database fixture, without discovering its tests twice.
    setUpClass = classmethod(navigation.SeriesNavigationHandlerTests.setUpClass.__func__)
    asyncSetUp = navigation.SeriesNavigationHandlerTests.asyncSetUp
    asyncTearDown = navigation.SeriesNavigationHandlerTests.asyncTearDown
    send = navigation.SeriesNavigationHandlerTests.send
    episode = navigation.SeriesNavigationHandlerTests.episode
    media_callback = navigation.SeriesNavigationHandlerTests.media_callback
    navigation_callback = navigation.SeriesNavigationHandlerTests.navigation_callback
    buttons = navigation.SeriesNavigationHandlerTests.buttons
    matching_buttons = navigation.SeriesNavigationHandlerTests.matching_buttons
    reset_mocks = navigation.SeriesNavigationHandlerTests.reset_mocks
    assert_media_retained = navigation.SeriesNavigationHandlerTests.assert_media_retained

    def project(self, poster='series-banner', title='Проект & <Название>',
                description='<p>Описание &amp; история: <b>друзья</b> отправляются в приключение.</p>'):
        movie = add_new_movie(title, content_type='series', poster_image=poster,
                              card_description=description, movie_trailer='trailer-video')
        database.execute('UPDATE Movies SET rating_kp=?,rating_imdb=?,age_rating=? WHERE movie_number=?',
                         (8.4, 8.7, '18+', movie))
        database.commit()
        return movie, add_movie_category(movie, 'Сезон 3', 3)

    def assert_card_details(self, markup, movie, episode_name='Серия 7: Приключение',
                            description='Описание & история: друзья отправляются в приключение.'):
        parsed = CaptionText(markup)
        for value in ('Проект & <Название>', description, 'КиноПоиск: 8,4', 'IMDb: 8,7',
                      'Возраст: 18+', 'Сезон 3', episode_name, 'Код'):
            self.assertIn(value, parsed.text)
        self.assertRegex(parsed.text, rf'Код[^\n]*\b{movie}\b')
        self.assertIn('&lt;Название&gt;', markup)
        self.assertTrue(all(tag in ('b', 'strong', 'i', 'em', 'u', 's', 'code', 'pre', 'a')
                            for tag, _ in parsed.tags), parsed.tags)
        return parsed.text

    async def test_quality_card_has_banner_description_ratings_code_and_episode_context(self):
        movie, season = self.project()
        episode, variants = self.episode(season, 'Серия 7: Приключение',
                                        [('1080p', 'Русская', 'video'), ('720p', 'English', 'video')])
        await self.send(123, callback=f'movsub_{episode}')
        card = self.mocks['send_photo'].call_args.kwargs
        self.assertEqual(card['photo'], 'series-banner')
        self.assertEqual(card['parse_mode'], 'HTML')
        text = self.assert_card_details(card['caption'], movie)
        self.assertIn('Выберите качество:', text)
        self.assertEqual({button.callback_data for button in self.matching_buttons('epvoices:')},
                         {f'epvoices:{episode}:{identifier}' for identifier in variants.values()})
        self.assertIn(f'wpage:{season}:0', str(card['reply_markup']))
        self.mocks['send_message'].assert_not_awaited()
        self.mocks['send_video'].assert_not_awaited()

    async def test_voice_card_has_same_banner_and_details_and_only_selected_quality_callbacks(self):
        movie, season = self.project()
        episode, variants = self.episode(season, 'Серия 7: Приключение',
            [('1080p', 'Русская', 'video'), ('720p', 'English', 'video'), ('720p', 'Русская', 'video')])
        selected = variants[('720p', 'English')]
        await self.send(123, callback=f'epvoices:{episode}:{selected}')
        card = self.mocks['send_photo'].call_args.kwargs
        self.assertEqual(card['photo'], 'series-banner')
        self.assertEqual(card['parse_mode'], 'HTML')
        text = self.assert_card_details(card['caption'], movie)
        self.assertIn('720p', text)
        self.assertIn('Выберите озвучку:', text)
        self.assertNotIn('1080p', text)
        self.assertEqual({button.callback_data for button in self.matching_buttons('epquality:')},
                         {f'epquality:{episode}:{variants[("720p", voice)]}' for voice in ('English', 'Русская')})
        self.assertIn(f'movsub_{episode}', str(card['reply_markup']))
        self.assertIn(f'wpage:{season}:0', str(card['reply_markup']))
        self.mocks['send_video'].assert_not_awaited()

    async def test_photo_card_buttons_navigate_quality_voice_and_back_without_losing_metadata(self):
        movie, season = self.project()
        episode, variants = self.episode(season, 'Серия 7: Приключение', [('720p', 'English', 'video')])
        await self.send(123, callback=f'movsub_{episode}')
        quality_card = self.mocks['send_photo'].call_args.kwargs
        await self.navigation_callback(f'epvoices:{episode}:{variants[("720p", "English")]}',
                                       quality_card['reply_markup'], caption=quality_card['caption'])
        voice_card = self.mocks['send_photo'].call_args.kwargs
        self.assertIn('Выберите озвучку:', self.assert_card_details(voice_card['caption'], movie))
        await self.navigation_callback(f'movsub_{episode}', voice_card['reply_markup'],
                                       caption=voice_card['caption'])
        self.assertIn('Выберите качество:',
                      self.assert_card_details(self.mocks['send_photo'].call_args.kwargs['caption'], movie))
        self.mocks['send_video'].assert_not_awaited()

    async def test_selection_from_delivered_video_and_document_keeps_file_and_has_no_card_credit(self):
        movie, season = self.project()
        episode, variants = self.episode(season, 'Серия 7: Приключение', [('720p', 'English', 'video')])
        with patch('database.contests.confirm_card') as credit:
            for kind in ('video', 'document'):
                for callback in (f'movsub_{episode}', f'epvoices:{episode}:{variants[("720p", "English")]}'):
                    with self.subTest(kind=kind, callback=callback):
                        self.reset_mocks()
                        await self.media_callback(callback, kind=kind)
                        self.assert_card_details(self.mocks['send_photo'].call_args.kwargs['caption'], movie)
                        self.assert_media_retained()
                        self.mocks['send_video'].assert_not_awaited()
                        self.mocks['send_document'].assert_not_awaited()
            credit.assert_not_called()
        self.assertEqual(database.execute('SELECT COUNT(*) FROM WatchEvents').fetchone()[0], 0)

    async def test_no_poster_text_cards_keep_description_ratings_and_matching_callbacks(self):
        movie, season = self.project(poster=None)
        episode, variants = self.episode(season, 'Серия 7: Приключение', [('720p', 'English', 'video')])
        for callback, prompt, prefix in ((f'movsub_{episode}', 'Выберите качество:', 'epvoices:'),
            (f'epvoices:{episode}:{variants[("720p", "English")]}', 'Выберите озвучку:', 'epquality:')):
            with self.subTest(callback=callback):
                self.reset_mocks()
                await self.send(123, callback=callback)
                call = self.mocks['send_message'].call_args
                self.assertEqual(call.kwargs['parse_mode'], 'HTML')
                text = call.kwargs.get('text', call.args[1] if len(call.args) > 1 else '')
                self.assertIn(prompt, self.assert_card_details(text, movie))
                self.assertEqual(len(self.matching_buttons(prefix, 'send_message')), 1)
                self.mocks['send_photo'].assert_not_awaited()
                self.mocks['send_video'].assert_not_awaited()

    async def test_poster_failure_falls_back_to_complete_text_card_for_each_selector(self):
        movie, season = self.project()
        episode, variants = self.episode(season, 'Серия 7: Приключение', [('720p', 'English', 'video')])
        self.mocks['send_photo'].side_effect = BadRequest('Wrong file identifier')
        for callback, prompt, prefix in ((f'movsub_{episode}', 'Выберите качество:', 'epvoices:'),
            (f'epvoices:{episode}:{variants[("720p", "English")]}', 'Выберите озвучку:', 'epquality:')):
            with self.subTest(callback=callback):
                self.reset_mocks()
                with self.assertLogs('handlers.users.find_movie', level='ERROR'):
                    await self.media_callback(callback)
                call = self.mocks['send_message'].call_args
                self.assertEqual(call.kwargs['parse_mode'], 'HTML')
                text = call.kwargs.get('text', call.args[1] if len(call.args) > 1 else '')
                self.assertIn(prompt, self.assert_card_details(text, movie))
                self.assertEqual(len(self.matching_buttons(prefix, 'send_message')), 1)
                self.mocks['send_photo'].assert_awaited_once()
                self.assert_media_retained()

    async def test_missing_voice_notice_keeps_target_episode_banner_metadata_and_quality(self):
        movie, season = self.project()
        first, current = self.episode(season, 'Серия 6: До приключения', [('720p', 'English', 'video')])
        target, available = self.episode(season, 'Серия 7: Приключение', [('720p', 'Русская', 'video')])
        await self.media_callback(f'epnext:{first}:{current[("720p", "English")]}:{target}')
        card = self.mocks['send_photo'].call_args.kwargs
        text = self.assert_card_details(card['caption'], movie)
        self.assertIn('озвучки', text)
        self.assertIn('720p', text)
        self.assertIn('Выберите озвучку:', text)
        self.assertNotIn('Серия 6:', text)
        self.assertEqual([button.callback_data for button in self.matching_buttons('epquality:')],
                         [f'epquality:{target}:{available[("720p", "Русская")]}'])
        self.assert_media_retained()

    async def test_original_file_voice_card_has_poster_details_and_legacy_zero_callback(self):
        movie, season = self.project()
        episode = add_movie_subcategory(season, 'Серия 7: Приключение', 'original-file', 'video')
        await self.send(123, callback=f'epvoices:{episode}:0')
        text = self.assert_card_details(self.mocks['send_photo'].call_args.kwargs['caption'], movie)
        self.assertIn('Исходный файл', text)
        self.assertIn('Выберите озвучку:', text)
        self.assertEqual([button.callback_data for button in self.matching_buttons('epquality:')],
                         [f'epquality:{episode}:0'])

    async def test_long_unicode_and_html_caption_keeps_footer_context_within_telegram_limit(self):
        title = 'Проект 😀 & <img src=x> ' * 600
        description = 'Описание 😀 & <script>alert(1)</script> ' * 800
        movie, season = self.project(title=title, description=description)
        episode_name = 'Серия 7: 😀 & <b>Приключение</b> ' * 500
        episode, variants = self.episode(season, episode_name, [('720p', 'English', 'video')])
        for callback, prompt in ((f'movsub_{episode}', 'Выберите качество:'),
                                (f'epvoices:{episode}:{variants[("720p", "English")]}', 'Выберите озвучку:')):
            with self.subTest(callback=callback):
                self.reset_mocks()
                await self.send(123, callback=callback)
                parsed = CaptionText(self.mocks['send_photo'].call_args.kwargs['caption'])
                self.assertLessEqual(utf16_length(parsed.text), 1024)
                for value in (prompt, 'Код', str(movie), 'Сезон 3', 'Серия 7:', 'Описание',
                              'КиноПоиск: 8,4', 'IMDb: 8,7', 'Возраст: 18+'):
                    self.assertIn(value, parsed.text)
                if 'epvoices:' in callback:
                    self.assertIn('720p', parsed.text)
                self.assertFalse(any(tag in ('img', 'script') for tag, _ in parsed.tags))
        self.assertEqual(database.execute('SELECT movie_title,card_description FROM Movies WHERE movie_number=?',
                                         (movie,)).fetchone(), (title, description))
        self.assertEqual(database.execute('SELECT name FROM MovieSubcategories WHERE id=?',
                                         (episode,)).fetchone()[0], episode_name)

    async def test_long_unicode_text_fallback_preserves_description_and_footer_within_message_limit(self):
        title = 'Проект 😀 & <img src=x> ' * 600
        description = 'Описание 😀 & <script>alert(1)</script> ' * 800
        movie, season = self.project(title=title, description=description)
        episode_name = 'Серия 7: 😀 & <b>Приключение</b> ' * 500
        episode, variants = self.episode(season, episode_name, [('720p', 'English', 'video')])
        self.mocks['send_photo'].side_effect = BadRequest('Wrong file identifier')
        for callback, prompt in ((f'movsub_{episode}', 'Выберите качество:'),
                                (f'epvoices:{episode}:{variants[("720p", "English")]}', 'Выберите озвучку:')):
            with self.subTest(callback=callback):
                self.reset_mocks()
                with self.assertLogs('handlers.users.find_movie', level='ERROR'):
                    await self.send(123, callback=callback)
                call = self.mocks['send_message'].call_args
                parsed = CaptionText(call.kwargs.get('text', call.args[1] if len(call.args) > 1 else ''))
                self.assertLessEqual(utf16_length(parsed.text), 4096)
                self.assertGreater(utf16_length(parsed.text), 1024)
                for value in (prompt, 'Код', str(movie), 'Сезон 3', 'Серия 7:', 'Описание',
                              'КиноПоиск: 8,4', 'IMDb: 8,7', 'Возраст: 18+'):
                    self.assertIn(value, parsed.text)
                if 'epvoices:' in callback:
                    self.assertIn('720p', parsed.text)
                self.assertFalse(any(tag in ('img', 'script') for tag, _ in parsed.tags))


if __name__ == '__main__':
    unittest.main()
