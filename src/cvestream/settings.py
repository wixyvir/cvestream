"""Runtime settings.

Sources, highest priority first: explicit init values, `CVESTREAM_*` environment
variables (`__` nests), `CVESTREAM_<NAME>_FILE` secret files, then an optional
YAML file (`CVESTREAM_SETTINGS_FILE`, default `./settings.yaml`). Values deep-merge
across sources. Durations are ISO 8601 in the environment (`PT30M`); YAML also
accepts a number of seconds.
"""

import os
import re
import types
import typing
from collections.abc import Mapping
from contextvars import ContextVar
from datetime import timedelta
from importlib.resources.abc import Traversable
from pathlib import Path
from typing import Any, Literal, Self
from urllib.parse import urlsplit

import yaml
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    field_validator,
    model_validator,
)
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
# EUVD's gateway rate limit (see `sources/euvd.py` `PAGE_PAUSE`).
DEFAULT_EUVD_PAGE_PAUSE = timedelta(seconds=1.5)
MAX_EUVD_PAGE_PAUSE = timedelta(seconds=60)
DATABASE_FILE = "cvestream.db"
DEFAULT_VALKEY_STARTUP_TIMEOUT = timedelta(seconds=60)
MAX_VALKEY_STARTUP_TIMEOUT = timedelta(hours=1)
VALKEY_SCHEMES = ("valkey", "valkeys")

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


class EuvdSourceSettings(SourceSettings):
    # Pause between two `/search` pages: EUVD answers 403 when paged too fast.
    page_pause: timedelta = Field(
        default=DEFAULT_EUVD_PAGE_PAUSE,
        ge=timedelta(0),
        le=MAX_EUVD_PAGE_PAUSE,
    )


class SourcesSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    euvd: EuvdSourceSettings = Field(default_factory=EuvdSourceSettings)
    cisa_kev: SourceSettings = Field(default_factory=SourceSettings)
    cert_eu: SourceSettings = Field(default_factory=SourceSettings)


class StateSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # SQLAlchemy URL. Secret: a PostgreSQL URL carries a password
    # (CVESTREAM_STATE__URL_FILE).
    url: SecretStr | None = None


class ValkeySettings(BaseModel):
    """Connection to the Valkey configuration store (`config_provider: valkey`)."""

    model_config = ConfigDict(extra="forbid")

    # `valkey://<user>@<host>:<port>/<db>` (`valkeys://` for TLS). The user name
    # is not secret; the password never goes in the URL.
    url: str | None = None
    # Secret (CVESTREAM_VALKEY__PASSWORD_FILE).
    password: SecretStr | None = None
    # How long the daemon waits for Valkey at startup before exiting with code 3.
    startup_timeout: timedelta = Field(
        default=DEFAULT_VALKEY_STARTUP_TIMEOUT,
        gt=timedelta(0),
        le=MAX_VALKEY_STARTUP_TIMEOUT,
    )

    @field_validator("url")
    @classmethod
    def _check_url(cls, value: str | None) -> str | None:
        # Messages never include the value: it names a user and a host, and a
        # mistake could put a password in it.
        if value is None:
            return None
        try:
            parts = urlsplit(value)
            port = parts.port  # a bad port raises ValueError here
        except ValueError:
            msg = "valkey.url is not a valid URL"
            raise ValueError(msg) from None
        if parts.scheme not in VALKEY_SCHEMES:
            msg = "valkey.url: scheme must be valkey:// or valkeys://"
            raise ValueError(msg)
        if parts.password is not None:
            msg = (
                "valkey.url must not contain a password: use valkey.password "
                "(CVESTREAM_VALKEY__PASSWORD_FILE)"
            )
            raise ValueError(msg)
        if not parts.hostname:
            msg = "valkey.url: a host is required"
            raise ValueError(msg)
        if port == 0:
            msg = "valkey.url: invalid port"
            raise ValueError(msg)
        if parts.query or parts.fragment:
            msg = "valkey.url: a query string or a fragment is not supported"
            raise ValueError(msg)
        if parts.path not in ("", "/") and not re.fullmatch(r"/[0-9]+", parts.path):
            msg = "valkey.url: the path must be a database number (/0)"
            raise ValueError(msg)
        return value


class ListenerSettings(BaseModel):
    """One HTTP listener. Each listener has its own default port."""

    model_config = ConfigDict(extra="forbid")

    # The container's network namespace is the boundary: only 8080 is published.
    host: str = "0.0.0.0"  # noqa: S104
    port: int = Field(ge=1, le=65535)


class PublicListenerSettings(ListenerSettings):
    port: int = Field(default=8080, ge=1, le=65535)
    # Where readers reach the feeds (reverse proxy, Ingress): only the operator
    # CLI uses it, to print feed URLs. Not a secret.
    base_url: str | None = None

    @field_validator("base_url")
    @classmethod
    def _check_base_url(cls, value: str | None) -> str | None:
        # Messages never repeat the value.
        if value is None:
            return None
        try:
            parts = urlsplit(value)
            parts.port  # noqa: B018 - a bad port raises ValueError here
        except ValueError:
            msg = "http.public.base_url is not a valid URL"
            raise ValueError(msg) from None
        if parts.scheme not in {"http", "https"}:
            msg = "http.public.base_url: scheme must be http:// or https://"
            raise ValueError(msg)
        if not parts.hostname:
            msg = "http.public.base_url: a host is required"
            raise ValueError(msg)
        if parts.username is not None or parts.password is not None:
            msg = "http.public.base_url must not contain user information"
            raise ValueError(msg)
        if parts.query or parts.fragment or "?" in value or "#" in value:
            msg = "http.public.base_url: a query string or a fragment is not supported"
            raise ValueError(msg)
        return value.removesuffix("/")


class PrivateListenerSettings(ListenerSettings):
    port: int = Field(default=9090, ge=1, le=65535)


class HttpSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    public: PublicListenerSettings = Field(default_factory=PublicListenerSettings)
    private: PrivateListenerSettings = Field(default_factory=PrivateListenerSettings)


class _EnvironSource(EnvSettingsSource):
    """Environment source reading an injected mapping instead of `os.environ`."""

    def __init__(
        self, settings_cls: type[BaseSettings], environ: Mapping[str, str]
    ) -> None:
        self._environ = environ
        super().__init__(settings_cls)

    def _load_env_vars(self) -> Mapping[str, str | None]:
        # `*_FILE` variables of secret fields belong to `_FileSecretsSource`;
        # left here, a nested one (`A__B_FILE`) would be an unknown `b_file` key.
        return {
            key.lower(): value
            for key, value in self._environ.items()
            if _secret_file_path(self.settings_cls, key) is None
        }


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


def _secret_file_path(settings_cls: type[BaseSettings], name: str) -> list[str] | None:
    """Field path of a `<PREFIX><PATH>_FILE` variable naming a `SecretStr` field."""
    prefix = (settings_cls.model_config.get("env_prefix") or "").upper()
    upper_name = name.upper()
    if not (upper_name.startswith(prefix) and upper_name.endswith("_FILE")):
        return None
    stem = upper_name[len(prefix) : -len("_FILE")]
    path = [part.lower() for part in stem.split("__")]
    if not stem or not _is_secret_field(settings_cls, path):
        return None  # not a secret: leave the variable alone
    return path


class _FileSecretsSource(PydanticBaseSettingsSource):
    """Reads `CVESTREAM_<PATH>_FILE` into `SecretStr` fields only."""

    def __init__(
        self, settings_cls: type[BaseSettings], environ: Mapping[str, str]
    ) -> None:
        super().__init__(settings_cls)
        self._environ = environ

    def get_field_value(
        self, field: FieldInfo, field_name: str
    ) -> tuple[Any, str, bool]:
        return None, field_name, False  # everything happens in __call__

    def __call__(self) -> dict[str, Any]:
        result: dict[str, Any] = {}
        upper = {key.upper(): key for key in self._environ}
        for upper_name, name in upper.items():
            path = _secret_file_path(self.settings_cls, name)
            if path is None:
                continue
            plain = upper_name.removesuffix("_FILE")
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


class _YamlSource(YamlConfigSettingsSource):
    """YAML source turning unreadable or malformed files into `SettingsError`."""

    def _read_file(self, file_path: Path | Traversable) -> dict[str, Any]:
        try:
            data = super()._read_file(file_path)
        # ValueError: PyYAML constructors reject impossible dates and bad tagged
        # scalars; RecursionError: pathologically nested documents.
        except (
            OSError,
            UnicodeDecodeError,
            ValueError,
            RecursionError,
            yaml.YAMLError,
        ) as exc:
            msg = f"settings file {file_path}: cannot parse YAML: {type(exc).__name__}"
            raise SettingsError(msg) from exc
        if not isinstance(data, dict):
            msg = f"settings file {file_path}: top level must be a mapping"
            raise SettingsError(msg)
        return data


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
    config_provider: Literal["yaml", "valkey"] = "yaml"
    endpoints_file: Path = Path("endpoints.yaml")
    data_dir: Path = Path("data")
    state: StateSettings = Field(default_factory=StateSettings)
    http: HttpSettings = Field(default_factory=HttpSettings)
    valkey: ValkeySettings = Field(default_factory=ValkeySettings)

    @model_validator(mode="after")
    def _valkey_needs_a_url(self) -> Self:
        if self.config_provider == "valkey" and self.valkey.url is None:
            msg = "config_provider valkey requires valkey.url (CVESTREAM_VALKEY__URL)"
            raise ValueError(msg)
        return self

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
        injected = _environ.get()
        environ = os.environ if injected is None else injected
        return (
            init_settings,
            _EnvironSource(settings_cls, environ),
            _FileSecretsSource(settings_cls, environ),
            _YamlSource(settings_cls, yaml_file=_yaml_file(environ)),
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


def state_url(settings: Settings) -> str:
    """`state.url`, or the SQLite file `<data_dir>/cvestream.db`."""
    if settings.state.url is not None:
        return settings.state.url.get_secret_value()
    return f"sqlite:///{settings.data_dir / DATABASE_FILE}"
