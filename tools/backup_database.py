"""Create a consistent SQLite backup, including committed WAL contents."""

import argparse
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("database", type=Path)
    parser.add_argument("--output-dir", type=Path, default=Path("runtime/backups"))
    args = parser.parse_args()
    source = args.database.resolve(strict=True)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    destination = args.output_dir.resolve() / f"backup-{stamp}.sqlite"
    with closing(sqlite3.connect(source.as_uri() + "?mode=ro", uri=True)) as original:
        with closing(sqlite3.connect(destination)) as backup:
            original.backup(backup)
    print(destination)


if __name__ == "__main__":
    main()
