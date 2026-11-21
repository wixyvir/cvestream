import os
import re
import sqlite3
from contextlib import closing
from pathlib import Path

import pytest
from typer.testing import CliRunner

from cvestream.cli.main import app

runner = CliRunner()
_ANSI = re.compile(r"\x1b\[[0-9;]*m")


@pytest.fixture(autouse=True)
def _isolated(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    # never read the developer's own settings.yaml or state
    monkeypatch.chdir(tmp_path)
    for name in [n for n in os.environ if n.startswith("CVESTREAM_")]:
        monkeypatch.delenv(name)
    monkeypatch.setenv("CVESTREAM_DATA_DIR", str(tmp_path / "data"))


def _plain(output: str) -> str:
    return _ANSI.sub("", output)


def _database(tmp_path: Path) -> Path:
    return tmp_path / "data" / "cvestream.db"


def test_db_without_arguments_shows_help() -> None:
    result = runner.invoke(app, ["db"])
    output = _plain(result.output)
    assert "Usage" in output
    assert "migrate" in output
    assert "backup" in output


def test_migrate_creates_the_database_and_is_idempotent(tmp_path: Path) -> None:
    first = runner.invoke(app, ["db", "migrate"])
    assert first.exit_code == 0, first.output
    assert _plain(first.output).strip().endswith("at revision 0003")
    assert _database(tmp_path).is_file()
    second = runner.invoke(app, ["db", "migrate"])
    assert second.exit_code == 0
    assert _plain(second.output) == _plain(first.output)


def test_migrate_never_prints_a_password(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(
        "CVESTREAM_STATE__URL", "postgresql+psycopg://user:hunter2@db.example/x"
    )
    result = runner.invoke(app, ["db", "migrate"])
    assert "hunter2" not in result.output
    # psycopg is not installed: a user error, not a traceback
    assert result.exit_code == 2
    assert "no database driver" in result.output


def test_migrate_reports_an_unwritable_location_as_a_user_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    blocker = tmp_path / "blocker"
    blocker.write_text("a file, not a directory", encoding="utf-8")
    monkeypatch.setenv("CVESTREAM_DATA_DIR", str(blocker / "data"))
    result = runner.invoke(app, ["db", "migrate"])
    assert result.exit_code == 2
    assert "cannot create directory" in result.output
    assert "Traceback" not in result.output


def test_backup_writes_a_copy_with_the_same_content(tmp_path: Path) -> None:
    assert runner.invoke(app, ["db", "migrate"]).exit_code == 0
    with closing(sqlite3.connect(_database(tmp_path))) as source:
        source.execute(
            "INSERT INTO source_cursor (source, cursor, updated_at) "
            "VALUES ('euvd', 'c1', '2026-01-01T00:00:00.000000Z')"
        )
        source.commit()
    target = tmp_path / "backup.db"

    result = runner.invoke(app, ["db", "backup", str(target)])

    assert result.exit_code == 0, result.output
    assert _plain(result.output).strip() == f"{target}: backup written"
    with closing(sqlite3.connect(target)) as copy:
        assert copy.execute("PRAGMA integrity_check").fetchall() == [("ok",)]
        assert copy.execute("SELECT source, cursor FROM source_cursor").fetchall() == [
            ("euvd", "c1")
        ]
        assert copy.execute("SELECT version_num FROM alembic_version").fetchall() == [
            ("0003",)
        ]


def test_backup_without_a_database_is_a_user_error(tmp_path: Path) -> None:
    result = runner.invoke(app, ["db", "backup", str(tmp_path / "backup.db")])
    assert result.exit_code == 2
    assert "no state database" in result.output
    assert "cvestream db migrate" in result.output
    assert not (tmp_path / "backup.db").exists()


def test_backup_never_overwrites_an_existing_file(tmp_path: Path) -> None:
    runner.invoke(app, ["db", "migrate"])
    target = tmp_path / "backup.db"
    target.write_text("precious", encoding="utf-8")
    result = runner.invoke(app, ["db", "backup", str(target)])
    assert result.exit_code == 2
    assert "already exists" in result.output
    assert target.read_text(encoding="utf-8") == "precious"


def test_backup_refuses_the_database_itself(tmp_path: Path) -> None:
    runner.invoke(app, ["db", "migrate"])
    result = runner.invoke(app, ["db", "backup", str(_database(tmp_path))])
    assert result.exit_code == 2


def test_backup_refuses_a_non_sqlite_url_without_connecting(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # psycopg is not installed: any attempt to connect would fail differently
    monkeypatch.setenv(
        "CVESTREAM_STATE__URL", "postgresql+psycopg://user:hunter2@db.example/x"
    )
    result = runner.invoke(app, ["db", "backup", str(tmp_path / "backup.db")])
    assert result.exit_code == 2
    assert "SQLite only" in result.output
    assert "pg_dump" in result.output
    assert "hunter2" not in result.output


def test_backup_to_an_unwritable_target_is_a_user_error(tmp_path: Path) -> None:
    runner.invoke(app, ["db", "migrate"])
    target = tmp_path / "missing-dir" / "backup.db"
    result = runner.invoke(app, ["db", "backup", str(target)])
    assert result.exit_code == 2
    assert "cannot write backup" in result.output
    assert "Traceback" not in result.output


def test_migrate_reports_an_invalid_url_without_quoting_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CVESTREAM_STATE__URL", "postgresql://u:hunter2@h:port/x")
    result = runner.invoke(app, ["db", "migrate"])
    assert result.exit_code == 2
    assert "not a valid database URL" in result.output
    assert "hunter2" not in result.output


def test_migrate_reports_a_file_that_is_not_a_database(tmp_path: Path) -> None:
    database = _database(tmp_path)
    database.parent.mkdir(parents=True)
    database.write_bytes(b"this is not a SQLite database, only some text" * 4)
    result = runner.invoke(app, ["db", "migrate"])
    assert result.exit_code == 2
    assert "cannot open database" in result.output
    assert "Traceback" not in result.output
