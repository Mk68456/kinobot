from tests.age_fixtures import reviewed_import
import contextlib
import copy
import io
import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from database.connection import database
from database.migrations import migrate
from tools.desktop_launcher import (scan, movie_title, run_batch, batch_plan, RetryingClient,
                                    TemporaryConnectionError, ConnectionExpired)
from webpanel import imports, repository


class BatchRepositoryTests(unittest.TestCase):
    def setUp(self):
        database.open(':memory:')
        with contextlib.redirect_stdout(io.StringIO()):
            migrate()

    def tearDown(self):
        database.close()

    def create(self, name, ready=True, admin=99):
        current = reviewed_import(admin, {'version': 1, 'root': 'D:/Films', 'title': name,
            'selection': name + '.mp4', 'files': [{'path': name + '.mp4', 'size': 123, 'mtime': '1'}]})
        if ready:
            database.execute("UPDATE WebAssets SET status='ready',file_id='video' WHERE id=?",
                             (current['body']['items'][0]['asset_id'],))
            database.commit()
        return current

    def test_separate_files_have_separate_idempotent_drafts(self):
        first, second = self.create('One'), self.create('Two')
        self.assertNotEqual(first['id'], second['id'])
        self.assertEqual(self.create('One')['id'], first['id'])
        self.assertEqual(database.execute('SELECT count(*) FROM Movies').fetchone()[0], 0)

    def test_individual_metadata_survives_launcher_resume_before_video_arrives(self):
        first, second = self.create('First', ready=False), self.create('Second', ready=False)
        for draft, text in ((first, 'First description'), (second, 'Second description')):
            body = dict(draft['body'], description=text, poster_url='https://example.com/' + draft['id'] + '.jpg')
            repository.save_draft(99, body, draft['id'])
        resumed_first = self.create('First', ready=False)
        resumed_second = self.create('Second', ready=False)
        self.assertEqual(resumed_first['body']['description'], 'First description')
        self.assertEqual(resumed_second['body']['description'], 'Second description')
        self.assertNotEqual(resumed_first['body']['poster_url'], resumed_second['body']['poster_url'])
        self.assertEqual(database.execute('SELECT count(*) FROM WebDrafts').fetchone()[0], 2)

    def test_bulk_publication_partial_success_and_retry(self):
        first = self.create('Ready')
        second = self.create('Waiting', ready=False)
        selections = [{'id': d['id'], 'review_token': repository.review_token(d)} for d in (first, second)]
        result = repository.publish_many(99, selections)
        self.assertEqual([r['ok'] for r in result], [True, False])
        repeated = repository.publish_many(99, selections)
        self.assertEqual(result[0]['movie_number'], repeated[0]['movie_number'])
        self.assertEqual(database.execute('SELECT count(*) FROM Movies').fetchone()[0], 1)

    def test_changes_and_other_admin_cannot_be_approved_from_old_snapshot(self):
        current = self.create('Changed')
        selection = {'id': current['id'], 'review_token': repository.review_token(current)}
        updated = copy.deepcopy(current['body'])
        updated['description'] = 'Edited after review'
        repository.save_draft(99, updated, current['id'])
        self.assertFalse(repository.publish_many(99, [selection])[0]['ok'])
        self.assertFalse(repository.publish_many(88, [selection])[0]['ok'])
        self.assertEqual(database.execute('SELECT count(*) FROM Movies').fetchone()[0], 0)

    def test_revision_and_rejected_stay_unpublished(self):
        for status in ('revision', 'rejected'):
            current = self.create(status)
            current = repository.review(current['id'], 99, status, 'Check title')
            result = repository.publish_many(99, [{'id': current['id'], 'review_token': repository.review_token(current)}])
            self.assertFalse(result[0]['ok'])

    def test_pagination_and_readiness_do_not_depend_on_recent_asset_list(self):
        self.create('Ready')
        self.create('Waiting', ready=False)
        self.create('Private', admin=88)
        page = repository.review_queue(99, 0, 1)
        next_page = repository.review_queue(99, 1, 1)
        self.assertEqual(page['total'], 2)
        self.assertNotEqual(page['items'][0]['id'], next_page['items'][0]['id'])
        self.assertEqual(sorted([page['items'][0]['can_publish'], next_page['items'][0]['can_publish']]), [False, True])

    def test_invalid_batch_is_rejected_before_any_publication(self):
        current = self.create('Movie')
        selected = {'id': current['id'], 'review_token': repository.review_token(current)}
        with self.assertRaises(ValueError):
            repository.publish_many(99, [selected, selected])
        self.assertEqual(database.execute('SELECT count(*) FROM Movies').fetchone()[0], 0)

    def test_new_pair_survives_ten_hour_shift_but_expires(self):
        with patch('webpanel.imports.time.time', return_value=100):
            token = imports.pair(99)
        with patch('webpanel.imports.is_admin', return_value=True):
            with patch('webpanel.imports.time.time', return_value=100 + 15 * 3600):
                self.assertEqual(imports.authenticate(token), 99)
            with patch('webpanel.imports.time.time', return_value=100 + 49 * 3600):
                self.assertIsNone(imports.authenticate(token))


    def test_reopen_rejected_preserves_files_and_metadata(self):
        current = self.create('Rejected')
        repository.review(current['id'], 99, 'rejected')
        reopened = imports.reopen(99, current['id'], 'rejected')
        self.assertEqual(reopened['status'], 'draft')
        self.assertEqual(reopened['body'], current['body'])
        self.assertEqual(repository.asset(current['body']['items'][0]['asset_id'], 99)['status'], 'ready')
        with self.assertRaises(ValueError):
            imports.reopen(88, current['id'], 'rejected')

    def test_reopen_published_requires_catalog_deletion(self):
        from database.movies import delete_movie_by_numb
        current = self.create('Published')
        number = repository.publish(current['id'], 99)
        with self.assertRaises(ValueError):
            imports.reopen(99, current['id'], 'published')
        self.assertEqual(repository.draft(current['id'], 99)['status'], 'published')
        delete_movie_by_numb(number)
        reopened = imports.reopen(99, current['id'], 'published')
        self.assertEqual(reopened['status'], 'draft')
        self.assertIsNone(reopened['movie_number'])
        new_number = repository.publish(current['id'], 99)
        self.assertNotEqual(new_number, number)


class BatchLauncherTests(unittest.TestCase):
    def test_recovery_reads_server_status_instead_of_stale_local_status(self):
        from tools.desktop_launcher import recover_batch_cards
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / 'One.mp4'
            source.write_bytes(b'video')
            item = batch_plan(folder)[0]
            item.update(status='complete', draft_id='draft-one')
            (Path(folder) / '.kinotime-batch.json').write_text(json.dumps({source.name:item}), encoding='utf-8')
            client = Mock()
            client.request.side_effect = [{'draft_status':'rejected'}, {'draft':{'status':'draft'}}]
            count, errors = recover_batch_cards(folder, client, 'rejected', Mock())
            self.assertEqual((count, errors), (1, []))
            self.assertEqual(batch_plan(folder)[0]['status'], 'prepared')
            client.request.assert_called_with('import/draft-one/reopen', {'kind':'rejected'})

    def test_send_step_resumes_rejected_batch_item(self):
        with tempfile.TemporaryDirectory() as folder:
            (Path(folder) / 'One.mp4').write_bytes(b'video')
            client = self.make_client()
            def request(path, body=None):
                if path == 'session':
                    return {'protocol':2,'admin_id':99,'bot_username':'ExampleBot'}
                if path == 'import':
                    return {'draft':{'id':'one','status':'rejected','body':{'items':[]}}}
                if path.endswith('/reopen'):
                    return {'draft':{'id':'one','status':'draft','body':{'items':[]}}}
                return {'files':[{'status':'ready'}]}
            client.request.side_effect = request
            run_batch(folder, client, threading.Event(), Mock(), Mock(), Mock(),
                      media=lambda path, report, stop:scan(path), prepare_only=True)
            self.assertEqual(batch_plan(folder)[0]['status'], 'rejected')
            run_batch(folder, client, threading.Event(), Mock(), Mock(), Mock(),
                      media=lambda path, report, stop:scan(path))
            self.assertEqual(batch_plan(folder)[0]['status'], 'complete')
            self.assertIn(unittest.mock.call('import/one/reopen', {'kind':'rejected'}), client.request.call_args_list)

    def test_filename_title_and_single_file_manifest(self):
        for filename, expected in [('Лёд.2018.BDRip.mp4', 'Лёд (2018)'),
                                   ('1917.2019.1080p.mkv', '1917 (2019)'),
                                   ('Movie_Name_2024_WEB-DL.mp4', 'Movie Name (2024)')]:
            self.assertEqual(movie_title(filename), expected)
        with tempfile.TemporaryDirectory() as folder:
            one = Path(folder) / 'One.2024.mp4'
            one.write_bytes(b'video')
            (Path(folder) / 'Two.mp4').write_bytes(b'video')
            manifest = scan(one)
            self.assertEqual(manifest['selection'], one.name)
            self.assertEqual(len(manifest['files']), 1)
            self.assertEqual(manifest['title'], 'One (2024)')

    def make_client(self):
        client = Mock(url='https://example.com')
        client.url = 'https://example.com'
        client.request.side_effect = lambda path, body=None: (
            {'protocol': 2, 'admin_id': 99, 'bot_username': 'ExampleBot'} if path == 'session' else
            {'draft': {'id': body['selection'], 'status': 'draft'}} if body is not None else
            {'files': [{'status': 'waiting_telegram'}]})
        return client

    def test_interrupted_encoding_pauses_children_and_preserves_reason(self):
        with tempfile.TemporaryDirectory() as folder:
            (Path(folder) / 'One.mp4').write_bytes(b'video')
            stop = threading.Event()
            report = Mock()

            def encode(path, progress, event):
                progress.variant('1080p / DUB', 'Кодирование · 1%', '')
                progress.variant('720p / DUB', 'Подготовлен', 'ready.mp4')
                event.preparation_error = 'Недостаточно места на диске'
                event.set()
                raise InterruptedError('Generic encoder interruption')

            run_batch(folder, self.make_client(), stop, report, Mock(), Mock(),
                      media=encode, prepare_only=True)
            item = batch_plan(folder)[0]
            self.assertEqual(item['status'], 'paused')
            self.assertEqual(item['variants'][0]['status'], 'Пауза')
            self.assertEqual(item['variants'][1]['status'], 'Подготовлен')
            self.assertIn('Недостаточно места', item['error'])
            self.assertIn('Недостаточно места', report.call_args.args[0])

    def test_prepare_registers_every_movie_without_sending_then_resumes(self):
        with tempfile.TemporaryDirectory() as folder:
            for name in ('One.mp4', 'Two.mp4'):
                (Path(folder) / name).write_bytes(b'video')
            client, send, connect = self.make_client(), Mock(), Mock()
            run_batch(folder, client, threading.Event(), Mock(), Mock(), connect,
                      media=lambda p, r, s: scan(p), send=send, prepare_only=True)
            send.assert_not_called()
            connect.assert_not_called()
            items = batch_plan(folder)
            self.assertEqual([i['status'] for i in items], ['prepared', 'prepared'])
            self.assertEqual(len({i['draft_id'] for i in items}), 2)
            run_batch(folder, client, threading.Event(), Mock(), Mock(), connect,
                      media=lambda p, r, s: scan(p), send=send, require_prepared=True)
            self.assertEqual(send.call_count, 2)

    def test_send_step_requires_preparation_before_any_upload(self):
        with tempfile.TemporaryDirectory() as folder:
            (Path(folder) / 'New.mp4').write_bytes(b'video')
            send, media = Mock(), Mock()
            with self.assertRaisesRegex(ValueError, 'Сначала выполните шаг 1'):
                run_batch(folder, self.make_client(), threading.Event(), Mock(), Mock(), Mock(),
                          media=media, send=send, require_prepared=True)
            send.assert_not_called()
            media.assert_not_called()

    def test_finished_movies_are_not_sent_again(self):
        with tempfile.TemporaryDirectory() as folder:
            for name in ('One.mp4', 'Two.mp4'):
                (Path(folder) / name).write_bytes(b'video')
            client, send = self.make_client(), Mock()
            run_batch(folder, client, threading.Event(), Mock(), Mock(), Mock(), media=lambda p, r, s: scan(p), send=send)
            self.assertEqual(send.call_count, 2)
            run_batch(folder, client, threading.Event(), Mock(), Mock(), Mock(), media=lambda p, r, s: scan(p), send=send)
            self.assertEqual(send.call_count, 2)
            self.assertTrue(all(i['status'] == 'complete' for i in batch_plan(folder)))
            saved = (Path(folder) / '.kinotime-batch.json').read_text()
            self.assertNotIn('Authorization', saved)
            self.assertEqual(len(json.loads(saved)), 2)

    def test_local_conversion_failure_does_not_block_other_movies(self):
        with tempfile.TemporaryDirectory() as folder:
            for name in ('Broken.mp4', 'Good.mp4'):
                (Path(folder) / name).write_bytes(b'video')
            def media(path, report, stop):
                if path.name == 'Broken.mp4':
                    raise ValueError('bad media')
                return scan(path)
            send = Mock()
            run_batch(folder, self.make_client(), threading.Event(), Mock(), Mock(), Mock(), media=media, send=send)
            send.assert_called_once()
            self.assertEqual([i['status'] for i in batch_plan(folder)], ['error', 'complete'])

    def test_uncertain_send_pauses_before_next_movie(self):
        with tempfile.TemporaryDirectory() as folder:
            for name in ('One.mp4', 'Two.mp4'):
                (Path(folder) / name).write_bytes(b'video')
            send = Mock(side_effect=RuntimeError('unknown Send result'))
            with self.assertRaises(RuntimeError):
                run_batch(folder, self.make_client(), threading.Event(), Mock(), Mock(), Mock(),
                          media=lambda p, r, s: scan(p), send=send)
            send.assert_called_once()
            self.assertEqual([i['status'] for i in batch_plan(folder)], ['paused', 'pending'])

    def test_manifest_rejection_skips_only_that_movie(self):
        with tempfile.TemporaryDirectory() as folder:
            for name in ('Bad.mp4', 'Good.mp4'):
                (Path(folder) / name).write_bytes(b'video')
            client = self.make_client()
            request = client.request.side_effect
            def reject_one(path, body=None):
                if body and body.get('selection') == 'Bad.mp4':
                    raise ValueError('Invalid movie data')
                return request(path, body)
            client.request.side_effect = reject_one
            send = Mock()
            run_batch(folder, client, threading.Event(), Mock(), Mock(), Mock(), media=lambda p, r, s: scan(p), send=send)
            self.assertEqual([i['status'] for i in batch_plan(folder)], ['error', 'complete'])
            send.assert_called_once()

    def test_old_server_is_rejected_before_conversion_or_upload(self):
        with tempfile.TemporaryDirectory() as folder:
            (Path(folder) / 'Movie.mp4').write_bytes(b'video')
            client = Mock()
            client.request.return_value = {'protocol': 1}
            media, send = Mock(), Mock()
            with self.assertRaisesRegex(ValueError, 'Сервер не поддерживает'):
                run_batch(folder, client, threading.Event(), Mock(), Mock(), Mock(), media=media, send=send)
            media.assert_not_called()
            send.assert_not_called()

    def test_network_retry_does_not_retry_expired_credentials(self):
        client = Mock()
        client.request.side_effect = [TemporaryConnectionError(), TemporaryConnectionError(), {'ok': True}]
        stop = Mock()
        stop.is_set.return_value = False
        stop.wait.return_value = False
        wrapper = RetryingClient(client, stop, Mock())
        self.assertEqual(wrapper.request('import', {'version': 1}), {'ok': True})
        self.assertEqual(stop.wait.call_count, 2)
        client.request.side_effect = ConnectionExpired('new code needed')
        with self.assertRaises(ConnectionExpired):
            wrapper.request('session')


if __name__ == '__main__':
    unittest.main()
