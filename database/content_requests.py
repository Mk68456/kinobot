import re
import json
from database.connection import database

STATUSES = {"new": "Новая", "planned": "В работе", "added": "Добавлен", "rejected": "Отклонён"}
FIELDS = ("id", "title", "content_type", "season", "status", "movie_number",
          "notification_job", "tmdb_id", "year", "overview", "poster_url", "draft_id",
          "created_at", "updated_at", "episode", "voice", "verification", "expected_episodes", "voice_evidence")


def identity(title, kind, season=0, tmdb_id=None, episode=0, voice=""):
    suffix = f":episode:{episode}:voice:{normalize_voice(voice)}" if episode or voice else ""
    return f"{kind}:{season}:" + (
        f"tmdb:{int(tmdb_id)}" if tmdb_id else re.sub(r"\W+", " ", title.casefold()).strip()
    ) + suffix


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


def find(title, kind, season=0, tmdb_id=None, episode=0, voice=""):
    row = database.execute(
        "SELECT id FROM ContentRequests WHERE identity=?", (identity(title, kind, season, tmdb_id, episode, voice),)
    ).fetchone()
    if row:
        return get(row[0])
    normalized = identity(title, kind, season)
    for request_id, saved_title in database.execute(
        "SELECT id,title FROM ContentRequests WHERE content_type=? AND season=? AND episode=? AND lower(voice)=lower(?)", (kind, season, episode, normalize_voice(voice))
    ).fetchall():
        if identity(saved_title, kind, season) == normalized:
            return get(request_id)
    return None


def vote(user_id, title, kind, season=0, tmdb_id=None, year=None, overview=None, poster_url=None,
         episode=0, voice="", verification="", expected_episodes="", voice_evidence=None):
    voice = normalize_voice(voice)
    if (
        kind not in ("movie", "series")
        or not title.strip()
        or len(title) > 200
        or type(season) is not int
        or not 0 <= season <= 999
        or type(episode) is not int or not 0 <= episode <= 9999
        or (episode and not season) or len(voice) > 80
        or (kind == "movie" and (season or episode))
    ):
        raise ValueError("Проверьте название, тип и номер сезона.")
    with database.transaction():
        request = find(title, kind, season, tmdb_id, episode, voice)
        if not request:
            database.execute(
                """INSERT INTO ContentRequests
                (identity,title,content_type,season,tmdb_id,year,overview,poster_url,episode,voice,verification,expected_episodes,created_at,updated_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)""",
                (identity(title, kind, season, tmdb_id, episode, voice), title, kind, season, tmdb_id,
                 str(year or '')[:4] or None, str(overview or '')[:2500], str(poster_url or '')[:1000], episode, voice, verification, expected_episodes),
            )
            request = find(title, kind, season, tmdb_id, episode, voice)
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
        if verification:
            database.execute("UPDATE ContentRequests SET verification=?,expected_episodes=? WHERE id=?",
                             (verification, expected_episodes, request["id"]))
        if voice and voice_evidence:
            evidence = json.loads(request.get("voice_evidence") or "[]")
            for proof in voice_evidence[:10]:
                if isinstance(proof, dict) and all(isinstance(proof.get(k), str) for k in
                                                  ("source", "translation_id", "checked_at")):
                    proof = {key: proof[key][:80] for key in ("source", "translation_id", "checked_at")}
                    evidence = [p for p in evidence if (p['source'], p['translation_id']) !=
                                (proof['source'], proof['translation_id'])]
                    evidence.append(proof)
            database.execute("UPDATE ContentRequests SET voice_evidence=? WHERE id=?",
                             (json.dumps(evidence[-10:], ensure_ascii=False), request["id"]))
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
    items = [{**dict(zip(FIELDS, row[:-1])), "votes": row[-1]} for row in rows]
    for item in items:
        item['voice_evidence_note'] = voice_evidence_label(item)
    return {"items": items,
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
        if not target_available(request, number, manual=True):
            raise ValueError("Запрошенные серии или озвучка ещё не найдены в каталоге.")
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


def normalize_voice(voice):
    return " ".join(str(voice or "").casefold().split())


def voice_evidence_label(request):
    evidence = json.loads(request.get('voice_evidence') or '[]')
    if not evidence:
        return ""
    sources = ', '.join(sorted({proof['source'] for proof in evidence}))
    checked = max(proof['checked_at'] for proof in evidence)
    return (f"Озвучка найдена у произведения · Kinobox: {sources} · проверено {checked}. "
            "Наличие нужных серий в этой озвучке требует отдельной проверки.")


def target_label(request):
    return ((f" · сезон {request['season']}" if request.get('season') else "")
            + (f" · серия {request['episode']}" if request.get('episode') else "")
            + (f" · озвучка: {request['voice']}" if request.get('voice') else ""))


def target_available(request, number, manual=False):
    from database.categories import episode_order
    from database.episode_variants import choices
    season, episode, voice = request.get('season', 0), request.get('episode', 0), request.get('voice', '')
    if not season:
        # A series card alone cannot prove that all seasons or a voice are present.
        if request['content_type'] == 'series':
            expected = request.get('expected_episodes', '')
            if not expected:
                if not manual:
                    return False
                if not voice:
                    return True
                seasons = database.execute('SELECT season_number FROM MovieCategories WHERE movie_number=?',
                                           (number,)).fetchall()
                return bool(seasons) and all(sn and target_available(
                    {**request, 'season': sn}, number, manual=True) for (sn,) in seasons)
            targets = {}
            for value in expected.split(','):
                sn, en = value.split(':')
                targets.setdefault(int(sn), []).append(en)
            return all(target_available({**request, 'season': sn, 'expected_episodes': ','.join(eps)}, number)
                       for sn, eps in targets.items())
        if not voice:
            return True
        return any(normalize_voice(row[0]) == normalize_voice(voice) for row in database.execute(
            """SELECT c.name FROM MovieCategories c JOIN MovieSubcategories s ON s.category_id=c.id
               WHERE c.movie_number=? AND COALESCE(s.file_id,'')!=''""", (number,)).fetchall())
    rows = database.execute("""SELECT s.id,s.name FROM MovieSubcategories s
        JOIN MovieCategories c ON c.id=s.category_id WHERE c.movie_number=? AND c.season_number=?""",
        (number, season)).fetchall()
    available = set()
    for row in rows:
        order = episode_order(row)
        if order[0] == 0 and (not voice or any(normalize_voice(c.voice) == normalize_voice(voice)
                                              for c in choices(row[0]) if c.file_id)):
            if not voice and not choices(row[0]):
                continue
            available.add(order[1])
    expected = {episode} if episode else {int(n) for n in request.get('expected_episodes', '').split(',') if n}
    if expected:
        return expected <= available
    numbered = {episode_order(row)[1] for row in rows if episode_order(row)[0] == 0}
    return manual and bool(numbered) and numbered <= available
