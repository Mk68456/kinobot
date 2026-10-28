"""Durable user requests; one provider lease, no automatic replay of uncertain work."""

import hashlib
import hmac
import json
import secrets
import time
import uuid

from database.connection import database

FIELDS = ('id', 'user_id', 'identity', 'payload', 'request_id', 'status', 'owner',
          'lease_hash', 'lease_until', 'draft_id', 'asset_id', 'file_id', 'sent_message_id',
          'error', 'created_at', 'updated_at', 'generation')


def get(job_id, user_id=None):
    row = database.execute('SELECT ' + ','.join(FIELDS) + ' FROM InstantJobs WHERE id=?', (job_id,)).fetchone()
    if not row or user_id is not None and row[1] != user_id:
        return None
    result = dict(zip(FIELDS, row))
    result['payload'] = json.loads(result['payload'])
    return result


def online():
    row = database.execute('SELECT last_seen FROM InstantAgent WHERE id=1').fetchone()
    active = database.execute("SELECT 1 FROM InstantJobs WHERE status='working' AND lease_until>?",
                              (time.time(),)).fetchone()
    return bool(row and row[0] > time.time() - 90 or active)


def create(user_id, payload, request_id):
    from tools.yougen_bridge import VOICES
    kind = payload.get('content_type')
    if (kind not in {'movie', 'series'} or payload.get('voice') not in VOICES
            or payload.get('quality') not in {'360p', '480p', '720p', '1080p'}
            or not isinstance(payload.get('title'), str) or not 1 <= len(payload['title']) <= 200):
        raise ValueError('Проверьте название, озвучку и качество.')
    season, episode = payload.get('season'), payload.get('episode')
    if (type(season) is not int or type(episode) is not int
            or kind == 'movie' and (season != 0 or episode != 0)
            or kind == 'series' and not (1 <= season <= 999 and 1 <= episode <= 999)):
        raise ValueError('Для быстрой выдачи выберите одну серию, не весь сезон.')
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    identity = hashlib.sha256((str(user_id) + '\0' + raw).encode()).hexdigest()
    with database.transaction():
        old = database.execute('SELECT id FROM InstantJobs WHERE identity=?', (identity,)).fetchone()
        if old:
            return get(old[0])
        if not online():
            raise ValueError('Обработчик сейчас недоступен. Можно отправить обычную заявку на добавление.')
        active = database.execute("SELECT 1 FROM InstantJobs WHERE user_id=? AND status IN ('queued','working','sending')",
                                  (user_id,)).fetchone()
        count = database.execute('SELECT COUNT(*) FROM InstantJobs WHERE user_id=? AND created_at>?',
                                 (user_id, time.time() - 3600)).fetchone()[0]
        total = database.execute("SELECT COUNT(*) FROM InstantJobs WHERE status IN ('queued','working')").fetchone()[0]
        if active or count >= 3 or total >= 100:
            raise ValueError('Очередь или лимит запросов заполнены. Дождитесь результата и попробуйте позже.')
        job_id, now = uuid.uuid4().hex, time.time()
        database.execute('INSERT INTO InstantJobs(id,user_id,identity,payload,request_id,status,created_at,updated_at) '
                         'VALUES (?,?,?,?,?,?,?,?)', (job_id, user_id, identity, raw, request_id, 'queued', now, now))
    return get(job_id)


def claim(owner):
    now = time.time()
    with database.transaction():
        database.execute('INSERT OR REPLACE INTO InstantAgent VALUES (1,?,?)', (owner, now))
        # A missing worker may already have sent a message. Never replay automatically.
        database.execute("UPDATE InstantJobs SET status='review',error='worker_interrupted',updated_at=? "
                         "WHERE status='working' AND lease_until<?", (now, now))
        if database.execute("SELECT 1 FROM InstantJobs WHERE status='working'").fetchone():
            return None
        row = database.execute("SELECT id FROM InstantJobs WHERE status='queued' ORDER BY created_at,id LIMIT 1").fetchone()
        if not row:
            return None
        token = secrets.token_urlsafe(24)
        database.execute("UPDATE InstantJobs SET status='working',owner=?,lease_hash=?,lease_until=?,updated_at=? WHERE id=?",
                         (owner, hashlib.sha256(token.encode()).hexdigest(), now + 900, now, row[0]))
    result = get(row[0])
    return {'id': result['id'], 'payload': result['payload'], 'lease': token}


def leased(job_id, owner, token):
    current = get(job_id)
    if (not current or current['owner'] != owner or not isinstance(token, str) or len(token) > 100
            or not hmac.compare_digest(current['lease_hash'] or '', hashlib.sha256(token.encode()).hexdigest())):
        raise ValueError('Задание не принадлежит этому обработчику.')
    if current['status'] == 'working' and current['lease_until'] < time.time():
        raise ValueError('Срок обработки истёк. Требуется проверка результата.')
    return current


def update(job_id, **fields):
    permitted = {'status', 'draft_id', 'asset_id', 'file_id', 'error', 'sent_message_id', 'generation'}
    if not fields or not set(fields) <= permitted:
        raise ValueError('Некорректное обновление задания.')
    with database.transaction():
        database.execute('UPDATE InstantJobs SET ' + ','.join(k + '=?' for k in fields) + ',updated_at=? WHERE id=?',
                         (*fields.values(), time.time(), job_id))


def recent(user_id):
    return [get(row[0], user_id) for row in database.execute(
        'SELECT id FROM InstantJobs WHERE user_id=? ORDER BY created_at DESC LIMIT 5', (user_id,)).fetchall()]
