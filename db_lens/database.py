from dataclasses import dataclass
from pathlib import Path
from typing import Protocol
import sqlite3
import time


@dataclass
class Result:
    columns: list[str]
    rows: list[tuple]
    truncated: bool = False


class Database(Protocol):
    def tables(self) -> list[str]: ...
    def schema(self, name: str) -> Result: ...
    def query(self, sql: str) -> Result: ...
    def browse(self, name: str) -> Result: ...
    def close(self) -> None: ...


def quote_identifier(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


class SQLiteDatabase:
    """Read-only SQLite adapter; each query has a time and output budget."""
    def __init__(self, path: str, limit: int = 500):
        self.path = Path(path).expanduser().resolve(strict=True)
        self.limit = limit
        self.connection = sqlite3.connect(self.path.as_uri() + '?mode=ro', uri=True)
        self.connection.execute('PRAGMA query_only = ON')
        # Deny connection-level operations as well as writes.
        denied = {sqlite3.SQLITE_ATTACH, sqlite3.SQLITE_DETACH,
                  sqlite3.SQLITE_INSERT, sqlite3.SQLITE_UPDATE, sqlite3.SQLITE_DELETE,
                  sqlite3.SQLITE_CREATE_TABLE, sqlite3.SQLITE_DROP_TABLE,
                  sqlite3.SQLITE_ALTER_TABLE, sqlite3.SQLITE_TRANSACTION,
                  sqlite3.SQLITE_SAVEPOINT}
        self.connection.set_authorizer(
            lambda action, a, b, db, source: sqlite3.SQLITE_DENY
            if action in denied else sqlite3.SQLITE_OK
        )

    def tables(self) -> list[str]:
        return [row[0] for row in self.connection.execute(
            "SELECT name FROM sqlite_schema WHERE type IN ('table','view') "
            "AND name NOT LIKE 'sqlite_%' ORDER BY name"
        )]

    def browse(self, name: str) -> Result:
        return self.query(f'SELECT * FROM {quote_identifier(name)} LIMIT 501')

    def schema(self, name: str) -> Result:
        return self.query(f'PRAGMA table_info({quote_identifier(name)})')

    def query(self, sql: str) -> Result:
        deadline = time.monotonic() + 3
        self.connection.set_progress_handler(lambda: int(time.monotonic() > deadline), 1000)
        try:
            cursor = self.connection.execute(sql)
            rows = cursor.fetchmany(self.limit + 1)
            return Result([col[0] for col in cursor.description or []],
                          rows[:self.limit], len(rows) > self.limit)
        finally:
            self.connection.set_progress_handler(None, 0)

    def close(self) -> None:
        self.connection.close()
