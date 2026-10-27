import contextlib
import io
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from database.connection import database
from database.migrations import migrate
from database.movies import add_new_movie
from database import content_requests
from webpanel import repository
from webpanel.server import notify_request_for_draft


class ContentRequestCardTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        database.open(":memory:")
        with contextlib.redirect_stdout(io.StringIO()):
            migrate()

    async def asyncTearDown(self):
        database.close()

    async def test_tmdb_request_keeps_card_and_combines_users(self):
        first = content_requests.vote(
            123, "Редкий фильм (2004)", "movie", tmdb_id=777, year="2004",
            overview="Краткое описание", poster_url="https://image.tmdb.org/poster.jpg",
        )
        second = content_requests.vote(456, "Другое локализованное название", "movie", tmdb_id=777)
        self.assertEqual(first["id"], second["id"])
        item = content_requests.web_page()["items"][0]
        self.assertEqual(item["votes"], 2)
        self.assertEqual(item["overview"], "Краткое описание")
        self.assertEqual(item["year"], "2004")

    async def test_linked_draft_publication_queues_one_notification(self):
        request_item = content_requests.vote(123, "Нужный фильм (2026)", "movie", tmdb_id=99)
        content_requests.vote(456, "Нужный фильм (2026)", "movie", tmdb_id=99)
        draft = repository.save_draft(99, {
            "content_type": "movie", "title": "Нужный фильм (2026)",
            "description": "", "poster_url": "", "season_name": "",
            "season_number": None, "movie_number": None, "category_id": None, "items": [],
        })
        content_requests.link_draft(request_item["id"], draft["id"])
        number = add_new_movie("Нужный фильм (2026)")
        source = SimpleNamespace(chat=SimpleNamespace(id=99), message_id=10)
        bot = SimpleNamespace(
            get_me=AsyncMock(return_value=SimpleNamespace(username="ExampleBot")),
            send_message=AsyncMock(return_value=source),
        )
        class RequestStub:
            app = {"uploads": SimpleNamespace(bot=bot)}

            def __getitem__(self, key):
                return {"admin_id": 99}[key]

        web_request = RequestStub()
        with patch("services.broadcasts.broadcasts.notify") as wake:
            await notify_request_for_draft(web_request, draft["id"], number)
            await notify_request_for_draft(web_request, draft["id"], number)
        current = content_requests.get(request_item["id"])
        self.assertEqual(current["status"], "added")
        recipients = database.execute(
            "SELECT user_id FROM BroadcastRecipients WHERE job_id=?", (current["notification_job"],)
        ).fetchall()
        self.assertEqual({row[0] for row in recipients}, {123, 456})
        bot.send_message.assert_awaited_once()
        wake.assert_called_once()


class RequestTargetTests(unittest.TestCase):
    def setUp(self):
        database.open(":memory:")
        with contextlib.redirect_stdout(io.StringIO()):
            migrate()

    def tearDown(self):
        database.close()

    def test_upgrade_preserves_existing_requests_and_votes(self):
        from database.migrations import MIGRATIONS
        database.close()
        database.open(":memory:")
        with contextlib.redirect_stdout(io.StringIO()):
            for migration in MIGRATIONS[:-2]:
                migration()
        database.execute(f"PRAGMA user_version={len(MIGRATIONS)-2}")
        database.execute("INSERT INTO ContentRequests(identity,title,content_type,season) VALUES (?,?,?,?)",
                         ('series:2:legacy', 'Legacy', 'series', 2))
        database.execute('INSERT INTO ContentRequestVotes VALUES (1,123)')
        migrate()
        migrate()
        item = content_requests.get(1)
        self.assertEqual((item['season'], item['episode'], item['voice']), (2, 0, ''))
        self.assertEqual(content_requests.web_page()['items'][0]['votes'], 1)

    def test_target_identity_and_vote_deduplication(self):
        base = content_requests.vote(1, "Show", "series", 1, 42)
        first = content_requests.vote(1, "Show", "series", 1, 42, episode=2, voice="  Студия   А ")
        second = content_requests.vote(2, "Show", "series", 1, 42, episode=2, voice="СТУДИЯ А")
        content_requests.vote(2, "Show", "series", 1, 42, episode=2, voice="студия а")
        other = content_requests.vote(1, "Show", "series", 1, 42, episode=3, voice="Студия А")
        self.assertEqual(first['id'], second['id'])
        self.assertEqual(len({base['id'], first['id'], other['id']}), 3)
        self.assertEqual(next(r for r in content_requests.web_page()['items'] if r['id']==first['id'])['votes'], 2)

    def test_completion_requires_every_requested_episode_and_voice(self):
        from database.categories import add_movie_category
        from database.episode_variants import add_series_file
        number = add_new_movie("Show", content_type="series")
        category = add_movie_category(number, "Сезон 1", 1)
        request = content_requests.vote(1, "Show", "series", 1, episode=0, voice="Studio",
                                        expected_episodes="1,2")
        def add(n, voice):
            add_series_file(category, {'name': f'Серия {n}', 'quality': '1080p', 'voice': voice},
                            {'file_id': f'file-{n}-{voice}', 'file_type': 'video'})
        add(1, 'Studio')
        add(2, 'Other')
        self.assertFalse(content_requests.target_available(request, number))
        with self.assertRaises(ValueError):
            content_requests.complete(request['id'], number, 99, 99, 10)
        add(2, 'Studio')
        self.assertTrue(content_requests.target_available(request, number))
        content_requests.complete(request['id'], number, 99, 99, 10)
        self.assertEqual(content_requests.get(request['id'])['status'], 'added')

    def test_whole_series_checks_each_season(self):
        from database.categories import add_movie_category, add_movie_subcategory
        number = add_new_movie("Show", content_type="series")
        first = add_movie_category(number, "Сезон 1", 1)
        add_movie_subcategory(first, "Серия 1", "file1", "video")
        target = {'content_type': 'series', 'season': 0, 'expected_episodes': '1:1,2:1'}
        self.assertFalse(content_requests.target_available(target, number))
        second = add_movie_category(number, "Сезон 2", 2)
        add_movie_subcategory(second, "Серия 1", "file2", "video")
        self.assertTrue(content_requests.target_available(target, number))
