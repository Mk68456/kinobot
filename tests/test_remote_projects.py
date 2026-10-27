import contextlib
import io
import threading
import unittest
from unittest.mock import Mock

from database.connection import database
from database.migrations import migrate
from webpanel import launcher_control as control
from tools import remote_projects


class ProjectCommandsTests(unittest.TestCase):
    def setUp(self):
        database.open(':memory:')
        with contextlib.redirect_stdout(io.StringIO()):
            migrate()
        self.status = {'busy': True, 'operation_busy': True, 'capabilities':
                       {'project_control': 1, 'remote_render': 1}, 'projects': [{'id': 'project-a'}]}
        self.beat()

    def tearDown(self):
        database.close()

    def beat(self):
        return control.heartbeat(99, {'instance_id': 'computer-a', 'protocol': 3,
                                     'folders': ['D:/Films'], 'status': self.status})

    def enqueue(self, action, payload):
        return control.enqueue(99, action, payload, 'computer-a')

    def test_can_pause_during_claimed_send_and_complete_both(self):
        send = self.enqueue('project_send', {'project_id': 'project-a', 'confirmed': True})
        self.beat()
        pause = self.enqueue('project_pause', {'project_id': 'project-a'})
        self.assertEqual(self.beat()['command']['id'], pause['id'])
        for command in (pause, send):
            control.complete(99, command['id'], True, instance_id='computer-a')

    def test_add_while_rendering_checks_folder_and_season(self):
        payload = {'folder': 'C:/Forbidden', 'mode': 'single', 'qualities': ['720p']}
        with self.assertRaises(ValueError):
            self.enqueue('project_add', payload)
        payload['folder'] = 'D:/Films'
        payload['series'] = {'movie_number': 1, 'season_number': True}
        with self.assertRaises(ValueError):
            self.enqueue('project_add', payload)
        payload['series']['season_number'] = 2
        self.assertEqual(self.enqueue('project_add', payload)['status'], 'pending')

    def test_rejects_wrong_project_unconfirmed_send_and_old_client(self):
        for action, payload in [('project_pause', {'project_id': 'other'}),
                                ('project_send', {'project_id': 'project-a'}),
                                ('project_parallelism', {'parallelism': True})]:
            with self.assertRaises(ValueError):
                self.enqueue(action, payload)
        self.status['capabilities'] = {}
        self.beat()
        with self.assertRaisesRegex(ValueError, 'Обновите'):
            self.enqueue('project_pause_all', {})

    def test_restart_releases_unfinished_command_without_replay(self):
        self.enqueue('project_cards', {'project_id': 'project-a'})
        self.beat()
        self.status['operation_busy'] = False
        self.assertIsNone(self.beat()['command'])
        self.assertEqual(control.state(99)['commands'][0]['status'], 'failed')

    def test_pause_sending_sets_stop_but_render_pause_does_not(self):
        manager, stop = Mock(), threading.Event()
        manager.get.return_value = {'state': 'running'}
        remote_projects.control(manager, 'project_pause', {'project_id': 'p'}, stop)
        manager.pause.assert_called_once_with('p')
        self.assertFalse(stop.is_set())
        manager.get.return_value = {'state': 'sending'}
        remote_projects.control(manager, 'project_pause', {'project_id': 'p'}, stop)
        self.assertTrue(stop.is_set())

    def test_snapshot_contains_variants_without_private_card_data(self):
        manager = Mock()
        manager.snapshots.return_value = [{'id': 'p', 'title': 'Title', 'folder': 'D:/Films',
            'state': 'running', 'message': '', 'priority': 0, 'card_data': {'secret': 'not exported'},
            'tasks': [{'state': 'done', 'count': 2, 'entry': {'path': 'episode.mkv'},
                       'plan': {'qualities': ['720p'], 'audio': [
                           {'name': 'Русский', 'enabled': True}, {'name': 'English', 'enabled': True}]}}]}]
        result = remote_projects.snapshot(manager)[0]
        self.assertEqual((result['ready'], result['total']), (2, 2))
        self.assertEqual(len(result['variants']), 2)
        self.assertNotIn('card_data', result)


if __name__ == '__main__':
    unittest.main()
