"""Typer root application: `run`, `poll`, `healthcheck`, `config` and `db`.

The commands that start the daemon import `cvestream.app` inside their body, so
`cvestream healthcheck` and the other commands never load the HTTP stack.
"""

import asyncio
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Annotated, NoReturn

import typer
from pydantic import ValidationError

from cvestream import __version__
from cvestream.cli.config import config_app
from cvestream.cli.db import db_app
from cvestream.cli.healthcheck import healthcheck
from cvestream.log import configure_logging
from cvestream.settings import Settings, SettingsError, load_settings
from cvestream.state.db import StateError

_USER_ERROR = 2
_UNEXPECTED_ERROR = 1
# `--debug`, readable by `main()` once the command has failed.
_DEBUG: ContextVar[bool] = ContextVar("debug", default=False)


@dataclass
class CliState:
    """Global options shared with subcommands through the Typer context."""

    debug: bool = False


# No pretty exceptions: an unexpected error is one line, the traceback is `--debug`.
app = typer.Typer(
    name="cvestream",
    no_args_is_help=True,
    add_completion=False,
    pretty_exceptions_enable=False,
)
app.add_typer(config_app, name="config")
app.add_typer(db_app, name="db")
app.command("healthcheck")(healthcheck)


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(__version__)
        raise typer.Exit


@app.callback()
def root(
    ctx: typer.Context,
    debug: Annotated[
        bool,
        typer.Option("--debug", help="Show tracebacks for unexpected errors."),
    ] = False,
    version: Annotated[
        bool,
        typer.Option(
            "--version",
            callback=_version_callback,
            is_eager=True,
            help="Print the version and exit.",
        ),
    ] = False,
) -> None:
    """cvestream: filtered vulnerability RSS feeds from EUVD and CISA KEV."""
    ctx.obj = CliState(debug=debug)
    _DEBUG.set(debug)


def _fail(message: str) -> NoReturn:
    typer.echo(message, err=True)
    raise typer.Exit(code=_USER_ERROR)


def _start() -> Settings:
    """Settings, with logging configured; a bad setting is a one-line user error."""
    try:
        settings = load_settings()
    except SettingsError as exc:
        _fail(str(exc))
    except ValidationError as exc:
        # `errors()` without the input: a setting may be a secret.
        problems = "; ".join(
            f"{'.'.join(str(part) for part in error['loc'])}: {error['msg']}"
            for error in exc.errors(include_input=False, include_url=False)
        )
        _fail(f"settings: {problems}")
    configure_logging(settings.log.level, settings.log.format)
    return settings


@app.command()
def run() -> None:
    """Run the daemon: poll the sources and serve the feeds."""
    settings = _start()
    # Imported here: the HTTP stack loads only for the commands that need it.
    from cvestream.app.runtime import serve

    try:
        code = asyncio.run(serve(settings))
    except StateError as exc:
        _fail(str(exc))
    if code:
        raise typer.Exit(code=code)


@app.command()
def poll(
    once: Annotated[
        bool,
        typer.Option("--once", help="Fetch every source once, run one cycle, exit."),
    ] = False,
) -> None:
    """Fetch every enabled source once and run a single pipeline cycle."""
    if not once:
        _fail("poll runs a single cycle: pass --once (the daemon is `cvestream run`)")
    settings = _start()
    # Imported here: the HTTP stack loads only for the commands that need it.
    from cvestream.app.runtime import poll_once

    try:
        code = asyncio.run(poll_once(settings))
    except StateError as exc:
        _fail(str(exc))
    if code:
        raise typer.Exit(code=code)


def main() -> None:
    try:
        app()
    # The top-level supervisor of the CLI: every unexpected error ends here.
    except Exception as exc:
        if _DEBUG.get():
            raise
        # The message is not printed: it may quote an input.
        name = type(exc).__name__
        typer.echo(
            f"unexpected error: {name} (run with --debug for the traceback)", err=True
        )
        raise SystemExit(_UNEXPECTED_ERROR) from None
