"""Durable admin imports. Discovery finishes before individual files enter the queue."""
import hashlib
import hmac
import json
import secrets
import time
import uuid

from database.connection import database
from services.provider_catalog import identity, variant_exists


def create(admin_id, payload):
    if (payload.get('content_type') not in ('movie', 'series') or not payload.get('title')
            or not str(payload.get('year', '')).isdigit() or len(payload['title']) > 180):
        raise ValueError('Укажите фильм/сериал, название и год.')
    with database.transaction():
        active = database.execute("SELECT id FROM ProviderBatches WHERE admin_id=? AND status IN ('prepared','queued','discovering','running','paused')",
                                  (admin_id,)).fetchone()
        if active:
            raise ValueError('Сначала завершите или отмените предыдущую массовую загрузку.')
        key = uuid.uuid4().hex
        database.execute('INSERT INTO ProviderBatches(id,admin_id,payload,status,created_at) VALUES (?,?,?,?,?)',
                         (key, admin_id, json.dumps(payload), 'prepared', time.time()))
    return get(key, admin_id)


def get(key, admin_id=None):
    row = database.execute('SELECT id,admin_id,payload,status,error,skipped FROM ProviderBatches WHERE id=?', (key,)).fetchone()
    if not row or admin_id is not None and row[1] != admin_id:
        return None
    result = dict(zip(('id', 'admin_id', 'payload', 'status', 'error', 'skipped'), row))
    result['payload'] = json.loads(result['payload'])
    result['counts'] = dict(database.execute('SELECT j.status,COUNT(*) FROM InstantJobs j JOIN ProviderBatchItems i ON i.job_id=j.id '
                                            'WHERE i.batch_id=? GROUP BY j.status', (key,)).fetchall())
    result['publication'] = dict(database.execute('SELECT d.status,COUNT(*) FROM ProviderBatchItems i '
        'JOIN InstantJobs j ON j.id=i.job_id JOIN WebDrafts d ON d.id=j.draft_id WHERE i.batch_id=? GROUP BY d.status',
        (key,)).fetchall())
    if result['status'] in ('running', 'review') and not result['error'] and result['counts'] and not any(
            result['counts'].get(s) for s in ('queued', 'working', 'bulk_paused', 'ready', 'sending', 'uncertain')):
        issue = database.execute('SELECT 1 FROM ProviderBatchItems i JOIN InstantJobs j ON j.id=i.job_id '
            'JOIN ProviderFiles f ON f.draft_id=j.draft_id WHERE i.batch_id=? AND f.publish_error IS NOT NULL LIMIT 1', (key,)).fetchone()
        status = 'review' if result['counts'].get('review') or issue else 'complete'
        database.execute('UPDATE ProviderBatches SET status=? WHERE id=?', (status, key))
        database.commit()
        result['status'] = status
    return result


def recent(admin_id):
    return [get(row[0], admin_id) for row in database.execute(
        'SELECT id FROM ProviderBatches WHERE admin_id=? ORDER BY created_at DESC LIMIT 5', (admin_id,)).fetchall()]


def control(key, admin_id, action):
    with database.transaction():
        batch = get(key, admin_id)
        if not batch:
            raise ValueError('Массовая загрузка недоступна.')
        old = batch['status']
        if action == 'start' and old == 'prepared':
            new = 'queued'
        elif action == 'pause' and old in ('queued', 'discovering', 'running'):
            new = 'paused'
            database.execute("UPDATE InstantJobs SET status='bulk_paused' WHERE status='queued' AND id IN "
                             '(SELECT job_id FROM ProviderBatchItems WHERE batch_id=?)', (key,))
        elif action == 'resume' and old == 'paused':
            new = 'running' if batch['counts'] else 'queued'
            database.execute("UPDATE InstantJobs SET status='queued' WHERE status IN ('bulk_paused','ready') AND id IN "
                             '(SELECT job_id FROM ProviderBatchItems WHERE batch_id=?)', (key,))
        elif action == 'retry' and old == 'review' and not batch['counts']:
            new = 'queued'
        elif action == 'cancel' and old not in ('cancelled', 'complete'):
            new = 'cancelled'
            database.execute("UPDATE InstantJobs SET status='cancelled' WHERE status IN ('queued','bulk_paused') AND id IN "
                             '(SELECT job_id FROM ProviderBatchItems WHERE batch_id=?)', (key,))
        else:
            raise ValueError('Действие уже применено или недоступно. Обновите статус.')
        database.execute('UPDATE ProviderBatches SET status=? WHERE id=?', (new, key))
    return get(key, admin_id)


def claim(owner, admin_id=None):
    now = time.time()
    with database.transaction():
        database.execute("UPDATE ProviderBatches SET status='review',error='discovery_interrupted' "
                         "WHERE status='discovering' AND lease_until<?", (now,))
        if database.execute("SELECT 1 FROM InstantJobs WHERE status='working'").fetchone():
            return None
        if database.execute("SELECT 1 FROM ProviderBatches WHERE status='discovering'").fetchone():
            return None
        query = "SELECT id FROM ProviderBatches WHERE status='queued'"
        parameters = ()
        if admin_id is not None:
            query += ' AND admin_id=?'
            parameters = (admin_id,)
        row = database.execute(query + ' ORDER BY created_at LIMIT 1', parameters).fetchone()
        if not row:
            return None
        token = secrets.token_urlsafe(24)
        database.execute("UPDATE ProviderBatches SET status='discovering',owner=?,lease_hash=?,lease_until=? WHERE id=?",
                         (owner, hashlib.sha256(token.encode()).hexdigest(), now + 3600, row[0]))
    result = get(row[0])
    return {'id': result['id'], 'payload': result['payload'], 'lease': token}


def plan(key, owner, token, payloads, error=None):
    with database.transaction():
        row = database.execute('SELECT owner,lease_hash,lease_until FROM ProviderBatches WHERE id=?', (key,)).fetchone()
        batch = get(key)
        if (not row or row[0] != owner or not isinstance(token, str) or not hmac.compare_digest(
                row[1] or '', hashlib.sha256(token.encode()).hexdigest()) or row[2] < time.time()):
            raise ValueError('Неверный или истёкший доступ к массовой загрузке.')
        if batch['status'] == 'running':
            return batch  # An identical completion request cannot enqueue twice.
        if batch['status'] != 'discovering':
            raise ValueError('Обход остановлен или отменён.')
        if error:
            database.execute("UPDATE ProviderBatches SET status='review',error=? WHERE id=?", (str(error)[:200], key))
            return get(key)
        if not isinstance(payloads, list) or not 1 <= len(payloads) <= 5000:
            raise ValueError('План должен содержать от 1 до 5000 вариантов.')
        from tools.yougen_bridge import VOICES
        seen, skipped = set(), 0
        for p in payloads:
            if (not isinstance(p, dict) or set(p) - {'metadata'} != {'title', 'query', 'year', 'content_type', 'season', 'episode', 'voice', 'quality'}
                    or any(p[k] != batch['payload'][k] for k in ('title', 'query', 'year', 'content_type'))
                    or 'metadata' in p and p['metadata'] != batch['payload'].get('metadata')
                    or p['voice'] not in VOICES or p['quality'] not in ('240p', '360p', '480p', '720p', '1080p', '1440p', '2160p')
                    or type(p['season']) is not int or type(p['episode']) is not int
                    or p['content_type'] == 'series' and not (1 <= p['season'] <= 999 and 1 <= p['episode'] <= 999)
                    or p['content_type'] == 'movie' and (p['season'] != 0 or p['episode'] != 0)):
                raise ValueError('План не соответствует выбранному произведению.')
            variant = identity(p, True)
            if variant in seen:
                continue
            seen.add(variant)
            prior = database.execute('SELECT draft_id FROM ProviderFiles WHERE identity=?', (variant,)).fetchone()
            if prior or variant_exists(p):
                # Prepared, ready or published variants never request the provider again.
                skipped += 1
                continue
            job_id = uuid.uuid4().hex
            stored = dict(p)
            if 'metadata' in batch['payload']:
                stored['metadata'] = batch['payload']['metadata']
            database.execute('INSERT INTO InstantJobs(id,user_id,identity,payload,status,created_at,updated_at) VALUES (?,?,?,?,?,?,?)',
                             (job_id, batch['admin_id'], 'bulk:' + key + ':' + variant, json.dumps(stored), 'queued', time.time(), time.time()))
            database.execute('INSERT INTO ProviderBatchItems VALUES (?,?)', (job_id, key))
        database.execute("UPDATE ProviderBatches SET status=?,skipped=?,error=NULL WHERE id=?", ('running' if seen else 'complete', skipped, key))
        if not database.execute('SELECT 1 FROM ProviderBatchItems WHERE batch_id=?', (key,)).fetchone():
            database.execute("UPDATE ProviderBatches SET status='complete' WHERE id=?", (key,))
    return get(key)


def heartbeat(key, owner, token):
    with database.transaction():
        row = database.execute('SELECT owner,lease_hash,lease_until,status FROM ProviderBatches WHERE id=?', (key,)).fetchone()
        if (not row or row[0] != owner or not isinstance(token, str) or not hmac.compare_digest(
                row[1] or '', hashlib.sha256(token.encode()).hexdigest()) or row[2] < time.time()):
            raise ValueError('Неверное подключение обхода.')
        if row[3] != 'discovering':
            return False
        database.execute('UPDATE ProviderBatches SET lease_until=? WHERE id=?', (time.time() + 3600, key))
        database.execute('INSERT OR REPLACE INTO InstantAgent VALUES (1,?,?)', (owner, time.time()))
    return True


def for_job(job_id):
    row = database.execute('SELECT batch_id FROM ProviderBatchItems WHERE job_id=?', (job_id,)).fetchone()
    return get(row[0]) if row else None
