"""What the CLI commands share: the global state, user errors, the store.

`CliState` lives here (not in `main.py`) so the command modules can import it
without importing the root application.

The commands of the operator CLI log nothing and never call `configure_logging()`:
stdout carries the command output, and `--json` must stay parseable.
"""

import secrets
from collections.abc import Callable, Generator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import NoReturn

import typer
from pydantic import ValidationError

from cvestream.config.provider import ConfigError, ConfigStore, open_provider
from cvestream.settings import Settings, SettingsError, load_settings

USER_ERROR = 2


def default_token() -> str:
    """A new feed token: 256 bits, URL-safe (43 characters)."""
    return secrets.token_urlsafe(32)


@dataclass
class CliState:
    """Global options and injected behaviour, shared through the Typer context."""

    debug: bool = False
    # Tests inject a fixed token (`CliRunner.invoke(..., obj=CliState(...))`).
    new_token: Callable[[], str] = field(default=default_token)


@dataclass(frozen=True, slots=True)
class Session:
    """The settings and the provider a command works with."""

    settings: Settings
    store: ConfigStore


def user_error(message: str) -> NoReturn:
    """One line on stderr, exit 2."""
    typer.echo(message, err=True)
    raise typer.Exit(code=USER_ERROR)


def settings_or_exit() -> Settings:
    """The settings; a bad one is a one-line user error."""
    try:
        return load_settings()
    except SettingsError as exc:
        user_error(str(exc))
    except ValidationError as exc:
        # `errors()` without the input: a setting may be a secret.
        problems = "; ".join(
            f"{'.'.join(str(part) for part in error['loc'])}: {error['msg']}"
            for error in exc.errors(include_input=False, include_url=False)
        )
        user_error(f"settings: {problems}")


@contextmanager
def open_store() -> Generator[Session]:
    """The active provider, closed on exit; a `ConfigError` is a user error."""
    settings = settings_or_exit()
    try:
        store, close = open_provider(settings)
    except ConfigError as exc:
        user_error(str(exc))
    try:
        yield Session(settings, store)
    except ConfigError as exc:
        user_error(str(exc))
    finally:
        close()


def state_of(ctx: typer.Context) -> CliState:
    """The `CliState` of the running command (a default one outside the root)."""
    return ctx.ensure_object(CliState)
