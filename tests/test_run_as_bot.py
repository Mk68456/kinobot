import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from tools import run_as_bot


class RunAsBotTests(unittest.TestCase):
    def test_root_drops_groups_gid_uid_before_exec_preserving_arguments(self):
        calls = Mock()
        account = SimpleNamespace(pw_name='bot', pw_uid=10001, pw_gid=10001, pw_dir='/home/bot')
        pwd = SimpleNamespace(getpwnam=Mock(return_value=account))
        with patch.dict('sys.modules', {'pwd': pwd}), patch.object(run_as_bot.os, 'getuid', return_value=0, create=True), \
                patch.object(run_as_bot.os, 'initgroups', calls.initgroups, create=True), \
                patch.object(run_as_bot.os, 'setgid', calls.setgid, create=True), \
                patch.object(run_as_bot.os, 'setuid', calls.setuid, create=True), \
                patch.object(run_as_bot.os, 'execvpe', calls.execvpe):
            run_as_bot.main(['/opt/venv/bin/python', 'app.py', 'argument with spaces'])
        self.assertEqual([c[0] for c in calls.mock_calls], ['initgroups', 'setgid', 'setuid', 'execvpe'])
        calls.initgroups.assert_called_once_with('bot', 10001)
        calls.setgid.assert_called_once_with(10001)
        calls.setuid.assert_called_once_with(10001)
        command, args, env = calls.execvpe.call_args.args
        self.assertEqual(command, '/opt/venv/bin/python')
        self.assertEqual(args, [command, 'app.py', 'argument with spaces'])
        self.assertEqual(env['HOME'], '/home/bot')

    def test_nonroot_executes_without_changing_identity(self):
        with patch.object(run_as_bot.os, 'getuid', return_value=123, create=True), \
                patch.object(run_as_bot.os, 'setuid', create=True) as setuid, \
                patch.object(run_as_bot.os, 'execvpe') as execute:
            run_as_bot.main(['python', 'app.py'])
        setuid.assert_not_called()
        execute.assert_called_once()

    def test_empty_command_is_rejected(self):
        with patch.object(run_as_bot.os, 'execvpe') as execute:
            with self.assertRaises(SystemExit):
                run_as_bot.main([])
        execute.assert_not_called()

    def test_privilege_failure_prevents_exec(self):
        account = SimpleNamespace(pw_name='bot', pw_uid=10001, pw_gid=10001, pw_dir='/home/bot')
        with patch.dict('sys.modules', {'pwd': SimpleNamespace(getpwnam=lambda _: account)}), \
                patch.object(run_as_bot.os, 'getuid', return_value=0, create=True), \
                patch.object(run_as_bot.os, 'initgroups', side_effect=PermissionError, create=True), \
                patch.object(run_as_bot.os, 'execvpe') as execute:
            with self.assertRaises(PermissionError):
                run_as_bot.main(['python', 'app.py'])
        execute.assert_not_called()
