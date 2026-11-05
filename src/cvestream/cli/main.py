"""Typer root application: the daemon commands and the operator commands.

The commands that start the daemon import `cvestream.app` inside their body, so
`cvestream healthcheck` and the other commands never load the HTTP stack.
"""

import asyncio
from contextvars import ContextVar
from typing import Annotated

import typer

from cvestream import __version__
from cvestream.cli.config import config_app
from cvestream.cli.context import CliState, settings_or_exit, user_error
from cvestream.cli.db import db_app
from cvestream.cli.endpoint import endpoint_app
from cvestream.cli.healthcheck import healthcheck
from cvestream.log import configure_logging
from cvestream.settings import Settings
from cvestream.state.db import StateError

_UNEXPECTED_ERROR = 1
# `--debug`, readable by `main()` once the command has failed.
_DEBUG: ContextVar[bool] = ContextVar("debug", default=False)


# No pretty exceptions: an unexpected error is one line, the traceback is `--debug`.
app = typer.Typer(
    name="cvestream",
    no_args_is_help=True,
    add_completion=False,
    pretty_exceptions_enable=False,
)
app.add_typer(config_app, name="config")
app.add_typer(db_app, name="db")
app.add_typer(endpoint_app, name="endpoint")
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
    """cvestream: filtered vulnerability RSS feeds for the products you run."""
    # `ensure_object`: a state injected by a test (`invoke(obj=...)`) survives.
    ctx.ensure_object(CliState).debug = debug
    _DEBUG.set(debug)


def _start() -> Settings:
    """Settings, with logging configured; a bad setting is a one-line user error."""
    settings = settings_or_exit()
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
        user_error(str(exc))
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
        user_error(
            "poll runs a single cycle: pass --once (the daemon is `cvestream run`)"
        )
    settings = _start()
    # Imported here: the HTTP stack loads only for the commands that need it.
    from cvestream.app.runtime import poll_once

    try:
        code = asyncio.run(poll_once(settings))
    except StateError as exc:
        user_error(str(exc))
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
