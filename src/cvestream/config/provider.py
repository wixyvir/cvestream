"""Configuration provider protocol, snapshot and error family.

A provider returns an immutable `ConfigSnapshot`; the pipeline swaps the
reference when the revision changes and never mutates a snapshot. An invalid
change raises `ConfigValidationError`, so the caller keeps the last valid
snapshot. This module logs nothing: logging `config.rejected` belongs to the
caller (story 1.6).

Error messages name the field and the error, never the input: a token must not
leak through an exception.
"""

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Protocol

from pydantic import ValidationError

from cvestream.config.models import ConfigDocument
from cvestream.domain.endpoint import Endpoint
from cvestream.errors import CvestreamError
from cvestream.settings import Settings

_VALUE_ERROR_PREFIX = "Value error, "
# Tags of the callable discriminator of product items: not part of the contract.
_TAG_SEGMENTS = frozenset({"shorthand", "entry"})


class ConfigError(CvestreamError):
    """The configuration could not be loaded."""


class ConfigUnavailableError(ConfigError):
    """The configuration store cannot be read (missing file, store down)."""


class ConfigAccessDeniedError(ConfigUnavailableError):
    """The store refused the credentials or the command (authentication, ACL).

    Unlike a network failure it does not heal by waiting: the daemon does not
    retry it at startup.
    """


class ConfigConflictError(ConfigUnavailableError):
    """The revision kept moving during a read, or a write kept losing its race.

    Transient: the next read or the next attempt may succeed, so it is retried
    like a network failure.
    """


class ConfigValidationError(ConfigError):
    """The configuration was read but is invalid; `errors` names each field."""

    def __init__(self, source: str, errors: Sequence[str]) -> None:
        self.source = source
        self.errors = tuple(errors)
        super().__init__(self._summary())

    def _summary(self) -> str:
        if not self.errors:
            return f"{self.source}: invalid configuration"
        more = len(self.errors) - 1
        suffix = f" (+{more} more)" if more else ""
        return f"{self.source}: {self.errors[0]}{suffix}"


@dataclass(frozen=True, slots=True)
class ConfigSnapshot:
    """One validated configuration. Disabled endpoints are kept."""

    revision: str
    endpoints: tuple[Endpoint, ...]
    # token -> endpoint id. Never printed: `repr=False`.
    tokens: Mapping[str, str] = field(repr=False)

    def endpoint_for_token(self, token: str) -> Endpoint | None:
        endpoint_id = self.tokens.get(token)
        if endpoint_id is None:
            return None
        return next(e for e in self.endpoints if e.id == endpoint_id)


class ConfigProvider(Protocol):
    def revision(self) -> str:
        """Cheap change check, called before each pipeline run."""
        ...

    def load(self) -> ConfigSnapshot:
        """Load and validate; raises `ConfigError`."""
        ...


def _is_tag(location: tuple[int | str, ...], index: int) -> bool:
    # A tag only follows `products[<i>]`; elsewhere `entry` is a real key name.
    return (
        location[index] in _TAG_SEGMENTS
        and index >= 2
        and isinstance(location[index - 1], int)
        and location[index - 2] == "products"
    )


def _format_location(location: tuple[int | str, ...]) -> str:
    path = ""
    for index, part in enumerate(location):
        if isinstance(part, int):
            path += f"[{part}]"
        elif _is_tag(location, index):
            continue
        else:
            path += f".{part}" if path else part
    return path


def _format_error(location: tuple[int | str, ...], message: str) -> str:
    message = message.removeprefix(_VALUE_ERROR_PREFIX)
    path = _format_location(location)
    return f"{path}: {message}" if path else message


def build_snapshot(data: object, *, revision: str, source: str) -> ConfigSnapshot:
    """Validate raw configuration `data` and build a snapshot.

    Shared by every provider (YAML now, Valkey later).
    """
    try:
        document = ConfigDocument.model_validate(data)
    except ValidationError as exc:
        # Not `str(exc)`: it embeds `input_value`, which can be a token.
        errors = [
            _format_error(error["loc"], error["msg"])
            for error in exc.errors(include_input=False, include_url=False)
        ]
        raise ConfigValidationError(source, errors) from None
    endpoints = tuple(endpoint.to_domain() for endpoint in document.endpoints)
    tokens = {endpoint.token: endpoint.id for endpoint in document.endpoints}
    return ConfigSnapshot(
        revision=revision, endpoints=endpoints, tokens=MappingProxyType(tokens)
    )


def open_provider(settings: Settings) -> tuple[ConfigProvider, Callable[[], None]]:
    """The provider `settings` names, and the function that closes it.

    Each provider module is imported here, not at the top: YAML mode never
    imports `valkey`.
    """
    if settings.config_provider == "valkey":
        from cvestream.config.valkey_provider import ValkeyConfigProvider

        valkey_provider = ValkeyConfigProvider.from_settings(settings.valkey)
        return valkey_provider, valkey_provider.close

    from cvestream.config.yaml_provider import YamlConfigProvider

    return YamlConfigProvider(settings.endpoints_file), _nothing_to_close


def _nothing_to_close() -> None:
    return None
