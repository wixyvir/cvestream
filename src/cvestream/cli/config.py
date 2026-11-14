"""`cvestream config`: validate, export the JSON Schema, export and import."""

import os
from pathlib import Path
from typing import Annotated, NoReturn

import typer

from cvestream.cli.context import open_store, warn_dropped_comments
from cvestream.config.models import ConfigDocument
from cvestream.config.provider import ConfigError
from cvestream.config.schema import SCHEMA_PATH, render_schema
from cvestream.config.yaml_provider import YamlConfigProvider, dump_document
from cvestream.settings import SettingsError, load_settings

config_app = typer.Typer(
    name="config",
    no_args_is_help=True,
    help="Configuration: validate, JSON Schema, export and import.",
)

_USER_ERROR = 2
_STDOUT = Path("-")


def _fail(message: str) -> NoReturn:
    typer.echo(message, err=True)
    raise typer.Exit(code=_USER_ERROR)


@config_app.command()
def validate(
    file: Annotated[
        Path | None,
        typer.Argument(
            help="Endpoints file. Default: the `endpoints_file` setting.",
            show_default=False,
        ),
    ] = None,
) -> None:
    """Check an endpoints file: exit 0 when valid, 2 with one error line otherwise."""
    try:
        path = file if file is not None else load_settings().endpoints_file
        snapshot = YamlConfigProvider(path).load()
    except (ConfigError, SettingsError) as exc:
        _fail(str(exc))
    typer.echo(f"{path}: valid ({len(snapshot.endpoints)} endpoints)")


@config_app.command()
def schema(
    output: Annotated[
        Path,
        typer.Option(
            "--output", "-o", help="Where to write the schema; `-` is stdout."
        ),
    ] = SCHEMA_PATH,
    check: Annotated[
        bool,
        typer.Option(
            "--check", help="Write nothing; exit 2 when the file is missing or stale."
        ),
    ] = False,
) -> None:
    """Write the JSON Schema of the configuration contract (v1)."""
    text = render_schema()
    if check and output == _STDOUT:
        _fail("--check compares a file: give --output a path, not -")
    if output == _STDOUT:
        typer.echo(text, nl=False)
        return
    if check:
        try:
            current = output.read_text(encoding="utf-8")
        except OSError, UnicodeDecodeError:
            current = None
        if current != text:
            _fail(f"{output} is stale; run: cvestream config schema")
        return
    try:
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open("w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
    except OSError as exc:
        _fail(f"{output}: cannot write: {type(exc).__name__}")


def _sorted(document: ConfigDocument) -> ConfigDocument:
    ordered = sorted(document.endpoints, key=lambda endpoint: endpoint.id)
    return document.model_copy(update={"endpoints": ordered})


@config_app.command("export")
def export(
    output: Annotated[
        Path | None,
        typer.Option(
            "--output",
            "-o",
            help="New file to write, mode 0600. Default: stdout.",
            show_default=False,
        ),
    ] = None,
) -> None:
    """Write the whole active configuration, tokens included, as a v1 YAML file.

    Endpoints are sorted by id, so the same configuration always gives the same
    bytes. The output holds every feed token: keep it private.
    """
    with open_store() as session:
        text = dump_document(_sorted(session.store.document()))
    if output is None:
        typer.echo(text, nl=False)
        return
    try:
        # `O_EXCL`: never overwrite; 0600 from the start, never world-readable.
        descriptor = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        _fail(f"{output}: already exists")
    except OSError as exc:
        _fail(f"{output}: cannot write: {type(exc).__name__}")
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
    except OSError as exc:
        output.unlink(missing_ok=True)
        _fail(f"{output}: cannot write: {type(exc).__name__}")
    typer.echo(f"{output}: configuration written")


def _changes(
    current: ConfigDocument | None, new: ConfigDocument
) -> tuple[int, int, int] | None:
    """`(added, updated, removed)`, or `None` when the current content is unknown."""
    if current is None:
        return None
    before = {endpoint.id: endpoint for endpoint in current.endpoints}
    after = {endpoint.id: endpoint for endpoint in new.endpoints}
    added = len(after.keys() - before.keys())
    removed = len(before.keys() - after.keys())
    updated = sum(
        1 for key in before.keys() & after.keys() if before[key] != after[key]
    )
    return added, updated, removed


@config_app.command("import")
def import_(
    file: Annotated[Path, typer.Argument(help="A v1 endpoints file to import.")],
) -> None:
    """Replace the whole active configuration with the content of FILE.

    The file is validated first, like `config validate`. Endpoints absent from
    the file are removed. It works even when the current content is invalid or
    missing (a Valkey store that was never seeded): this is the repair path.
    """
    try:
        document = YamlConfigProvider(file).document()
    except ConfigError as exc:
        _fail(str(exc))
    with open_store() as session:
        target = session.settings.endpoints_file
        if session.settings.config_provider == "yaml" and _same_file(file, target):
            _fail(f"{file}: is the active configuration file, nothing to import")
        try:
            current: ConfigDocument | None = session.store.document()
        except ConfigError:
            current = None  # invalid, unseeded or unreadable: `replace` repairs it
        revision = session.store.replace(document)
        warn_dropped_comments(session)
    count = len(document.endpoints)
    changes = _changes(current, document)
    if changes is None:
        detail = " (the previous content could not be read)"
    else:
        added, updated, removed = changes
        detail = f" (added {added}, updated {updated}, removed {removed})"
    typer.echo(f"imported {count} endpoints{detail}, revision {revision}")


def _same_file(left: Path, right: Path) -> bool:
    try:
        return left.resolve() == right.resolve()
    except OSError:
        return False
