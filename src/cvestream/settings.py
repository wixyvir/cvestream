"""Runtime settings.

Sources, highest priority first: explicit init values, `CVESTREAM_*` environment
variables (`__` nests), `CVESTREAM_<NAME>_FILE` secret files, then an optional
YAML file (`CVESTREAM_SETTINGS_FILE`, default `./settings.yaml`). Values deep-merge
across sources. Durations are ISO 8601 in the environment (`PT30M`); YAML also
accepts a number of seconds.
"""

import os
import types
import typing
from collections.abc import Mapping
from contextvars import ContextVar
from datetime import timedelta
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr
from pydantic.fields import FieldInfo
from pydantic_settings import (
    BaseSettings,
    EnvSettingsSource,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
    YamlConfigSettingsSource,
)

from cvestream.errors import CvestreamError

ENV_PREFIX = "CVESTREAM_"
SETTINGS_FILE_ENV = "CVESTREAM_SETTINGS_FILE"
DEFAULT_SETTINGS_FILE = Path("settings.yaml")
MAX_SOURCE_INTERVAL = timedelta(minutes=60)  # NFR-1
DEFAULT_SOURCE_INTERVAL = timedelta(minutes=15)

# The environment seen by the next `Settings()` construction. `load_settings`
# sets it so tests (and callers) can inject an environment mapping.
_environ: ContextVar[Mapping[str, str] | None] = ContextVar("environ", default=None)


class SettingsError(CvestreamError):
    """Settings could not be loaded (unreadable file, conflicting variables)."""


class LogSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    level: Literal["debug", "info", "warning", "error"] = "info"
    format: Literal["json", "console"] = "json"


class SourceSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    interval: timedelta = Field(
        default=DEFAULT_SOURCE_INTERVAL,
        gt=timedelta(0),
        le=MAX_SOURCE_INTERVAL,
    )


class SourcesSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    euvd: SourceSettings = Field(default_factory=SourceSettings)
    cisa_kev: SourceSettings = Field(default_factory=SourceSettings)


class _EnvironSource(EnvSettingsSource):
    """Environment source reading an injected mapping instead of `os.environ`."""

    def __init__(
        self, settings_cls: type[BaseSettings], environ: Mapping[str, str]
    ) -> None:
        self._environ = environ
        super().__init__(settings_cls)

    def _load_env_vars(self) -> Mapping[str, str | None]:
        return {key.lower(): value for key, value in self._environ.items()}


def _unwrap_optional(annotation: Any) -> Any:
    if typing.get_origin(annotation) in (typing.Union, types.UnionType):
        members = [a for a in typing.get_args(annotation) if a is not type(None)]
        if len(members) == 1:
            return members[0]
    return annotation


def _is_secret_field(model: type[BaseModel], path: list[str]) -> bool:
    for index, name in enumerate(path):
        field: FieldInfo | None = model.model_fields.get(name)
        if field is None:
            return False
        annotation = _unwrap_optional(field.annotation)
        if index == len(path) - 1:
            return annotation is SecretStr
        if not (isinstance(annotation, type) and issubclass(annotation, BaseModel)):
            return False
        model = annotation
    return False


class _FileSecretsSource(PydanticBaseSettingsSource):
    """Reads `CVESTREAM_<PATH>_FILE` into `SecretStr` fields only."""

    def __init__(
        self, settings_cls: type[BaseSettings], environ: Mapping[str, str]
    ) -> None:
        super().__init__(settings_cls)
        self._environ = environ
        self._prefix = settings_cls.model_config.get("env_prefix") or ""

    def get_field_value(
        self, field: FieldInfo, field_name: str
    ) -> tuple[Any, str, bool]:
        return None, field_name, False  # everything happens in __call__

    def __call__(self) -> dict[str, Any]:
        result: dict[str, Any] = {}
        upper = {key.upper(): key for key in self._environ}
        for upper_name, name in upper.items():
            if not (
                upper_name.startswith(self._prefix.upper())
                and upper_name.endswith("_FILE")
            ):
                continue
            stem = upper_name[len(self._prefix) : -len("_FILE")]
            path = [part.lower() for part in stem.split("__")]
            if not stem or not _is_secret_field(self.settings_cls, path):
                continue  # not a secret: leave the variable alone
            plain = f"{self._prefix}{stem}".upper()
            if plain in upper:
                msg = f"{upper[plain]} and {name} are both set; set only one"
                raise SettingsError(msg)
            _assign(result, path, self._read(name, self._environ[name]))
        return result

    @staticmethod
    def _read(variable: str, file_path: str) -> str:
        try:
            content = Path(file_path).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            msg = (
                f"{variable}: cannot read secret file {file_path}: {type(exc).__name__}"
            )
            raise SettingsError(msg) from None
        return content.removesuffix("\n").removesuffix("\r")


def _assign(target: dict[str, Any], path: list[str], value: Any) -> None:
    for part in path[:-1]:
        target = target.setdefault(part, {})
    target[path[-1]] = value


def _yaml_file(environ: Mapping[str, str]) -> Path:
    explicit = environ.get(SETTINGS_FILE_ENV)
    if not explicit:
        return DEFAULT_SETTINGS_FILE  # absent file is fine: YAML is optional
    path = Path(explicit)
    if not path.is_file():
        msg = f"{SETTINGS_FILE_ENV}: settings file not found: {path}"
        raise SettingsError(msg)
    return path


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix=ENV_PREFIX, env_nested_delimiter="__", extra="forbid"
    )

    log: LogSettings = Field(default_factory=LogSettings)
    sources: SourcesSettings = Field(default_factory=SourcesSettings)

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        # No .env and no secrets directory: both defaults are dropped.
        environ = _environ.get() or os.environ
        return (
            init_settings,
            _EnvironSource(settings_cls, environ),
            _FileSecretsSource(settings_cls, environ),
            YamlConfigSettingsSource(settings_cls, yaml_file=_yaml_file(environ)),
        )


def _load[S: Settings](settings_cls: type[S], environ: Mapping[str, str] | None) -> S:
    token = _environ.set(environ)
    try:
        return settings_cls()
    finally:
        _environ.reset(token)


def load_settings(environ: Mapping[str, str] | None = None) -> Settings:
    """Build `Settings` from `environ` (default: the process environment)."""
    return _load(Settings, environ)
