import re
from database.connection import database

STATUSES = {"new": "Новая", "planned": "В работе", "added": "Добавлен", "rejected": "Отклонён"}
FIELDS = ("id", "title", "content_type", "season", "status", "movie_number",
          "notification_job", "tmdb_id", "year", "overview", "poster_url", "draft_id",
          "created_at", "updated_at")


def identity(title, kind, season=0, tmdb_id=None):
    return f"{kind}:{season}:" + (
        f"tmdb:{int(tmdb_id)}" if tmdb_id else re.sub(r"\W+", " ", title.casefold()).strip()
    )


def get(request_id):
    row = database.execute(
        f"SELECT {','.join(FIELDS)} FROM ContentRequests WHERE id=?",
        (request_id,),
    ).fetchone()
    return (
        dict(zip(FIELDS, row))
        if row
        else None
    )


def find(title, kind, season=0, tmdb_id=None):
    row = database.execute(
        "SELECT id FROM ContentRequests WHERE identity=?", (identity(title, kind, season, tmdb_id),)
    ).fetchone()
    if row:
        return get(row[0])
    normalized = identity(title, kind, season)
    for request_id, saved_title in database.execute(
        "SELECT id,title FROM ContentRequests WHERE content_type=? AND season=?", (kind, season)
    ).fetchall():
        if identity(saved_title, kind, season) == normalized:
            return get(request_id)
    return None


def vote(user_id, title, kind, season=0, tmdb_id=None, year=None, overview=None, poster_url=None):
    if (
        kind not in ("movie", "series")
        or not title.strip()
        or len(title) > 200
        or type(season) is not int
        or not 0 <= season <= 999
        or (kind == "movie" and season)
    ):
        raise ValueError("Проверьте название, тип и номер сезона.")
    with database.transaction():
        request = find(title, kind, season, tmdb_id)
        if not request:
            database.execute(
                """INSERT INTO ContentRequests
                (identity,title,content_type,season,tmdb_id,year,overview,poster_url,created_at,updated_at)
                VALUES (?,?,?,?,?,?,?,?,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)""",
                (identity(title, kind, season, tmdb_id), title, kind, season, tmdb_id,
                 str(year or '')[:4] or None, str(overview or '')[:2500], str(poster_url or '')[:1000]),
            )
            request = find(title, kind, season, tmdb_id)
        elif any((tmdb_id, year, overview, poster_url)):
            database.execute(
                """UPDATE ContentRequests SET
                tmdb_id=COALESCE(tmdb_id,?),year=COALESCE(year,?),
                overview=CASE WHEN overview='' THEN ? ELSE overview END,
                poster_url=CASE WHEN poster_url='' THEN ? ELSE poster_url END,
                updated_at=CURRENT_TIMESTAMP WHERE id=?""",
                (tmdb_id, str(year or '')[:4] or None, str(overview or '')[:2500],
                 str(poster_url or '')[:1000], request["id"]),
            )
        database.execute("INSERT OR IGNORE INTO ContentRequestVotes VALUES (?,?)", (request["id"], user_id))
    return get(request["id"])


def page(offset=0):
    return database.execute(
        "SELECT r.id,r.title,r.content_type,r.season,r.status,count(v.user_id) FROM ContentRequests r LEFT JOIN ContentRequestVotes v ON v.request_id=r.id GROUP BY r.id ORDER BY CASE r.status WHEN 'new' THEN 0 WHEN 'planned' THEN 1 ELSE 2 END,count(v.user_id) DESC,r.id LIMIT 11 OFFSET ?",
        (offset,),
    ).fetchall()


def set_status(request_id, status):
    if status not in ("new", "planned", "rejected"):
        raise ValueError("Недопустимый статус.")
    with database.transaction():
        database.execute(
            "UPDATE ContentRequests SET status=?,updated_at=CURRENT_TIMESTAMP WHERE id=? AND status!='added'", (status, request_id)
        )


def web_page(offset=0, limit=50):
    offset = max(0, int(offset))
    limit = max(1, min(int(limit), 100))
    total = database.execute("SELECT count(*) FROM ContentRequests").fetchone()[0]
    rows = database.execute(
        f"""SELECT {','.join('r.' + f for f in FIELDS)},count(v.user_id)
        FROM ContentRequests r LEFT JOIN ContentRequestVotes v ON v.request_id=r.id
        GROUP BY r.id ORDER BY CASE r.status WHEN 'new' THEN 0 WHEN 'planned' THEN 1
        WHEN 'rejected' THEN 2 ELSE 3 END,count(v.user_id) DESC,r.updated_at DESC
        LIMIT ? OFFSET ?""", (limit, offset)
    ).fetchall()
    return {"items": [{**dict(zip(FIELDS, row[:-1])), "votes": row[-1]} for row in rows],
            "total": total, "offset": offset, "limit": limit}


def link_draft(request_id, draft_id):
    with database.transaction():
        request = get(request_id)
        if not request or request["status"] == "added":
            raise ValueError("Заявка недоступна.")
        database.execute(
            "UPDATE ContentRequests SET draft_id=?,status='planned',updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (draft_id, request_id),
        )
    return get(request_id)


def by_draft(draft_id):
    row = database.execute("SELECT id FROM ContentRequests WHERE draft_id=?", (draft_id,)).fetchone()
    return get(row[0]) if row else None


def complete(request_id, number, admin_id, chat_id, message_id):
    from database.search import get_movie_from_numb

    with database.transaction():
        request = get(request_id)
        movie = get_movie_from_numb(number)
        if not request or not movie or movie["content_type"] != request["content_type"]:
            raise ValueError("Код не найден или тип произведения не совпадает.")
        if (
            request["season"]
            and not database.execute(
                "SELECT 1 FROM MovieCategories WHERE movie_number=? AND season_number=?",
                (number, request["season"]),
            ).fetchone()
        ):
            raise ValueError("У сериала ещё нет запрошенного сезона.")
        if request["notification_job"]:
            return request["notification_job"]
        job = database.execute(
            "INSERT INTO BroadcastJobs(source_chat_id,source_message_id,admin_id) VALUES (?,?,?)",
            (chat_id, message_id, admin_id),
        ).lastrowid
        database.execute(
            "INSERT INTO BroadcastRecipients(job_id,user_id) SELECT ?,user_id FROM ContentRequestVotes WHERE request_id=?",
            (job, request_id),
        )
        database.execute(
            "UPDATE ContentRequests SET status='added',movie_number=?,notification_job=?,updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (number, job, request_id),
        )
        return job
