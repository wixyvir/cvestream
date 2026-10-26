"""Database access: engine, transactions and migrations.

`State.transaction()` is the only commit point of the application: it yields a
`Repository` bound to one connection, commits when the block ends and rolls
everything back when it raises. SQLite pragmas are set only in the connect
event, only for the SQLite dialect.
"""

import sqlite3
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import structlog
from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import Engine, create_engine, event, make_url
from sqlalchemy.pool import ConnectionPoolEntry

from cvestream.state.repository import Repository
from cvestream.state.schema import NaiveDatetimeError, StateError

__all__ = ["BUSY_TIMEOUT_MS", "MIGRATIONS", "NaiveDatetimeError", "State", "StateError"]

BUSY_TIMEOUT_MS = 5000
# Alembic package-resource script location: the files ship with the package.
MIGRATIONS = "cvestream.state:migrations"

log = structlog.get_logger()


def _configure_sqlite(dbapi_connection: Any, _record: ConnectionPoolEntry) -> None:
    # Order matters. `journal_mode` cannot change inside a transaction, and with
    # `autocommit = False` the driver keeps a transaction open at all times.
    # `autocommit = False` (Python 3.12+) leaves the driver's legacy transaction
    # mode, in which a SELECT before the first write runs outside the transaction.
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
    finally:
        cursor.close()
    dbapi_connection.autocommit = False


class State:
    """The state database. The engine is created on first use."""

    def __init__(self, url: str) -> None:
        self._url = make_url(url)
        self._engine: Engine | None = None

    @property
    def dialect(self) -> str:
        """`sqlite`, `postgresql`..., from the URL (no driver is imported)."""
        return self._url.get_backend_name()

    @property
    def display_url(self) -> str:
        """The URL with its password hidden."""
        return self._url.render_as_string(hide_password=True)

    @property
    def sqlite_path(self) -> Path | None:
        """The database file of a file-based SQLite URL, else `None`."""
        database = self._url.database
        if self.dialect != "sqlite" or not database or database == ":memory:":
            return None
        if self._url.query.get("mode") == "memory":
            return None
        return Path(database)

    def _get_engine(self) -> Engine:
        if self._engine is None:
            self._engine = create_engine(self._url)
            if self._engine.dialect.name == "sqlite":
                event.listen(self._engine, "connect", _configure_sqlite)
        return self._engine

    def migrate(self) -> str:
        """Upgrade to the head revision (a no-op when there) and return it."""
        path = self.sqlite_path
        if path is not None:
            path.parent.mkdir(parents=True, exist_ok=True)
        config = Config()
        config.set_main_option("script_location", MIGRATIONS)
        with self._get_engine().begin() as connection:
            config.attributes["connection"] = connection
            command.upgrade(config, "head")
        head = ScriptDirectory.from_config(config).get_current_head()
        if head is None:
            raise StateError("the migrations have no head revision")
        log.info("state.migrated", revision=head)
        return head

    def current_revision(self) -> str | None:
        with self._get_engine().connect() as connection:
            return MigrationContext.configure(connection).get_current_revision()

    @contextmanager
    def transaction(self) -> Generator[Repository]:
        """The only commit point: commit on success, roll back on any error."""
        with self._get_engine().begin() as connection:
            yield Repository(connection)

    @contextmanager
    def raw_sqlite_connection(self) -> Generator[sqlite3.Connection]:
        """The DBAPI connection of a SQLite database (backup API)."""
        if self.dialect != "sqlite":
            raise StateError("a raw SQLite connection needs a SQLite database")
        raw = self._get_engine().raw_connection()
        try:
            driver_connection = raw.driver_connection
            if not isinstance(driver_connection, sqlite3.Connection):
                raise StateError("the SQLite driver connection is not sqlite3")
            yield driver_connection
        finally:
            raw.close()

    def dispose(self) -> None:
        if self._engine is not None:
            self._engine.dispose()
            self._engine = None
