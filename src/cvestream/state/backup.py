"""Online backup of the SQLite state database."""

import sqlite3
from contextlib import closing
from pathlib import Path

from cvestream.state.db import State
from cvestream.state.schema import StateError


def backup(state: State, target: Path) -> None:
    """Copy the database to `target` with the SQLite online backup API.

    The copy is a consistent snapshot that includes the WAL content, taken
    while the daemon keeps running. `target` is created exclusively, so an
    existing file is never overwritten (`FileExistsError`), and a failed copy
    leaves no partial file behind. A SQLite failure raises `StateError`.
    """
    with state.raw_sqlite_connection() as source:
        # Exclusive creation: no window between an existence check and the copy.
        target.open("xb").close()
        try:
            with closing(sqlite3.connect(target)) as destination:
                source.backup(destination)
        except sqlite3.Error as exc:
            target.unlink(missing_ok=True)
            msg = f"{target}: cannot write backup: {type(exc).__name__}"
            raise StateError(msg) from exc
        except BaseException:
            target.unlink(missing_ok=True)
            raise
