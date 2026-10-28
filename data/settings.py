import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("DATA_DIR", PROJECT_ROOT / "runtime")).resolve()
# Keep the existing local database unless a data directory is explicitly configured.
_default_db = DATA_DIR / "bot.sqlite" if "DATA_DIR" in os.environ else PROJECT_ROOT / "bot.sqlite"
DATABASE_PATH = Path(os.environ.get("DATABASE_PATH", _default_db)).resolve()
BACKUP_DIR = Path(os.environ.get("BACKUP_DIR", DATA_DIR / "backups")).resolve()

BOT_API_BASE = os.environ.get("BOT_API_BASE", "https://api.telegram.org").rstrip("/")
WEB_ENABLED = os.environ.get("WEB_ENABLED", "0") == "1"
WEB_HOST = os.environ.get("WEB_HOST", "127.0.0.1")
WEB_PORT = int(os.environ.get("WEB_PORT", "8088"))
WEB_PUBLIC_URL = os.environ.get("WEB_PUBLIC_URL", f"http://127.0.0.1:{WEB_PORT}").rstrip("/")
UPLOAD_DIR = Path(os.environ.get("UPLOAD_DIR", DATA_DIR / "uploads")).resolve()
UPLOAD_QUOTA_BYTES = int(os.environ.get("UPLOAD_QUOTA_BYTES", "20000000000"))
WEB_MAX_FILE_BYTES = 2_000_000_000
WEB_CHUNK_BYTES = 8 * 1024 * 1024

# The first rollout is visible to administrators only; enable PUBLIC after a live check.
INSTANT_ENABLED = os.environ.get('INSTANT_ENABLED', '0') == '1'
INSTANT_PUBLIC = os.environ.get('INSTANT_PUBLIC', '0') == '1'
INSTANT_AGENT_ENABLED = os.environ.get('INSTANT_AGENT_ENABLED', '0') == '1'
