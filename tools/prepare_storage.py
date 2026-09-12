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
    print("Storage permissions prepared for UID 10001.", flush=True)


if __name__ == "__main__":
    prepare()
