import hashlib
import secrets
import time
from database.connection import database
from services.access import is_admin


def digest(token):
    return hashlib.sha256(token.encode()).hexdigest()


def create_login(admin_id):
    if not is_admin(admin_id):
        raise ValueError("Нет доступа.")
    token = secrets.token_urlsafe(32)
    with database.transaction():
        database.execute(
            "DELETE FROM WebLoginTokens WHERE expires_at<? OR admin_id=?", (time.time(), admin_id)
        )
        database.execute("DELETE FROM WebSessions WHERE expires_at<?", (time.time(),))
        database.execute(
            "INSERT INTO WebLoginTokens VALUES (?,?,?)", (digest(token), admin_id, time.time() + 600)
        )
    return token


def exchange_login(token):
    with database.transaction():
        row = database.execute(
            "SELECT admin_id,expires_at FROM WebLoginTokens WHERE token_hash=?", (digest(token),)
        ).fetchone()
        if not row or row[1] < time.time() or not is_admin(row[0]):
            raise ValueError("Ссылка истекла или уже использована. Отправьте боту /web.")
        database.execute("DELETE FROM WebLoginTokens WHERE token_hash=?", (digest(token),))
        session = secrets.token_urlsafe(32)
        csrf = secrets.token_urlsafe(32)
        database.execute(
            "INSERT INTO WebSessions VALUES (?,?,?,?)", (digest(session), row[0], csrf, time.time() + 43200)
        )
        return session


def get_session(token):
    row = database.execute(
        "SELECT admin_id,csrf,expires_at FROM WebSessions WHERE token_hash=?", (digest(token),)
    ).fetchone()
    if not row or row[2] < time.time() or not is_admin(row[0]):
        return None
    return {"admin_id": row[0], "csrf": row[1]}


def logout(token):
    with database.transaction():
        database.execute("DELETE FROM WebSessions WHERE token_hash=?", (digest(token),))
