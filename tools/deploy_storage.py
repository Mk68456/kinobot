"""Initialize or back up the Docker data volume without overwriting its database."""

import argparse
import os
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path


def snapshot(source, destination):
    destination.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(source.resolve().as_uri() + "?mode=ro", uri=True)) as src:
        if src.execute("PRAGMA quick_check").fetchone()[0] != "ok":
            raise ValueError("Database integrity check failed")
        with closing(sqlite3.connect(destination)) as dst:
            src.backup(dst)


def initialize(directory, source=None):
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / "bot.sqlite"
    if target.exists():
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        snapshot(target, directory / "backups" / f"deploy-{stamp}.sqlite")
        print("Existing volume database preserved and backed up.")
    elif source is not None:
        if not source.is_file():
            raise ValueError("Source database missing; refusing to create an empty catalog")
        # Import atomically: an interrupted copy must not become the working database.
        temporary = directory / "import.sqlite"
        snapshot(source, temporary)
        temporary.replace(target)
        print("Existing catalog imported.")
    else:
        print("No database in volume; a new catalog will be created on startup.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path)
    args = parser.parse_args()
    directory = Path("/app/runtime")
    initialize(directory, args.source)
    # This command runs as root only for volume initialization; the bot uses UID 10001.
    for path in [directory, *directory.rglob("*")]:
        if not path.is_symlink():
            os.chown(path, 10001, 10001)


if __name__ == "__main__":
    main()
