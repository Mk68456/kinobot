"""Persistent referral attribution and a single, immutable draw per contest.

All changes are synchronous: never await inside these transactions.
"""

import csv
import hashlib
import io
import json
import math
import re
import secrets
import time
from datetime import datetime, timezone
from urllib.parse import urlsplit

from database.connection import database


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def _integer(value, label='ID', maximum=2**53 - 1):
    if isinstance(value, bool) or not re.fullmatch(r'[1-9][0-9]{0,15}', str(value or '')):
        raise ValueError(f'{label}: требуется положительное целое число.')
    value = int(value)
    if value > maximum:
        raise ValueError(f'{label}: слишком большое число.')
    return value


def _text(value, label, limit, required=False):
    if not isinstance(value, str):
        raise ValueError(f'{label}: требуется текст.')
    value = value.strip()
    if len(value) > limit or (required and not value):
        raise ValueError(f'{label}: заполните поле, не более {limit} символов.')
    return value


def normalize(value):
    if not isinstance(value, dict):
        raise ValueError('Заполните настройки конкурса.')
    result = {key: _text(value.get(key, ''), label, limit, required) for key, label, limit, required in (
        ('title', 'Название', 100, True), ('gift_name', 'Приз', 150, True),
        ('description', 'Описание', 500, False), ('gift_url', 'Ссылка на подарок', 250, False),
        ('photo_url', 'Изображение', 1000, False), ('channel_url', 'Канал новостей', 250, False),
        ('organizer_url', 'Контакт организатора', 250, False))}
    for key in ('gift_url', 'photo_url', 'channel_url', 'organizer_url'):
        if not result[key]:
            continue
        url = urlsplit(result[key])
        if url.scheme != 'https' or not url.hostname or url.username or url.password or url.fragment:
            raise ValueError('Ссылки должны начинаться с https:// и не содержать пароль или фрагмент.')
        if key == 'gift_url' and (url.netloc.lower() != 't.me' or not re.fullmatch(r'/nft/[A-Za-z0-9_-]+', url.path) or url.query):
            raise ValueError('Для приза укажите публичную ссылку https://t.me/nft/…')
        if key in ('channel_url', 'organizer_url') and (url.netloc.lower() != 't.me' or not re.fullmatch(r'/[A-Za-z][A-Za-z0-9_]{3,31}', url.path) or url.query):
            raise ValueError('Для канала и контакта укажите ссылку https://t.me/имя.')
    try:
        date = datetime.fromisoformat(str(value.get('ends_at', '')).replace('Z', '+00:00'))
        if date.tzinfo is None:
            raise ValueError()
        result['ends_at'] = date.timestamp()
        if not math.isfinite(result['ends_at']):
            raise ValueError()
    except (ValueError, OverflowError):
        raise ValueError('Укажите дату окончания с часовым поясом.') from None
    if result['ends_at'] <= time.time():
        raise ValueError('Дата окончания должна быть в будущем.')
    result['ticket_cap'] = _integer(value.get('ticket_cap', 20), 'Лимит билетов', 1000)
    return result


def get(contest_id):
    row = database.execute('SELECT * FROM Contests WHERE id=?', (_integer(contest_id),)).fetchone()
    if not row:
        raise ValueError('Конкурс не найден.')
    names = [r[1] for r in database.execute('PRAGMA table_info(Contests)')]
    result = dict(zip(names, row))
    result['body'] = json.loads(result['body'])
    result['draw_result'] = json.loads(result['draw_result']) if result['draw_result'] else None
    return result


def current():
    row = database.execute("SELECT id FROM Contests WHERE status IN ('active','closed','finished') ORDER BY id DESC LIMIT 1").fetchone()
    return get(row[0]) if row else None


def is_open(contest):
    return contest['status'] == 'active' and time.time() < contest['body']['ends_at']


def _audit(contest_id, admin_id, action, detail):
    database.execute('INSERT INTO ContestAudit(contest_id,admin_id,action,detail,created_at) VALUES (?,?,?,?,?)',
                     (contest_id, admin_id, action, _json(detail), time.time()))


def save(admin_id, value, contest_id=None, revision=None):
    body = normalize(value)
    with database.transaction():
        if contest_id is None:
            if database.execute("SELECT 1 FROM Contests WHERE status IN ('active','closed')").fetchone():
                raise ValueError('Сначала завершите текущий конкурс.')
            contest_id = database.execute('INSERT INTO Contests(body,created_by,created_at) VALUES (?,?,?)',
                                          (_json(body), admin_id, time.time())).lastrowid
        else:
            item = get(contest_id)
            if item['status'] != 'draft':
                raise ValueError('Условия после запуска не изменяются. Создайте новый конкурс.')
            if item['revision'] != revision:
                raise ValueError('Настройки изменились. Обновите страницу.')
            database.execute('UPDATE Contests SET body=?,revision=revision+1 WHERE id=?', (_json(body), contest_id))
        _audit(contest_id, admin_id, 'save', body)
    return get(contest_id)


def launch(admin_id, contest_id, revision):
    with database.transaction():
        item = get(contest_id)
        if item['status'] == 'active':
            return item
        if item['status'] != 'draft' or item['revision'] != revision:
            raise ValueError('Обновите настройки перед запуском.')
        if database.execute("SELECT 1 FROM Contests WHERE status IN ('active','closed')").fetchone():
            raise ValueError('Другой конкурс ещё не завершён.')
        if not item['body']['gift_url']:
            raise ValueError('Перед запуском укажите ссылку на конкретный коллекционный подарок.')
        if not item['body']['organizer_url']:
            raise ValueError('Укажите контакт организатора для выдачи приза победителю.')
        if time.time() >= item['body']['ends_at']:
            raise ValueError('Срок уже истёк. Измените дату окончания.')
        database.execute("UPDATE Contests SET status='active',revision=revision+1 WHERE id=?", (contest_id,))
        _audit(contest_id, admin_id, 'launch', {})
    return get(contest_id)


def join(contest_id, user_id, source='direct'):
    item = get(contest_id)
    row = database.execute('SELECT id,user_id,code,source FROM ContestMembers WHERE contest_id=? AND user_id=?',
                           (contest_id, user_id)).fetchone()
    if not row and is_open(item):
        if not database.execute('SELECT 1 FROM Users WHERE id=?', (user_id,)).fetchone():
            raise ValueError('Сначала запустите бота.')
        with database.transaction():
            database.execute('INSERT OR IGNORE INTO ContestMembers(contest_id,user_id,code,source,joined_at) VALUES (?,?,?,?,?)',
                             (contest_id, user_id, secrets.token_hex(8), source, time.time()))
        row = database.execute('SELECT id,user_id,code,source FROM ContestMembers WHERE contest_id=? AND user_id=?',
                               (contest_id, user_id)).fetchone()
    return dict(zip(('id', 'user_id', 'code', 'source'), row)) if row else None


def accept_start(user_id, payload, is_new):
    """Called immediately after add_user, before any await. Never reassign an invite."""
    match = re.fullmatch(r'(ref|contest|campaign)_([1-9][0-9]{0,8})(?:_([a-f0-9]{16}))?', payload)
    if not match:
        return None
    kind, raw_id, code = match.groups()
    contest_id = int(raw_id)
    if not database.execute('SELECT 1 FROM Contests WHERE id=?', (contest_id,)).fetchone():
        return None
    item = get(contest_id)
    inviter = None
    if kind == 'ref':
        inviter = database.execute('SELECT user_id FROM ContestMembers WHERE contest_id=? AND code=?',
                                   (contest_id, code)).fetchone()
        if not inviter:
            return None
    elif code is not None:
        return None
    source = 'referral' if inviter else ('channel' if kind == 'campaign' else 'direct')
    with database.transaction():
        join(contest_id, user_id, source)
        if is_new and inviter and inviter[0] != user_id and is_open(item):
            database.execute('INSERT OR IGNORE INTO ContestReferrals(user_id,contest_id,inviter_id,registered_at) VALUES (?,?,?,?)',
                             (user_id, contest_id, inviter[0], time.time()))
    return contest_id


def confirm_card(user_id, movie_number):
    row = database.execute("SELECT contest_id FROM ContestReferrals WHERE user_id=? AND status='pending'", (user_id,)).fetchone()
    if not row or not is_open(get(row[0])):
        return False
    if not database.execute('SELECT 1 FROM Movies WHERE movie_number=?', (movie_number,)).fetchone():
        return False
    with database.transaction():
        result = database.execute("UPDATE ContestReferrals SET status='confirmed',confirmed_at=?,movie_number=? WHERE user_id=? AND status='pending'",
                                   (time.time(), movie_number, user_id))
    return result.rowcount == 1


def _roster(contest_id):
    cap = get(contest_id)['body']['ticket_cap']
    rows = database.execute("""SELECT m.id,m.user_id,u.username,
        COALESCE(SUM(r.status='confirmed'),0) FROM ContestMembers m
        JOIN Users u ON u.id=m.user_id
        LEFT JOIN ContestReferrals r ON r.contest_id=m.contest_id AND r.inviter_id=m.user_id
        WHERE m.contest_id=? GROUP BY m.id ORDER BY m.id""", (contest_id,)).fetchall()
    return [dict(zip(('member_id', 'user_id', 'username', 'referrals', 'tickets'), (*r, min(r[3], cap)))) for r in rows]


def snapshot(contest_id):
    rows = database.execute('SELECT member_id,user_id,username,referrals,tickets FROM ContestSnapshot WHERE contest_id=? ORDER BY member_id',
                           (contest_id,)).fetchall()
    return [dict(zip(('member_id', 'user_id', 'username', 'referrals', 'tickets'), row)) for row in rows]


def _display_roster(item):
    return snapshot(item['id']) if item['closed_at'] is not None else _roster(item['id'])


def _token(contest_id):
    item = get(contest_id)
    referrals = database.execute('SELECT user_id,inviter_id,status,confirmed_at,reason FROM ContestReferrals WHERE contest_id=? ORDER BY user_id',
                                  (contest_id,)).fetchall()
    return hashlib.sha256(_json([item['revision'], _roster(contest_id), referrals]).encode()).hexdigest()


def overview(contest_id):
    item = get(contest_id)
    roster = _display_roster(item)
    counts = dict(database.execute('SELECT status,COUNT(*) FROM ContestReferrals WHERE contest_id=? GROUP BY status', (contest_id,)))
    return {**item, 'accepting': is_open(item), 'token': _token(contest_id),
            'stats': {'members': database.execute('SELECT COUNT(*) FROM ContestMembers WHERE contest_id=?', (contest_id,)).fetchone()[0],
                      'eligible': sum(r['tickets'] > 0 for r in roster), 'tickets': sum(r['tickets'] for r in roster),
                      'confirmed': counts.get('confirmed', 0), 'pending': counts.get('pending', 0), 'excluded': counts.get('excluded', 0),
                      'channel_visits': database.execute("SELECT COUNT(*) FROM ContestMembers WHERE contest_id=? AND source='channel'", (contest_id,)).fetchone()[0]}}


def personal(contest_id, user_id):
    item = get(contest_id)
    member = join(contest_id, user_id)
    if item['closed_at'] is not None:
        row = database.execute('SELECT referrals,tickets FROM ContestSnapshot WHERE contest_id=? AND user_id=?', (contest_id, user_id)).fetchone()
        confirmed, tickets = row or (0, 0)
    else:
        confirmed = database.execute("SELECT COUNT(*) FROM ContestReferrals WHERE contest_id=? AND inviter_id=? AND status='confirmed'", (contest_id, user_id)).fetchone()[0]
        tickets = min(confirmed, item['body']['ticket_cap'])
    pending = database.execute("SELECT COUNT(*) FROM ContestReferrals WHERE contest_id=? AND inviter_id=? AND status='pending'", (contest_id, user_id)).fetchone()[0]
    result = item['draw_result']
    return {**item, 'member': member, 'confirmed': confirmed, 'tickets': tickets, 'pending': pending,
            'is_winner': bool(result and result['winner_user_id'] == user_id),
            'claimed': bool(database.execute('SELECT 1 FROM ContestClaims WHERE contest_id=?', (contest_id,)).fetchone())}


def review_referral(admin_id, contest_id, user_id, exclude, reason):
    user_id = _integer(user_id)
    reason = _text(reason, 'Причина', 300, True)
    if not isinstance(exclude, bool):
        raise ValueError('Укажите действие проверки.')
    with database.transaction():
        if get(contest_id)['status'] != 'active':
            raise ValueError('После фиксации списка приглашения не изменяются.')
        row = database.execute('SELECT status,confirmed_at FROM ContestReferrals WHERE contest_id=? AND user_id=?', (contest_id, user_id)).fetchone()
        if not row:
            raise ValueError('Приглашение не найдено.')
        status = 'excluded' if exclude else ('confirmed' if row[1] is not None else 'pending')
        database.execute('UPDATE ContestReferrals SET status=?,reason=? WHERE user_id=?', (status, reason, user_id))
        _audit(contest_id, admin_id, 'referral_review', {'user_id': user_id, 'before': row[0], 'after': status, 'reason': reason})
    return overview(contest_id)


def close(admin_id, contest_id, token):
    with database.transaction():
        item = get(contest_id)
        if item['status'] in ('closed', 'finished'):
            return overview(contest_id)
        if item['status'] != 'active' or not isinstance(token, str) or not secrets.compare_digest(token, _token(contest_id)):
            raise ValueError('Список участников изменился. Обновите его перед фиксацией.')
        roster = _roster(contest_id)
        for row in roster:
            database.execute('INSERT INTO ContestSnapshot VALUES (?,?,?,?,?,?)', (contest_id, *row.values()))
        database.execute("UPDATE Contests SET status='closed',closed_at=?,revision=revision+1 WHERE id=?", (time.time(), contest_id))
        _audit(contest_id, admin_id, 'close', {'eligible': sum(r['tickets'] > 0 for r in roster), 'tickets': sum(r['tickets'] for r in roster)})
    return overview(contest_id)


def draw(admin_id, contest_id):
    with database.transaction():
        item = get(contest_id)
        if item['draw_result']:
            return item['draw_result']
        if item['status'] != 'closed':
            raise ValueError('Сначала зафиксируйте список участников.')
        roster = snapshot(contest_id)
        total = sum(row['tickets'] for row in roster)
        if not total:
            raise ValueError('Нет участников с подтверждёнными приглашениями. Победитель не выбран.')
        selected = secrets.randbelow(total) + 1
        boundary = 0
        for row in roster:
            boundary += row['tickets']
            if selected <= boundary:
                winner = row
                break
        result = {'winner_user_id': winner['user_id'], 'winner_member_id': winner['member_id'],
                  'winner_username': winner['username'], 'ticket': selected, 'total_tickets': total,
                  'eligible': sum(row['tickets'] > 0 for row in roster), 'drawn_at': time.time(),
                  'snapshot_hash': hashlib.sha256(_json(roster).encode()).hexdigest(),
                  'algorithm': 'secrets.randbelow(total) + 1; ordered member_id; capped tickets'}
        database.execute("UPDATE Contests SET status='finished',draw_result=?,revision=revision+1 WHERE id=?", (_json(result), contest_id))
        _audit(contest_id, admin_id, 'draw', result)
    return result


def cancel(admin_id, contest_id, revision):
    with database.transaction():
        item = get(contest_id)
        if item['status'] == 'cancelled':
            return item
        if item['status'] == 'finished' or item['revision'] != revision:
            raise ValueError('Завершённый розыгрыш не отменяется. Обновите страницу.')
        database.execute("UPDATE Contests SET status='cancelled',revision=revision+1 WHERE id=?", (contest_id,))
        _audit(contest_id, admin_id, 'cancel', {})
    return get(contest_id)


def claim(contest_id, user_id):
    result = get(contest_id)['draw_result']
    if not result or result['winner_user_id'] != user_id:
        raise ValueError('Получение приза доступно только победителю.')
    with database.transaction():
        database.execute('INSERT OR IGNORE INTO ContestClaims VALUES (?,?,?)', (contest_id, user_id, time.time()))


def delivered(admin_id, contest_id, note):
    note = _text(note, 'Комментарий о передаче', 300, True)
    with database.transaction():
        item = get(contest_id)
        if item['status'] != 'finished':
            raise ValueError('Сначала выберите победителя.')
        if item['delivered_at'] is None:
            database.execute('UPDATE Contests SET delivered_at=?,delivery_note=? WHERE id=?', (time.time(), note, contest_id))
            _audit(contest_id, admin_id, 'delivered', {'note': note})
    return get(contest_id)


def page(contest_id, kind='members', offset=0):
    offset = max(0, int(offset))
    item = get(contest_id)
    if kind == 'members':
        rows = _display_roster(item)
        return {'items': rows[offset:offset + 50], 'total': len(rows), 'limit': 50}
    if kind == 'referrals':
        sql = '''SELECT r.user_id,u.username,r.inviter_id,r.status,r.reason,r.registered_at,r.confirmed_at
                 FROM ContestReferrals r LEFT JOIN Users u ON u.id=r.user_id WHERE r.contest_id=?'''
        rows = database.execute(sql + ' ORDER BY r.registered_at,r.user_id LIMIT 50 OFFSET ?', (contest_id, offset)).fetchall()
        names = ('user_id', 'username', 'inviter_id', 'status', 'reason', 'registered_at', 'confirmed_at')
        total = database.execute('SELECT COUNT(*) FROM ContestReferrals WHERE contest_id=?', (contest_id,)).fetchone()[0]
    elif kind == 'audit':
        rows = database.execute('SELECT admin_id,action,detail,created_at FROM ContestAudit WHERE contest_id=? ORDER BY id DESC LIMIT 50 OFFSET ?', (contest_id, offset)).fetchall()
        names = ('admin_id', 'action', 'detail', 'created_at')
        total = database.execute('SELECT COUNT(*) FROM ContestAudit WHERE contest_id=?', (contest_id,)).fetchone()[0]
    else:
        raise ValueError('Неизвестный список.')
    return {'items': [dict(zip(names, r)) for r in rows], 'total': total, 'limit': 50}


def history():
    return [{'id': r[0], 'title': json.loads(r[1])['title'], 'status': r[2]}
            for r in database.execute('SELECT id,body,status FROM Contests ORDER BY id DESC LIMIT 100')]


def export(contest_id):
    item = get(contest_id)
    rows = _display_roster(item)
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(('member_id', 'telegram_user_id', 'username', 'confirmed_referrals', 'tickets'))
    for row in rows:
        username = row['username'] or ''
        if username.startswith(('=', '+', '-', '@', '\t', '\r', '\n')):
            username = "'" + username
        writer.writerow((row['member_id'], row['user_id'], username, row['referrals'], row['tickets']))
    return output.getvalue()


def ends_label(contest):
    from datetime import timedelta
    return datetime.fromtimestamp(contest['body']['ends_at'], timezone(timedelta(hours=3))).strftime('%d.%m.%Y %H:%M МСК')
