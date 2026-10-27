"""Remote render commands and directory results use only an in-memory database."""
import contextlib
import copy
import io
import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from database.connection import database
from database.migrations import migrate
from webpanel import launcher_control as control

A, B = 'remote-computer-a', 'remote-computer-b'


class RemoteControlTests(unittest.TestCase):
    def setUp(self):
        database.open(':memory:')
        with contextlib.redirect_stdout(io.StringIO()):
            migrate()

    def tearDown(self):
        database.close()

    def heartbeat(self, instance=A, enabled=True, busy=False, capabilities=None, admin=99, protocol=3):
        return control.heartbeat(admin, {
            'instance_id': instance, 'protocol': protocol, 'name': instance,
            'folders': ['D:/' + instance],
            'status': {'busy': busy, 'remote_browse_enabled': enabled,
                       'capabilities': {'folder_browser': 1, 'remote_render': 1} if capabilities is None else capabilities},
        })

    def claim(self, action='browse_folder', payload=None, instance=A):
        command = control.enqueue(99, action, payload or {}, instance)
        self.assertEqual(self.heartbeat(instance)['command']['id'], command['id'])
        return command

    def listing(self):
        return {'folder': 'D:/Films', 'parent': 'D:/',
                'entries': [{'name': 'Новый проект', 'path': 'D:/Films/Новый проект'}],
                'offset': 0, 'next_offset': None}

    def test_prepare_carries_one_explicit_folder_and_all_options(self):
        self.heartbeat()
        command = self.claim('prepare', {'folder': ' C:/Render/New project ', 'mode': ' SINGLE ',
            'hardware': ' NVIDIA ', 'load': ' ECO ', 'catalog_section': ' CARTOONS ',
            'qualities': ['source', '720p', '720p']})
        payload = json.loads(database.execute('SELECT payload FROM LauncherCommands WHERE id=?', (command['id'],)).fetchone()[0])
        self.assertEqual(payload, {'folder': 'C:/Render/New project', 'mode': 'single', 'hardware': 'nvidia',
                                 'load': 'eco', 'catalog_section': 'cartoons', 'qualities': ['source', '720p']})

    def test_send_accepts_same_explicit_settings(self):
        self.heartbeat()
        command = self.claim('send', {'folder': 'D:/Prepared', 'mode': 'batch', 'hardware': 'amd',
                                     'load': 'performance', 'catalog_section': 'anime', 'qualities': ['360p']})
        self.assertEqual(command['action'], 'send')

    def test_legacy_prepare_without_new_options_remains_compatible(self):
        self.heartbeat(capabilities={}, enabled=False, protocol=2)
        command = control.enqueue(99, 'prepare', {'quality_policy': 'source_only'}, A)
        claimed = self.heartbeat(capabilities={}, enabled=False, protocol=2)['command']
        self.assertEqual(claimed['id'], command['id'])
        self.assertEqual(claimed['payload']['qualities'], ['source'])
        control.complete(99, command['id'], True, 'Started')

    def test_legacy_client_cannot_silently_ignore_explicit_folder(self):
        self.heartbeat(capabilities={}, enabled=False)
        with self.assertRaisesRegex(ValueError, 'Обновите лаунчер'):
            control.enqueue(99, 'prepare', {'folder': 'D:/' + A, 'qualities': ['720p']}, A)

    def test_disabled_remote_browsing_keeps_whitelist_but_denies_arbitrary_paths(self):
        self.heartbeat(enabled=False)
        for action in ('switch_folder', 'prepare', 'send'):
            payload = {'folder': 'C:/Other', 'qualities': ['720p']}
            with self.subTest(action=action), self.assertRaisesRegex(ValueError, 'не разрешена'):
                control.enqueue(99, action, payload, A)
        with self.assertRaisesRegex(ValueError, 'Включите удалённый выбор'):
            control.enqueue(99, 'browse_folder', {'folder': 'D:/' + A}, A)
        allowed = control.enqueue(99, 'prepare', {'folder': 'D:/' + A, 'qualities': ['720p']}, A)
        self.assertEqual(allowed['status'], 'pending')

    def test_capability_and_local_permission_are_both_required(self):
        for capabilities, enabled, protocol in (({}, True, 3), ({'remote_render': 1}, True, 3),
                                                ({'folder_browser': 1}, False, 3),
                                                ({'folder_browser': 1, 'remote_render': 1}, True, 2)):
            with self.subTest(capabilities=capabilities, enabled=enabled, protocol=protocol):
                database.execute('DELETE FROM LauncherAgents')
                self.heartbeat(capabilities=capabilities, enabled=enabled, protocol=protocol)
                with self.assertRaises(ValueError):
                    control.enqueue(99, 'browse_folder', {}, A)

    def test_busy_computer_allows_browsing_refresh_and_stop(self):
        self.heartbeat(busy=True)
        for action in ('prepare', 'send', 'switch_folder'):
            with self.subTest(action=action), self.assertRaisesRegex(ValueError, 'занят'):
                control.enqueue(99, action, {'folder': 'C:/Render', 'qualities': ['720p']}, A)
        for action in ('browse_folder', 'refresh', 'stop'):
            with self.subTest(action=action):
                command = control.enqueue(99, action, {}, A)
                claimed = self.heartbeat(busy=True)['command']
                self.assertEqual(claimed['id'], command['id'])
                control.complete(99, command['id'], True, 'Accepted', A)

    def test_root_listing_accepts_empty_folder_and_null_parent(self):
        self.heartbeat()
        command = self.claim()
        data = {'folder': '', 'parent': None, 'entries': [{'name': 'C:', 'path': 'C:/'}],
                'offset': 0, 'next_offset': None}
        control.complete(99, command['id'], True, 'Диски компьютера', A, data)
        current = control.state(99)['commands'][0]
        self.assertEqual(current['result'], 'Диски компьютера')
        self.assertEqual(current['data'], data)
        raw = database.execute('SELECT result FROM LauncherCommands WHERE id=?', (command['id'],)).fetchone()[0]
        self.assertEqual(json.loads(raw), {'message': 'Диски компьютера', 'data': data})

    def test_stop_is_not_blocked_by_pending_or_claimed_folder_browsing(self):
        self.heartbeat(busy=True)
        browse = control.enqueue(99, 'browse_folder', {}, A)
        stop = control.enqueue(99, 'stop', {}, A)
        self.assertEqual(self.heartbeat(busy=True)['command']['id'], browse['id'])
        self.assertEqual(self.heartbeat(busy=True)['command']['id'], stop['id'])
        control.complete(99, stop['id'], True, 'Stopping', A)
        another = control.enqueue(99, 'stop', {}, A)
        self.assertEqual(self.heartbeat(busy=True)['command']['id'], another['id'])

    def test_read_only_command_does_not_allow_two_mutations_to_overlap(self):
        self.heartbeat()
        self.claim()
        for action in ('prepare', 'send', 'switch_folder', 'refresh', 'browse_folder'):
            with self.subTest(action=action), self.assertRaisesRegex(ValueError, 'Предыдущая команда'):
                control.enqueue(99, action, {'folder': 'D:/Films', 'qualities': ['720p']}, A)

    def test_listing_is_isolated_by_admin_and_target_computer(self):
        self.heartbeat()
        self.heartbeat(B)
        command = self.claim()
        for admin, instance in ((99, B), (100, A), (99, '')):
            with self.subTest(admin=admin, instance=instance), self.assertRaisesRegex(ValueError, 'другому компьютеру'):
                control.complete(admin, command['id'], True, 'Folders', instance, self.listing())
        self.assertEqual(control.state(99)['commands'][0]['status'], 'claimed')
        control.complete(99, command['id'], True, 'Folders', A, self.listing())
        self.assertEqual(control.state(100)['commands'], [])
        self.assertEqual(control.state(99)['commands'][0]['instance_id'], A)
        self.assertIsNone(self.heartbeat(B)['command'])

    def test_poll_returns_only_latest_successful_listing_per_computer(self):
        self.heartbeat()
        self.heartbeat(B)
        completed = {}
        for instance in (A, B):
            for index in range(2):
                command = self.claim(instance=instance)
                data = self.listing()
                data['folder'] = f'D:/Films/{instance}/{index}'
                control.complete(99, command['id'], True, f'Page {index}', instance, data)
                completed[instance] = command['id']
        pending = control.enqueue(99, 'browse_folder', {}, A)
        failed = self.claim(instance=B)
        control.complete(99, failed['id'], False, 'Disconnected drive', B)
        commands = control.state(99)['commands']
        listings = {command['instance_id']: command['id'] for command in commands if 'data' in command}
        self.assertEqual(listings, completed)
        self.assertEqual(sum('data' in command for command in commands), 2)
        self.assertEqual(next(command['status'] for command in commands if command['id'] == pending['id']), 'pending')
        for command in commands:
            if command['status'] == 'done':
                self.assertIn(command['result'], ('Page 0', 'Page 1'))

    def test_folder_permissions_do_not_expand_to_another_computer(self):
        self.heartbeat()
        self.heartbeat(B, enabled=False)
        command = control.enqueue(99, 'prepare', {'folder': 'C:/Arbitrary', 'qualities': ['720p']}, A)
        self.assertEqual(command['instance_id'], A)
        with self.assertRaisesRegex(ValueError, 'не разрешена'):
            control.enqueue(99, 'prepare', {'folder': 'C:/Arbitrary', 'qualities': ['720p']}, B)

    def test_listings_are_paginated_and_capped(self):
        self.heartbeat()
        command = self.claim(payload={'folder': 'D:/Films', 'offset': 100})
        data = {'folder': 'D:/Films', 'parent': 'D:/', 'entries': [
            {'name': str(index), 'path': 'D:/Films/' + str(index)} for index in range(100)],
            'offset': 100, 'next_offset': 200}
        control.complete(99, command['id'], True, 'Page two', A, data)
        self.assertEqual(len(control.state(99)['commands'][0]['data']['entries']), 100)

    def test_invalid_listing_leaves_command_claimed_for_failure_acknowledgement(self):
        self.heartbeat()
        command = self.claim()
        bad = []
        for field, value in (('folder', 'relative'), ('parent', 1), ('entries', [{'name': 'x', 'path': 'relative'}]),
                             ('offset', True), ('next_offset', 0), ('entries', [{}] * 101)):
            data = self.listing()
            data[field] = value
            bad.append(data)
        bad.extend(({}, [], {**self.listing(), 'other': 'value'}))
        for data in bad:
            with self.subTest(data=data), self.assertRaises(ValueError):
                control.complete(99, command['id'], True, 'Folders', A, data)
        self.assertEqual(control.state(99)['commands'][0]['status'], 'claimed')
        control.complete(99, command['id'], False, 'Permission denied', A)
        self.assertNotIn('data', control.state(99)['commands'][0])

    def test_large_listing_data_rejected_without_modifying_command(self):
        self.heartbeat()
        command = self.claim()
        data = self.listing()
        data['entries'] = [{'name': 'я' * 2000, 'path': 'D:/Films/' + str(index)} for index in range(100)]
        with self.assertRaisesRegex(ValueError, 'слишком большой'):
            control.complete(99, command['id'], True, 'Folders', A, data)
        self.assertEqual(control.state(99)['commands'][0]['status'], 'claimed')

    def test_data_cannot_be_attached_to_mutating_or_failed_command(self):
        self.heartbeat()
        command = self.claim('refresh')
        with self.assertRaises(ValueError):
            control.complete(99, command['id'], True, 'Refresh', A, self.listing())
        control.complete(99, command['id'], True, '{"message":"ordinary text"}', A)
        self.assertEqual(control.state(99)['commands'][0]['result'], '{"message":"ordinary text"}')
        command = self.claim()
        with self.assertRaises(ValueError):
            control.complete(99, command['id'], False, 'Failure', A, self.listing())

    def test_invalid_options_paths_and_offsets_do_not_enqueue(self):
        self.heartbeat()
        for field, value in (('mode', 'all'), ('mode', None), ('hardware', 'cuda'), ('load', 'turbo'),
                             ('catalog_section', 'series'), ('folder', 'relative'), ('folder', 'C:relative'),
                             ('folder', 'https://example.com'), ('folder', 'D:/bad\x00path')):
            payload = {'folder': 'D:/Render', 'qualities': ['720p'], field: value}
            with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                control.enqueue(99, 'prepare', payload, A)
        for offset in (-1, True, 1.5, 1_000_001):
            with self.subTest(offset=offset), self.assertRaises(ValueError):
                control.enqueue(99, 'browse_folder', {'offset': offset}, A)
        self.assertEqual(control.state(99)['commands'], [])

    def test_completion_cannot_be_repeated_and_bad_target_does_not_claim_other_commands(self):
        self.heartbeat()
        command = self.claim()
        control.complete(99, command['id'], True, 'Folders', A, self.listing())
        with self.assertRaises(ValueError):
            control.complete(99, command['id'], True, 'Folders', A, self.listing())
        before = copy.deepcopy(control.state(99)['commands'][0])
        self.assertIsNone(self.heartbeat(B, admin=100)['command'])
        self.assertEqual(control.state(99)['commands'][0], before)


class RemoteControlAdapters(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        database.open(':memory:')
        with contextlib.redirect_stdout(io.StringIO()):
            migrate()

    async def asyncTearDown(self):
        database.close()

    async def test_complete_endpoint_passes_folder_data_to_owned_command(self):
        from webpanel import server
        control.heartbeat(99, {'instance_id': A, 'protocol': 3, 'folders': [],
            'status': {'remote_browse_enabled': True, 'capabilities': {'folder_browser': 1}}})
        command = control.enqueue(99, 'browse_folder', {}, A)
        control.heartbeat(99, {'instance_id': A, 'protocol': 3, 'status': {}})

        class Request(dict):
            match_info = {'id': str(command['id'])}

        data = {'folder': '', 'parent': None, 'entries': [], 'offset': 0, 'next_offset': None}
        with patch.object(server, 'body', AsyncMock(return_value={'ok': True, 'result': 'Disks', 'data': data})):
            response = await server.desktop_control_complete(Request(admin_id=99, instance_id=A))
        self.assertEqual(response.status, 200)
        self.assertEqual(control.state(99)['commands'][0]['data'], data)

    async def test_session_advertises_server_remote_capability(self):
        from webpanel import server

        class Request(dict):
            app = {'uploads': SimpleNamespace(bot=SimpleNamespace(get_me=AsyncMock(
                return_value=SimpleNamespace(username='Example', full_name='Example bot'))))}

        response = await server.desktop_session(Request(admin_id=99))
        self.assertEqual(json.loads(response.body)['capabilities']['remote_control'], 1)


if __name__ == '__main__':
    unittest.main()
