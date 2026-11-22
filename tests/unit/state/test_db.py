import ast
from datetime import UTC, datetime
from pathlib import Path

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from sqlalchemy import insert, inspect, select

import cvestream
from cvestream.state import schema
from cvestream.state.db import BUSY_TIMEOUT_MS, MIGRATIONS, State, StateError
from tests.conftest import FakeClock

TABLES = {
    "vulnerability",
    "alias",
    "source_record",
    "publication",
    "source_cursor",
    "feed_cache",
}


def _raw(state: State, sql: str) -> list[tuple[object, ...]]:
    with state.raw_sqlite_connection() as raw:
        return [tuple(row) for row in raw.execute(sql).fetchall()]


def test_migrate_creates_the_schema_at_head(tmp_path: Path) -> None:
    state = State(f"sqlite:///{tmp_path / 'a.db'}")
    assert state.migrate() == "0003"
    assert state.current_revision() == "0003"
    with state.raw_sqlite_connection() as raw:
        names = {
            row[0]
            for row in raw.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
    assert names == TABLES | {"alembic_version"}
    state.dispose()


def test_second_migrate_is_a_no_op(state: State) -> None:
    assert state.migrate() == "0003"
    assert state.current_revision() == "0003"


def test_migrate_creates_a_missing_parent_directory(tmp_path: Path) -> None:
    database = tmp_path / "missing" / "deeper" / "state.db"
    state = State(f"sqlite:///{database}")
    state.migrate()
    assert database.is_file()
    state.dispose()


@pytest.mark.parametrize(
    "url",
    ["", "not a url", "postgresql://user:hunter2@db.example:port/x"],
)
def test_an_invalid_url_is_a_state_error_that_never_quotes_it(url: str) -> None:
    with pytest.raises(StateError) as caught:
        State(url)
    assert "hunter2" not in str(caught.value)
    assert caught.value.__cause__ is None
    assert caught.value.__suppress_context__


def test_migrate_reports_a_file_that_is_not_a_database(tmp_path: Path) -> None:
    database = tmp_path / "state.db"
    database.write_bytes(b"this is not a SQLite database, only some text" * 4)
    state = State(f"sqlite:///{database}")
    with pytest.raises(StateError, match="cannot open database: DatabaseError"):
        state.migrate()
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


def _upgrade_to(state: State, revision: str) -> None:
    config = Config()
    config.set_main_option("script_location", MIGRATIONS)
    with state._get_engine().begin() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, revision)


def test_migrating_from_0001_keeps_the_existing_rows(tmp_path: Path) -> None:
    state = State(f"sqlite:///{tmp_path / 'old.db'}")
    _upgrade_to(state, "0001")
    assert state.current_revision() == "0001"
    with state.transaction() as repository:
        repository.set_cursor("euvd", "c1", datetime(2026, 1, 1, tzinfo=UTC))
    assert state.migrate() == "0003"
    assert state.current_revision() == "0003"
    with state.transaction() as repository:
        assert repository.cursors() == {"euvd": "c1"}
    assert _raw(state, "SELECT count(*) FROM feed_cache") == [(0,)]
    state.dispose()


def test_the_feed_cache_migration_can_be_downgraded(tmp_path: Path) -> None:
    state = State(f"sqlite:///{tmp_path / 'down.db'}")
    state.migrate()
    config = Config()
    config.set_main_option("script_location", MIGRATIONS)
    with state._get_engine().begin() as connection:
        config.attributes["connection"] = connection
        command.downgrade(config, "0001")
    assert state.current_revision() == "0001"
    with state.raw_sqlite_connection() as raw:
        names = {
            row[0]
            for row in raw.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
    assert "feed_cache" not in names
    state.dispose()


def test_the_is_fresh_migration_keeps_rows_and_can_be_downgraded(
    tmp_path: Path,
) -> None:
    state = State(f"sqlite:///{tmp_path / 'fresh.db'}")
    _upgrade_to(state, "0002")
    assert state.current_revision() == "0002"
    # a row as written at revision 0002, before the column existed (raw insert:
    # `add_publication` always writes `is_fresh` now)
    with state.transaction() as repository:
        vulnerability_id = repository.create_vulnerability(
            datetime(2026, 1, 1, tzinfo=UTC)
        )
        repository._connection.execute(
            insert(schema.publication).values(
                endpoint_id="e",
                vulnerability_id=vulnerability_id,
                kind="new",
                guid="cvestream:e:CVE-2026-1:new",
                primary_id="CVE-2026-1",
                band="high",
                is_known_exploited=False,
                products_json=["php"],
                labels_json=[],
                published_at=datetime(2026, 1, 1, tzinfo=UTC),
            )
        )
    assert state.migrate() == "0003"
    assert state.current_revision() == "0003"
    # a row from before the column exists reads NULL, not False
    with state.transaction() as repository:
        rows = repository._connection.execute(
            select(schema.publication.c.is_fresh)
        ).all()
    assert rows == [(None,)]
    config = Config()
    config.set_main_option("script_location", MIGRATIONS)
    with state._get_engine().begin() as connection:
        config.attributes["connection"] = connection
        command.downgrade(config, "0002")
    assert state.current_revision() == "0002"
    with state.raw_sqlite_connection() as raw:
        columns = {row[1] for row in raw.execute("PRAGMA table_info(publication)")}
    assert "is_fresh" not in columns
    assert _raw(state, "SELECT count(*) FROM publication") == [(1,)]
    state.dispose()
