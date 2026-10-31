"""`cvestream config`: validate configuration files and export the JSON Schema."""

from pathlib import Path
from typing import Annotated, NoReturn

import typer

from cvestream.config.provider import ConfigError
from cvestream.config.schema import SCHEMA_PATH, render_schema
from cvestream.config.yaml_provider import YamlConfigProvider
from cvestream.settings import SettingsError, load_settings

config_app = typer.Typer(
    name="config",
    no_args_is_help=True,
    help="Configuration contract: validate files and export the JSON Schema.",
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
