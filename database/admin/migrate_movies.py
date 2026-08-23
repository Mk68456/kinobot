import sqlite3
from loader import database, cursor

def migrate_movies_schema():
    """Добавляем поля для карточек к фильмам"""
    
    # Проверяем существование таблицы Movies перед модификацией
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='Movies'")
    if not cursor.fetchone():
        print("❌ Таблица Movies не найдена")
        return
    
    # Создаем колонку с изображением постера (картинки)
    try:
        cursor.execute('''ALTER TABLE Movies ADD COLUMN poster_image TEXT''')
        print("✅ Колонка poster_image создана")
    except sqlite3.OperationalError as e:
        if "duplicate column name" in str(e).lower():
            print("ℹ️ Колонка poster_image уже существует")
        else:
            raise
    
    # Создаем колонку с описанием карточки (HTML контент)
    try:
        cursor.execute('''ALTER TABLE Movies ADD COLUMN card_description TEXT''')
        print("✅ Колонка card_description создана")
    except sqlite3.OperationalError as e:
        if "duplicate column name" in str(e).lower():
            print("ℹ️ Колонка card_description уже существует")
        else:
            raise
    
    # Создаем колонку с типом оформления (для разных стилей в будущем)
    try:
        cursor.execute('''ALTER TABLE Movies ADD COLUMN card_style TEXT DEFAULT 'simple' ''')
        print("✅ Колонка card_style создана")
    except sqlite3.OperationalError as e:
        if "duplicate column name" in str(e).lower():
            print("ℹ️ Колонка card_style уже существует")
        else:
            raise

    # Создаем колонку с прикреплённым файлом фильма (file_id в Telegram)
    try:
        cursor.execute('''ALTER TABLE Movies ADD COLUMN movie_file TEXT''')
        print("✅ Колонка movie_file создана")
    except sqlite3.OperationalError as e:
        if "duplicate column name" in str(e).lower():
            print("ℹ️ Колонка movie_file уже существует")
        else:
            raise

    # Тип прикреплённого файла (document / video), чтобы знать каким методом отправлять
    try:
        cursor.execute('''ALTER TABLE Movies ADD COLUMN movie_file_type TEXT''')
        print("✅ Колонка movie_file_type создана")
    except sqlite3.OperationalError as e:
        if "duplicate column name" in str(e).lower():
            print("ℹ️ Колонка movie_file_type уже существует")
        else:
            raise

    database.commit()
    print("\nМиграция выполнена успешно!")

def check_schema():
    """Проверяем, есть ли новые колонки и возвращаем статус таблицы"""
    cursor.execute("PRAGMA table_info(Movies)")
    columns = {row[1]: row for row in cursor.fetchall()}
    
    has_poster = 'poster_image' in columns
    has_description = 'card_description' in columns
    has_style = 'card_style' in columns
    has_file = 'movie_file' in columns
    has_file_type = 'movie_file_type' in columns
    
    print(f"\nСтатус таблицы Movies:")
    print(f"  poster_image: {has_poster}")
    print(f"  card_description: {has_description}")
    print(f"  card_style: {has_style}")
    print(f"  movie_file: {has_file}")
    print(f"  movie_file_type: {has_file_type}")
    
    if not all([has_poster, has_description, has_style, has_file, has_file_type]):
        migrate_movies_schema()
    else:
        print("\n✅ Все необходимые колонки уже существуют")

if __name__ == "__main__":
    check_schema()