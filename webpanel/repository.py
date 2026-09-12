import json
import uuid
from html import escape
from urllib.parse import urlsplit
from database.connection import database
from database.movies import add_new_movie
from database.categories import add_movie_category, add_movie_subcategory

ASSET_FIELDS = (
    "id,admin_id,source,filename,size,file_type,file_id,status,received,first_hash,error,created_at"
)


def asset(asset_id, admin_id):
    result = database.execute(
        f"SELECT {ASSET_FIELDS} FROM WebAssets WHERE id=? AND admin_id=?", (asset_id, admin_id)
    ).fetchone()
    return dict(zip(ASSET_FIELDS.split(","), result)) if result else None


def assets(admin_id):
    return [
        dict(zip(ASSET_FIELDS.split(","), row))
        for row in database.execute(
            f"SELECT {ASSET_FIELDS} FROM WebAssets WHERE admin_id=? ORDER BY created_at DESC,id DESC LIMIT 500",
            (admin_id,),
        )
    ]


def receive_telegram(admin_id, message):
    media = message.video or message.document
    if not media:
        raise ValueError("Нужно видео или документ.")
    file_type = "video" if message.video else "document"
    name = media.file_name or message.caption or f"Видео {message.message_id}.mp4"
    asset_id = uuid.uuid4().hex
    with database.transaction():
        database.execute(
            """INSERT OR IGNORE INTO WebAssets
            (id,admin_id,source,filename,size,file_type,file_id,status,source_chat_id,source_message_id)
            VALUES (?,?,'telegram',?,?,?,?,'ready',?,?)""",
            (
                asset_id,
                admin_id,
                str(name)[:240],
                media.file_size or 0,
                file_type,
                media.file_id,
                message.chat.id,
                message.message_id,
            ),
        )
    return database.execute(
        "SELECT id FROM WebAssets WHERE source_chat_id=? AND source_message_id=?",
        (message.chat.id, message.message_id),
    ).fetchone()[0]


def draft(draft_id, admin_id):
    row = database.execute(
        "SELECT id,body,status,movie_number FROM WebDrafts WHERE id=? AND admin_id=?", (draft_id, admin_id)
    ).fetchone()
    return (
        {"id": row[0], "body": json.loads(row[1]), "status": row[2], "movie_number": row[3]} if row else None
    )


def drafts(admin_id):
    return [
        {"id": row[0], "body": json.loads(row[1]), "status": row[2], "movie_number": row[3]}
        for row in database.execute(
            "SELECT id,body,status,movie_number FROM WebDrafts WHERE admin_id=? ORDER BY updated_at DESC,id DESC LIMIT 100",
            (admin_id,),
        )
    ]


def normalize_draft(body):
    if not isinstance(body, dict):
        raise ValueError("Некорректный черновик.")
    result = {
        key: str(body.get(key) or "").strip() for key in ("title", "description", "poster_url", "season_name")
    }
    for key, limit in [("title", 200), ("description", 2500), ("poster_url", 1000), ("season_name", 120)]:
        if len(result[key]) > limit:
            raise ValueError(f"Слишком длинное поле: {key}.")
    if result["poster_url"]:
        url = urlsplit(result["poster_url"])
        if url.scheme != "https" or not url.hostname or url.username or url.password:
            raise ValueError("Постер должен быть HTTPS-ссылкой.")
    if body.get("content_type") not in ("movie", "series"):
        raise ValueError("Выберите фильм или сериал.")
    result["content_type"] = body["content_type"]
    for key in ("movie_number", "category_id", "season_number"):
        value = body.get(key)
        if value in ("", None):
            result[key] = None
        elif type(value) is int and 0 < value < 2**53:
            result[key] = value
        else:
            raise ValueError("Некорректный номер фильма, сезона или категории.")
    items = body.get("items", [])
    if not isinstance(items, list) or len(items) > 200:
        raise ValueError("В черновике может быть до 200 файлов.")
    result["items"] = []
    seen = set()
    for item in items:
        if not isinstance(item, dict):
            raise ValueError("Некорректный файл.")
        asset_id = str(item.get("asset_id", ""))
        name = str(item.get("name", "")).strip()
        if len(asset_id) != 32 or asset_id in seen or not 1 <= len(name) <= 120:
            raise ValueError("Проверьте названия файлов и повторы в списке.")
        seen.add(asset_id)
        result["items"].append({"asset_id": asset_id, "name": name})
    return result


def save_draft(admin_id, body, draft_id=None):
    body = normalize_draft(body)
    for item in body["items"]:
        if not asset(item["asset_id"], admin_id):
            raise ValueError("Файл не найден в вашей библиотеке.")
    with database.transaction():
        if draft_id:
            old = draft(draft_id, admin_id)
            if not old or old["status"] != "draft":
                raise ValueError("Черновик уже опубликован или недоступен.")
            database.execute(
                "UPDATE WebDrafts SET body=?,updated_at=CURRENT_TIMESTAMP WHERE id=?",
                (json.dumps(body, ensure_ascii=False), draft_id),
            )
        else:
            draft_id = uuid.uuid4().hex
            database.execute(
                "INSERT INTO WebDrafts(id,admin_id,body) VALUES (?,?,?)",
                (draft_id, admin_id, json.dumps(body, ensure_ascii=False)),
            )
    return draft(draft_id, admin_id)


def publish(draft_id, admin_id):
    with database.transaction():
        current = draft(draft_id, admin_id)
        if not current:
            raise ValueError("Черновик не найден.")
        if current["status"] == "published":
            return current["movie_number"]
        body = normalize_draft(current["body"])
        items = body["items"]
        if not items:
            raise ValueError("Добавьте хотя бы один файл.")
        files = [asset(item["asset_id"], admin_id) for item in items]
        if any(not file or file["status"] != "ready" or not file["file_id"] for file in files):
            raise ValueError("Дождитесь загрузки всех файлов в Telegram.")
        movie_number = body["movie_number"]
        if movie_number:
            movie = database.execute(
                "SELECT content_type FROM Movies WHERE movie_number=?", (movie_number,)
            ).fetchone()
            if not movie or movie[0] != body["content_type"]:
                raise ValueError("Выбранный фильм или сериал недоступен.")
        else:
            if not body["title"]:
                raise ValueError("Введите название фильма или сериала.")
            movie_number = add_new_movie(
                body["title"],
                poster_image=body["poster_url"],
                card_description=escape(body["description"]),
                content_type=body["content_type"],
            )
        if body["content_type"] == "movie":
            if len(items) != 1:
                raise ValueError(
                    "У фильма должен быть один основной файл. Для другого фильма создайте новый черновик."
                )
            old_file = database.execute(
                "SELECT movie_file FROM Movies WHERE movie_number=?", (movie_number,)
            ).fetchone()[0]
            if old_file:
                raise ValueError("У фильма уже есть файл. Заменить его можно через Telegram-админку.")
            database.execute(
                "UPDATE Movies SET movie_file=?,movie_file_type=? WHERE movie_number=?",
                (files[0]["file_id"], files[0]["file_type"], movie_number),
            )
        else:
            category_id = body["category_id"]
            if category_id:
                if not database.execute(
                    "SELECT 1 FROM MovieCategories WHERE id=? AND movie_number=?", (category_id, movie_number)
                ).fetchone():
                    raise ValueError("Сезон не принадлежит выбранному сериалу.")
            else:
                name = body["season_name"] or (
                    f"Сезон {body['season_number']}" if body["season_number"] else ""
                )
                if not name:
                    raise ValueError("Введите название или номер сезона.")
                if database.execute(
                    "SELECT 1 FROM MovieCategories WHERE movie_number=? AND (name=? OR season_number=?)",
                    (movie_number, name, body["season_number"]),
                ).fetchone():
                    raise ValueError("Этот сезон уже существует — выберите его в списке.")
                category_id = add_movie_category(movie_number, name, body["season_number"])
            names = {
                row[0].casefold()
                for row in database.execute(
                    "SELECT name FROM MovieSubcategories WHERE category_id=?", (category_id,)
                )
            }
            for item, file in zip(items, files):
                if item["name"].casefold() in names:
                    raise ValueError(
                        f"Серия «{item['name']}» уже есть. Измените название или уберите повтор."
                    )
                names.add(item["name"].casefold())
                add_movie_subcategory(category_id, item["name"], file["file_id"], file["file_type"])
        database.execute(
            "UPDATE WebDrafts SET status='published',movie_number=? WHERE id=?", (movie_number, draft_id)
        )
        return movie_number
