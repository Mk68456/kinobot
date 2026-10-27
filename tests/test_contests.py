import contextlib
import io
import tempfile
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from aiohttp.test_utils import TestClient, TestServer

from database import contests
from database.connection import database
from database.migrations import MIGRATIONS, migrate
from database.movies import add_new_movie
from database.people import add_user
from webpanel import auth
from webpanel.server import create_app
from webpanel.uploads import Uploads


def settings():
    return {'title': 'KinoTime × THE SOPRANOS NEWS', 'gift_name': 'Test Gift #1',
            'gift_url': 'https://t.me/nft/TestGift-1', 'photo_url': '', 'description': 'Конкурс',
            'channel_url': 'https://t.me/GramSoprano', 'organizer_url': 'https://t.me/Organizer',
            'ends_at': datetime.fromtimestamp(time.time() + 86400, timezone.utc).isoformat(), 'ticket_cap': 2}


class ContestTests(unittest.TestCase):
    def setUp(self):
        database.open(':memory:')
        self.addCleanup(database.close)
        with contextlib.redirect_stdout(io.StringIO()):
            migrate()
        add_user(100, 'Host')
        self.movie = add_new_movie('Test film')
        self.item = contests.save(99, settings())
        self.id = self.item['id']
        contests.launch(99, self.id, self.item['revision'])
        self.member = contests.join(self.id, 100)

    def invite(self, user_id, inviter=None, confirm=True):
        inviter = inviter or self.member
        with database.transaction():
            fresh = add_user(user_id, 'friend')
            contests.accept_start(user_id, f"ref_{self.id}_{inviter['code']}", fresh)
        if confirm:
            contests.confirm_card(user_id, self.movie)

    def freeze(self):
        return contests.close(99, self.id, contests.overview(self.id)['token'])

    def test_first_start_and_card_are_both_required(self):
        self.invite(200, confirm=False)
        personal = contests.personal(self.id, 100)
        self.assertEqual((personal['pending'], personal['tickets']), (1, 0))
        self.assertFalse(contests.confirm_card(200, 999999))
        self.assertTrue(contests.confirm_card(200, self.movie))
        self.assertFalse(contests.confirm_card(200, self.movie))
        self.assertEqual(contests.personal(self.id, 100)['tickets'], 1)

    def test_existing_user_self_invite_and_unblock_do_not_count(self):
        for user_id in (100, 300):
            add_user(user_id)
            database.execute('UPDATE Users SET is_active=0 WHERE id=?', (user_id,))
            self.invite(user_id)
        self.assertEqual(contests.overview(self.id)['stats']['confirmed'], 0)
        self.assertEqual(database.execute('SELECT COUNT(*) FROM ContestReferrals').fetchone()[0], 0)

    def test_inviter_cannot_be_reassigned_by_repeated_start(self):
        add_user(101)
        other = contests.join(self.id, 101)
        self.invite(200, confirm=False)
        self.invite(200, other)
        self.assertEqual(contests.personal(self.id, 100)['tickets'], 1)
        self.assertEqual(contests.personal(self.id, 101)['tickets'], 0)

    def test_channel_campaign_never_creates_an_invite(self):
        fresh = add_user(200)
        self.assertEqual(contests.accept_start(200, f'campaign_{self.id}', fresh), self.id)
        contests.confirm_card(200, self.movie)
        self.assertEqual(contests.overview(self.id)['stats']['channel_visits'], 1)
        self.assertEqual(database.execute('SELECT COUNT(*) FROM ContestReferrals').fetchone()[0], 0)
        self.invite(200)
        self.assertEqual(contests.personal(self.id, 100)['tickets'], 0)

    def test_invalid_and_unknown_links_do_not_attribute(self):
        fresh = add_user(200)
        for value in ('ref_1', 'ref_1_aaaaaaaaaaaaaaaa', 'contest_999', 'campaign_1_extra', 'share_abc', 'ref_01_' + self.member['code']):
            self.assertIsNone(contests.accept_start(200, value, fresh))
        self.assertEqual(database.execute('SELECT COUNT(*) FROM ContestReferrals').fetchone()[0], 0)

    def test_ticket_cap_does_not_hide_total_referrals(self):
        for user_id in (200, 201, 202):
            self.invite(user_id)
        personal = contests.personal(self.id, 100)
        self.assertEqual((personal['confirmed'], personal['tickets']), (3, 2))
        self.assertEqual(contests.overview(self.id)['stats']['tickets'], 2)

    def test_deadline_prevents_both_registration_and_late_confirmation(self):
        self.invite(200, confirm=False)
        deadline = contests.get(self.id)['body']['ends_at']
        with patch('database.contests.time.time', return_value=deadline):
            self.invite(201)
            self.assertFalse(contests.confirm_card(200, self.movie))
        self.assertEqual(database.execute('SELECT COUNT(*) FROM ContestReferrals').fetchone()[0], 1)
        self.assertEqual(contests.personal(self.id, 100)['tickets'], 0)

    def test_review_excludes_and_restores_with_audit(self):
        self.invite(200)
        contests.review_referral(99, self.id, 200, True, 'Проверка')
        self.assertEqual(contests.personal(self.id, 100)['tickets'], 0)
        contests.review_referral(99, self.id, 200, False, 'Проверено')
        self.assertEqual(contests.personal(self.id, 100)['tickets'], 1)
        self.assertEqual([r['action'] for r in contests.page(self.id, 'audit')['items']].count('referral_review'), 2)

    def test_pending_review_return_still_requires_real_card(self):
        self.invite(200, confirm=False)
        contests.review_referral(99, self.id, 200, True, 'Проверка')
        self.assertFalse(contests.confirm_card(200, self.movie))
        contests.review_referral(99, self.id, 200, False, 'Проверено')
        self.assertEqual(contests.personal(self.id, 100)['tickets'], 0)
        self.assertTrue(contests.confirm_card(200, self.movie))

    def test_stale_close_preview_cannot_freeze_new_data(self):
        token = contests.overview(self.id)['token']
        self.invite(200)
        with self.assertRaisesRegex(ValueError, 'изменился'):
            contests.close(99, self.id, token)
        self.assertEqual(contests.get(self.id)['status'], 'active')
        self.assertEqual(contests.snapshot(self.id), [])

    def test_close_freezes_and_review_and_confirmation_cannot_change_it(self):
        self.invite(200)
        self.invite(201, confirm=False)
        self.freeze()
        before = contests.snapshot(self.id)
        self.assertFalse(contests.confirm_card(201, self.movie))
        with self.assertRaises(ValueError):
            contests.review_referral(99, self.id, 200, True, 'Проверка')
        self.assertEqual(contests.snapshot(self.id), before)
        self.assertEqual(contests.close(99, self.id, 'old')['status'], 'closed')

    def test_weighted_draw_boundaries_and_idempotency(self):
        self.invite(200)
        self.invite(201)
        add_user(101, 'Other')
        other = contests.join(self.id, 101)
        self.invite(202, other)
        self.freeze()
        with patch('database.contests.secrets.randbelow', return_value=2) as rng:
            result = contests.draw(99, self.id)
            self.assertEqual(result['winner_user_id'], 101)
            self.assertEqual(result['ticket'], 3)
            self.assertEqual(contests.draw(88, self.id), result)
            rng.assert_called_once_with(3)
        self.assertEqual([r['action'] for r in contests.page(self.id, 'audit')['items']].count('draw'), 1)
        self.assertEqual(contests.personal(self.id, 101)['tickets'], 1)

    def test_empty_draw_and_draw_without_snapshot_are_blocked(self):
        with self.assertRaises(ValueError):
            contests.draw(99, self.id)
        self.freeze()
        with self.assertRaises(ValueError):
            contests.draw(99, self.id)
        self.assertIsNone(contests.get(self.id)['draw_result'])

    def test_winner_claim_and_manual_delivery_are_idempotent(self):
        self.invite(200)
        self.freeze()
        contests.draw(99, self.id)
        with self.assertRaises(ValueError):
            contests.claim(self.id, 200)
        contests.claim(self.id, 100)
        contests.claim(self.id, 100)
        self.assertEqual(database.execute('SELECT COUNT(*) FROM ContestClaims').fetchone()[0], 1)
        contests.delivered(99, self.id, 'Передан в Telegram')
        contests.delivered(99, self.id, 'Повтор')
        self.assertEqual(contests.get(self.id)['delivery_note'], 'Передан в Telegram')
        with self.assertRaises(ValueError):
            contests.cancel(99, self.id, contests.get(self.id)['revision'])

    def test_conditions_are_immutable_after_launch_and_only_one_contest_can_run(self):
        with self.assertRaises(ValueError):
            contests.save(99, settings(), self.id, contests.get(self.id)['revision'])
        with self.assertRaises(ValueError):
            contests.save(99, settings())
        contests.cancel(99, self.id, contests.get(self.id)['revision'])
        item = contests.save(99, settings())
        contests.launch(99, item['id'], item['revision'])
        self.assertEqual(contests.current()['id'], item['id'])

    def test_cancel_retains_participant_history_and_blocks_new_invites(self):
        self.invite(200)
        contests.cancel(99, self.id, contests.get(self.id)['revision'])
        self.invite(201)
        self.assertEqual(contests.overview(self.id)['stats']['tickets'], 1)
        self.assertEqual(contests.page(self.id)['items'][0]['tickets'], 1)
        self.assertIn('Host', contests.export(self.id))
        self.assertEqual(database.execute('SELECT COUNT(*) FROM ContestReferrals').fetchone()[0], 1)

    def test_settings_validation_and_required_prize_and_contact(self):
        for overrides in ({'gift_url':'javascript:alert(1)'}, {'channel_url':'https://t.me/x?start=a'},
                          {'photo_url':'http://example.com/p.jpg'}, {'ticket_cap':0}, {'ticket_cap':True},
                          {'ends_at':'2027-01-01T12:00'}, {'title':''}, {'description':'x'*501}):
            with self.subTest(overrides=overrides), self.assertRaises(ValueError):
                contests.normalize({**settings(), **overrides})
        contests.cancel(99, self.id, contests.get(self.id)['revision'])
        item = contests.save(99, {**settings(), 'gift_url':''})
        with self.assertRaisesRegex(ValueError, 'подарок'):
            contests.launch(99, item['id'], item['revision'])
        item = contests.save(99, {**settings(), 'organizer_url':''}, item['id'], item['revision'])
        with self.assertRaisesRegex(ValueError, 'контакт'):
            contests.launch(99, item['id'], item['revision'])

    def test_csv_escapes_formula_username_and_snapshot_does_not_follow_rename(self):
        database.execute('UPDATE Users SET username=? WHERE id=100', ('=HYPERLINK("evil")',))
        self.invite(200)
        self.freeze()
        database.execute('UPDATE Users SET username=? WHERE id=100', ('Renamed',))
        csv = contests.export(self.id)
        self.assertIn("'=HYPERLINK", csv)
        self.assertNotIn('Renamed', csv)

    def test_legacy_migration_preserves_catalog_users_and_creates_tables_once(self):
        # Build a version-17 database separately, then exercise the actual upgrade.
        database.close()
        database.open(':memory:')
        with contextlib.redirect_stdout(io.StringIO()):
            for migration in MIGRATIONS[:-1]:
                migration()
        database.execute(f'PRAGMA user_version={len(MIGRATIONS)-1}')
        add_user(777, 'Keep')
        movie = add_new_movie('Keep film')
        with contextlib.redirect_stdout(io.StringIO()):
            migrate()
            migrate()
        self.assertEqual(database.execute('SELECT username FROM Users WHERE id=777').fetchone()[0], 'Keep')
        self.assertEqual(database.execute('SELECT movie_title FROM Movies WHERE movie_number=?', (movie,)).fetchone()[0], 'Keep film')
        self.assertEqual(database.execute('PRAGMA user_version').fetchone()[0], len(MIGRATIONS))

    def test_restart_preserves_referrals_frozen_snapshot_and_result(self):
        self.invite(200)
        self.freeze()
        expected = contests.draw(99, self.id)
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'test.sqlite'
            import sqlite3
            with contextlib.closing(sqlite3.connect(path)) as target:
                database.connection.backup(target)
            database.close()
            database.open(path)
            self.assertEqual(contests.draw(99, self.id), expected)
            self.assertEqual(contests.personal(self.id, 100)['tickets'], 1)
            database.close()


class ContestWebTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        database.open(':memory:')
        self.addCleanup(database.close)
        with contextlib.redirect_stdout(io.StringIO()):
            migrate()
        self.patchers = [patch('data.config.ADMIN', [99]), patch.object(Uploads, 'start'),
                         patch.object(Uploads, 'stop', new_callable=AsyncMock),
                         patch('services.movie_sharing.bot_username', 'ExampleBot')]
        for patcher in self.patchers:
            patcher.start()
            self.addCleanup(patcher.stop)
        config = SimpleNamespace(WEB_PUBLIC_URL='http://127.0.0.1', WEB_CHUNK_BYTES=1024,
                                 WEB_MAX_FILE_BYTES=1000, BOT_API_BASE='https://api.telegram.org',
                                 UPLOAD_DIR=Path(tempfile.gettempdir()), UPLOAD_QUOTA_BYTES=10000)
        self.client = TestClient(TestServer(create_app(None, config)))
        await self.client.start_server()
        self.addAsyncCleanup(self.client.close)
        session = auth.exchange_login(auth.create_login(99))
        self.headers = {'Origin':'http://127.0.0.1', 'Cookie':'kinobot_session='+session,
                        'X-CSRF-Token':auth.get_session(session)['csrf']}

    async def test_authentication_csrf_and_desktop_token_do_not_grant_admin_access(self):
        response = await self.client.get('/api/contests')
        self.assertEqual(response.status, 401)
        response = await self.client.post('/api/contests', json={'body':settings()}, headers={'Authorization':'Bearer desktop', 'Origin':'http://127.0.0.1'})
        self.assertEqual(response.status, 401)
        headers = {**self.headers, 'X-CSRF-Token':'wrong'}
        response = await self.client.post('/api/contests', json={'body':settings()}, headers=headers)
        self.assertEqual(response.status, 403)
        self.assertEqual(contests.history(), [])

    async def test_complete_admin_workflow_and_export(self):
        response = await self.client.post('/api/contests', json={'body':settings()}, headers=self.headers)
        self.assertEqual(response.status, 201)
        item = await response.json()
        url = f"/api/contests/{item['id']}"
        response = await self.client.post(url, json={'action':'launch', 'revision':item['revision']}, headers=self.headers)
        self.assertEqual(response.status, 200)
        add_user(100)
        member = contests.join(item['id'], 100)
        fresh = add_user(200)
        contests.accept_start(200, f"ref_{item['id']}_{member['code']}", fresh)
        contests.confirm_card(200, add_new_movie('Movie'))
        response = await self.client.get(url, headers=self.headers)
        info = await response.json()
        self.assertIn('campaign_', info['campaign_link'])
        self.assertIn('не требуется', info['announcement'])
        for action, values in [('close', {'token':info['token']}), ('draw', {})]:
            response = await self.client.post(url, json={'action':action, **values}, headers=self.headers)
            self.assertEqual(response.status, 200)
        response = await self.client.get(url+'/page', headers=self.headers)
        self.assertEqual((await response.json())['items'][0]['tickets'], 1)
        response = await self.client.get(url+'/export', headers=self.headers)
        self.assertEqual(response.status, 200)
        self.assertIn('100', await response.text())
        response = await self.client.get('/static/contests.js')
        self.assertEqual(response.status, 200)
