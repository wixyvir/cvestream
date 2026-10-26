"""Online backup of the SQLite state database."""

import sqlite3
from contextlib import closing
from pathlib import Path

from cvestream.state.db import State


def backup(state: State, target: Path) -> None:
    """Copy the database to `target` with the SQLite online backup API.

    The copy is a consistent snapshot that includes the WAL content, taken
    while the daemon keeps running.
    """
    with (
        state.raw_sqlite_connection() as source,
        closing(sqlite3.connect(target)) as destination,
    ):
        source.backup(destination)
