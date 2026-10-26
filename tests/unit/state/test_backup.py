import sqlite3
from contextlib import closing
from pathlib import Path

import pytest

from cvestream.state.backup import backup
from cvestream.state.db import State, StateError
from tests.conftest import FakeClock


def test_backup_copies_a_consistent_snapshot(
    state: State, fake_clock: FakeClock, tmp_path: Path
) -> None:
    with state.transaction() as repo:
        repo.set_cursor("euvd", "c1", fake_clock.now())
        repo.set_cursor("cisa_kev", None, fake_clock.now())
    target = tmp_path / "copy.db"

    backup(state, target)

    with closing(sqlite3.connect(target)) as copy:
        assert copy.execute("PRAGMA integrity_check").fetchall() == [("ok",)]
        rows = copy.execute("SELECT source, cursor FROM source_cursor").fetchall()
        assert sorted(rows) == [("cisa_kev", None), ("euvd", "c1")]
        tables = {
            row[0]
            for row in copy.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        assert {"vulnerability", "publication", "alembic_version"} <= tables


def test_backup_includes_content_still_in_the_wal(
    state: State, fake_clock: FakeClock, tmp_path: Path
) -> None:
    # WAL mode: committed rows may live in the -wal file, not the main file yet
    with state.transaction() as repo:
        repo.set_cursor("euvd", "in-wal", fake_clock.now())
    target = tmp_path / "copy.db"
    backup(state, target)
    with closing(sqlite3.connect(target)) as copy:
        assert copy.execute("SELECT cursor FROM source_cursor").fetchall() == [
            ("in-wal",)
        ]


def test_backup_refuses_a_non_sqlite_database(tmp_path: Path) -> None:
    state = State("postgresql+psycopg://user:secret@db.example/cvestream")
    with pytest.raises(StateError):
        backup(state, tmp_path / "copy.db")
