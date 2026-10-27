"""Offline dispatcher checks: opt-in, PIN flows and deep-link protection."""
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from database import age_access
from database.connection import database
from database.categories import add_movie_category, add_movie_subcategory
from database.torrents import add_movie_torrent
from services import movie_sharing
import tests.test_handlers as common
from tests.test_age_access import movie


class AgeHandlerTests(unittest.IsolatedAsyncioTestCase):
    setUpClass = classmethod(common.HandlerTests.setUpClass.__func__)
    asyncTearDown = common.HandlerTests.asyncTearDown
    send = common.HandlerTests.send

    async def asyncSetUp(self):
        await common.HandlerTests.asyncSetUp(self)
        database.execute('DELETE FROM AgePreferences WHERE user_id=123')
        database.commit()
        patcher = patch('services.subscriptions.check_subscription', AsyncMock(return_value=True))
        patcher.start()
        self.addCleanup(patcher.stop)

    def reset(self):
        for mock in self.mocks.values():
            mock.reset_mock()

    def no_media(self):
        for name in ('send_photo', 'send_video', 'send_document', 'copy_message'):
            self.mocks[name].assert_not_awaited()

    async def parent(self, limit=6):
        await self.send(123, callback='age:parent')
        await self.send(123, callback=f'age:limit:{limit}')
        await self.send(123, text='123456')
        await self.send(123, text='123456')

    async def test_old_user_gets_one_notice_and_persistent_confirmation(self):
        with patch('handlers.start.check_user_sub', AsyncMock(return_value=True)):
            await self.send(123, text='/start')
            self.assertIn('По умолчанию', str(self.mocks['send_message'].call_args))
            self.reset()
            await self.send(123, text='/start')
            self.assertNotIn('По умолчанию', str(self.mocks['send_message'].call_args))
        await self.send(123, callback='age:adult')
        self.assertIn('Мне исполнилось 18 лет', str(self.mocks['send_message'].call_args))
        self.assertEqual(age_access.preferences(123)['max_age'], 16)
        await self.send(123, callback='age:confirm')
        self.assertEqual(age_access.preferences(123)['max_age'], 18)
        await self.send(123, callback='age:safe')
        self.assertEqual(age_access.preferences(123)['max_age'], 16)

    async def test_pin_requires_two_matching_entries_and_deletes_pin_messages(self):
        await self.send(123, callback='age:parent')
        await self.send(123, callback='age:limit:6')
        await self.send(123, text='123456')
        self.assertFalse(age_access.preferences(123)['parental'])
        await self.send(123, text='000000')
        self.assertFalse(age_access.preferences(123)['parental'])
        await self.send(123, text='123456')
        self.assertTrue(age_access.preferences(123)['parental'])
        self.assertEqual(age_access.preferences(123)['max_age'], 6)
        self.assertEqual(self.mocks['delete_message'].await_count, 3)
        self.assertNotIn('123456', str(self.mocks['send_message'].await_args_list))

    async def test_parental_limit_cannot_be_bypassed_by_old_confirmation_or_start(self):
        await self.parent()
        for callback in ('age:confirm', 'age:safe', 'age:adult', 'age:limit:16'):
            await self.send(123, callback=callback)
            self.assertEqual(age_access.preferences(123)['max_age'], 6)
        with patch('handlers.start.check_user_sub', AsyncMock(return_value=True)):
            await self.send(123, text='/start')
        self.assertTrue(age_access.preferences(123)['parental'])
        self.assertEqual(age_access.preferences(123)['max_age'], 6)

    async def test_pin_changes_limit_then_disables_to_safe_mode(self):
        await self.parent()
        await self.send(123, callback='age:parent')
        await self.send(123, callback='age:limit:12')
        await self.send(123, text='000000')
        self.assertEqual(age_access.preferences(123)['max_age'], 6)
        await self.send(123, text='123456')
        self.assertEqual(age_access.preferences(123)['max_age'], 12)
        await self.send(123, callback='age:off')
        await self.send(123, text='123456')
        self.assertFalse(age_access.preferences(123)['parental'])
        self.assertEqual(age_access.preferences(123)['max_age'], 16)

    async def test_pin_lockout_cannot_be_reset_by_start_or_settings(self):
        await self.parent()
        await self.send(123, callback='age:off')
        for _ in range(5):
            await self.send(123, text='000000')
        with patch('handlers.start.check_user_sub', AsyncMock(return_value=True)):
            await self.send(123, text='/start')
        await self.send(123, callback='age:off')
        await self.send(123, text='123456')
        self.assertTrue(age_access.preferences(123)['parental'])
        self.assertIn('15 минут', str(self.mocks['send_message'].call_args))

    async def test_expired_parental_setup_does_not_enable_lock(self):
        await self.send(123, callback='age:parent')
        await self.send(123, callback='age:limit:6')
        await self.dp.storage.update_data(chat=123, user=123, expires=0)
        await self.send(123, text='123456')
        self.assertFalse(age_access.preferences(123)['parental'])
        self.assertIsNone(await self.dp.storage.get_state(chat=123, user=123))

    async def test_restricted_old_buttons_never_send_metadata_files_or_trailers(self):
        code = movie('18+', 'Restricted Series', 'series')
        category = add_movie_category(code, 'Season 1', 1)
        episode = add_movie_subcategory(category, 'Episode 1', 'file', 'video')
        target = add_movie_subcategory(category, 'Episode 2', 'second', 'video')
        torrent = add_movie_torrent(code, 'Torrent', 'torrent')
        callbacks = [f'dlfile_{code}', f'trailer_{code}',
                     f'movcat_{category}', f'movsub_{episode}', f'epseasons:{code}', f'wpage:{category}:0',
                     f'epquality:{episode}:0', f'epvoices:{episode}:0', f'epnext:{episode}:0:{target}',
                     f'gettorrent_{torrent}', f'wp:e:{episode}:1:m:0']
        for callback in callbacks:
            with self.subTest(callback=callback):
                self.reset()
                await self.send(123, callback=callback)
                self.no_media()
                self.assertIn('возрастных', str(self.mocks['answer_callback_query'].call_args))
                self.assertNotIn('Secret description', str(self.mocks['send_message'].await_args_list))
        self.assertEqual(database.execute('SELECT COUNT(*) FROM EpisodeProgress').fetchone()[0], 0)

    async def test_share_invitation_subscription_bypass_never_bypasses_age(self):
        code = movie('18+')
        token = movie_sharing.share_token(code)
        with patch('handlers.start.check_user_sub', AsyncMock(return_value=True)):
            await self.send(123, text='/start share_' + token)
        self.mocks['send_photo'].assert_awaited_once()
        self.mocks['send_video'].assert_not_awaited()
        self.assertIn('возрастных', str(self.mocks['send_photo'].call_args))
        self.reset()
        await self.send(123, callback=f'dlfile_{code}')
        self.no_media()
        self.assertEqual(database.execute('SELECT COUNT(*) FROM WatchEvents').fetchone()[0], 0)

    async def test_movie_deep_link_and_numeric_search_show_locked_poster_card(self):
        code = movie('18+')
        with patch('handlers.start.check_user_sub', AsyncMock(return_value=True)):
            await self.send(123, text=f'/start movie_{code}')
        self.mocks['send_photo'].assert_awaited_once()
        await self.send(123, callback='catalog:all')
        await self.send(123, text=str(code))
        self.assertEqual(self.mocks['send_photo'].await_count, 2)
        self.mocks['send_video'].assert_not_awaited()
        self.assertIn('Secret description', str(self.mocks['send_photo'].call_args))
        self.assertIn('age:settings', str(self.mocks['send_photo'].call_args))

    async def test_contest_catalog_shows_restricted_cards_without_crediting_referral(self):
        contest = common.HandlerTests.create_contest(self)
        hidden = movie('18+', 'Restricted Title')
        visible = movie('0+', 'Family Title')
        await self.send(123, callback=f'contest:catalog:{contest}:0')
        self.assertIn(f'contest:card:{contest}:{visible}', str(self.mocks['send_message'].call_args))
        self.assertIn('Restricted Title', str(self.mocks['send_message'].call_args))
        self.reset()
        with patch('database.contests.confirm_card') as credit:
            await self.send(123, callback=f'contest:card:{contest}:{hidden}')
            credit.assert_not_called()
        self.mocks['send_photo'].assert_awaited_once()
        self.mocks['send_video'].assert_not_awaited()

    async def test_adult_confirmation_allows_card_and_file_without_repeated_confirmation(self):
        code = movie('18+')
        await self.send(123, callback='age:confirm')
        self.reset()
        await self.send(123, callback=f'filecard_{code}')
        self.mocks['send_photo'].assert_awaited_once()
        await self.send(123, callback=f'dlfile_{code}')
        self.mocks['send_video'].assert_awaited()
        self.assertEqual(age_access.preferences(123)['max_age'], 18)

    async def test_restriction_change_during_callback_answer_stops_file_send(self):
        code = movie('18+')
        age_access.confirm_adult(123)
        async def answer(*args, **kwargs):
            age_access.safe_mode(123)
        self.mocks['answer_callback_query'].side_effect = answer
        await self.send(123, callback=f'dlfile_{code}')
        self.mocks['send_video'].assert_not_awaited()
        self.mocks['copy_message'].assert_not_awaited()

    async def test_safe_and_parental_limits_gate_files_but_allow_all_cards(self):
        for parental, limit in ((False, 16), (True, 6)):
            if parental:
                age_access.enable_parental(123, limit, '123456')
            for rating, permitted in (('0+', True), ('6+', True), ('12+', limit >= 12),
                                      ('18+', False), (None, False)):
                with self.subTest(parental=parental, rating=rating):
                    code = movie(rating)
                    self.reset()
                    await self.send(123, callback=f'filecard_{code}')
                    self.mocks['send_photo'].assert_awaited_once()
                    if not permitted:
                        self.assertIn('age:settings', str(self.mocks['send_photo'].call_args))
                        self.assertNotIn(f'dlfile_{code}', str(self.mocks['send_photo'].call_args))
                    self.reset()
                    await self.send(123, callback=f'dlfile_{code}')
                    self.assertEqual(self.mocks['send_video'].await_count, int(permitted))

    async def test_unmarked_files_unlock_after_confirmation_and_relock_in_safe_mode(self):
        code = movie(None)
        for confirm, permitted in ((False, False), (True, True), (False, False)):
            (age_access.confirm_adult if confirm else age_access.safe_mode)(123)
            self.reset()
            await self.send(123, callback=f'dlfile_{code}')
            self.assertEqual(self.mocks['send_video'].await_count, int(permitted))

    async def test_unknown_external_posters_are_not_shown_in_safe_mode(self):
        from handlers.users.content_requests import send_missing_cards
        message = SimpleNamespace(chat=SimpleNamespace(id=123), answer=AsyncMock(), answer_photo=AsyncMock())
        with patch('handlers.users.content_requests.search', AsyncMock()) as search:
            self.assertFalse(await send_missing_cards(message, 'Adult', 'movie'))
            search.assert_not_awaited()
        message.answer_photo.assert_not_awaited()

    async def test_inline_share_shows_restricted_card_preview(self):
        from handlers.users.inline_share import inline_share
        from database.search import get_movie_from_numb
        from handlers.users.inline_share import results
        code = movie('18+', 'Restricted Title')
        token = movie_sharing.share_token(code)
        query = SimpleNamespace(query='share_' + token, from_user=SimpleNamespace(id=123), answer=AsyncMock())
        await inline_share(query)
        self.assertEqual(len(query.answer.call_args.args[0]), 2)
        info = dict(get_movie_from_numb(code))
        info.update(content_type='series', poster_image='cached-poster',
                    card_description='Secret description')
        previews = results(code, info, token, 'ExampleBot')
        self.assertEqual(len(previews), 2)
        self.assertEqual(previews[0].photo_file_id, 'cached-poster')
        self.assertIn('Restricted Title', previews[0].caption)
        self.assertIn('Secret description', previews[0].caption)
        self.assertIn('Открыть сериал', str(previews[0].reply_markup))
        self.assertIn('start=share_' + token, str(previews[0].reply_markup))
        self.assertEqual(previews[1].type, 'article')
        self.assertIn('Restricted Title', previews[1].input_message_content.message_text)

    async def test_parental_limit_keeps_all_cards_visible(self):
        await self.parent(6)
        visible = movie('6+', 'Allowed')
        movie('12+', 'Hidden')
        movie(None, 'Unmarked')
        await self.send(123, callback='catalog:all')
        markup = str(self.mocks['send_message'].call_args.kwargs['reply_markup'])
        self.assertIn(f'moviepick_{visible}', markup)
        self.assertIn('Hidden', markup)
        self.assertIn('Unmarked', markup)
