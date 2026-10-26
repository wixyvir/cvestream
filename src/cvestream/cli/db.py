"""`cvestream db`: migrate and back up the state database."""

from pathlib import Path
from typing import Annotated, NoReturn

import typer

from cvestream.settings import SettingsError, load_settings, state_url
from cvestream.state.backup import backup as write_backup
from cvestream.state.db import State, StateError

db_app = typer.Typer(
    name="db",
    no_args_is_help=True,
    help="State database: migrate and back up.",
)

_USER_ERROR = 2


def _fail(message: str) -> NoReturn:
    typer.echo(message, err=True)
    raise typer.Exit(code=_USER_ERROR)


@db_app.command()
def migrate() -> None:
    """Create or upgrade the state database to the latest revision."""
    try:
        state = State(state_url(load_settings()))
        revision = state.migrate()
    except (SettingsError, StateError) as exc:
        _fail(str(exc))
    typer.echo(f"{state.display_url}: at revision {revision}")
    state.dispose()


@db_app.command()
def backup(
    file: Annotated[
        Path, typer.Argument(help="Where to write the copy (must not exist).")
    ],
) -> None:
    """Copy the SQLite state database with the SQLite online backup API."""
    try:
        state = State(state_url(load_settings()))
    except (SettingsError, StateError) as exc:
        _fail(str(exc))
    source = state.sqlite_path
    if state.dialect != "sqlite":
        _fail("db backup supports SQLite only; use pg_dump for PostgreSQL")
    if source is None or not source.is_file():
        where = source or state.display_url
        _fail(f"{where}: no state database; run: cvestream db migrate")
    if file.exists():
        _fail(f"{file}: already exists")
    if source.resolve() == file.resolve():
        _fail(f"{file}: is the state database itself")
    try:
        write_backup(state, file)
    except StateError as exc:
        _fail(str(exc))
    except OSError as exc:
        _fail(f"{file}: cannot write backup: {type(exc).__name__}")
    finally:
        state.dispose()
    typer.echo(f"{file}: backup written")
