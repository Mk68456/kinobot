"""Persisted viewing limits; declaring adulthood is not identity verification."""
import hashlib
import hmac
import re
import secrets
import time

from database.connection import database

RATINGS = ('0+', '6+', '12+', '16+', '18+')
PIN_ROUNDS = 210_000


def create_table():
    database.execute('''CREATE TABLE AgePreferences (
        user_id INTEGER PRIMARY KEY, max_age INTEGER NOT NULL DEFAULT 16
        CHECK(max_age IN (0,6,12,16,18)), pin_salt TEXT, pin_hash TEXT,
        attempts INTEGER NOT NULL DEFAULT 0, locked_until REAL NOT NULL DEFAULT 0,
        notified INTEGER NOT NULL DEFAULT 0, adult_confirmed_at REAL)''')


def preferences(user_id):
    row = database.execute('SELECT max_age,pin_hash,notified,locked_until FROM AgePreferences WHERE user_id=?',
                           (user_id,)).fetchone()
    return {'max_age': row[0] if row else 16, 'parental': bool(row and row[1]),
            'notified': bool(row and row[2]), 'locked_until': row[3] if row else 0}


def _ensure(user_id):
    database.execute('INSERT OR IGNORE INTO AgePreferences(user_id) VALUES (?)', (user_id,))


def mark_notified(user_id):
    with database.transaction():
        _ensure(user_id)
        database.execute('UPDATE AgePreferences SET notified=1 WHERE user_id=?', (user_id,))


def allowed(user_id, info):
    if not info:
        return False
    limit = preferences(user_id)['max_age']
    rating = info.get('age_rating')
    return limit == 18 or rating in RATINGS and int(rating[:-1]) <= limit


def sql_filter(user_id, column='age_rating'):
    # Column names are selected by application code, never request parameters.
    if column not in ('age_rating', 'm.age_rating'):
        raise ValueError('Invalid age column')
    limit = preferences(user_id)['max_age']
    if limit == 18:
        return '1=1', []
    values = [rating for rating in RATINGS if int(rating[:-1]) <= limit]
    return column + ' IN (' + ','.join('?' for _ in values) + ')', values


def confirm_adult(user_id):
    with database.transaction():
        _ensure(user_id)
        if preferences(user_id)['parental']:
            raise ValueError('Изменение доступно только с родительским PIN.')
        database.execute('UPDATE AgePreferences SET max_age=18,adult_confirmed_at=? WHERE user_id=?',
                         (time.time(), user_id))


def safe_mode(user_id):
    with database.transaction():
        _ensure(user_id)
        if preferences(user_id)['parental']:
            raise ValueError('Изменение доступно только с родительским PIN.')
        database.execute('UPDATE AgePreferences SET max_age=16 WHERE user_id=?', (user_id,))


def _validate_pin(pin):
    if not isinstance(pin, str) or not re.fullmatch(r'[0-9]{6}', pin):
        raise ValueError('PIN должен содержать ровно 6 цифр.')


def _hash(pin, salt):
    return hashlib.pbkdf2_hmac('sha256', pin.encode('ascii'), bytes.fromhex(salt), PIN_ROUNDS).hex()


def enable_parental(user_id, limit, pin):
    if type(limit) is not int or limit not in (0, 6, 12, 16):
        raise ValueError('Выберите ограничение 0+, 6+, 12+ или 16+.')
    _validate_pin(pin)
    salt = secrets.token_hex(16)
    digest = _hash(pin, salt)
    with database.transaction():
        _ensure(user_id)
        if preferences(user_id)['parental']:
            raise ValueError('Родительский режим уже включён. Сначала введите текущий PIN.')
        database.execute('''UPDATE AgePreferences SET max_age=?,pin_salt=?,pin_hash=?,attempts=0,
            locked_until=0 WHERE user_id=?''', (limit, salt, digest, user_id))


def change_parental(user_id, pin, limit=None):
    """Verify and mutate atomically; lockout survives /start and process restarts."""
    if limit is not None and (type(limit) is not int or limit not in (0, 6, 12, 16)):
        raise ValueError('Недопустимое возрастное ограничение.')
    now = time.time()
    with database.transaction():
        row = database.execute('SELECT pin_salt,pin_hash,attempts,locked_until FROM AgePreferences WHERE user_id=?',
                               (user_id,)).fetchone()
        if not row or not row[1]:
            return False, 'Родительский режим уже отключён. Откройте настройки заново.'
        if row[3] > now:
            return False, 'Слишком много попыток. Повторите через 15 минут.'
        correct = bool(isinstance(pin, str) and re.fullmatch(r'[0-9]{6}', pin)
                       and hmac.compare_digest(_hash(pin, row[0]), row[1]))
        if not correct:
            attempts = (row[2] if row[3] == 0 else 0) + 1
            locked = now + 900 if attempts >= 5 else 0
            database.execute('UPDATE AgePreferences SET attempts=?,locked_until=? WHERE user_id=?',
                             (attempts, locked, user_id))
            return False, ('Слишком много попыток. Повторите через 15 минут.' if locked else 'Неверный PIN.')
        if limit is None:
            database.execute('''UPDATE AgePreferences SET max_age=16,pin_salt=NULL,pin_hash=NULL,
                attempts=0,locked_until=0 WHERE user_id=?''', (user_id,))
        else:
            database.execute('UPDATE AgePreferences SET max_age=?,attempts=0,locked_until=0 WHERE user_id=?',
                             (limit, user_id))
        return True, 'Настройки сохранены.'
