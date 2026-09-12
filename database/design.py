from database.connection import database

DEFAULT_TEXT = (
    "🍿 KinoTime — сегодня хороший вечер для кино\n\n"
    "Фильм на вечер или ещё одна серия? Выбирай свою историю — всё в одном боте.\n\n"
    "🎬 Находи фильмы и сериалы в каталоге.\n"
    "📺 Открывай сезоны и выбирай нужную серию.\n"
    "✅ Отмечай просмотренное, чтобы помнить, где остановился.\n\n"
    "С чего начнём? 👇"
)
SUBSCRIPTION_NOTE = "\n\nЧтобы открыть каталог, подпишись на каналы ниже и нажми «Я подписался»."


def get_design():
    values = dict(database.execute("SELECT key,value FROM BotDesign").fetchall())
    return {"text": values.get("welcome_text", DEFAULT_TEXT), "photo": values.get("welcome_photo", "")}


def set_design(key, value):
    if key not in ("welcome_text", "welcome_photo"):
        raise ValueError("Неизвестная настройка")
    if key == "welcome_text" and (not value.strip() or len(value.encode("utf-16-le")) // 2 > 800):
        raise ValueError("Введите текст длиной до 800 символов (эмодзи могут занимать два символа).")
    with database.transaction():
        database.execute(
            "INSERT INTO BotDesign(key,value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )


def reset_text():
    with database.transaction():
        database.execute("DELETE FROM BotDesign WHERE key='welcome_text'")
