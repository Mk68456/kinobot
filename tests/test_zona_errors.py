import unittest
from urllib.error import HTTPError, URLError

from services.zona_errors import lookup_failure


class LookupFailureTests(unittest.TestCase):
    def test_http_stage_and_signed_url_redaction(self):
        for host, stage in [('vsw01.zonasearch.com', 'ссылок на видео'),
                            ('apiw1.mzona.net', 'списка источников')]:
            message, code = lookup_failure(HTTPError(
                f'https://{host}/?q=PRIVATE_SIGNATURE', 500, 'PRIVATE_REASON', {}, None))
            self.assertIn(stage, message)
            self.assertIn('500', message)
            self.assertEqual(code, 'http_500')
            self.assertNotIn('PRIVATE', message)

    def test_network_and_unknown_errors_do_not_disclose_exception(self):
        for error in [URLError('PRIVATE_URL'), TimeoutError('PRIVATE_URL'), ValueError('PRIVATE_URL')]:
            message, code = lookup_failure(error)
            self.assertNotIn('PRIVATE', message)
            self.assertIn(code, {'network', 'unsupported_response'})
