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


def _local_imports():
    database.execute("ALTER TABLE WebAssets ADD COLUMN source_path TEXT")
    database.execute("ALTER TABLE WebAssets ADD COLUMN source_mtime TEXT")
    database.execute("ALTER TABLE WebDrafts ADD COLUMN import_key TEXT")
    database.execute("ALTER TABLE WebDrafts ADD COLUMN review_history TEXT NOT NULL DEFAULT '[]'")
    database.execute("CREATE UNIQUE INDEX idx_import_key ON WebDrafts(admin_id,import_key)")
    database.execute("""CREATE TABLE DesktopSessions (
        token_hash TEXT PRIMARY KEY, admin_id INTEGER NOT NULL, expires_at REAL NOT NULL)""")


def _publication_package():
    database.execute('''CREATE TABLE PublicationPackage (
        draft_id TEXT PRIMARY KEY REFERENCES WebDrafts(id) ON DELETE CASCADE,
        admin_id INTEGER NOT NULL, review_token TEXT NOT NULL,
        added_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)''')


def _movie_sharing():
    database.execute("CREATE TABLE MovieShareLinks (token TEXT PRIMARY KEY, movie_number INTEGER UNIQUE NOT NULL)")
    database.execute("CREATE TABLE MovieShareAccess (user_id INTEGER NOT NULL, movie_number INTEGER NOT NULL, PRIMARY KEY(user_id,movie_number))")


def _content_requests():
    database.execute("""CREATE TABLE ContentRequests (
        id INTEGER PRIMARY KEY, identity TEXT UNIQUE NOT NULL, title TEXT NOT NULL,
        content_type TEXT NOT NULL, season INTEGER NOT NULL DEFAULT 0,
        status TEXT NOT NULL DEFAULT 'new', movie_number INTEGER, notification_job INTEGER)""")
    database.execute("""CREATE TABLE ContentRequestVotes (
        request_id INTEGER NOT NULL REFERENCES ContentRequests(id), user_id INTEGER NOT NULL,
        PRIMARY KEY(request_id,user_id))""")


def _ratings():
    database.execute('ALTER TABLE Movies ADD COLUMN rating_kp REAL')
    database.execute('ALTER TABLE Movies ADD COLUMN rating_imdb REAL')
    database.execute('ALTER TABLE Movies ADD COLUMN age_rating TEXT')


def _rating_cache():
    database.execute('CREATE TABLE RatingCache (cache_key TEXT PRIMARY KEY, body TEXT NOT NULL, fetched_at TEXT NOT NULL)')
    database.execute('ALTER TABLE Movies ADD COLUMN rating_source_id INTEGER')
    database.execute('ALTER TABLE Movies ADD COLUMN ratings_fetched_at TEXT')


def _grouped_files():
    database.execute('CREATE TABLE GroupedFiles (asset_id TEXT PRIMARY KEY REFERENCES WebAssets(id), draft_id TEXT NOT NULL REFERENCES WebDrafts(id))')


def _launcher_remote_control():
    database.execute('''CREATE TABLE LauncherAgents (
        admin_id INTEGER PRIMARY KEY, instance_id TEXT NOT NULL, status TEXT NOT NULL,
        folders TEXT NOT NULL, last_seen TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)''')
    database.execute('''CREATE TABLE LauncherCommands (
        id INTEGER PRIMARY KEY AUTOINCREMENT, admin_id INTEGER NOT NULL,
        action TEXT NOT NULL, payload TEXT NOT NULL DEFAULT '{}', status TEXT NOT NULL DEFAULT 'pending',
        result TEXT, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)''')
    database.execute('CREATE INDEX idx_launcher_commands_admin ON LauncherCommands(admin_id,status,id)')


def _request_cards_and_drafts():
    database.execute('ALTER TABLE ContentRequests ADD COLUMN tmdb_id INTEGER')
    database.execute('ALTER TABLE ContentRequests ADD COLUMN year TEXT')
    database.execute("ALTER TABLE ContentRequests ADD COLUMN overview TEXT NOT NULL DEFAULT ''")
    database.execute("ALTER TABLE ContentRequests ADD COLUMN poster_url TEXT NOT NULL DEFAULT ''")
    database.execute('ALTER TABLE ContentRequests ADD COLUMN draft_id TEXT REFERENCES WebDrafts(id)')
    database.execute("ALTER TABLE ContentRequests ADD COLUMN created_at TEXT NOT NULL DEFAULT ''")
    database.execute("ALTER TABLE ContentRequests ADD COLUMN updated_at TEXT NOT NULL DEFAULT ''")
    database.execute("UPDATE ContentRequests SET created_at=CURRENT_TIMESTAMP,updated_at=CURRENT_TIMESTAMP")
    database.execute('CREATE UNIQUE INDEX idx_content_request_draft ON ContentRequests(draft_id) WHERE draft_id IS NOT NULL')


def _telegram_storage_backup():
    database.execute('''CREATE TABLE TelegramStorage (
        admin_id INTEGER PRIMARY KEY, chat_id INTEGER NOT NULL, title TEXT NOT NULL,
        enabled INTEGER NOT NULL DEFAULT 1 CHECK(enabled IN (0,1)),
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)''')
    database.execute('''CREATE TABLE TelegramStorageFiles (
        file_id TEXT PRIMARY KEY, file_type TEXT NOT NULL,
        storage_chat_id INTEGER, storage_message_id INTEGER,
        status TEXT NOT NULL DEFAULT 'pending', attempts INTEGER NOT NULL DEFAULT 0,
        error TEXT, updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)''')
    database.execute('CREATE INDEX idx_storage_files_status ON TelegramStorageFiles(status,updated_at)')


def _catalog_sections():
    database.execute('''CREATE TABLE CatalogSections (
        section_key TEXT PRIMARY KEY, title TEXT NOT NULL, emoji TEXT NOT NULL,
        required_content_type TEXT, enabled INTEGER NOT NULL DEFAULT 0 CHECK(enabled IN (0,1)),
        sort_order INTEGER NOT NULL)''')
    sections = [
        ('movies', 'Фильмы', '🎬', 'movie', 1, 10),
        ('series', 'Сериалы', '📺', 'series', 1, 20),
        ('cartoons', 'Мультфильмы', '🧸', 'movie', 0, 30),
        ('animated_series', 'Мультсериалы', '🎨', 'series', 0, 40),
        ('anime', 'Аниме', '⛩', None, 0, 50),
    ]
    for section in sections:
        database.execute('INSERT INTO CatalogSections VALUES (?,?,?,?,?,?)', section)
    database.execute("ALTER TABLE Movies ADD COLUMN catalog_section TEXT NOT NULL DEFAULT 'movies'")
    database.execute("UPDATE Movies SET catalog_section=CASE WHEN COALESCE(content_type,'movie')='series' THEN 'series' ELSE 'movies' END")
    database.execute('CREATE INDEX idx_movies_catalog_section ON Movies(catalog_section,movie_number)')


def _episode_qualities():
    database.execute("""CREATE TABLE EpisodeVariants (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        episode_id INTEGER NOT NULL REFERENCES MovieSubcategories(id) ON DELETE CASCADE,
        quality TEXT NOT NULL, voice TEXT NOT NULL DEFAULT '',
        file_id TEXT NOT NULL, file_type TEXT NOT NULL,
        UNIQUE(episode_id,quality,voice))""")


def _referral_contests():
    database.execute("""CREATE TABLE Contests (
        id INTEGER PRIMARY KEY AUTOINCREMENT, body TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'draft', revision INTEGER NOT NULL DEFAULT 1,
        created_by INTEGER NOT NULL, created_at REAL NOT NULL,
        closed_at REAL, draw_result TEXT, delivered_at REAL, delivery_note TEXT)""")
    database.execute("""CREATE TABLE ContestMembers (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        contest_id INTEGER NOT NULL REFERENCES Contests(id), user_id INTEGER NOT NULL,
        code TEXT NOT NULL UNIQUE, source TEXT NOT NULL, joined_at REAL NOT NULL,
        UNIQUE(contest_id,user_id))""")
    database.execute("""CREATE TABLE ContestReferrals (
        user_id INTEGER PRIMARY KEY, contest_id INTEGER NOT NULL REFERENCES Contests(id),
        inviter_id INTEGER NOT NULL, registered_at REAL NOT NULL,
        confirmed_at REAL, movie_number INTEGER, status TEXT NOT NULL DEFAULT 'pending',
        reason TEXT NOT NULL DEFAULT '')""")
    database.execute('CREATE INDEX idx_contest_referrals ON ContestReferrals(contest_id,inviter_id,status)')
    database.execute("""CREATE TABLE ContestSnapshot (
        contest_id INTEGER NOT NULL REFERENCES Contests(id), member_id INTEGER NOT NULL,
        user_id INTEGER NOT NULL, username TEXT, referrals INTEGER NOT NULL, tickets INTEGER NOT NULL,
        PRIMARY KEY(contest_id,user_id))""")
    database.execute("""CREATE TABLE ContestAudit (
        id INTEGER PRIMARY KEY AUTOINCREMENT, contest_id INTEGER NOT NULL REFERENCES Contests(id),
        admin_id INTEGER NOT NULL, action TEXT NOT NULL, detail TEXT NOT NULL, created_at REAL NOT NULL)""")
    database.execute("""CREATE TABLE ContestClaims (
        contest_id INTEGER PRIMARY KEY REFERENCES Contests(id), user_id INTEGER NOT NULL, claimed_at REAL NOT NULL)""")


MIGRATIONS = (_initial_schema, _roles_and_jobs, _watch_progress, _web_panel, _welcome_design, _local_imports,
              _publication_package, _movie_sharing, _content_requests, _ratings, _rating_cache, _grouped_files,
              _launcher_remote_control, _request_cards_and_drafts)
MIGRATIONS += (_telegram_storage_backup,)
MIGRATIONS += (_catalog_sections, _episode_qualities,)
MIGRATIONS += (_referral_contests,)


def _multiple_launchers():
    database.execute("ALTER TABLE WebDrafts ADD COLUMN launcher_instance TEXT NOT NULL DEFAULT ''")
    database.execute('''UPDATE WebDrafts SET launcher_instance=COALESCE(
        (SELECT instance_id FROM LauncherAgents WHERE admin_id=WebDrafts.admin_id),'')
        WHERE import_key IS NOT NULL''')
    database.execute('''CREATE TABLE LauncherAgents_new (
        admin_id INTEGER NOT NULL, instance_id TEXT NOT NULL, name TEXT NOT NULL,
        protocol INTEGER NOT NULL DEFAULT 2, status TEXT NOT NULL, folders TEXT NOT NULL,
        last_seen TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, PRIMARY KEY(admin_id,instance_id))''')
    database.execute('''INSERT INTO LauncherAgents_new(admin_id,instance_id,name,status,folders,last_seen)
        SELECT admin_id,instance_id,'Компьютер (старая версия)',status,folders,last_seen FROM LauncherAgents''')
    database.execute("ALTER TABLE LauncherCommands ADD COLUMN instance_id TEXT NOT NULL DEFAULT ''")
    database.execute('''UPDATE LauncherCommands SET instance_id=COALESCE(
        (SELECT instance_id FROM LauncherAgents WHERE admin_id=LauncherCommands.admin_id),'')''')
    database.execute("UPDATE LauncherCommands SET status='failed',result='Команда прервана обновлением панели.' WHERE status='claimed'")
    database.execute('DROP TABLE LauncherAgents')
    database.execute('ALTER TABLE LauncherAgents_new RENAME TO LauncherAgents')
    database.execute('CREATE INDEX idx_launcher_command_device ON LauncherCommands(admin_id,instance_id,status,id)')
    database.execute("ALTER TABLE DesktopSessions ADD COLUMN instance_id TEXT NOT NULL DEFAULT ''")
    database.execute("ALTER TABLE WebAssets ADD COLUMN content_sha256 TEXT NOT NULL DEFAULT ''")
    database.execute('ALTER TABLE WebAssets ADD COLUMN reused_asset_id TEXT')
    database.execute('CREATE INDEX idx_asset_digest ON WebAssets(admin_id,content_sha256,status)')
    database.execute('''CREATE TABLE LauncherTransfers (
        content_key TEXT PRIMARY KEY, admin_id INTEGER NOT NULL,
        asset_id TEXT NOT NULL REFERENCES WebAssets(id) ON DELETE CASCADE,
        instance_id TEXT NOT NULL, status TEXT NOT NULL, expires_at REAL NOT NULL,
        updated_at REAL NOT NULL)''')


MIGRATIONS += (_multiple_launchers,)


def _age_preferences():
    from database.age_access import create_table
    create_table()


MIGRATIONS += (_age_preferences,)


def _stream_projects():
    database.execute('''CREATE TABLE StreamProjects (
        draft_id TEXT PRIMARY KEY REFERENCES WebDrafts(id), admin_id INTEGER NOT NULL,
        instance_id TEXT NOT NULL, root TEXT NOT NULL, expected INTEGER NOT NULL,
        completed INTEGER NOT NULL DEFAULT 0, manifest TEXT NOT NULL,
        UNIQUE(admin_id,instance_id,root))''')


MIGRATIONS += (_stream_projects,)


def _request_targets():
    database.execute("ALTER TABLE ContentRequests ADD COLUMN episode INTEGER NOT NULL DEFAULT 0")
    database.execute("ALTER TABLE ContentRequests ADD COLUMN voice TEXT NOT NULL DEFAULT ''")
    database.execute("ALTER TABLE ContentRequests ADD COLUMN verification TEXT NOT NULL DEFAULT ''")
    database.execute("ALTER TABLE ContentRequests ADD COLUMN expected_episodes TEXT NOT NULL DEFAULT ''")


MIGRATIONS += (_request_targets,)


def _request_voice_evidence():
    database.execute("ALTER TABLE ContentRequests ADD COLUMN voice_evidence TEXT NOT NULL DEFAULT '[]'")


MIGRATIONS += (_request_voice_evidence,)


def _zona_jobs():
    database.execute("""CREATE TABLE ZonaJobs (
        id TEXT PRIMARY KEY, admin_id INTEGER NOT NULL, page_url TEXT NOT NULL,
        source_id TEXT NOT NULL, label TEXT NOT NULL, metadata TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'queued', error TEXT, path TEXT,
        file_id TEXT, asset_id TEXT, message_id INTEGER,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(admin_id,source_id))""")
    database.execute("CREATE INDEX idx_zona_jobs_status ON ZonaJobs(status,created_at)")


MIGRATIONS += (_zona_jobs,)


def _instant_jobs():
    database.execute('''CREATE TABLE InstantJobs (
        id TEXT PRIMARY KEY, user_id INTEGER NOT NULL, identity TEXT NOT NULL UNIQUE,
        payload TEXT NOT NULL, request_id INTEGER, status TEXT NOT NULL,
        owner TEXT, lease_hash TEXT, lease_until REAL, draft_id TEXT, asset_id TEXT,
        file_id TEXT, sent_message_id INTEGER, error TEXT,
        created_at REAL NOT NULL, updated_at REAL NOT NULL,
        generation INTEGER NOT NULL DEFAULT 0)''')
    database.execute('CREATE INDEX idx_instant_queue ON InstantJobs(status,created_at)')
    database.execute('''CREATE TABLE InstantAgent (
        id INTEGER PRIMARY KEY CHECK(id=1), owner TEXT NOT NULL, last_seen REAL NOT NULL)''')


MIGRATIONS += (_instant_jobs,)


def _provider_batches():
    database.execute('''CREATE TABLE ProviderBatches (
        id TEXT PRIMARY KEY, admin_id INTEGER NOT NULL, payload TEXT NOT NULL,
        status TEXT NOT NULL, owner TEXT, lease_hash TEXT, lease_until REAL,
        error TEXT, created_at REAL NOT NULL, skipped INTEGER NOT NULL DEFAULT 0)''')
    database.execute('''CREATE TABLE ProviderBatchItems (
        job_id TEXT PRIMARY KEY REFERENCES InstantJobs(id),
        batch_id TEXT NOT NULL REFERENCES ProviderBatches(id))''')
    database.execute('''CREATE TABLE ProviderFiles (
        identity TEXT PRIMARY KEY, admin_id INTEGER NOT NULL, draft_id TEXT NOT NULL,
        asset_id TEXT NOT NULL, payload TEXT NOT NULL, publish_error TEXT)''')
    database.execute('''CREATE TABLE ProviderTitles (
        identity TEXT PRIMARY KEY, movie_number INTEGER NOT NULL REFERENCES Movies(movie_number) ON DELETE CASCADE)''')
    database.execute('''CREATE TABLE ProviderPolicy (
        id INTEGER PRIMARY KEY CHECK(id=1), automatic INTEGER NOT NULL, age_rating TEXT NOT NULL)''')
    # Adopt only confirmed receipts from v25; do not request or publish old files.
    import json
    import re
    from services.provider_catalog import identity
    for admin, draft_id, asset_id, raw, number in database.execute('''
            SELECT a.admin_id,j.draft_id,j.asset_id,j.payload,d.movie_number
            FROM InstantJobs j JOIN WebAssets a ON a.id=j.asset_id JOIN WebDrafts d ON d.id=j.draft_id
            WHERE j.status IN ('sent','ready') AND a.status='ready' AND a.file_id=j.file_id
            ORDER BY j.created_at''').fetchall():
        payload = json.loads(raw)
        if not re.fullmatch(r'(?:19|20)\d{2}', str(payload.get('year', ''))):
            continue
        database.execute('INSERT OR IGNORE INTO ProviderFiles(identity,admin_id,draft_id,asset_id,payload) VALUES (?,?,?,?,?)',
                         (identity(payload, True), admin, draft_id, asset_id, raw))
        if number:
            database.execute('INSERT OR IGNORE INTO ProviderTitles VALUES (?,?)', (identity(payload), number))


MIGRATIONS += (_provider_batches,)


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
