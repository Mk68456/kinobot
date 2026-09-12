"""Explicit database lifecycle; importing repositories does not open files."""

import sqlite3
from contextlib import contextmanager
from pathlib import Path


class Database:
    def __init__(self):
        self.connection = None
        self.depth = 0

    def open(self, path):
        if self.connection is not None:
            raise RuntimeError("Database is already open")
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(str(path), timeout=10)
        self.connection.execute("PRAGMA foreign_keys=ON")
        self.connection.execute("PRAGMA journal_mode=WAL")

    def execute(self, sql, parameters=()):
        if self.connection is None:
            raise RuntimeError("Open the database during application startup first")
        return self.connection.execute(sql, parameters)

    def commit(self):
        if not self.depth:
            self.connection.commit()

    @contextmanager
    def transaction(self):
        # No await is allowed inside this synchronous transaction.
        savepoint = f"transaction_{self.depth}"
        self.execute(f"SAVEPOINT {savepoint}")
        self.depth += 1
        try:
            yield
        except BaseException:
            self.execute(f"ROLLBACK TO {savepoint}")
            raise
        finally:
            self.depth -= 1
            self.execute(f"RELEASE {savepoint}")

    def close(self):
        if self.connection is not None:
            self.connection.close()
            self.connection = None


class Cursor:
    """Compatibility adapter for existing synchronous repositories."""

    def execute(self, sql, parameters=()):
        self.result = database.execute(sql, parameters)
        return self.result

    def fetchone(self):
        return self.result.fetchone()

    def fetchall(self):
        return self.result.fetchall()

    @property
    def lastrowid(self):
        return self.result.lastrowid


database = Database()
cursor = Cursor()
