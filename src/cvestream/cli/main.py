"""Typer root application. Subcommands arrive with later stories."""

from dataclasses import dataclass
from typing import Annotated

import typer

from cvestream import __version__
from cvestream.cli.config import config_app
from cvestream.cli.db import db_app


@dataclass
class CliState:
    """Global options shared with subcommands through the Typer context."""

    debug: bool = False


app = typer.Typer(name="cvestream", no_args_is_help=True, add_completion=False)
app.add_typer(config_app, name="config")
app.add_typer(db_app, name="db")


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


def main() -> None:
    app()
