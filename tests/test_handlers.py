import contextlib
import importlib
import io
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from aiogram import Bot, Dispatcher, types
from aiogram.dispatcher.filters import StateFilter
from database.connection import database
from database.migrations import migrate
from database.people import add_user
from database import roles
from database.movies import add_new_movie
from database.torrents import add_movie_torrent
from database.categories import add_movie_category, add_movie_subcategory
import loader


class HandlerTests(unittest.IsolatedAsyncioTestCase):
    async def test_truly_empty_catalog_does_not_claim_cards_were_hidden(self):
        await self.send(123, callback='catalog_section:movies')
        text = self.mocks['send_message'].call_args.args[1]
        self.assertIn('нет доступных вам карточек', text)
        self.assertNotIn('Возрастные настройки скрывают', text)

    async def test_unrated_catalog_shows_cards_before_and_after_confirmation(self):
        from database import age_access
        age_access.safe_mode(123)
        number = add_new_movie('Hidden test')
        await self.send(123, callback='catalog_section:movies')
        result = self.mocks['send_message'].call_args
        self.assertIn(f'moviepick_{number}', str(result.kwargs['reply_markup']))
        await self.send(123, callback='age:confirm')
        await self.send(123, callback='catalog_section:movies')
        self.assertIn(f'moviepick_{number}', str(self.mocks['send_message'].call_args))

    async def test_unrated_search_opens_locked_card_without_suggesting_duplicate(self):
        from database import age_access
        age_access.safe_mode(123)
        add_new_movie('Hidden test')
        await self.send(123, callback='catalog_section:movies')
        self.mocks['send_message'].reset_mock()
        with patch('handlers.users.content_requests.send_missing_cards', AsyncMock()) as missing:
            await self.send(123, text='Hidden test')
        missing.assert_not_awaited()
        self.assertIn('возрастных настройках', str(self.mocks['send_message'].await_args_list))
        self.assertIn('Hidden test', str(self.mocks['send_message'].await_args_list))
        self.mocks['send_video'].assert_not_awaited()

    async def test_catalog_menu_is_deleted_only_after_new_message_arrives(self):
        from unittest.mock import Mock
        for callback in ('catalog_section:movies', 'catalog_section:series',
                         'find_movie_movie', 'find_movie_series'):
            with self.subTest(callback=callback):
                await self.dp.storage.reset_state(chat=123, user=123)
                events = Mock()
                self.mocks['send_message'].reset_mock()
                self.mocks['delete_message'].reset_mock()
                events.attach_mock(self.mocks['send_message'], 'send')
                events.attach_mock(self.mocks['delete_message'], 'delete')
                await self.send(123, callback=callback)
                self.assertEqual([c[0] for c in events.mock_calls], ['send', 'delete'])
                self.assertEqual(await self.dp.storage.get_state(chat=123, user=123), 'Users_:find_movie')

    async def test_catalog_delivery_failure_preserves_menu_and_reports_error(self):
        from aiogram.utils.exceptions import BadRequest
        for callback in ('catalog_section:movies', 'catalog_section:series',
                         'find_movie_movie', 'find_movie_series'):
            with self.subTest(callback=callback):
                self.mocks['delete_message'].reset_mock()
                self.mocks['send_message'].side_effect = BadRequest('simulated delivery failure')
                with self.assertLogs('handlers.users.find_movie', level='ERROR'):
                    await self.send(123, callback=callback)
                self.mocks['delete_message'].assert_not_awaited()
                self.assertTrue(self.mocks['answer_callback_query'].call_args.kwargs['show_alert'])
        self.mocks['send_message'].side_effect = None

    async def test_catalog_query_failure_preserves_menu(self):
        with patch('database.movie_queries.get_movies_page', side_effect=RuntimeError('query failed')):
            with self.assertLogs('handlers.users.find_movie', level='ERROR'):
                await self.send(123, callback='catalog_section:movies')
        self.mocks['delete_message'].assert_not_awaited()
        self.mocks['send_message'].assert_not_awaited()
        self.assertIn('Меню сохранено', str(self.mocks['answer_callback_query'].call_args))

    def create_contest(self):
        from database import contests
        from datetime import datetime, timezone
        import time
        item = contests.save(99, {'title':'Test', 'gift_name':'Gift #1', 'gift_url':'https://t.me/nft/Gift-1',
                                  'organizer_url':'https://t.me/Organizer', 'ticket_cap':20,
                                  'channel_url':'https://t.me/GramSoprano',
                                  'ends_at':datetime.fromtimestamp(time.time()+86400, timezone.utc).isoformat()})
        contests.launch(99, item['id'], item['revision'])
        return item['id']

    async def test_contest_referral_waits_for_required_subscriptions_and_resumes_catalog(self):
        from database import contests
        contest_id = self.create_contest()
        member = contests.join(contest_id, 123)
        movie = add_new_movie('Subscribed movie', movie_file='file', movie_file_type='video')
        database.execute("INSERT INTO channels_info VALUES ('first','https://t.me/FirstRequired',-100123)")
        database.execute("INSERT INTO channels_info VALUES ('second','https://t.me/SecondRequired',-100456)")
        database.commit()
        with patch('services.movie_sharing.bot_username', 'ExampleBot'), patch.object(
            loader.bot, 'get_chat_member', AsyncMock(return_value=types.ChatMember(status='left'))
        ) as membership:
            await self.send(456, text=f"/start ref_{contest_id}_{member['code']}")
            self.assertIn('Новости розыгрыша', str(self.mocks['send_message'].call_args))
            membership.assert_not_awaited()
            await self.send(456, callback=f'contest:catalog:{contest_id}:0')
            markup = self.mocks['send_message'].call_args.kwargs['reply_markup']
            buttons = [button for row in markup.inline_keyboard for button in row]
            self.assertEqual([button.url for button in buttons if button.url],
                             ['https://t.me/FirstRequired', 'https://t.me/SecondRequired'])
            retry = next(button.callback_data for button in buttons if button.text == 'Я подписался✅')
            self.assertEqual(retry, f'contest:catalog:{contest_id}:0')
            self.assertEqual(contests.personal(contest_id, 123)['tickets'], 0)
            self.assertEqual(contests.personal(contest_id, 123)['pending'], 1)
            self.assertEqual(database.execute('SELECT COUNT(*) FROM MovieShareAccess').fetchone()[0], 0)
            membership.return_value = types.ChatMember(status='member')
            membership.reset_mock()
            await self.send(456, callback=retry)
            self.assertIn(f'contest:card:{contest_id}:{movie}', str(self.mocks['send_message'].call_args))
            self.assertEqual([call.args[0] for call in membership.await_args_list], [-100123, -100456])
            self.assertTrue(all(call.kwargs['user_id'] == 456 for call in membership.await_args_list))
            self.assertEqual(contests.personal(contest_id, 123)['tickets'], 0)
            await self.send(456, callback=f'contest:card:{contest_id}:{movie}')
            self.assertEqual(contests.personal(contest_id, 123)['tickets'], 1)
            await self.send(456, callback=f'contest:card:{contest_id}:{movie}')
            self.assertEqual(contests.personal(contest_id, 123)['tickets'], 1)
            await self.send(456, callback=f'dlfile_{movie}')
            self.mocks['send_video'].assert_awaited_once()
            membership.return_value = types.ChatMember(status='left')
            await self.send(456, callback=f'dlfile_{movie}')
            self.mocks['send_video'].assert_awaited_once()

    async def test_contest_old_and_direct_card_buttons_require_subscription_in_every_section(self):
        from database import contests
        from database.catalog_sections import all_sections, set_enabled
        contest_id = self.create_contest()
        member = contests.join(contest_id, 123)
        with patch('services.movie_sharing.bot_username', 'ExampleBot'):
            await self.send(456, text=f"/start ref_{contest_id}_{member['code']}")
        for section in all_sections():
            set_enabled(section['key'], True)
        movies = [add_new_movie(section, content_type=kind, catalog_section=section,
                                poster_image='poster', movie_trailer='trailer', movie_file='file')
                  for section, kind in (('movies', 'movie'), ('series', 'series'),
                                        ('cartoons', 'movie'), ('animated_series', 'series'), ('anime', 'series'))]
        self.mocks['send_message'].reset_mock()
        callbacks = [f'contest:catalog:{contest_id}:1',
                     *(f'contest:card:{contest_id}:{movie}' for movie in movies)]
        with patch('services.subscriptions.check_subscription', AsyncMock(return_value=False)) as subscription:
            for callback in callbacks:
                with self.subTest(callback=callback):
                    await self.send(456, callback=callback)
                    self.assertIn('обязательные каналы', self.mocks['send_message'].call_args.args[1])
                    markup = self.mocks['send_message'].call_args.kwargs['reply_markup']
                    retry = next(button for row in markup.inline_keyboard for button in row
                                 if button.text == 'Я подписался✅')
                    self.assertEqual(retry.callback_data, callback)
            self.assertEqual(subscription.await_count, len(callbacks))
        self.mocks['send_photo'].assert_not_awaited()
        self.mocks['send_video'].assert_not_awaited()
        self.mocks['send_document'].assert_not_awaited()
        self.assertEqual(contests.personal(contest_id, 123)['tickets'], 0)
        self.assertEqual(contests.personal(contest_id, 123)['pending'], 1)
        self.assertEqual(database.execute('SELECT COUNT(*) FROM WatchEvents').fetchone()[0], 0)
        # The direct-card subscription button resumes exactly this card.
        with patch('services.subscriptions.check_subscription', AsyncMock(return_value=True)):
            await self.send(456, callback=retry.callback_data)
        self.assertIn('anime', self.mocks['send_photo'].call_args.kwargs['caption'])
        self.assertEqual(contests.personal(contest_id, 123)['tickets'], 1)
        self.mocks['send_photo'].reset_mock()
        self.mocks['send_video'].reset_mock()
        with patch('services.subscriptions.check_subscription', AsyncMock(return_value=False)):
            await self.send(456, callback=retry.callback_data)
        self.mocks['send_photo'].assert_not_awaited()
        self.mocks['send_video'].assert_not_awaited()

    async def test_contest_catalog_does_not_use_movie_share_access_as_subscription(self):
        from services import movie_sharing
        contest_id = self.create_contest()
        movie = add_new_movie('Shared movie')
        database.execute('INSERT INTO MovieShareAccess VALUES (?,?)', (123, movie))
        database.commit()
        self.assertTrue(movie_sharing.permits(123, f'dlfile_{movie}'))
        with patch('services.subscriptions.check_subscription', AsyncMock(return_value=False)):
            await self.send(123, callback=f'contest:catalog:{contest_id}:0')
            await self.send(123, callback=f'contest:card:{contest_id}:{movie}')
        self.assertNotIn('Shared movie', str(self.mocks['send_message'].call_args))
        self.assertIn('обязательные каналы', self.mocks['send_message'].call_args.args[1])

    async def test_contest_catalog_preserves_role_subscription_bypass(self):
        contest_id = self.create_contest()
        movie = add_new_movie('VIP movie')
        database.execute("INSERT INTO channels_info VALUES ('required','https://t.me/Required',-100123)")
        database.commit()
        role = roles.save_role('VIP')
        roles.set_bypass(role, True)
        roles.assign_role(123, role)
        with patch.object(loader.bot, 'get_chat_member', AsyncMock(return_value=types.ChatMember(status='left'))) as membership:
            await self.send(123, callback=f'contest:catalog:{contest_id}:0')
            await self.send(123, callback=f'contest:card:{contest_id}:{movie}')
            membership.assert_not_awaited()
        self.assertIn('VIP movie', self.mocks['send_message'].call_args.args[1])

    async def test_contest_subscription_api_error_keeps_invite_pending_and_shows_channels(self):
        from aiogram.utils.exceptions import ChatNotFound
        from database import contests
        contest_id = self.create_contest()
        member = contests.join(contest_id, 123)
        movie = add_new_movie('Blocked movie', poster_image='poster', movie_trailer='trailer')
        database.execute("INSERT INTO channels_info VALUES ('required','https://t.me/Required',-100123)")
        database.commit()
        with patch('services.movie_sharing.bot_username', 'ExampleBot'):
            await self.send(456, text=f"/start ref_{contest_id}_{member['code']}")
        with patch.object(loader.bot, 'get_chat_member', AsyncMock(side_effect=ChatNotFound('Chat not found'))), \
             self.assertLogs('services.subscriptions', level='WARNING'):
            await self.send(456, callback=f'contest:card:{contest_id}:{movie}')
        self.assertIn('https://t.me/Required', str(self.mocks['send_message'].call_args))
        self.mocks['send_photo'].assert_not_awaited()
        self.mocks['send_video'].assert_not_awaited()
        self.assertEqual(contests.personal(contest_id, 123)['pending'], 1)
        self.assertEqual(contests.personal(contest_id, 123)['tickets'], 0)

    async def test_contest_start_retry_after_failed_message_keeps_first_attribution(self):
        from database import contests
        contest_id = self.create_contest()
        member = contests.join(contest_id, 123)
        with patch('services.movie_sharing.bot_username', 'ExampleBot'):
            self.mocks['send_message'].side_effect = RuntimeError('Network failure')
            with self.assertRaises(RuntimeError):
                await self.send(456, text=f"/start ref_{contest_id}_{member['code']}")
            self.mocks['send_message'].side_effect = None
            await self.send(456, text=f"/start ref_{contest_id}_{member['code']}")
        self.assertEqual(database.execute('SELECT COUNT(*) FROM ContestReferrals WHERE user_id=456').fetchone()[0], 1)

    async def test_contest_failed_card_does_not_confirm_invite(self):
        from database import contests
        contest_id = self.create_contest()
        member = contests.join(contest_id, 123)
        with patch('services.movie_sharing.bot_username', 'ExampleBot'):
            await self.send(456, text=f"/start ref_{contest_id}_{member['code']}")
            movie = add_new_movie('Unavailable card')
            self.mocks['send_message'].side_effect = [RuntimeError('Card rejected'), None]
            await self.send(456, callback=f'contest:card:{contest_id}:{movie}')
        self.assertEqual(contests.personal(contest_id, 123)['tickets'], 0)
        self.assertEqual(contests.personal(contest_id, 123)['pending'], 1)

    async def test_contest_button_available_even_before_subscription_and_old_users_get_no_ticket(self):
        from database import contests
        contest_id = self.create_contest()
        member = contests.join(contest_id, 123)
        with patch('handlers.start.check_user_sub', AsyncMock(return_value=False)):
            await self.send(123, text='/start')
        self.assertIn('contest:view', str(self.mocks['send_message'].call_args))
        with patch('services.movie_sharing.bot_username', 'ExampleBot'):
            await self.send(123, text=f"/start ref_{contest_id}_{member['code']}")
            await self.send(123, text='/contest')
        self.assertEqual(database.execute('SELECT COUNT(*) FROM ContestReferrals').fetchone()[0], 0)

    async def test_contest_claim_is_restricted_to_winner_and_broken_buttons_answer(self):
        from database import contests
        contest_id = self.create_contest()
        member = contests.join(contest_id, 123)
        with patch('services.movie_sharing.bot_username', 'ExampleBot'):
            await self.send(456, text=f"/start ref_{contest_id}_{member['code']}")
            movie = add_new_movie('Movie')
            await self.send(456, callback=f'contest:card:{contest_id}:{movie}')
            contests.close(99, contest_id, contests.overview(contest_id)['token'])
            contests.draw(99, contest_id)
            await self.send(456, callback=f'contest:claim:{contest_id}')
            self.assertEqual(database.execute('SELECT COUNT(*) FROM ContestClaims').fetchone()[0], 0)
            await self.send(123, callback=f'contest:claim:{contest_id}')
            self.assertEqual(database.execute('SELECT COUNT(*) FROM ContestClaims').fetchone()[0], 1)
            self.assertIn('Связаться с организатором', str(self.mocks['send_message'].call_args))
            await self.send(123, callback='contest:view:999999')
            self.assertIn('не найден', str(self.mocks['answer_callback_query'].call_args))

    async def test_trailer_links_work_for_each_catalog_category(self):
        from handlers.users.find_movie import _build_pick_markup
        from database.search import get_movie_from_numb

        with patch("services.trailers.find_trailer", AsyncMock(return_value="https://youtu.be/abcdefghijk")) as lookup, \
             patch("services.tmdb.TMDB_API_KEY", "offline-key"), \
             patch("services.subscriptions.check_subscription", AsyncMock(return_value=True)):
            for section, kind in (("movies", "movie"), ("series", "series"),
                                  ("animated_series", "series"), ("cartoons", "movie"), ("anime", "series")):
                code = add_new_movie(section, content_type=kind, catalog_section=section)
                markup = _build_pick_markup(code, get_movie_from_numb(code), 123)
                self.assertIn(f"trailer_{code}", str(markup))
                await self.send(123, callback=f"trailer_{code}")
                lookup.assert_awaited_with(section, kind)
                self.assertIn("https://youtu.be/abcdefghijk", str(self.mocks["send_message"].call_args))

    async def test_general_search_opens_all_categories_and_clears_filter(self):
        from keyboards.users.keyboard import find_movie_markup

        first = find_movie_markup().inline_keyboard[0][0]
        self.assertEqual(first.text, "🔎 Общий поиск")
        self.assertEqual(first.callback_data, "catalog:all")
        movie = add_new_movie("Film", content_type="movie", catalog_section="movies")
        series = add_new_movie("Show", content_type="series", catalog_section="series")
        cartoon = add_new_movie("Cartoon", content_type="series", catalog_section="animated_series")
        await self.dp.storage.update_data(chat=123, user=123, data={"catalog_section": "movies", "content_type": "movie"})
        with patch("services.subscriptions.check_subscription", AsyncMock(return_value=True)):
            await self.send(123, callback="catalog:all")
        result = str(self.mocks["send_message"].call_args)
        for code in (movie, series, cartoon):
            self.assertIn(f"moviepick_{code}", result)
        self.assertIn("Общий поиск", result)
        data = await self.dp.storage.get_data(chat=123, user=123)
        self.assertIsNone(data["content_type"])
        self.assertIsNone(data["catalog_section"])

    async def test_missing_search_card_has_poster_description_and_one_action(self):
        from handlers.users.content_requests import send_missing_cards
        message = SimpleNamespace(chat=SimpleNamespace(id=123), answer_photo=AsyncMock(), answer=AsyncMock())
        candidate = {"tmdb_id": 55, "title": "Редкий фильм", "year": "1998",
                     "overview": "Краткое описание", "poster_url": "https://image.tmdb.org/p.jpg"}
        with patch("handlers.users.content_requests.search", AsyncMock(return_value=[candidate])):
            self.assertTrue(await send_missing_cards(message, "Редкий фильм", "movie"))
        call = message.answer_photo.call_args
        self.assertIn("Краткое описание", call.kwargs["caption"])
        buttons = call.kwargs["reply_markup"].inline_keyboard
        self.assertEqual(sum(len(row) for row in buttons), 1)
        self.assertEqual(buttons[0][0].callback_data, "request:quick:movie:55")

    async def test_voice_button_retains_source_in_request_and_admin(self):
        proof = {'source': 'kodik', 'translation_id': '77', 'checked_at': '2026-10-25T12:00:00+00:00'}
        options = {'status': 'ok', 'voices': [{'name': 'Studio A', 'evidence': [proof]}]}
        with (patch("handlers.users.content_requests.check_subscription", AsyncMock(return_value=True)),
              patch("handlers.users.content_requests.details", AsyncMock(return_value={
                  'title': 'Show', 'year': '2020', 'tmdb_id': 42})),
              patch("handlers.users.content_requests.lookup_voices", AsyncMock(return_value=options))):
            await self.send(123, callback="request:quick:movie:42")
            data = await self.dp.storage.get_data(chat=123, user=123)
            token = data['request_voice_token']
            self.assertIn('Studio A', str(self.mocks['send_message'].call_args))
            await self.send(123, callback=f"request:voice:pick:{token}:0")
            self.assertEqual(await self.dp.storage.get_state(chat=123, user=123), 'Request:confirm')
            await self.send(123, callback="request:confirm")
            from database import content_requests
            import json
            item = content_requests.web_page()['items'][0]
            self.assertEqual(json.loads(item['voice_evidence']), [proof])
            self.assertIn('kodik', item['voice_evidence_note'])
            await self.send(99, callback=f"requests:show:{item['id']}")
            self.assertIn('kodik', str(self.mocks['send_message'].call_args))
            self.assertIn('отдельной проверки', str(self.mocks['send_message'].call_args))

    async def test_voice_pages_reject_stale_buttons_and_allow_manual_input(self):
        options = {'status': 'ok', 'voices': [{'name': f'Studio {i}', 'evidence': []} for i in range(12)]}
        with (patch("handlers.users.content_requests.check_subscription", AsyncMock(return_value=True)),
              patch("handlers.users.content_requests.details", AsyncMock(return_value={
                  'title': 'Film', 'year': '2020', 'tmdb_id': 42})),
              patch("handlers.users.content_requests.lookup_voices", AsyncMock(return_value=options))):
            await self.send(123, callback="request:quick:movie:42")
            data = await self.dp.storage.get_data(chat=123, user=123)
            token = data['request_voice_token']
            for callback in ('request:voice:pick:oldtoken:0', f'request:voice:pick:{token}:999',
                             f'request:voices:{token}:999'):
                await self.send(123, callback=callback)
                self.assertEqual(await self.dp.storage.get_state(chat=123, user=123), 'Request:voice')
            await self.send(123, callback=f'request:voices:{token}:10')
            self.assertIn('Studio 11', str(self.mocks['send_message'].call_args))
            await self.send(123, callback='request:voice:manual')
            await self.send(123, text='My Studio')
            await self.send(123, callback='request:confirm')
            from database import content_requests
            item = content_requests.web_page()['items'][0]
            self.assertEqual((item['voice'], item['voice_evidence']), ('my studio', '[]'))

    async def test_unavailable_voice_provider_keeps_any_voice_request_working(self):
        with (patch("handlers.users.content_requests.check_subscription", AsyncMock(return_value=True)),
              patch("handlers.users.content_requests.details", AsyncMock(return_value={
                  'title': 'Film', 'year': '2020', 'tmdb_id': 42})),
              patch("handlers.users.content_requests.lookup_voices", AsyncMock(return_value={
                  'status': 'unavailable', 'voices': []}))):
            await self.send(123, callback='request:quick:movie:42')
            self.assertIn('недоступен', str(self.mocks['send_message'].call_args))
            await self.send(123, callback='request:voice:any')
            await self.send(123, callback='request:confirm')
            from database import content_requests
            self.assertEqual(content_requests.web_page()['items'][0]['voice'], '')

    async def test_quick_series_request_requires_parameters_before_vote(self):
        with (patch("handlers.users.content_requests.check_subscription", AsyncMock(return_value=True)),
              patch("handlers.users.content_requests.details", AsyncMock(return_value={
                  'title': 'Show', 'year': '2020', 'tmdb_id': 42})),
              patch("handlers.users.content_requests.series_structure", AsyncMock(return_value=None))):
            await self.send(123, callback="request:quick:series:42")
            self.assertEqual(await self.dp.storage.get_state(chat=123, user=123), 'Request:season')
            self.assertEqual(database.execute('SELECT count(*) FROM ContentRequests').fetchone()[0], 0)
            await self.send(123, text="2")
            await self.send(123, text="3")
            await self.send(123, text="Студия А")
            await self.send(123, callback="request:confirm")
            from database import content_requests
            item = content_requests.web_page()['items'][0]
            self.assertEqual((item['season'], item['episode'], item['voice']), (2, 3, 'студия а'))
            self.assertIn('ручная проверка', item['verification'])

    async def test_episode_release_validation_and_manual_fallback(self):
        from handlers.users.content_requests import verify_target
        state = SimpleNamespace(get_data=AsyncMock(return_value={
            "request_kind": "series", "request_selected": {"tmdb_id": 42},
            "request_season": 2, "request_episode": 3}),
            set_state=AsyncMock(), update_data=AsyncMock())
        message = SimpleNamespace(answer=AsyncMock())
        structure = {"status": "Ended", "seasons": [{"season_number": 2}]}
        for air_date, valid in [("2001-01-01", True), ("2999-01-01", False), (None, False)]:
            with patch("handlers.users.content_requests.series_structure", AsyncMock(side_effect=[
                structure, {"episodes": [{"episode_number": 3, "air_date": air_date}]}])):
                self.assertEqual(await verify_target(message, state), valid)
        with patch("handlers.users.content_requests.series_structure", AsyncMock(return_value=None)):
            self.assertTrue(await verify_target(message, state))
            self.assertIn("ручная проверка", state.update_data.call_args.kwargs['request_verification'])
        with patch("handlers.users.content_requests.series_structure", AsyncMock(return_value={
                "status": "Canceled", "seasons": [{"season_number": 1}]})):
            self.assertFalse(await verify_target(message, state))
            state.set_state.assert_awaited_with("Request:season")

    async def test_user_can_request_series_season_and_duplicate_vote_is_unique(self):
        from database import content_requests as requests

        for _ in range(2):
            with (
                patch("handlers.users.content_requests.check_subscription", AsyncMock(return_value=True)),
                patch("handlers.users.content_requests.search", AsyncMock(return_value=[])),
            ):
                await self.send(123, callback="request:new")
                await self.send(123, callback="request:type:series")
                await self.send(123, text="Example (2025)")
                await self.send(123, callback="request:pick:0")
                await self.send(123, text="2")
                await self.send(123, text="0")
                await self.send(123, callback="request:voice:any")
                await self.send(123, callback="request:confirm")
        rows = requests.page()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][2:4], ("series", 2))
        self.assertEqual(rows[0][-1], 1)
        await self.send(123, callback=f"requests:status:{rows[0][0]}:planned")
        self.assertEqual(requests.get(rows[0][0])["status"], "new")
        await self.send(99, callback=f"requests:status:{rows[0][0]}:planned")
        self.assertEqual(requests.get(rows[0][0])["status"], "planned")

    async def test_request_completion_targets_only_voters_and_is_idempotent(self):
        from database import content_requests as requests

        request = requests.vote(123, "Wanted", "movie")
        requests.vote(456, "Wanted", "movie", tmdb_id=1234)
        requests.vote(789, "Other", "movie")
        number = add_new_movie("Wanted")
        job = requests.complete(request["id"], number, 99, 99, 10)
        again = requests.complete(request["id"], number, 99, 99, 11)
        self.assertEqual(job, again)
        recipients = database.execute(
            "SELECT user_id FROM BroadcastRecipients WHERE job_id=?", (job,)
        ).fetchall()
        self.assertEqual({r[0] for r in recipients}, {123, 456})
        self.assertEqual(requests.get(request["id"])["status"], "added")
        season = requests.vote(123, "Wanted", "series", 2)
        series = add_new_movie("Wanted", content_type="series")
        with self.assertRaises(ValueError):
            requests.complete(season["id"], series, 99, 99, 12)
        category = add_movie_category(series, "Season 2", 2)
        add_movie_subcategory(category, "Серия 1", "file", "video")
        requests.complete(season["id"], series, 99, 99, 12)

    async def test_missing_search_seeds_request_and_existing_movie_opens(self):
        with (
            patch("handlers.users.content_requests.check_subscription", AsyncMock(return_value=True)),
            patch("handlers.users.content_requests.search", AsyncMock(return_value=[])),
        ):
            state = self.dp.current_state(chat=123, user=123)
            await state.update_data(missing_query="Known (2025)", missing_kind="movie")
            add_new_movie("Known (2025)", movie_file="file", movie_file_type="video")
            await self.send(123, callback="request:search")
            self.assertEqual((await state.get_data())["request_choices"][0]["title"], "Known (2025)")
            await self.send(123, callback="request:pick:0")
            await self.send(123, callback="request:voice:any")
            self.assertEqual(database.execute("SELECT count(*) FROM ContentRequests").fetchone()[0], 0)
            self.assertIsNone(await state.get_state())

    async def test_file_return_opens_card_without_deleting_video(self):
        number = add_new_movie('Return movie', movie_file='video', movie_file_type='video')
        with patch('services.subscriptions.check_subscription', AsyncMock(return_value=True)):
            await self.send(123, callback=f'dlfile_{number}')
            markup = self.mocks['send_video'].call_args.kwargs['reply_markup']
            self.assertIn(f'filecard_{number}', str(markup))
            await self.send(123, callback=f'filecard_{number}', document='video')
        self.mocks['delete_message'].assert_not_awaited()
        self.assertIn('Return movie', str(self.mocks['send_message'].call_args))
        category = add_movie_category(number, '1080p')
        sub = add_movie_subcategory(category, 'Dub', 'file', 'video')
        from handlers.users.find_movie import _episode_file_markup
        self.assertIn(f'filecard_{number}', str(_episode_file_markup(sub,123)))

    async def test_share_button_without_startup_username_recovers_on_click(self):
        from services import movie_sharing
        from handlers.users.find_movie import _build_pick_markup
        from database.search import get_movie_from_numb
        from types import SimpleNamespace
        number = add_new_movie('Share fallback')
        with patch.object(movie_sharing, 'bot_username', None):
            markup = _build_pick_markup(number, get_movie_from_numb(number), 123)
            self.assertIn('switch_inline_query', str(markup))
            with patch('services.subscriptions.check_subscription', AsyncMock(return_value=True)), patch.object(loader.bot, 'get_me', AsyncMock(return_value=SimpleNamespace(username='ExampleBot'))):
                await self.send(123, callback=f'sharefilm_{number}')
            self.assertIn('https://t.me/share/url', str(self.mocks['send_message'].call_args))
            self.assertIn('switch_inline_query', str(self.mocks['edit_message_reply_markup'].call_args))

    async def test_inline_share_returns_photo_and_text_but_no_public_search(self):
        from handlers.users.inline_share import inline_share, results
        from services import movie_sharing
        from types import SimpleNamespace
        from database.search import get_movie_from_numb
        number=add_new_movie('Photo movie',poster_image='cached-photo',card_description='<b>Description</b>')
        database.execute("UPDATE Movies SET age_rating='0+' WHERE movie_number=?", (number,))
        token=movie_sharing.share_token(number)
        query=SimpleNamespace(query='share_'+token,from_user=SimpleNamespace(id=123),answer=AsyncMock())
        with patch.object(movie_sharing,'bot_username','ExampleBot'):
            await inline_share(query)
        choices=query.answer.call_args.args[0]
        self.assertEqual(choices[0].photo_file_id,'cached-photo')
        self.assertIn('Description',choices[0].caption)
        self.assertNotIn('<b>',choices[0].caption)
        self.assertIn('start=share_',str(choices[0].reply_markup))
        self.assertEqual(choices[1].type,'article')
        info=get_movie_from_numb(number)
        info['poster_image']='https://example.com/poster.jpg'
        self.assertEqual(results(number,info,token,'ExampleBot')[0].photo_url,info['poster_image'])
        for value in ('Photo movie','share_'+('x'*32)):
            query.query=value;query.answer.reset_mock()
            await inline_share(query)
            self.assertEqual(query.answer.call_args.args[0],[])

    async def test_shared_movie_bypasses_only_its_files_and_keeps_catalog_locked(self):
        from services import movie_sharing
        from urllib.parse import urlparse, parse_qs

        movie_sharing.bot_username = "ExampleBot"
        number = add_new_movie("Shared", movie_file="shared-file", movie_file_type="video")
        other = add_new_movie("Other", movie_file="private-file", movie_file_type="video")
        url = movie_sharing.share_url(number, "Shared")
        link = parse_qs(urlparse(url).query)["url"][0]
        payload = parse_qs(urlparse(link).query)["start"][0]
        with patch("handlers.start.check_user_sub", AsyncMock()) as subscription:
            await self.send(123, text="/start " + payload)
            subscription.assert_not_awaited()
        markup = self.mocks["send_message"].call_args.kwargs["reply_markup"]
        self.assertIn("Поделиться фильмом", str(markup))
        with patch(
            "services.subscriptions.check_subscription", AsyncMock(return_value=False)
        ) as subscription:
            await self.send(123, callback=f"dlfile_{number}")
            subscription.assert_not_awaited()
            self.mocks["send_video"].assert_awaited_once()
            await self.send(123, callback=f"dlfile_{other}")
            self.assertEqual(self.mocks["send_video"].await_count, 1)
            await self.send(123, callback=f"movcatback_{number}")
            self.assertEqual(subscription.await_count, 2)
        with patch(
            "handlers.users.find_movie.check_subscription", AsyncMock(return_value=False)
        ) as subscription:
            await self.send(123, callback="catalog:home")
            subscription.assert_awaited_once()
        self.assertIsNone(movie_sharing.accept(777, "share_" + "a" * 32))
        self.assertFalse(movie_sharing.permits(777, f"dlfile_{number}"))

    async def test_shared_categories_and_torrents_resolve_actual_movie(self):
        from services import movie_sharing

        movie_sharing.bot_username = "ExampleBot"
        first, second = add_new_movie("First"), add_new_movie("Second")
        movie_sharing.share_url(first, "First")
        token = database.execute(
            "SELECT token FROM MovieShareLinks WHERE movie_number=?", (first,)
        ).fetchone()[0]
        movie_sharing.accept(123, "share_" + token)
        for number in (first, second):
            category = add_movie_category(number, "1080p")
            sub = add_movie_subcategory(category, "Dub", "file", "video")
            torrent = add_movie_torrent(number, "Torrent", "file")
            for data in (
                f"filecard_{number}", f"movcat_{category}",
                f"movsub_{sub}",
                f"gettorrent_{torrent}",
                f"wp:f:{number}:1:c:0",
            ):
                self.assertEqual(movie_sharing.permits(123, data), number == first, data)
        self.assertFalse(movie_sharing.permits(123, f"movcatback_{first}"))
        self.assertFalse(movie_sharing.permits(123, "catalog:home"))

    async def test_movie_deep_link_opens_after_subscription(self):
        number = add_new_movie("Linked movie")
        with patch("handlers.start.check_user_sub", AsyncMock(return_value=False)):
            await self.send(123, text=f"/start movie_{number}")
        self.assertEqual(
            (await self.dp.current_state(chat=123, user=123).get_data())["pending_movie"], number
        )
        with (
            patch("handlers.users.check_user_sub.check_subscription", AsyncMock(return_value=True)),
            patch("handlers.start.open_linked_movie", AsyncMock()) as opened,
        ):
            await self.send(123, callback="check")
            opened.assert_awaited_once_with(123, 123, number)
        self.assertIsNone((await self.dp.current_state(chat=123, user=123).get_data())["pending_movie"])

    async def test_movie_deep_link_opens_directly_for_subscriber(self):
        with (
            patch("handlers.start.check_user_sub", AsyncMock(return_value=True)),
            patch("handlers.start.open_linked_movie", AsyncMock()) as opened,
        ):
            await self.send(123, text="/start movie_42")
            opened.assert_awaited_once_with(123, 123, 42)

    async def test_start_reactivates_user_and_exits_old_dialogue(self):
        database.execute("UPDATE Users SET is_active=0 WHERE id=?", (123,))
        database.commit()
        await self.dp.storage.set_state(chat=123, user=123, state="Users_:find_movie")
        with patch("handlers.start.check_user_sub", AsyncMock(return_value=True)):
            await self.send(123, text="/start")
        self.mocks["send_message"].assert_awaited_once()
        self.assertIsNone(await self.dp.storage.get_state(chat=123, user=123))
        self.assertEqual(database.execute("SELECT is_active FROM Users WHERE id=?", (123,)).fetchone()[0], 1)

    async def test_back_from_file_restores_card_then_catalog_then_home(self):
        series = add_new_movie("Series", content_type="series")
        database.execute("UPDATE Movies SET poster_image=? WHERE movie_number=?", ("poster", series))
        database.commit()
        season = add_movie_category(series, "Season 1", 1)
        episode = add_movie_subcategory(season, "Episode 1", "file", "document")
        await self.send(123, callback=f"wpage:{season}:0", document="file")
        self.mocks["send_photo"].assert_awaited_once()
        self.mocks["edit_message_reply_markup"].assert_not_awaited()
        self.assertIn(f"movsub_{episode}", str(self.mocks["send_photo"].call_args))
        await self.send(123, callback=f"movcatback_{series}")
        self.assertIn("catalog:home", str(self.mocks["send_message"].call_args))
        await self.send(123, callback="catalog:home")
        self.assertIn("KinoTime", str(self.mocks["send_message"].call_args))
        self.assertIsNone(await self.dp.storage.get_state(chat=123, user=123))

    async def test_back_from_card_restores_exact_catalog_section(self):
        animated = add_new_movie(
            "Animated show",
            content_type="series",
            catalog_section="animated_series",
        )
        regular = add_new_movie("Regular show", content_type="series", catalog_section="series")

        await self.send(123, callback=f"movcatback_{animated}")

        call = self.mocks["send_message"].call_args
        self.assertIn("Мультсериалы", call.args[1])
        callbacks = [
            button.callback_data
            for row in call.kwargs["reply_markup"].inline_keyboard
            for button in row
        ]
        self.assertIn(f"moviepick_{animated}", callbacks)
        self.assertNotIn(f"moviepick_{regular}", callbacks)

    async def test_episode_file_buttons_navigation_and_personal_mark(self):
        from database import progress

        series = add_new_movie("Series", content_type="series")
        season = add_movie_category(series, "Season 1", 1)
        first = add_movie_subcategory(season, "Episode 1", "video1", "video")
        last = add_movie_subcategory(season, "Episode 2", "doc2", "document")
        with patch("services.subscriptions.check_subscription", AsyncMock(return_value=True)):
            await self.send(123, callback=f"movsub_{first}")
            self.assertIn(f"epvoices:{first}:0", str(self.mocks["send_message"].call_args))
            await self.send(123, callback=f"epquality:{first}:0")
            markup = str(self.mocks["send_video"].call_args.kwargs["reply_markup"])
            self.assertIn(f"epnext:{first}:0:{last}", markup)
            self.assertNotIn("Предыдущая серия", markup)
            self.assertIn(f"wp:e:{first}:1:m:0", markup)
            await self.send(123, callback=f"wp:e:{first}:1:m:0")
            self.assertIn(first, progress.episode_marks(123, season))
            self.assertNotIn(first, progress.episode_marks(99, season))
            updated = str(self.mocks["edit_message_reply_markup"].call_args)
            self.assertIn(f"wp:e:{first}:0:m:0", updated)
            self.assertIn(f"epnext:{first}:0:{last}", updated)
            await self.send(123, callback=f"movsub_{last}")
            await self.send(123, callback=f"epquality:{last}:0")
            markup = str(self.mocks["send_document"].call_args.kwargs["reply_markup"])
            self.assertIn(f"epnext:{last}:0:{first}", markup)
            self.assertNotIn("Следующая серия", markup)
            await self.send(123, callback=f"wpage:{season}:0")
            self.assertIn(f"movsub_{first}", str(self.mocks["edit_message_reply_markup"].call_args))

    async def test_quality_selection_sends_exact_variant_and_keeps_episode_mark(self):
        from database.episode_variants import add_series_file, variants
        series = add_new_movie('Series', content_type='series')
        season = add_movie_category(series, 'Season 1', 1)
        with database.transaction():
            episode = add_series_file(season, {'name':'Episode 1','quality':'1080p'}, {'file_id':'full','file_type':'video'})
            add_series_file(season, {'name':'Episode 1','quality':'720p'}, {'file_id':'small','file_type':'video'})
        selected = variants(episode)[1][0]
        await self.send(123, callback=f'movsub_{episode}')
        self.assertIn('1080p', str(self.mocks['send_message'].call_args))
        await self.send(123, callback=f'epquality:{episode}:{selected}')
        self.assertEqual(self.mocks['send_video'].call_args.kwargs['video'], 'small')
        self.assertIn(f'wp:e:{episode}:1:m:{selected}', str(self.mocks['send_video'].call_args))

    async def test_start_exits_admin_dialogues_without_changing_design(self):
        from database.design import get_design, DEFAULT_TEXT

        for current in ("DesignEdit:text", "DesignEdit:photo", "WebInbox:collecting", "RoleStates:name"):
            with self.subTest(state=current):
                await self.dp.storage.set_state(chat=99, user=99, state=current)
                self.mocks["send_message"].reset_mock()
                with patch("handlers.start.check_user_sub", AsyncMock(return_value=True)):
                    await self.send(99, text="/start")
                self.mocks["send_message"].assert_awaited_once()
                self.assertIn("KinoTime", str(self.mocks["send_message"].call_args))
                self.assertIsNone(await self.dp.storage.get_state(chat=99, user=99))
                self.assertEqual(get_design()["text"], DEFAULT_TEXT)

    async def test_missing_subscription_channel_does_not_silence_start_or_admin(self):
        from aiogram.utils.exceptions import ChatNotFound

        with (
            patch("services.subscriptions.get_all_channels_cod", return_value=[(-100123,)]),
            patch.object(
                loader.bot, "get_chat_member", AsyncMock(side_effect=ChatNotFound("Chat not found"))
            ),
        ):
            await self.send(123, text="/start")
            self.mocks["send_message"].assert_awaited_once()
            self.mocks["send_message"].reset_mock()
            await self.send(99, text="/admin")
            self.mocks["send_message"].assert_awaited_once()
            self.assertIn("Админ-панель", str(self.mocks["send_message"].call_args))

    @classmethod
    def setUpClass(cls):
        # Related fixtures may register the handlers first during test discovery.
        # Recreating the dispatcher would leave cached handlers on the old instance.
        if loader.dp is None:
            loader.initialize("123456:FAKE_TOKEN_FOR_OFFLINE_TESTS")
        importlib.import_module("handlers")
        cls.dp = loader.dp

    async def asyncSetUp(self):
        database.open(":memory:")
        with contextlib.redirect_stdout(io.StringIO()):
            migrate()
        Bot.set_current(loader.bot)
        Dispatcher.set_current(self.dp)
        self.dp.storage.data.clear()
        self.mocks = {}
        for name in (
            "send_message",
            "answer_callback_query",
            "delete_message",
            "edit_message_text",
            "edit_message_reply_markup",
            "copy_message",
            "send_document",
            "send_video",
            "send_photo",
            "get_me",
        ):
            mock = (AsyncMock(return_value=SimpleNamespace(username="ExampleBot", id=123456,
                                                          first_name="ExampleBot"))
                    if name == "get_me" else AsyncMock())
            self.mocks[name] = mock
            patcher = patch.object(loader.bot, name, mock)
            patcher.start()
            self.addCleanup(patcher.stop)
        patcher = patch("data.config.ADMIN", [99])
        patcher.start()
        self.addCleanup(patcher.stop)
        add_user(123)
        # Existing behavior tests use explicitly opted-in adults. Age tests reset this preference.
        from database import age_access
        for user_id in (123, 99, 456, 789):
            age_access.confirm_adult(user_id)
            age_access.mark_notified(user_id)

    async def asyncTearDown(self):
        database.close()

    async def send(self, user_id, text=None, callback=None, document=None, photo=None):
        # StateFilter caches the raw state within one Telegram update context.
        StateFilter.ctx_state.set(await self.dp.storage.get_state(chat=user_id, user=user_id))
        message = {
            "message_id": 10,
            "date": 1,
            "chat": {"id": user_id, "type": "private"},
            "from": {"id": user_id, "is_bot": False, "first_name": "Test"},
            "text": text or "menu",
        }
        if document:
            message.pop("text")
            message["document"] = {"file_id": document, "file_unique_id": "fake"}
        if photo:
            message.pop("text", None)
            message["photo"] = [{"file_id": photo, "file_unique_id": "photo", "width": 1280, "height": 720}]
        payload = {"update_id": 1}
        if callback is not None:
            payload["callback_query"] = {
                "id": "test",
                "from": message["from"],
                "chat_instance": "test",
                "message": message,
                "data": callback,
            }
        else:
            payload["message"] = message
        return await self.dp.process_update(types.Update(**payload))

    async def test_design_photo_and_start_send_one_welcome(self):
        from database.design import get_design

        await self.send(99, callback="design:photo")
        await self.send(99, photo="welcome_photo_id")
        self.assertEqual(get_design()["photo"], "welcome_photo_id")
        self.mocks["send_photo"].reset_mock()
        self.mocks["send_message"].reset_mock()
        with patch("handlers.start.check_user_sub", AsyncMock(return_value=True)):
            await self.send(123, text="/start")
        self.mocks["send_photo"].assert_awaited_once()
        self.mocks["send_message"].assert_not_awaited()
        self.assertIn("KinoTime", self.mocks["send_photo"].call_args.kwargs["caption"])

    async def test_design_admin_access_and_text_validation(self):
        from database.design import get_design, DEFAULT_TEXT

        await self.send(123, callback="design:text")
        self.assertIsNone(await self.dp.current_state(chat=123, user=123).get_state())
        await self.send(99, callback="design:text")
        await self.send(99, text="a" * 801)
        self.assertEqual(get_design()["text"], DEFAULT_TEXT)
        await self.send(99, text="Мой <текст> 🍿")
        self.assertEqual(get_design()["text"], "Мой <текст> 🍿")
        await self.send(99, callback="design:confirm_reset")
        self.assertEqual(get_design()["text"], DEFAULT_TEXT)

    async def test_welcome_invalid_photo_falls_back_and_subscription_stays_required(self):
        from database.design import set_design
        from aiogram.utils.exceptions import BadRequest

        set_design("welcome_photo", "expired")
        self.mocks["send_photo"].side_effect = BadRequest("Wrong file identifier")
        with patch("handlers.start.check_user_sub", AsyncMock(return_value=False)):
            await self.send(123, text="/start")
        self.mocks["send_message"].assert_awaited_once()
        args = self.mocks["send_message"].call_args
        self.assertIn("подпишись", args.args[1])
        self.assertIn("check", str(args.kwargs["reply_markup"]))

    async def test_admin_callbacks_block_regular_user_before_database_changes(self):
        for callback in ("role:create", "role:home", "add_cod", "send_func", "bot_stat", "broadcast:list"):
            await self.send(123, callback=callback)
            self.mocks["answer_callback_query"].assert_awaited()
        self.assertEqual(roles.list_roles(), [])
        self.assertIsNone(await self.dp.current_state(chat=123, user=123).get_state())
        self.mocks["send_message"].assert_not_awaited()

    async def test_role_create_configure_assign_revoke_through_dispatcher(self):
        await self.send(99, callback="role:create")
        await self.send(99, text="VIP <test>")
        role = roles.list_roles()[0]
        self.assertEqual(role[1], "VIP <test>")
        self.assertIn("&lt;test&gt;", self.mocks["send_message"].call_args.kwargs["text"])
        await self.send(99, callback=f"role:bypass:{role[0]}:1")
        await self.send(99, callback="role:user")
        await self.send(99, text="123")
        await self.send(99, callback=f"role:assign:{role[0]}")
        self.assertTrue(roles.bypasses_subscription(123))
        await self.send(99, callback="role:user")
        await self.send(99, text="123")
        await self.send(99, callback="role:assign:0")
        self.assertFalse(roles.bypasses_subscription(123))

    async def test_admin_state_message_is_still_protected(self):
        from handlers.admin.roles import RoleStates

        await self.dp.current_state(chat=123, user=123).set_state(RoleStates.name.state)
        await self.send(123, text="Forbidden")
        self.assertEqual(roles.list_roles(), [])

    async def test_old_download_buttons_recheck_subscription_and_honor_role(self):
        movie = add_new_movie("Test", movie_file="fake", movie_file_type="document")
        torrent = add_movie_torrent(movie, "torrent", "fake")
        category = add_movie_category(movie, "audio")
        episode = add_movie_subcategory(category, "episode", "fake", "document")
        database.execute("INSERT INTO channels_info VALUES ('test','https://t.me/test',-100123)")
        database.commit()
        with patch.object(
            loader.bot, "get_chat_member", AsyncMock(return_value=types.ChatMember(status="left"))
        ) as check:
            for callback in (f"dlfile_{movie}", f"gettorrent_{torrent}", f"movsub_{episode}"):
                await self.send(123, callback=callback)
            self.mocks["send_document"].assert_not_awaited()
            role = roles.save_role("VIP")
            roles.set_bypass(role, True)
            roles.assign_role(123, role)
            check.reset_mock()
            for callback in (f"dlfile_{movie}", f"gettorrent_{torrent}", f"movsub_{episode}"):
                await self.send(123, callback=callback)
            self.assertEqual(self.mocks["send_document"].await_count, 3)
            check.assert_not_awaited()

    async def test_broadcast_needs_confirmation_and_queues_only_once(self):
        await self.send(99, callback="send_func")
        await self.send(99, text="Рассылка")
        self.assertEqual(database.execute("SELECT COUNT(*) FROM BroadcastJobs").fetchone()[0], 0)
        await self.send(99, callback="send_yes")
        await self.send(99, callback="send_yes")
        self.assertEqual(database.execute("SELECT COUNT(*) FROM BroadcastJobs").fetchone()[0], 1)

    async def test_simple_movie_creation_after_module_split(self):
        with patch("services.tmdb.search", AsyncMock(return_value=[])):
            await self.send(99, callback="addtype_movie")
            await self.send(99, text="Тестовый фильм")
        for _ in range(3):
            await self.send(99, callback="skip_step")
        await self.send(99, callback="filemode_simple")
        await self.send(99, document="fake_movie")
        row = database.execute("SELECT movie_title,movie_file,content_type FROM Movies").fetchone()
        self.assertEqual(row, ("Тестовый фильм", "fake_movie", "movie"))
        self.assertIsNone(await self.dp.current_state(chat=99, user=99).get_state())

    async def test_series_category_creation_after_module_split(self):
        with patch("services.tmdb.search", AsyncMock(return_value=[])):
            await self.send(99, callback="addtype_series")
            await self.send(99, text="Тестовый сериал")
        for _ in range(3):
            await self.send(99, callback="skip_step")
        await self.send(99, callback="filemode_categories")
        await self.send(99, text="1")
        await self.send(99, text="Серия 1")
        await self.send(99, document="fake_episode")
        await self.send(99, callback="catbuild_finish_category")
        await self.send(99, callback="catbuild_finish")
        self.assertEqual(database.execute("SELECT content_type FROM Movies").fetchone()[0], "series")
        self.assertEqual(database.execute("SELECT season_number FROM MovieCategories").fetchone()[0], 1)
        self.assertEqual(
            database.execute("SELECT file_id FROM MovieSubcategories").fetchone()[0], "fake_episode"
        )

    async def test_personal_watch_buttons_refresh_labels_and_allow_unmarking(self):
        from database import progress

        series = add_new_movie("Series", content_type="series")
        season = add_movie_category(series, "Сезон 1", 1)
        episode = add_movie_subcategory(season, "Серия 1", "fake", "document")
        await self.send(123, callback=f"movcat_{season}")
        markup = self.mocks["send_message"].call_args.kwargs["reply_markup"]
        self.assertIn("Серия 1 ❌", [b.text for row in markup.inline_keyboard for b in row])
        await self.send(123, callback=f"wp:e:{episode}:1:p:0")
        markup = self.mocks["edit_message_reply_markup"].call_args.kwargs["reply_markup"]
        self.assertIn("Серия 1 ✅", [b.text for row in markup.inline_keyboard for b in row])
        self.assertEqual(progress.totals(123), (0, 1))
        self.assertEqual(progress.totals(124), (0, 0))
        await self.send(123, callback=f"movcatback_{series}")
        markup = self.mocks["send_message"].call_args.kwargs["reply_markup"]
        self.assertIn(f"moviepick_{series}", [b.callback_data for row in markup.inline_keyboard for b in row])
        await self.send(123, callback=f"wp:e:{episode}:0:p:0")
        self.assertEqual(progress.totals(123), (0, 0))

    async def test_progress_subscription_and_admin_statistics_access(self):
        from database import progress

        movie = add_new_movie("Film")
        database.execute("INSERT INTO channels_info VALUES ('test','https://t.me/test',-100123)")
        database.commit()
        with patch.object(
            loader.bot, "get_chat_member", AsyncMock(return_value=types.ChatMember(status="left"))
        ):
            await self.send(123, callback=f"wp:f:{movie}:1:c:0")
            self.assertEqual(progress.totals(123), (0, 0))
            role = roles.save_role("VIP")
            roles.set_bypass(role, True)
            roles.assign_role(123, role)
            await self.send(123, callback=f"wp:f:{movie}:1:c:0")
            self.assertEqual(progress.totals(123), (1, 0))
        self.mocks["send_message"].reset_mock()
        await self.send(123, callback="watchstats:user:123:0")
        self.mocks["send_message"].assert_not_awaited()
        await self.send(99, callback="watchstats:films:123:0")
        self.assertIn("Film", self.mocks["send_message"].call_args.kwargs["text"])

    async def test_admin_can_see_exact_watched_episode_without_counting_downloads(self):
        series = add_new_movie("Series", content_type="series")
        season = add_movie_category(series, "Сезон 1", 1)
        episode = add_movie_subcategory(season, "Серия 1", "fake", "document")
        await self.send(123, callback=f"movsub_{episode}")
        from database import progress

        self.assertEqual(progress.totals(123), (0, 0))
        await self.send(123, callback=f"wp:s:{season}:1:c:0")
        await self.send(99, callback=f"watchstats:episodes:123:{series}:0")
        text = self.mocks["send_message"].call_args.kwargs["text"]
        self.assertIn("Сезон 1 → Серия 1", text)
        await self.send(99, callback="watchstats:user:123:0")
        self.assertIn("1 из 1 серий", self.mocks["send_message"].call_args.kwargs["text"])
