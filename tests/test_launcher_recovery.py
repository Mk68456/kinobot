import unittest
from unittest.mock import Mock
from tools.desktop_launcher import send_with_recovery, BeforeSendError

class RecoveryTests(unittest.TestCase):
    def test_retries_after_eight_seconds(self):
        client=Mock();client.request.return_value={'files':[{'status':'waiting_telegram'}]}
        stop=Mock();stop.is_set.return_value=False;stop.wait.return_value=False
        desktop=object();connect=Mock(side_effect=[BeforeSendError('заголовок чата'),desktop]);send=Mock()
        result=send_with_recovery(client,{'draft':{'id':'a'}},{},None,stop,Mock(),connect,send)
        self.assertIs(result,desktop);stop.wait.assert_called_once_with(8);send.assert_called_once()
    def test_server_ack_skips_resend(self):
        client=Mock();client.request.side_effect=[{'files':[{'status':'waiting_telegram'}]},{'files':[{'status':'ready'}]}]
        stop=Mock();stop.is_set.return_value=False;stop.wait.return_value=False
        send=Mock(side_effect=BeforeSendError('заголовок чата'))
        send_with_recovery(client,{'draft':{'id':'a'}},{},object(),stop,Mock(),Mock(),send)
        self.assertEqual(send.call_count,1)
    def test_stop_interrupts_retry(self):
        client=Mock();client.request.return_value={'files':[{'status':'waiting_telegram'}]}
        stop=Mock();stop.is_set.return_value=False;stop.wait.return_value=True
        with self.assertRaises(InterruptedError):
            send_with_recovery(client,{'draft':{'id':'a'}},{},None,stop,Mock(),Mock(side_effect=BeforeSendError('заголовок чата')),Mock())
    def test_ambiguous_error_is_not_retried(self):
        client=Mock();client.request.return_value={'files':[{'status':'waiting_telegram'}]}
        stop=Mock();stop.is_set.return_value=False
        with self.assertRaises(ValueError):
            send_with_recovery(client,{'draft':{'id':'a'}},{},object(),stop,Mock(),Mock(),Mock(side_effect=ValueError('uncertain send')))
        stop.wait.assert_not_called()

class ReceiptTests(unittest.TestCase):
    def test_receipt_requires_visible_matching_bot_and_confirmed_id(self):
        from tools.desktop_launcher import TelegramDesktop
        desktop=TelegramDesktop('test_bot','Test Bot');desktop._confirmed_receipts={'abc123'}
        bounds=Mock(left=0,top=0,bottom=1000);bounds.width.return_value=1000
        rect=Mock(left=300,top=700,bottom=780);rect.height.return_value=80
        control=Mock();control.element_info.control_type='ListItem';control.is_visible.return_value=True;control.rectangle.return_value=rect
        control.window_text.return_value='Test Bot\nФайл принят: abc123';control.element_info.name='';control.legacy_properties.return_value={}
        desktop.window=Mock();desktop.window.rectangle.return_value=bounds;desktop.window.descendants.return_value=[control]
        self.assertTrue(desktop.confirmed_message_matches())
        rect.top=-50
        self.assertFalse(desktop.confirmed_message_matches())
        rect.top=700;control.window_text.return_value='Other Bot\nФайл принят: abc123'
        self.assertFalse(desktop.confirmed_message_matches())
        control.window_text.return_value='Test Bot\nФайл принят: unknown'
        self.assertFalse(desktop.confirmed_message_matches())


class ApiRecoveryTests(unittest.TestCase):
    def test_incomplete_response_is_retryable(self):
        import http.client
        from tools.desktop_launcher import Client, TemporaryConnectionError
        client = Client('https://example.com', 'a' * 32)
        client.opener = Mock()
        client.opener.open.side_effect = http.client.IncompleteRead(b'partial', 10)
        with self.assertRaises(TemporaryConnectionError):
            client.request('session')

    def test_server_delay_and_recovery_notification(self):
        from tools.desktop_launcher import RetryingClient, TemporaryConnectionError
        client = Mock()
        client.request.side_effect = [TemporaryConnectionError('Busy', 45), {'ok': True}]
        stop = Mock()
        stop.is_set.return_value = False
        stop.wait.return_value = False
        report = Mock()
        self.assertEqual(RetryingClient(client, stop, report).request('session'), {'ok': True})
        stop.wait.assert_called_once_with(45)
        self.assertIn('восстановлена', report.call_args.args[0])


class PanelConnectionMessageTests(unittest.TestCase):
    def test_expired_code_is_not_reported_as_network_outage(self):
        from tools.desktop_launcher import panel_connection_error, ConnectionExpired
        label, reason = panel_connection_error(ConnectionExpired('Unauthorized'))
        self.assertIn('/desktop', label)
        self.assertIn('Код подключения', reason)

    def test_network_reason_is_preserved(self):
        from tools.desktop_launcher import panel_connection_error, TemporaryConnectionError
        label, reason = panel_connection_error(TemporaryConnectionError('timeout'))
        self.assertIn('повтор', label)
        self.assertEqual(reason, 'timeout')


class RejectedSendResumeTests(unittest.TestCase):
    def test_rejected_draft_is_reopened_before_send(self):
        from tools.desktop_launcher import resume_rejected_before_send
        client = Mock()
        client.request.side_effect = [{'draft_status':'rejected'}, {'draft':{'id':'one','status':'draft','body':{'items':[]}}}]
        prepared = {'draft':{'id':'one','status':'rejected'}}
        result = resume_rejected_before_send(client, prepared, Mock())
        self.assertEqual(result['draft']['status'], 'draft')
        client.request.assert_called_with('import/one/reopen', {'kind':'rejected'})

    def test_published_draft_is_never_automatically_reopened(self):
        from tools.desktop_launcher import resume_rejected_before_send
        client = Mock()
        client.request.return_value = {'draft_status':'published'}
        with self.assertRaises(ValueError):
            resume_rejected_before_send(client, {'draft':{'id':'one'}}, Mock())
        self.assertEqual(client.request.call_count, 1)


class ApiNotFoundMessageTests(unittest.TestCase):
    def test_missing_reopen_route_explains_server_update(self):
        import urllib.error
        from tools.desktop_launcher import Client
        client = Client('https://example.com', 'a'*32)
        client.opener = Mock()
        client.opener.open.side_effect = urllib.error.HTTPError('https://example.com',404,'Not Found',{},None)
        with self.assertRaisesRegex(ValueError, 'webpanel/server.py'):
            client.request('import/one/reopen', {'kind':'rejected'})

    def test_missing_draft_explains_pairing_and_preserves_files(self):
        import urllib.error
        from tools.desktop_launcher import Client
        client = Client('https://example.com', 'a'*32)
        client.opener = Mock()
        client.opener.open.side_effect = urllib.error.HTTPError('https://example.com',404,'Not Found',{},None)
        with self.assertRaisesRegex(ValueError, 'Черновик не найден'):
            client.request('import/one')
