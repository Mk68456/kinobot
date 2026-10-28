"""Set ownership of configured SQLite storage before dropping root privileges."""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def prepare():
    from data.settings import DATABASE_PATH, BACKUP_DIR, UPLOAD_DIR

    for directory in {DATABASE_PATH.parent, BACKUP_DIR, UPLOAD_DIR}:
        if directory.resolve() in (Path("/"), Path("/etc"), Path("/usr"), Path("/var")):
            raise ValueError("Configure a dedicated storage directory")
        directory.mkdir(parents=True, exist_ok=True)
        os.chown(directory, 10001, 10001)
        os.chmod(directory, directory.stat().st_mode | 0o700)
    for path in (DATABASE_PATH, Path(str(DATABASE_PATH) + "-wal"), Path(str(DATABASE_PATH) + "-shm")):
        if path.exists():
            os.chown(path, 10001, 10001)
            os.chmod(path, path.stat().st_mode | 0o600)
    from data.settings import DATA_DIR, INSTANT_AGENT_ENABLED
    if INSTANT_AGENT_ENABLED:
        private = DATA_DIR / 'yougen'
        if private.is_symlink():
            raise ValueError('Agent storage must not be a symlink')
        private.mkdir(parents=True, exist_ok=True)
        os.chown(private, 10001, 10001)
        os.chmod(private, 0o700)
        for name in ('bridge.json', 'provider.session', 'provider.session-journal',
                     'provider.session-wal', 'provider.session-shm', 'remote.sqlite',
                     'remote.sqlite-wal', 'remote.sqlite-shm', 'remote.sqlite-journal'):
            path = private / name
            if path.is_symlink():
                raise ValueError('Agent storage files must not be symlinks')
            if path.is_file():
                os.chown(path, 10001, 10001)
                os.chmod(path, 0o600)
    print("Storage permissions prepared for UID 10001.", flush=True)


if __name__ == "__main__":
    prepare()
