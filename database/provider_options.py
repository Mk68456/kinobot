"""Short lived, leased provider menu lookups. No media transfer."""
import hashlib
import hmac
import json
import secrets
import time
import uuid
from database.connection import database
from tools.provider_identity import valid_voice_name


def setup():
    database.execute("CREATE TABLE IF NOT EXISTS ProviderOptions (id TEXT PRIMARY KEY,user_id INTEGER,payload TEXT,status TEXT,owner TEXT,lease TEXT,expires REAL,updated REAL,choices TEXT)")
    database.commit()


def get(key, user_id=None):
    setup()
    row = database.execute('SELECT id,user_id,payload,status,expires,choices FROM ProviderOptions WHERE id=?', (key,)).fetchone()
    if not row or user_id is not None and row[1] != user_id:
        return None
    return dict(id=row[0],user_id=row[1],payload=json.loads(row[2]),status=row[3],expires=row[4],choices=json.loads(row[5] or '[]'))


def create(user_id, payload):
    setup()
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    now = time.time()
    with database.transaction():
        old = database.execute("SELECT id FROM ProviderOptions WHERE user_id=? AND payload=? AND ((status='ready' AND expires>?) OR (status IN ('queued','working') AND updated>?)) ORDER BY updated DESC LIMIT 1", (user_id,raw,now,now-180)).fetchone()
        if old:
            return get(old[0],user_id)
        count = database.execute('SELECT COUNT(*) FROM ProviderOptions WHERE user_id=? AND updated>?',(user_id,now-60)).fetchone()[0]
        if count >= 8:
            raise ValueError('Слишком много проверок. Подождите минуту.')
        key = uuid.uuid4().hex
        database.execute("INSERT INTO ProviderOptions VALUES (?,?,?,'queued',NULL,NULL,0,?,NULL)",(key,user_id,raw,now))
    return get(key,user_id)


def claim(owner):
    setup()
    now = time.time()
    with database.transaction():
        # Serialize with normal delivery and bulk menu traversal.
        if database.execute("SELECT 1 FROM InstantJobs WHERE status='working' AND lease_until>?",(now,)).fetchone():
            return None
        if database.execute("SELECT 1 FROM sqlite_master WHERE name='ProviderBatches'").fetchone() and database.execute("SELECT 1 FROM ProviderBatches WHERE status='discovering' AND lease_until>?",(now,)).fetchone():
            return None
        database.execute("UPDATE ProviderOptions SET status='error' WHERE status='working' AND expires<?",(now,))
        if database.execute("SELECT 1 FROM ProviderOptions WHERE status='working'").fetchone():
            return None
        row = database.execute("SELECT id FROM ProviderOptions WHERE status='queued' AND updated>? ORDER BY updated LIMIT 1",(now-180,)).fetchone()
        if not row:
            return None
        token=secrets.token_urlsafe(24)
        database.execute("UPDATE ProviderOptions SET status='working',owner=?,lease=?,expires=? WHERE id=?",(owner,hashlib.sha256(token.encode()).hexdigest(),now+150,row[0]))
    return dict(id=row[0],payload=get(row[0])['payload'],lease=token)


def finish(key,owner,token,choices):
    setup()
    with database.transaction():
        row=database.execute('SELECT status,owner,lease,expires,payload FROM ProviderOptions WHERE id=?',(key,)).fetchone()
        if not row or row[0]!='working' or row[1]!=owner or row[3]<time.time() or not isinstance(token,str) or not hmac.compare_digest(row[2],hashlib.sha256(token.encode()).hexdigest()):
            raise ValueError('Проверка не принадлежит обработчику или истекла.')
        payload=json.loads(row[4])
        if choices is not None:
            if not isinstance(choices,list) or not 1<=len(choices)<=100 or len(set(choices))!=len(choices) or any(not valid_voice_name(v) for v in choices):
                raise ValueError('Неверный список вариантов.')
            if payload.get('voice') and any(v not in {'240p','360p','480p','720p','1080p','1440p','2160p'} for v in choices):
                raise ValueError('Неверное качество.')
        database.execute('UPDATE ProviderOptions SET status=?,choices=?,expires=?,updated=? WHERE id=?',('ready' if choices else 'error',json.dumps(choices or []),time.time()+300,time.time(),key))


def permits(user_id,payload):
    item=get(payload.get('options_id',''),user_id)
    if not item or item['status']!='ready' or item['expires']<time.time() or payload.get('quality') not in item['choices']:
        return False
    return all(item['payload'].get(k)==payload.get(k) for k in ('title','query','year','content_type','season','episode','voice'))
