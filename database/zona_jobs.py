"""Durable administrator pilot jobs, independent of published catalog cards."""

import json
import uuid

from database.connection import database

FIELDS = ('id', 'admin_id', 'page_url', 'source_id', 'label', 'metadata', 'status', 'error',
          'path', 'file_id', 'asset_id', 'message_id', 'created_at', 'updated_at')


def get(job_id, admin_id=None):
    row = database.execute(f"SELECT {','.join(FIELDS)} FROM ZonaJobs WHERE id=?", (job_id,)).fetchone()
    result = dict(zip(FIELDS, row)) if row else None
    return result if result and (admin_id is None or result['admin_id'] == admin_id) else None


def enqueue(admin_id, page_url, source):
    with database.transaction():
        row = database.execute('SELECT id FROM ZonaJobs WHERE admin_id=? AND source_id=?',
                               (admin_id, source['source_id'])).fetchone()
        if row:
            # Repeated callbacks never duplicate an upload, including ambiguous interruptions.
            return get(row[0])
        job_id = uuid.uuid4().hex
        database.execute('''INSERT INTO ZonaJobs(id,admin_id,page_url,source_id,label,metadata)
            VALUES (?,?,?,?,?,?)''', (job_id, admin_id, page_url, source['source_id'], source['label'],
                                     json.dumps(source.get('metadata', {}), ensure_ascii=False)))
    return get(job_id)


def update(job_id, **values):
    if not values or set(values) - {'status', 'error', 'path', 'file_id', 'asset_id', 'message_id'}:
        raise ValueError('Некорректное обновление задания')
    with database.transaction():
        database.execute('UPDATE ZonaJobs SET ' + ','.join(key + '=?' for key in values)
                         + ',updated_at=CURRENT_TIMESTAMP WHERE id=?', (*values.values(), job_id))


def recent(admin_id):
    return [get(row[0]) for row in database.execute(
        'SELECT id FROM ZonaJobs WHERE admin_id=? ORDER BY created_at DESC,rowid DESC LIMIT 5',
        (admin_id,)).fetchall()]


def retry(job_id, admin_id):
    job = get(job_id, admin_id)
    if not job or job['status'] not in {'error', 'interrupted'}:
        raise ValueError('Задание уже выполняется или завершено')
    update(job_id, status='queued', error=None)
    return get(job_id)


def recover():
    with database.transaction():
        database.execute("UPDATE ZonaJobs SET status='queued' WHERE status='downloading'")
        database.execute("""UPDATE ZonaJobs SET status='interrupted',
            error='Отправка прервана. Проверьте чат перед повтором: видео могло уже прийти.'
            WHERE status='sending'""")
