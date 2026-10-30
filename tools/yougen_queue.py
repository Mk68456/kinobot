"""Persistent single-worker request queue, isolated from KinoTime's database."""

from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3
import uuid


class Queue:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.path)) as db:
            db.execute('''CREATE TABLE IF NOT EXISTS Jobs (
                id TEXT PRIMARY KEY, request_key TEXT NOT NULL UNIQUE, payload TEXT NOT NULL,
                status TEXT NOT NULL, phase TEXT NOT NULL, details TEXT NOT NULL DEFAULT '{}',
                error TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP)''')
            db.commit()

    def enqueue(self, payload, identity=None):
        required = {'query', 'result_title', 'title', 'voice', 'quality', 'season', 'episode'}
        if set(payload) - {'content_type'} != required:
            raise ValueError('Некорректный состав задания.')
        for name in required - {'season', 'episode'}:
            if not isinstance(payload[name], str) or not 1 <= len(payload[name]) <= 200:
                raise ValueError('Заполните строковые поля задания.')
        kind = payload.get('content_type', 'series')
        if (kind not in {'movie', 'series'} or any(type(payload[n]) is not int for n in ['season', 'episode'])
                or kind == 'series' and any(not 1 <= payload[n] <= 999 for n in ['season', 'episode'])
                or kind == 'movie' and any(payload[n] != 0 for n in ['season', 'episode'])):
            raise ValueError('Сезон и серия должны быть положительными номерами.')
        from tools.yougen_bridge import VOICES
        if payload['voice'] not in VOICES or payload['quality'] not in {'240p', '360p', '480p', '720p', '1080p', '1440p', '2160p'}:
            raise ValueError('Выберите поддерживаемую озвучку и качество.')
        raw = json.dumps(payload, sort_keys=True, ensure_ascii=False)
        key = hashlib.sha256((('remote:' + identity) if identity else raw).encode()).hexdigest()
        with closing(sqlite3.connect(self.path)) as db:
            db.execute('INSERT OR IGNORE INTO Jobs(id,request_key,payload,status,phase) VALUES (?,?,?,?,?)',
                       (uuid.uuid4().hex, key, raw, 'queued', 'queued'))
            db.commit()
            return db.execute('SELECT id FROM Jobs WHERE request_key=?', (key,)).fetchone()[0]

    def claim(self):
        with closing(sqlite3.connect(self.path)) as db:
            db.execute('BEGIN IMMEDIATE')
            if db.execute("SELECT 1 FROM Jobs WHERE status='working'").fetchone():
                return None
            row = db.execute("SELECT id FROM Jobs WHERE status='queued' ORDER BY created_at,rowid LIMIT 1").fetchone()
            if not row:
                return None
            db.execute("UPDATE Jobs SET status='working',phase='claimed',updated_at=CURRENT_TIMESTAMP WHERE id=?", row)
            db.commit()
        return self.get(row[0])

    def get(self, job_id):
        with closing(sqlite3.connect(self.path)) as db:
            db.row_factory = sqlite3.Row
            row = db.execute('SELECT * FROM Jobs WHERE id=?', (job_id,)).fetchone()
        if not row:
            return None
        result = dict(row)
        result['payload'], result['details'] = json.loads(row['payload']), json.loads(row['details'])
        result.pop('request_key')
        return result

    def update(self, job_id, phase, *, status='working', details=None, error=None):
        current = self.get(job_id)
        if not current or current['status'] != 'working':
            raise ValueError('Задание не принадлежит работающему обработчику.')
        if status not in {'working', 'complete', 'needs_review'}:
            raise ValueError('Некорректный статус задания.')
        merged = {**current['details'], **(details or {})}
        with closing(sqlite3.connect(self.path)) as db:
            db.execute('UPDATE Jobs SET phase=?,status=?,details=?,error=?,updated_at=CURRENT_TIMESTAMP WHERE id=?',
                       (phase, status, json.dumps(merged, ensure_ascii=False), error, job_id))
            db.commit()

    def interrupt(self, job_id):
        """Explicit operator action only, after stopping the old process."""
        self.update(job_id, 'interrupted', status='needs_review', error='Проверьте чат и черновик перед повтором.')
