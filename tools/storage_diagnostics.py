"""Read-only storage diagnostics for hosting panels with restart-bound terminals."""

import os
import stat
import time


def run():
    from data.settings import DATABASE_PATH, BACKUP_DIR

    print("STORAGE DIAGNOSTICS: bot is paused; database is not opened or modified.", flush=True)
    print(
        f"uid={getattr(os, 'getuid', lambda: 'Windows')()} gid={getattr(os, 'getgid', lambda: 'Windows')()}",
        flush=True,
    )
    paths = [
        DATABASE_PATH.parent,
        DATABASE_PATH,
        DATABASE_PATH.with_name(DATABASE_PATH.name + "-wal"),
        DATABASE_PATH.with_name(DATABASE_PATH.name + "-shm"),
        BACKUP_DIR,
    ]
    for path in paths:
        try:
            info = path.stat()
            print(
                f"path={path} owner={info.st_uid}:{info.st_gid} "
                f"mode={stat.filemode(info.st_mode)} "
                f"readable={os.access(path, os.R_OK)} writable={os.access(path, os.W_OK)}",
                flush=True,
            )
        except OSError as error:
            print(f"path={path} status={type(error).__name__}", flush=True)
    print(
        "Diagnostics complete. Container stays running. "
        "Set STORAGE_DIAGNOSTICS=0 and restart to resume the bot.",
        flush=True,
    )
    while True:
        time.sleep(30)
