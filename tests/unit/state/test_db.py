import ast
from pathlib import Path

import pytest
from alembic.autogenerate import compare_metadata
from alembic.runtime.migration import MigrationContext
from sqlalchemy import insert, inspect, select

import cvestream
from cvestream.state import schema
from cvestream.state.db import BUSY_TIMEOUT_MS, State, StateError
from tests.conftest import FakeClock

TABLES = {"vulnerability", "alias", "source_record", "publication", "source_cursor"}


def _raw(state: State, sql: str) -> list[tuple[object, ...]]:
    with state.raw_sqlite_connection() as raw:
        return [tuple(row) for row in raw.execute(sql).fetchall()]


def test_migrate_creates_the_schema_at_head(tmp_path: Path) -> None:
    state = State(f"sqlite:///{tmp_path / 'a.db'}")
    assert state.migrate() == "0001"
    assert state.current_revision() == "0001"
    with state.raw_sqlite_connection() as raw:
        names = {
            row[0]
            for row in raw.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
    assert names == TABLES | {"alembic_version"}
    state.dispose()


def test_second_migrate_is_a_no_op(state: State) -> None:
    assert state.migrate() == "0001"
    assert state.current_revision() == "0001"


def test_migrate_creates_a_missing_parent_directory(tmp_path: Path) -> None:
    database = tmp_path / "missing" / "deeper" / "state.db"
    state = State(f"sqlite:///{database}")
    state.migrate()
    assert database.is_file()
    state.dispose()


def test_metadata_equals_migrations(state: State) -> None:
    with state._get_engine().connect() as connection:
        diff = compare_metadata(MigrationContext.configure(connection), schema.metadata)
    assert diff == []


def test_the_migration_creates_the_declared_names(state: State) -> None:
    with state._get_engine().connect() as connection:
        indexes = {
            index["name"] for index in inspect(connection).get_indexes("publication")
        }
    assert "ix_publication_endpoint_id_published_at" in indexes


def test_sqlite_pragmas_on_a_pooled_connection(state: State) -> None:
    assert _raw(state, "PRAGMA journal_mode") == [("wal",)]
    assert _raw(state, "PRAGMA foreign_keys") == [(1,)]
    assert _raw(state, "PRAGMA busy_timeout") == [(BUSY_TIMEOUT_MS,)]
    assert BUSY_TIMEOUT_MS == 5000


def test_foreign_keys_are_enforced(state: State, fake_clock: FakeClock) -> None:
    with pytest.raises(Exception, match="FOREIGN KEY"), state.transaction() as repo:
        repo.add_aliases(999, ["CVE-2026-1"])


def test_an_error_rolls_back_every_write(state: State, fake_clock: FakeClock) -> None:
    class Boom(Exception):
        pass

    with pytest.raises(Boom), state.transaction() as repo:
        repo.create_vulnerability(fake_clock.now())
        repo.set_cursor("euvd", "c1", fake_clock.now())
        raise Boom

    with state.transaction() as repo:
        assert repo.cursors() == {}
        assert repo.active_since(fake_clock.now().replace(year=2000)) == []


def test_a_successful_block_commits(state: State, fake_clock: FakeClock) -> None:
    with state.transaction() as repo:
        repo.set_cursor("euvd", "c1", fake_clock.now())
    with state.transaction() as repo:
        assert repo.cursors() == {"euvd": "c1"}


def test_select_then_write_share_one_transaction(
    state: State, fake_clock: FakeClock
) -> None:
    # autocommit=False: a SELECT before the first write is inside the transaction,
    # so rolling back undoes the write and nothing leaks out of the block.
    with pytest.raises(RuntimeError), state._get_engine().begin() as connection:
        connection.execute(select(schema.source_cursor))
        connection.execute(
            insert(schema.source_cursor).values(
                source="euvd", cursor=None, updated_at=fake_clock.now()
            )
        )
        raise RuntimeError
    with state.transaction() as repo:
        assert repo.cursors() == {}


def test_in_transaction_flag_follows_autocommit_false(state: State) -> None:
    with state.raw_sqlite_connection() as raw:
        assert raw.autocommit is False


def test_display_url_hides_the_password_without_connecting() -> None:
    state = State("postgresql+psycopg://user:secret@db.example/cvestream")
    assert "secret" not in state.display_url
    assert "db.example" in state.display_url
    assert state.dialect == "postgresql"
    assert state.sqlite_path is None


def test_sqlite_path() -> None:
    assert State("sqlite:////data/cvestream.db").sqlite_path == Path(
        "/data/cvestream.db"
    )
    assert State("sqlite:///data/cvestream.db").sqlite_path == Path("data/cvestream.db")
    assert State("sqlite://").sqlite_path is None
    assert State("sqlite:///:memory:").sqlite_path is None


def test_raw_sqlite_connection_refuses_other_dialects() -> None:
    state = State("postgresql+psycopg://user:secret@db.example/cvestream")
    with pytest.raises(StateError), state.raw_sqlite_connection():
        pass


def test_no_commit_outside_the_state_transaction() -> None:
    """`.commit()` is never called; `.transaction()` only by the pipeline run."""
    root = Path(cvestream.__file__).parent
    commits: list[str] = []
    transactions: set[str] = set()
    for path in root.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        relative = path.relative_to(root).as_posix()
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                if node.func.attr == "commit":
                    commits.append(relative)
                if node.func.attr == "transaction":
                    transactions.add(relative)
    assert commits == []
    assert transactions <= {"pipeline/run.py"}


def test_pragma_text_is_only_in_the_connect_event() -> None:
    root = Path(cvestream.__file__).parent
    for path in root.rglob("*.py"):
        if path.name == "db.py":
            continue
        assert "PRAGMA" not in path.read_text(encoding="utf-8"), path
