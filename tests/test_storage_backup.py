import contextlib
import io
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from database.connection import database
from database.migrations import migrate
from services import storage_backup


class TelegramStorageTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        database.open(':memory:')
        with contextlib.redirect_stdout(io.StringIO()):
            migrate()

    def tearDown(self):
        database.close()

    async def test_existing_catalog_is_discovered_and_backed_up(self):
        database.execute("INSERT INTO Movies(movie_title,movie_number,content_type) VALUES ('Film',1,'movie')")
        database.execute("INSERT INTO MovieCategories(movie_number,name) VALUES (1,'Dub')")
        category = database.execute('SELECT id FROM MovieCategories').fetchone()[0]
        database.execute("INSERT INTO MovieSubcategories(category_id,name,file_id,file_type) VALUES (?,?,?,?)",
                         (category, '1080p', 'old-bot-file-id', 'video'))
        storage_backup.configure(99, -100123, 'Storage')
        bot = SimpleNamespace(send_video=AsyncMock(return_value=SimpleNamespace(message_id=77)),
                              send_document=AsyncMock())
        storage_backup.backup.bot = bot
        await storage_backup.backup._drain()
        current = storage_backup.status()
        self.assertEqual(current['counts'], {'total': 1, 'ready': 1, 'pending': 0, 'error': 0})
        self.assertEqual(storage_backup.storage_reference('old-bot-file-id'),
                         {'chat_id': -100123, 'message_id': 77})
        bot.send_video.assert_awaited_once()

    async def test_storage_message_is_used_instead_of_old_bot_file_id(self):
        database.execute("INSERT INTO TelegramStorageFiles VALUES (?,?,?,?,'ready',1,NULL,CURRENT_TIMESTAMP)",
                         ('old-file', 'document', -100123, 88))
        bot = SimpleNamespace(copy_message=AsyncMock(return_value=SimpleNamespace(message_id=5)),
                              send_document=AsyncMock(), send_video=AsyncMock())
        markup = object()
        await storage_backup.send_catalog_file(bot, 42, 'old-file', 'document', 'Movie', markup)
        bot.copy_message.assert_awaited_once_with(
            42, -100123, 88, caption='Movie', parse_mode='', reply_markup=markup)
        bot.send_document.assert_not_awaited()

    async def test_failed_copy_falls_back_to_current_file_id(self):
        database.execute("INSERT INTO TelegramStorageFiles VALUES (?,?,?,?,'ready',1,NULL,CURRENT_TIMESTAMP)",
                         ('current-file', 'document', -100123, 88))
        bot = SimpleNamespace(copy_message=AsyncMock(side_effect=RuntimeError('channel unavailable')),
                              send_document=AsyncMock(), send_video=AsyncMock())
        await storage_backup.send_catalog_file(bot, 42, 'current-file', 'document')
        bot.send_document.assert_awaited_once_with(
            42, document='current-file', caption=None, parse_mode='', reply_markup=None)


if __name__ == '__main__':
    unittest.main()
