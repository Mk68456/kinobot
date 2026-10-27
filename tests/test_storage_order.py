import contextlib
import io
import unittest

from database.connection import database
from database.migrations import migrate
from tests.age_fixtures import reviewed_import
from webpanel import repository


class StorageOrderTests(unittest.TestCase):
    def setUp(self):
        database.open(':memory:')
        with contextlib.redirect_stdout(io.StringIO()):
            migrate()

    def tearDown(self):
        database.close()

    def file(self, label, file_id, source_chat, source_message):
        draft = reviewed_import(99, {'version':1, 'root':'D:/'+label, 'title':label,
                                    'files':[{'path':label+'.mp4', 'size':1}]})
        asset_id = draft['body']['items'][0]['asset_id']
        database.execute("UPDATE WebAssets SET status='ready',file_id=?,source_chat_id=?,source_message_id=?,created_at='2026-10-18' WHERE id=?",
                         (file_id, source_chat, source_message, asset_id))
        database.commit()
        return asset_id

    def test_recovered_original_order_survives_later_backups(self):
        first = self.file('First', 'storage:first', -100123, 10)
        second = self.file('Second', 'storage:second', -100123, 20)
        database.execute("INSERT INTO TelegramStorageFiles(file_id,file_type,storage_chat_id,storage_message_id,status) VALUES ('storage:first','video',-100123,999,'ready')")
        database.commit()
        files = {f['id']:f for f in repository.assets(99)}
        self.assertEqual(files[first]['storage_message_id'], 10)
        self.assertEqual(files[second]['storage_message_id'], 20)
        self.assertEqual(files[first]['created_at'], files[second]['created_at'])
        self.assertEqual(repository.assets(100), [])

    def test_private_incoming_message_is_not_storage_upload_date(self):
        known = self.file('Known', 'telegram-known', 99, 100000)
        unknown = self.file('Unknown', 'telegram-unknown', 99, 999999)
        database.execute("INSERT INTO TelegramStorageFiles(file_id,file_type,storage_chat_id,storage_message_id,status) VALUES ('telegram-known','video',-100123,30,'ready')")
        database.commit()
        files = {f['id']:f for f in repository.assets(99)}
        self.assertEqual(files[known]['storage_message_id'], 30)
        self.assertNotIn('storage_message_id', files[unknown])
