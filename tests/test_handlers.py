import contextlib
import importlib
import io
import unittest
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
    @classmethod
    def setUpClass(cls):
        cls.dp = loader.initialize("123456:FAKE_TOKEN_FOR_OFFLINE_TESTS")
        importlib.import_module("handlers")

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
        ):
            mock = AsyncMock()
            self.mocks[name] = mock
            patcher = patch.object(loader.bot, name, mock)
            patcher.start()
            self.addCleanup(patcher.stop)
        patcher = patch("data.config.ADMIN", [99])
        patcher.start()
        self.addCleanup(patcher.stop)
        add_user(123)

    async def asyncTearDown(self):
        database.close()

    async def send(self, user_id, text=None, callback=None, document=None):
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
        markup = self.mocks["edit_message_reply_markup"].call_args.kwargs["reply_markup"]
        self.assertIn("Серия 1 ❌", [b.text for row in markup.inline_keyboard for b in row])
        await self.send(123, callback=f"wp:e:{episode}:1:p:0")
        markup = self.mocks["edit_message_reply_markup"].call_args.kwargs["reply_markup"]
        self.assertIn("Серия 1 ✅", [b.text for row in markup.inline_keyboard for b in row])
        self.assertEqual(progress.totals(123), (0, 1))
        self.assertEqual(progress.totals(124), (0, 0))
        await self.send(123, callback=f"movcatback_{series}")
        markup = self.mocks["edit_message_reply_markup"].call_args.kwargs["reply_markup"]
        self.assertIn("Сезон 1 ✅ (1/1)", [b.text for row in markup.inline_keyboard for b in row])
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
