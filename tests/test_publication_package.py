from tests.age_fixtures import reviewed_import
import contextlib
import io
import unittest

from database.connection import database
from database.migrations import migrate
from webpanel import repository


class PublicationPackageTests(unittest.TestCase):
    def setUp(self):
        database.open(':memory:')
        with contextlib.redirect_stdout(io.StringIO()):
            migrate()

    def tearDown(self):
        database.close()

    def create_ready(self, title):
        draft = reviewed_import(99, {
            'version': 1, 'root': f'D:/Films/{title}', 'title': title,
            'selection': 'movie.mp4',
            'files': [{'path': 'movie.mp4', 'size': 123, 'mtime': '1'}],
        })
        item = draft['body']['items'][0]
        database.execute("UPDATE WebAssets SET status='ready',file_id='file-'||id WHERE id=?",
                         (item['asset_id'],))
        database.commit()
        return repository.draft(draft['id'], 99)

    def test_each_card_is_checked_then_added_to_persistent_package(self):
        first = self.create_ready('First')
        second = self.create_ready('Second')
        package = repository.package_add(99, first['id'], repository.review_token(first))
        self.assertEqual([item['id'] for item in package['items']], [first['id']])
        package = repository.package_add(99, second['id'], repository.review_token(second))
        self.assertEqual({item['id'] for item in package['items']}, {first['id'], second['id']})
        self.assertEqual(len(repository.publication_package(99)['items']), 2)

    def test_edit_removes_card_until_it_is_checked_again(self):
        draft = self.create_ready('Editable')
        token = repository.review_token(draft)
        repository.package_add(99, draft['id'], token)
        edited = dict(draft['body'], description='New description')
        repository.save_draft(99, edited, draft['id'])
        self.assertEqual(repository.publication_package(99)['items'], [])

    def test_publish_package_publishes_all_movies_and_is_idempotent(self):
        drafts = [self.create_ready(name) for name in ('One', 'Two')]
        selections = []
        for draft in drafts:
            repository.package_add(99, draft['id'], repository.review_token(draft))
            selections.append({'id': draft['id'], 'review_token': repository.review_token(draft)})
        result = repository.package_publish(99, selections)
        self.assertEqual([item['ok'] for item in result], [True, True])
        self.assertEqual(database.execute('SELECT count(*) FROM Movies').fetchone()[0], 2)
        self.assertEqual(len(repository.publication_package(99)['items']), 0)
        repeated = repository.package_publish(99, selections)
        self.assertEqual([item['ok'] for item in repeated], [True, True])

    def test_revision_then_restore_requires_new_approval(self):
        draft = self.create_ready('Review')
        repository.package_add(99, draft['id'], repository.review_token(draft))
        repository.review(draft['id'], 99, 'revision', 'Fix poster')
        repository.review(draft['id'], 99, 'draft')
        self.assertEqual(repository.publication_package(99)['items'], [])

    def test_invalid_package_does_not_publish_any_movie(self):
        draft = self.create_ready('Valid')
        token = repository.review_token(draft)
        repository.package_add(99, draft['id'], token)
        selection = {'id': draft['id'], 'review_token': token}
        for items in ([selection, selection], [selection, {'id': 'invalid', 'review_token': None}]):
            with self.assertRaises(ValueError):
                repository.package_publish(99, items)
        self.assertEqual(database.execute('SELECT count(*) FROM Movies').fetchone()[0], 0)

    def test_other_admin_cannot_add_or_remove_movie(self):
        draft = self.create_ready('Private')
        token = repository.review_token(draft)
        with self.assertRaises(ValueError):
            repository.package_add(100, draft['id'], token)
        repository.package_add(99, draft['id'], token)
        repository.package_remove(100, draft['id'])
        self.assertEqual(len(repository.publication_package(99)['items']), 1)
        repository.package_remove(99, draft['id'])
        self.assertEqual(repository.publication_package(99)['items'], [])


if __name__ == '__main__':
    unittest.main()
