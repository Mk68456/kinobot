"""Numbered, transactional migrations for existing and empty databases."""

import logging
import sqlite3
from contextlib import closing
from datetime import datetime, timezone

from database.connection import database

logger = logging.getLogger(__name__)


def _initial_schema():
    database.execute("CREATE TABLE IF NOT EXISTS Users (id INTEGER PRIMARY KEY)")
    database.execute("CREATE TABLE IF NOT EXISTS Movies (movie_title TEXT, movie_number INTEGER PRIMARY KEY)")
    database.execute("CREATE TABLE IF NOT EXISTS channels_info (title TEXT, link TEXT, cod INTEGER)")
    from database.admin.migrate_movies import check_schema
    from database.admin.migrate_users import check_users_schema
    from database.categories import create_categories_tables
    from database.stats import create_stats_tables
    from database.torrents import create_torrents_table

    check_schema()
    check_users_schema()
    create_categories_tables()
    create_stats_tables()
    create_torrents_table()


def _roles_and_jobs():
    database.execute("""CREATE TABLE Roles (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL UNIQUE,
        bypass_subscription INTEGER NOT NULL DEFAULT 0 CHECK (bypass_subscription IN (0,1)))""")
    database.execute("""CREATE TABLE UserRoles (
        user_id INTEGER PRIMARY KEY,
        role_id INTEGER NOT NULL REFERENCES Roles(id) ON DELETE CASCADE)""")
    columns = {row[1] for row in database.execute("PRAGMA table_info(Users)")}
    if "is_active" not in columns:
        database.execute("ALTER TABLE Users ADD COLUMN is_active INTEGER NOT NULL DEFAULT 1")
    database.execute("""CREATE TABLE BroadcastJobs (
        id INTEGER PRIMARY KEY AUTOINCREMENT, source_chat_id INTEGER NOT NULL,
        source_message_id INTEGER NOT NULL, admin_id INTEGER NOT NULL,
        status TEXT NOT NULL DEFAULT 'pending', created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)""")
    database.execute("""CREATE TABLE BroadcastRecipients (
        job_id INTEGER NOT NULL REFERENCES BroadcastJobs(id) ON DELETE CASCADE,
        user_id INTEGER NOT NULL, status TEXT NOT NULL DEFAULT 'pending', error TEXT,
        PRIMARY KEY(job_id,user_id))""")
    database.execute(
        "CREATE TABLE MovieSequence (id INTEGER PRIMARY KEY CHECK(id=1), value INTEGER NOT NULL)"
    )
    database.execute(
        "INSERT INTO MovieSequence VALUES (1,(SELECT COALESCE(MAX(movie_number),0) FROM Movies))"
    )
    for table, columns in (
        ("Movies", "movie_number"),
        ("Users", "id"),
        ("MovieCategories", "movie_number"),
        ("MovieSubcategories", "category_id"),
        ("MovieTorrents", "movie_number"),
        ("SearchEvents", "created_at"),
        ("WatchEvents", "created_at"),
        ("SearchEvents", "user_id,created_at"),
        ("WatchEvents", "user_id,created_at"),
    ):
        name = f"idx_{table}_{columns.replace(',', '_')}"
        database.execute(f"CREATE INDEX IF NOT EXISTS {name} ON {table}({columns})")


def _watch_progress():
    database.execute("""CREATE TABLE EpisodeProgress (
        user_id INTEGER NOT NULL,
        episode_id INTEGER NOT NULL REFERENCES MovieSubcategories(id) ON DELETE CASCADE,
        watched_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY(user_id,episode_id))""")
    database.execute("""CREATE TABLE FilmProgress (
        user_id INTEGER NOT NULL, movie_number INTEGER NOT NULL,
        watched_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY(user_id,movie_number))""")
    database.execute("CREATE INDEX idx_episode_progress_episode ON EpisodeProgress(episode_id)")
    database.execute("CREATE INDEX idx_film_progress_movie ON FilmProgress(movie_number)")
    database.execute("""CREATE TRIGGER delete_film_progress AFTER DELETE ON Movies
        BEGIN DELETE FROM FilmProgress WHERE movie_number=OLD.movie_number; END""")


def _web_panel():
    database.execute("""CREATE TABLE WebLoginTokens (
        token_hash TEXT PRIMARY KEY, admin_id INTEGER NOT NULL, expires_at REAL NOT NULL)""")
    database.execute("""CREATE TABLE WebSessions (
        token_hash TEXT PRIMARY KEY, admin_id INTEGER NOT NULL, csrf TEXT NOT NULL, expires_at REAL NOT NULL)""")
    database.execute("""CREATE TABLE WebAssets (
        id TEXT PRIMARY KEY, admin_id INTEGER NOT NULL, source TEXT NOT NULL,
        filename TEXT NOT NULL, size INTEGER NOT NULL, file_type TEXT NOT NULL,
        file_id TEXT, status TEXT NOT NULL, received INTEGER NOT NULL DEFAULT 0,
        first_hash TEXT, error TEXT, source_chat_id INTEGER, source_message_id INTEGER,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(source_chat_id,source_message_id))""")
    database.execute("CREATE INDEX idx_web_assets_admin ON WebAssets(admin_id,created_at)")
    database.execute("""CREATE TABLE WebDrafts (
        id TEXT PRIMARY KEY, admin_id INTEGER NOT NULL, body TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'draft', movie_number INTEGER,
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)""")
    database.execute("CREATE INDEX idx_web_drafts_admin ON WebDrafts(admin_id,updated_at)")


def _welcome_design():
    database.execute("CREATE TABLE BotDesign (key TEXT PRIMARY KEY, value TEXT NOT NULL)")


MIGRATIONS = (_initial_schema, _roles_and_jobs, _watch_progress, _web_panel, _welcome_design)


def migrate(backup_dir=None):
    version = database.execute("PRAGMA user_version").fetchone()[0]
    if version > len(MIGRATIONS):
        raise RuntimeError("Database schema is newer than this application")
    existing = database.execute("SELECT 1 FROM sqlite_master WHERE type='table' LIMIT 1").fetchone()
    if version < len(MIGRATIONS) and existing and backup_dir is not None:
        backup_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        path = backup_dir / f"before-migration-{stamp}.sqlite"
        with closing(sqlite3.connect(path)) as target:
            database.connection.backup(target)
        logger.info("Database backup: %s", path)
    for index in range(version, len(MIGRATIONS)):
        with database.transaction():
            MIGRATIONS[index]()
            database.execute(f"PRAGMA user_version={index + 1}")
        logger.info("Applied database migration %s", index + 1)
