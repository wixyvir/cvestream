"""Valkey configuration provider: the only module that imports `valkey`.

The configuration lives under the `cvestream:v1:*` keys of `config/keys.py` (a
public contract, `docs/config-contract.md`). Reading assembles the keys into the
same v1 document the YAML provider validates, through `build_snapshot()`.

Read protocol (the daemon user has no `MULTI` or `WATCH`): `GET revision`,
pipelined reads of the keys, `GET revision` again; two different revisions mean
a writer was in the middle of a change, so the read starts over (at most
`SNAPSHOT_RETRIES` retries, then `ConfigConflictError`).

Write protocol (`update`): `WATCH revision`, read, apply the change, validate,
`MULTI`, write every key, `INCR revision`, `EXEC`; a concurrent writer makes
`EXEC` fail with `WatchError` and the whole sequence runs again (at most
`WRITE_ATTEMPTS` attempts).

Messages never carry a password, a token or a raw value: a valkey-py exception
message is never forwarded, and a value is never quoted. Host, port, user name
and key names are fine.
"""

import json
import re
from collections.abc import Callable, Generator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Final
from urllib.parse import urlsplit

from pydantic import ValidationError
from valkey import Valkey
from valkey.exceptions import (
    AuthenticationError,
    AuthorizationError,
    BusyLoadingError,
    ExecAbortError,
    NoPermissionError,
    ResponseError,
    ValkeyError,
    WatchError,
)

from cvestream.config import keys
from cvestream.config.models import ConfigDocument
from cvestream.config.provider import (
    ConfigAccessDeniedError,
    ConfigConflictError,
    ConfigSnapshot,
    ConfigUnavailableError,
    ConfigValidationError,
    build_snapshot,
)
from cvestream.settings import ValkeySettings

SOURCE: Final = "valkey"
# A cycle is delayed at most by these (valkey-py has no default timeout at all).
SOCKET_TIMEOUT: Final = 5.0
SOCKET_CONNECT_TIMEOUT: Final = 5.0
# Retries of a read whose revision moved (4 attempts at most).
SNAPSHOT_RETRIES: Final = 3
WRITE_ATTEMPTS: Final = 5

# What an endpoint key must not carry: they come from the key names.
_RESERVED_FIELDS: Final = frozenset({"id", "token", "products"})
_REVISION = re.compile(r"[0-9]{1,20}")
_ENDPOINT_INDEX = re.compile(r"^endpoints\[(\d+)\]")
_NOT_SEEDED = (
    f"{keys.SCHEMA}: missing, the store is not seeded (see docs/config-contract.md)"
)

type Document = dict[str, Any]


@dataclass(frozen=True, slots=True)
class RawEndpoint:
    """The raw values of one endpoint; `None` is a missing key."""

    endpoint: bytes | None
    products: bytes | None
    token: bytes | None = field(repr=False)


@dataclass(frozen=True, slots=True)
class RawConfig:
    """What was read from Valkey, before any decoding."""

    schema: bytes | None
    endpoints: Mapping[str, RawEndpoint]


# --- Pure functions: no client, tested without a server ----------------------


def parse_endpoint_ids(members: Sequence[bytes]) -> list[str]:
    """Endpoint ids of the `endpoints` set, sorted; each is a valid slug."""
    ids: list[str] = []
    for member in members:
        try:
            text = member.decode("utf-8")
        except UnicodeDecodeError:
            text = ""
        if keys.ENDPOINT_ID.fullmatch(text) is None:
            # Never echoed: the member is arbitrary data.
            msg = f"{keys.ENDPOINTS}: a member is not a valid endpoint id"
            raise ConfigValidationError(SOURCE, [msg])
        ids.append(text)
    return sorted(ids)


def _decode(key: str, value: bytes, errors: list[str]) -> str | None:
    try:
        return value.decode("utf-8")
    except UnicodeDecodeError:
        errors.append(f"{key}: not valid UTF-8")
        return None


def _reject_constant(_: str) -> object:
    raise ValueError  # NaN, Infinity


def _json(key: str, value: bytes, errors: list[str]) -> object | None:
    """The decoded JSON of `value`; on failure the problem is added to `errors`."""
    text = _decode(key, value, errors)
    if text is None:
        return None
    try:
        return json.loads(text, parse_constant=_reject_constant)
    except ValueError, RecursionError:
        # Not `str(exc)`: a JSON error message quotes a part of the document.
        errors.append(f"{key}: invalid JSON")
        return None


def assemble(raw: RawConfig) -> Document:
    """The v1 document of the raw values, ready for `build_snapshot()`.

    Raises `ConfigValidationError` naming each key at fault.
    """
    if raw.schema is None:
        raise ConfigValidationError(SOURCE, [_NOT_SEEDED])
    errors: list[str] = []
    version = _json(keys.SCHEMA, raw.schema, errors)
    endpoints: list[Document] = []
    for endpoint_id in sorted(raw.endpoints):
        values = raw.endpoints[endpoint_id]
        entry = _assemble_endpoint(endpoint_id, values, errors)
        if entry is not None:
            endpoints.append(entry)
    if errors:
        raise ConfigValidationError(SOURCE, errors)
    return {"version": version, "endpoints": endpoints}


def _assemble_endpoint(
    endpoint_id: str, values: RawEndpoint, errors: list[str]
) -> Document | None:
    start = len(errors)
    endpoint_key = keys.endpoint(endpoint_id)
    entry: Document = {"id": endpoint_id}
    if values.endpoint is None:
        errors.append(f"{endpoint_key}: missing")
    else:
        body = _json(endpoint_key, values.endpoint, errors)
        if body is not None:
            if not isinstance(body, dict):
                errors.append(f"{endpoint_key}: must be a JSON object")
            elif reserved := sorted(_RESERVED_FIELDS & body.keys()):
                fields = ", ".join(reserved)
                errors.append(f"{endpoint_key}: must not contain {fields}")
            else:
                entry.update(body)
    token_key = keys.token(endpoint_id)
    if values.token is None:
        errors.append(f"{token_key}: missing")
    else:
        token = _decode(token_key, values.token, errors)
        if token is not None:
            entry["token"] = token  # raw text, not JSON
    if values.products is not None:
        products_key = keys.products(endpoint_id)
        products = _json(products_key, values.products, errors)
        if products is not None:
            if isinstance(products, list):
                entry["products"] = products
            else:
                errors.append(f"{products_key}: must be a JSON array")
    return entry if len(errors) == start else None


def _name_endpoints(errors: Sequence[str], ids: Sequence[str]) -> list[str]:
    """Rewrite `endpoints[<i>]` to `endpoint:<id>`: the key the operator fixes."""

    def rewrite(error: str) -> str:
        found = _ENDPOINT_INDEX.match(error)
        if found is None or int(found.group(1)) >= len(ids):
            return error
        return f"endpoint:{ids[int(found.group(1))]}{error[found.end() :]}"

    return [rewrite(error) for error in errors]


def validated_snapshot(
    document: Document, *, revision: str, ids: Sequence[str]
) -> ConfigSnapshot:
    """`build_snapshot()` with the errors naming endpoint ids, not indexes."""
    try:
        return build_snapshot(document, revision=revision, source=SOURCE)
    except ConfigValidationError as exc:
        raise ConfigValidationError(SOURCE, _name_endpoints(exc.errors, ids)) from None


def _dump(value: object) -> str:
    return json.dumps(value, separators=(",", ":"), ensure_ascii=False, sort_keys=True)


def _as_bytes(value: object) -> bytes | None:
    """A GET result: bytes, or `None` for a missing key."""
    if value is None or isinstance(value, bytes):
        return value
    msg = f"{SOURCE}: unexpected reply type {type(value).__name__}"
    raise ConfigUnavailableError(msg)


def _as_members(value: object) -> list[bytes]:
    """A SMEMBERS result."""
    if isinstance(value, set | list | tuple | frozenset) and all(
        isinstance(member, bytes) for member in value
    ):
        return list(value)
    msg = f"{SOURCE}: unexpected reply type {type(value).__name__}"
    raise ConfigUnavailableError(msg)


def _revision_text(value: object) -> str:
    """The revision of a `GET revision` result; a missing key is `"0"`."""
    raw = _as_bytes(value)
    if raw is None:
        return "0"
    text = raw.decode("utf-8", errors="replace")
    if _REVISION.fullmatch(text) is None:
        msg = f"{keys.REVISION}: not an integer"
        raise ConfigValidationError(SOURCE, [msg])
    return text


def _document_of(snapshot_document: Document) -> ConfigDocument:
    try:
        return ConfigDocument.model_validate(snapshot_document)
    except ValidationError:  # pragma: no cover - `validated_snapshot` ran first
        raise ConfigValidationError(SOURCE, ["invalid configuration"]) from None


def _endpoint_json(document: ConfigDocument, endpoint_id: str) -> tuple[str, str]:
    """The `endpoint:<id>` and `endpoint:<id>:products` values of an endpoint."""
    endpoint = next(e for e in document.endpoints if e.id == endpoint_id)
    dumped = endpoint.model_dump(mode="json", exclude={"id", "token", "products"})
    products = endpoint.model_dump(mode="json", include={"products"})["products"]
    return _dump(dumped), _dump(products)


# --- The provider -------------------------------------------------------------


class ValkeyConfigProvider:
    """Reads and writes the configuration in a Valkey server."""

    def __init__(
        self, client: Valkey, *, user: str = "default", address: str = "valkey:6379"
    ) -> None:
        self._client = client
        self._user = user
        self._address = address

    @classmethod
    def from_settings(cls, settings: ValkeySettings) -> ValkeyConfigProvider:
        if settings.url is None:
            msg = "config_provider valkey requires valkey.url"
            raise ConfigValidationError("settings", [msg])
        parts = urlsplit(settings.url)
        password = settings.password.get_secret_value() if settings.password else None
        client = Valkey.from_url(
            settings.url,
            password=password,
            socket_timeout=SOCKET_TIMEOUT,
            socket_connect_timeout=SOCKET_CONNECT_TIMEOUT,
            decode_responses=False,
        )
        host = parts.hostname or "valkey"
        return cls(
            client,
            user=parts.username or "default",
            address=f"{host}:{parts.port or 6379}",
        )

    def close(self) -> None:
        self._client.close()

    # -- error mapping --

    def _map_response(self, exc: ResponseError, key: str | None) -> Exception:
        text = str(exc)
        if isinstance(exc, NoPermissionError | ExecAbortError) or text.startswith(
            ("NOAUTH", "WRONGPASS", "NOPERM")
        ):
            what = "the configuration" if key is None else key
            msg = f"{SOURCE}: user '{self._user}' is not allowed to access {what}"
            return ConfigAccessDeniedError(msg)
        if text.startswith("WRONGTYPE"):
            where = key or "a configuration key"
            return ConfigValidationError(SOURCE, [f"{where}: wrong type"])
        return ConfigUnavailableError(f"{SOURCE}: unexpected reply (ResponseError)")

    def _map(self, exc: ValkeyError) -> Exception:
        # Order matters: the authentication errors subclass ConnectionError, and
        # NoPermissionError subclasses ResponseError.
        if isinstance(exc, AuthenticationError | AuthorizationError):
            msg = f"{SOURCE}: authentication failed for user '{self._user}'"
            return ConfigAccessDeniedError(msg)
        if isinstance(exc, ResponseError):
            return self._map_response(exc, None)
        if isinstance(exc, BusyLoadingError):
            msg = f"{SOURCE}: {self._address} is still loading its dataset"
            return ConfigUnavailableError(msg)
        name = type(exc).__name__
        if isinstance(exc, WatchError):  # pragma: no cover - retried by `update`
            return ConfigConflictError(f"{SOURCE}: write conflict")
        if name in {"ConnectionError", "TimeoutError"}:
            msg = f"{SOURCE}: cannot reach {self._address}: {name}"
            return ConfigUnavailableError(msg)
        return ConfigUnavailableError(f"{SOURCE}: unexpected {name}")

    @contextmanager
    def _mapped(self) -> Generator[None]:
        try:
            yield
        except WatchError:
            raise  # retried by `update`
        except ValkeyError as exc:
            raise self._map(exc) from None
        except OSError as exc:
            msg = f"{SOURCE}: cannot reach {self._address}: {type(exc).__name__}"
            raise ConfigUnavailableError(msg) from None

    def _checked(self, result: object, key: str) -> object:
        """A pipelined result; a failed command is mapped, naming its key."""
        if isinstance(result, ResponseError):
            raise self._map_response(result, key)
        return result

    # -- reading --

    def _read_revision(self) -> str:
        with self._mapped():
            return _revision_text(self._client.get(keys.REVISION))

    def _read_raw(self) -> RawConfig:
        with self._mapped():
            head = self._client.pipeline(transaction=False)
            head.get(keys.SCHEMA)
            head.smembers(keys.ENDPOINTS)
            schema_result, members_result = head.execute(raise_on_error=False)
            schema = _as_bytes(self._checked(schema_result, keys.SCHEMA))
            ids = parse_endpoint_ids(
                _as_members(self._checked(members_result, keys.ENDPOINTS))
            )
            if not ids:
                return RawConfig(schema, {})
            body = self._client.pipeline(transaction=False)
            queried: list[str] = []
            for endpoint_id in ids:
                for key in (
                    keys.endpoint(endpoint_id),
                    keys.products(endpoint_id),
                    keys.token(endpoint_id),
                ):
                    body.get(key)
                    queried.append(key)
            results = body.execute(raise_on_error=False)
        values = [
            _as_bytes(self._checked(result, key))
            for key, result in zip(queried, results, strict=True)
        ]
        endpoints = {
            endpoint_id: RawEndpoint(*values[index * 3 : index * 3 + 3])
            for index, endpoint_id in enumerate(ids)
        }
        return RawConfig(schema, endpoints)

    def revision(self) -> str:
        """The current revision (`"0"` when the key is missing). One `GET`."""
        return self._read_revision()

    def load(self) -> ConfigSnapshot:
        """A consistent, validated snapshot; see the module docstring."""
        for _ in range(SNAPSHOT_RETRIES + 1):
            before = self._read_revision()
            raw = self._read_raw()
            after = self._read_revision()
            if before == after:
                return validated_snapshot(
                    assemble(raw), revision=before, ids=sorted(raw.endpoints)
                )
        msg = f"{SOURCE}: the revision kept changing during the read"
        raise ConfigConflictError(msg)

    # -- writing --

    def update(self, change: Callable[[ConfigDocument], ConfigDocument]) -> str:
        """Apply `change` to the stored document; return the new revision.

        `change` is pure and may run several times (once per attempt). An
        invalid result raises `ConfigValidationError` and writes nothing.
        """
        for _ in range(WRITE_ATTEMPTS):
            try:
                with self._mapped():
                    return self._attempt(change)
            except WatchError:
                continue
        msg = f"{SOURCE}: write conflict, gave up after {WRITE_ATTEMPTS} attempts"
        raise ConfigConflictError(msg)

    def _attempt(self, change: Callable[[ConfigDocument], ConfigDocument]) -> str:
        with self._client.pipeline(transaction=True) as pipe:
            pipe.watch(keys.REVISION)  # immediate mode from here to `multi()`
            raw = self._read_raw_watched(pipe)
            current = self._current_document(raw)
            new = change(current)
            new_json = new.model_dump(mode="json")
            validated_snapshot(
                new_json,
                revision="pending",
                ids=[e.id for e in new.endpoints],
            )
            new_ids = sorted(e.id for e in new.endpoints)
            removed = sorted(set(raw.endpoints) - set(new_ids))
            pipe.multi()
            pipe.set(keys.SCHEMA, str(keys.SCHEMA_VALUE))
            if removed:
                pipe.srem(keys.ENDPOINTS, *removed)
                pipe.delete(
                    *(
                        key
                        for endpoint_id in removed
                        for key in (
                            keys.endpoint(endpoint_id),
                            keys.products(endpoint_id),
                            keys.token(endpoint_id),
                        )
                    )
                )
            if new_ids:
                pipe.sadd(keys.ENDPOINTS, *new_ids)
            by_id = {e.id: e for e in new.endpoints}
            for endpoint_id in new_ids:
                body, products = _endpoint_json(new, endpoint_id)
                pipe.set(keys.endpoint(endpoint_id), body)
                pipe.set(keys.products(endpoint_id), products)
                pipe.set(keys.token(endpoint_id), by_id[endpoint_id].token)
            pipe.incr(keys.REVISION)
            results = pipe.execute()
        revision = results[-1]
        if not isinstance(revision, int):
            msg = f"{SOURCE}: unexpected reply type {type(revision).__name__}"
            raise ConfigUnavailableError(msg)
        return str(revision)

    def _read_raw_watched(self, pipe: Any) -> RawConfig:
        """Read the stored keys in immediate mode, inside `WATCH`."""

        def get(key: str) -> bytes | None:
            try:
                return _as_bytes(pipe.get(key))
            except ResponseError as exc:
                raise self._map_response(exc, key) from None

        try:
            members = _as_members(pipe.smembers(keys.ENDPOINTS))
        except ResponseError as exc:
            raise self._map_response(exc, keys.ENDPOINTS) from None
        ids = parse_endpoint_ids(members)
        endpoints = {
            endpoint_id: RawEndpoint(
                get(keys.endpoint(endpoint_id)),
                get(keys.products(endpoint_id)),
                get(keys.token(endpoint_id)),
            )
            for endpoint_id in ids
        }
        return RawConfig(get(keys.SCHEMA), endpoints)

    @staticmethod
    def _current_document(raw: RawConfig) -> ConfigDocument:
        if raw.schema is None and not raw.endpoints:
            # An unseeded store: the first write seeds it.
            return ConfigDocument(version=1, endpoints=[])
        document = assemble(raw)
        validated_snapshot(document, revision="current", ids=sorted(raw.endpoints))
        return _document_of(document)
