import contextlib
import io
import unittest

from database.connection import database
from database.migrations import migrate
from webpanel import launcher_control


class LauncherControlTests(unittest.TestCase):
    def setUp(self):
        database.open(':memory:')
        with contextlib.redirect_stdout(io.StringIO()):
            migrate()

    def tearDown(self):
        database.close()

    def test_phone_command_is_scoped_claimed_and_completed(self):
        launcher_control.heartbeat(99, {'instance_id':'launcher-123',
            'folders':['D:/Movies'], 'status':{'busy':False,'folder':'D:/Movies'}})
        command=launcher_control.enqueue(99,'switch_folder',{'folder':'D:/Movies'})
        with self.assertRaises(ValueError):
            launcher_control.enqueue(99,'prepare',{})
        self.assertIsNone(launcher_control.heartbeat(100, {'instance_id':'other-123',
            'folders':[], 'status':{}})['command'])
        claimed=launcher_control.heartbeat(99, {'instance_id':'launcher-123',
            'folders':['D:/Movies'], 'status':{}})['command']
        self.assertEqual(claimed['id'],command['id'])
        launcher_control.complete(99,claimed['id'],True,'Folder selected')
        self.assertEqual(launcher_control.state(99)['commands'][0]['status'],'done')

    def test_phone_cannot_select_unregistered_folder(self):
        launcher_control.heartbeat(99, {'instance_id':'launcher-123',
            'folders':['D:/Movies'], 'status':{}})
        with self.assertRaises(ValueError):
            launcher_control.enqueue(99,'switch_folder',{'folder':'C:/Secret'})

    def test_phone_quality_selection_is_validated_and_deduplicated(self):
        launcher_control.heartbeat(99, {'instance_id':'launcher-123',
            'folders':['D:/Movies'], 'status':{}})
        with self.assertRaises(ValueError):
            launcher_control.enqueue(99, 'prepare', {'qualities': []})
        with self.assertRaises(ValueError):
            launcher_control.enqueue(99, 'prepare', {'qualities': ['4k']})
        command = launcher_control.enqueue(99, 'prepare',
            {'qualities': ['1080p', '720p', '720p']})
        claimed = launcher_control.heartbeat(99, {'instance_id':'launcher-123',
            'folders':['D:/Movies'], 'status':{}})['command']
        self.assertEqual(claimed['id'], command['id'])
        self.assertEqual(claimed['payload']['qualities'], ['1080p', '720p'])


if __name__ == '__main__':
    unittest.main()
