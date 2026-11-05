"""Valkey provider logic that needs no server (the server tests are marked `valkey`)."""

import json
from collections.abc import Iterator
from typing import Any

import pytest
from pydantic import SecretStr
from valkey import Valkey
from valkey.exceptions import (
    AuthenticationError,
    AuthorizationError,
    BusyLoadingError,
    ExecAbortError,
    NoPermissionError,
    ResponseError,
    ValkeyError,
)
from valkey.exceptions import ConnectionError as ValkeyConnectionError
from valkey.exceptions import TimeoutError as ValkeyTimeoutError

from cvestream.config import keys
from cvestream.config.provider import (
    ConfigAccessDeniedError,
    ConfigConflictError,
    ConfigUnavailableError,
    ConfigValidationError,
)
from cvestream.config.valkey_provider import (
    SNAPSHOT_RETRIES,
    RawConfig,
    RawEndpoint,
    ValkeyConfigProvider,
    assemble,
    parse_endpoint_ids,
)
from cvestream.settings import ValkeySettings

TOKEN_A = "a" * 43
TOKEN_B = "b" * 43
SECRET_TOKEN = "SeCrEt" + "x" * 40


def _json(value: object) -> bytes:
    return json.dumps(value).encode()


def _endpoint(
    *,
    body: object = None,
    products: object = None,
    token: str | bytes | None = TOKEN_A,
    with_products: bool = True,
) -> RawEndpoint:
    body = {"name": "PHP", "enabled": True} if body is None else body
    return RawEndpoint(
        endpoint=_json(body),
        products=_json(["php >= 8.2"] if products is None else products)
        if with_products
        else None,
        token=token.encode() if isinstance(token, str) else token,
    )


def _raw(**endpoints: RawEndpoint) -> RawConfig:
    return RawConfig(schema=b"1", endpoints=endpoints)


def _errors(raw: RawConfig) -> tuple[str, ...]:
    with pytest.raises(ConfigValidationError) as excinfo:
        assemble(raw)
    return excinfo.value.errors


# --- assemble ----------------------------------------------------------------


def test_assemble_happy_path() -> None:
    document = assemble(
        _raw(
            **{
                "team-php": _endpoint(
                    body={"name": "PHP", "enabled": False, "min_cvss": 7.5},
                    products=["php >= 8.2", {"name": "symfony", "aliases": ["sf"]}],
                ),
                "team-go": _endpoint(token=TOKEN_B),
            }
        )
    )
    assert document == {
        "version": 1,
        "endpoints": [
            {
                "id": "team-go",
                "name": "PHP",
                "enabled": True,
                "token": TOKEN_B,
                "products": ["php >= 8.2"],
            },
            {
                "id": "team-php",
                "name": "PHP",
                "enabled": False,
                "min_cvss": 7.5,
                "token": TOKEN_A,
                "products": ["php >= 8.2", {"name": "symfony", "aliases": ["sf"]}],
            },
        ],
    }


def test_assemble_sorts_ids() -> None:
    document = assemble(_raw(b=_endpoint(token=TOKEN_B), a=_endpoint()))
    assert [e["id"] for e in document["endpoints"]] == ["a", "b"]


def test_assemble_without_endpoints() -> None:
    assert assemble(_raw()) == {"version": 1, "endpoints": []}


def test_missing_products_key_is_omitted() -> None:
    document = assemble(_raw(a=_endpoint(with_products=False)))
    assert "products" not in document["endpoints"][0]


def test_missing_schema_means_not_seeded() -> None:
    errors = _errors(RawConfig(schema=None, endpoints={}))
    assert len(errors) == 1
    assert errors[0].startswith("cvestream:v1:schema: missing")
    assert "not seeded" in errors[0]


def test_token_is_raw_text_not_json() -> None:
    document = assemble(_raw(a=_endpoint(token=TOKEN_A)))
    assert document["endpoints"][0]["token"] == TOKEN_A
    # A JSON-quoted token is a different (invalid) value, never unquoted.
    quoted = assemble(_raw(a=_endpoint(token=f'"{TOKEN_A}"')))
    assert quoted["endpoints"][0]["token"] == f'"{TOKEN_A}"'


def test_bad_json_names_the_key_and_not_the_value() -> None:
    raw = _raw(
        a=RawEndpoint(
            endpoint=b'{"name": "oops-secret-content',
            products=_json([]),
            token=TOKEN_A.encode(),
        )
    )
    (error,) = _errors(raw)
    assert error == "cvestream:v1:endpoint:a: invalid JSON"
    assert "oops-secret-content" not in error


def test_bad_products_json_names_the_products_key() -> None:
    raw = _raw(
        a=RawEndpoint(_json({"name": "x"}), b"[not json", TOKEN_A.encode()),
    )
    assert _errors(raw) == ("cvestream:v1:endpoint:a:products: invalid JSON",)


def test_nan_is_not_json() -> None:
    raw = _raw(
        a=RawEndpoint(b'{"name": "x", "min_cvss": NaN}', None, TOKEN_A.encode()),
    )
    assert _errors(raw) == ("cvestream:v1:endpoint:a: invalid JSON",)


def test_invalid_utf8_names_the_key() -> None:
    raw = _raw(a=RawEndpoint(b'{"name": "\xff"}', None, TOKEN_A.encode()))
    assert _errors(raw) == ("cvestream:v1:endpoint:a: not valid UTF-8",)


def test_invalid_utf8_token_does_not_leak() -> None:
    raw = _raw(a=RawEndpoint(_json({"name": "x"}), None, b"\xff\xfe"))
    assert _errors(raw) == ("cvestream:v1:token:a: not valid UTF-8",)


def test_endpoint_json_must_be_an_object() -> None:
    raw = _raw(a=_endpoint(body=["name"]))
    assert _errors(raw) == ("cvestream:v1:endpoint:a: must be a JSON object",)


def test_products_json_must_be_an_array() -> None:
    raw = _raw(a=_endpoint(products={"name": "php"}))
    assert _errors(raw) == ("cvestream:v1:endpoint:a:products: must be a JSON array",)


@pytest.mark.parametrize("field", ["id", "token", "products"])
def test_endpoint_json_must_not_carry_reserved_fields(field: str) -> None:
    raw = _raw(a=_endpoint(body={"name": "x", field: "z"}))
    assert _errors(raw) == (f"cvestream:v1:endpoint:a: must not contain {field}",)


def test_missing_endpoint_key_is_an_error() -> None:
    raw = _raw(a=RawEndpoint(None, _json([]), TOKEN_A.encode()))
    assert _errors(raw) == ("cvestream:v1:endpoint:a: missing",)


def test_missing_token_names_the_endpoint() -> None:
    raw = _raw(a=_endpoint(token=None))
    assert _errors(raw) == ("cvestream:v1:token:a: missing",)


def test_every_faulty_key_is_reported() -> None:
    raw = _raw(
        a=RawEndpoint(None, b"x", None),
        b=_endpoint(token=TOKEN_B, products={"x": 1}),
    )
    assert len(_errors(raw)) == 4


# --- parse_endpoint_ids ------------------------------------------------------


def test_parse_endpoint_ids_sorts() -> None:
    assert parse_endpoint_ids([b"b", b"a-1"]) == ["a-1", "b"]


@pytest.mark.parametrize("member", [b"A", b"a:b", b"", b"a*", b"\xff", b"a" * 41])
def test_parse_endpoint_ids_rejects_invalid_members(member: bytes) -> None:
    with pytest.raises(ConfigValidationError) as excinfo:
        parse_endpoint_ids([b"ok", member])
    assert excinfo.value.errors == (
        "cvestream:v1:endpoints: a member is not a valid endpoint id",
    )


# --- the revision bracket ----------------------------------------------------


class _ScriptedProvider(ValkeyConfigProvider):
    """Replaces the two readers with scripted values: no server, no socket."""

    def __init__(self, revisions: list[str], raw: RawConfig) -> None:
        super().__init__(Valkey())  # lazy: it never connects
        self._revisions = iter(revisions)
        self._raw = raw
        self.raw_reads = 0

    def _read_revision(self) -> str:
        return next(self._revisions)

    def _read_raw(self) -> RawConfig:
        self.raw_reads += 1
        return self._raw


def test_load_returns_a_snapshot_with_the_bracket_revision() -> None:
    provider = _ScriptedProvider(["7", "7"], _raw(a=_endpoint()))
    snapshot = provider.load()
    assert snapshot.revision == "7"
    assert [e.id for e in snapshot.endpoints] == ["a"]
    assert snapshot.tokens == {TOKEN_A: "a"}
    assert provider.raw_reads == 1


def test_load_retries_when_the_revision_moves() -> None:
    provider = _ScriptedProvider(["1", "2", "2", "2"], _raw(a=_endpoint()))
    assert provider.load().revision == "2"
    assert provider.raw_reads == 2


def test_load_gives_up_after_three_retries() -> None:
    revisions = [str(n) for n in range(2 * (SNAPSHOT_RETRIES + 1))]
    provider = _ScriptedProvider(revisions, _raw(a=_endpoint()))
    with pytest.raises(ConfigConflictError, match="kept changing"):
        provider.load()
    assert provider.raw_reads == SNAPSHOT_RETRIES + 1


def test_load_succeeds_on_the_last_attempt() -> None:
    revisions = ["1", "2", "3", "4", "5", "6", "7", "7"]
    provider = _ScriptedProvider(revisions, _raw(a=_endpoint()))
    assert provider.load().revision == "7"
    assert provider.raw_reads == SNAPSHOT_RETRIES + 1


def test_load_names_the_endpoint_in_model_errors() -> None:
    provider = _ScriptedProvider(
        ["1", "1"],
        _raw(a=_endpoint(), b=_endpoint(body={"name": "x", "min_cvss": 11})),
    )
    with pytest.raises(ConfigValidationError) as excinfo:
        provider.load()
    (error,) = excinfo.value.errors
    assert error.startswith("endpoint:b.min_cvss: ")


def test_load_names_the_endpoint_for_a_duplicate_token() -> None:
    provider = _ScriptedProvider(["1", "1"], _raw(a=_endpoint(), b=_endpoint()))
    with pytest.raises(ConfigValidationError) as excinfo:
        provider.load()
    (error,) = excinfo.value.errors
    assert error == "endpoint:b.token: same token as endpoint 'a'"


def test_a_token_never_appears_in_a_validation_message() -> None:
    bad = {"name": "x", "min_cvss": "high"}
    provider = _ScriptedProvider(
        ["1", "1"],
        _raw(a=_endpoint(body=bad, token=SECRET_TOKEN), b=_endpoint(token=TOKEN_B)),
    )
    with pytest.raises(ConfigValidationError) as excinfo:
        provider.load()
    assert SECRET_TOKEN not in str(excinfo.value)
    assert SECRET_TOKEN not in repr(excinfo.value.errors)


def test_a_token_in_an_invalid_token_value_is_not_quoted() -> None:
    provider = _ScriptedProvider(["1", "1"], _raw(a=_endpoint(token="short-secret!")))
    with pytest.raises(ConfigValidationError) as excinfo:
        provider.load()
    assert "short-secret" not in str(excinfo.value)
    assert excinfo.value.errors[0].startswith("endpoint:a.token: ")


def test_a_missing_schema_is_a_validation_error_not_unavailable() -> None:
    provider = _ScriptedProvider(["0", "0"], RawConfig(None, {}))
    with pytest.raises(ConfigValidationError, match="not seeded"):
        provider.load()


def test_a_snapshot_repr_hides_tokens() -> None:
    provider = _ScriptedProvider(["1", "1"], _raw(a=_endpoint(token=SECRET_TOKEN)))
    assert SECRET_TOKEN not in repr(provider.load())


# --- error mapping -----------------------------------------------------------


@pytest.fixture
def provider() -> Iterator[ValkeyConfigProvider]:
    client = Valkey()
    yield ValkeyConfigProvider(client, user="cvestream-daemon", address="valkey:6379")
    client.close()


@pytest.mark.parametrize(
    "exc",
    [
        AuthenticationError("wrong password: hunter2"),
        AuthorizationError("nope"),
    ],
)
def test_authentication_failures_are_access_denied(
    provider: ValkeyConfigProvider, exc: ValkeyError
) -> None:
    mapped = provider._map(exc)
    assert isinstance(mapped, ConfigAccessDeniedError)
    assert str(mapped) == "valkey: authentication failed for user 'cvestream-daemon'"


@pytest.mark.parametrize(
    "exc",
    [
        NoPermissionError("User x has no permissions to run the 'set' command"),
        ExecAbortError("Transaction discarded because of previous errors."),
        ResponseError("NOAUTH Authentication required."),
        ResponseError("WRONGPASS invalid username-password pair"),
    ],
)
def test_permission_failures_are_access_denied(
    provider: ValkeyConfigProvider, exc: ValkeyError
) -> None:
    mapped = provider._map(exc)
    assert isinstance(mapped, ConfigAccessDeniedError)
    assert "cvestream-daemon" in str(mapped)
    assert "has no permissions" not in str(mapped)


@pytest.mark.parametrize(
    ("exc", "name"),
    [
        (ValkeyConnectionError("Error 111 connecting to valkey:6379"), "Connection"),
        (ValkeyTimeoutError("Timeout reading from socket"), "Timeout"),
    ],
)
def test_network_failures_are_unavailable(
    provider: ValkeyConfigProvider, exc: ValkeyError, name: str
) -> None:
    mapped = provider._map(exc)
    assert type(mapped) is ConfigUnavailableError
    assert str(mapped) == f"valkey: cannot reach valkey:6379: {name}Error"


def test_busy_loading_is_unavailable_not_access_denied(
    provider: ValkeyConfigProvider,
) -> None:
    mapped = provider._map(BusyLoadingError("Valkey is loading the dataset"))
    assert type(mapped) is ConfigUnavailableError


def test_unknown_valkey_error_is_unavailable_and_not_forwarded(
    provider: ValkeyConfigProvider,
) -> None:
    mapped = provider._map(ValkeyError("secret-looking message"))
    assert type(mapped) is ConfigUnavailableError
    assert "secret-looking" not in str(mapped)


def test_wrongtype_is_a_validation_error_naming_the_key(
    provider: ValkeyConfigProvider,
) -> None:
    mapped = provider._map_response(
        ResponseError("WRONGTYPE Operation"), keys.ENDPOINTS
    )
    assert isinstance(mapped, ConfigValidationError)
    assert mapped.errors == ("cvestream:v1:endpoints: wrong type",)


def test_the_mapped_context_raises_from_none(provider: ValkeyConfigProvider) -> None:
    with pytest.raises(ConfigUnavailableError) as excinfo, provider._mapped():
        raise ValkeyConnectionError("password=hunter2")
    assert excinfo.value.__cause__ is None
    assert excinfo.value.__suppress_context__ is True
    assert "hunter2" not in str(excinfo.value)


def test_os_errors_are_unavailable(provider: ValkeyConfigProvider) -> None:
    with pytest.raises(ConfigUnavailableError, match="cannot reach valkey:6379"):
        with provider._mapped():
            raise ConnectionResetError("password=hunter2")


def test_a_failed_pipelined_command_names_its_key(
    provider: ValkeyConfigProvider,
) -> None:
    result = NoPermissionError("No permissions to access a key")
    with pytest.raises(ConfigAccessDeniedError, match="cvestream:v1:token:a"):
        provider._checked(result, keys.token("a"))
    assert provider._checked(b"ok", keys.SCHEMA) == b"ok"


@pytest.mark.parametrize("value", [b"12", None])
def test_revision_of_a_missing_key_is_zero(
    provider: ValkeyConfigProvider, value: bytes | None, monkeypatch: Any
) -> None:
    class _Client:
        def get(self, key: str) -> bytes | None:
            assert key == keys.REVISION
            return value

    monkeypatch.setattr(provider, "_client", _Client())
    assert provider.revision() == (value.decode() if value else "0")


def test_a_non_integer_revision_is_rejected(
    provider: ValkeyConfigProvider, monkeypatch: Any
) -> None:
    class _Client:
        def get(self, key: str) -> bytes:
            return b"abc"

    monkeypatch.setattr(provider, "_client", _Client())
    with pytest.raises(ConfigValidationError, match="not an integer"):
        provider.revision()


# --- construction ------------------------------------------------------------


def test_from_settings_reads_user_address_and_password() -> None:
    settings = ValkeySettings(
        url="valkey://cvestream-daemon@valkey.internal:6380/2",
        password=SecretStr("pw-secret"),
    )
    provider = ValkeyConfigProvider.from_settings(settings)
    try:
        assert provider._user == "cvestream-daemon"
        assert provider._address == "valkey.internal:6380"
        kwargs = provider._client.connection_pool.connection_kwargs
        assert kwargs["username"] == "cvestream-daemon"
        assert kwargs["password"] == "pw-secret"
        assert kwargs["db"] == 2
        assert kwargs["socket_timeout"] == 5.0
        assert kwargs["socket_connect_timeout"] == 5.0
        assert kwargs["decode_responses"] is False
        assert "pw-secret" not in repr(provider)
    finally:
        provider.close()


def test_from_settings_defaults() -> None:
    provider = ValkeyConfigProvider.from_settings(ValkeySettings(url="valkey://h"))
    try:
        assert provider._user == "default"
        assert provider._address == "h:6379"
    finally:
        provider.close()


def test_from_settings_without_url_is_a_validation_error() -> None:
    with pytest.raises(ConfigValidationError):
        ValkeyConfigProvider.from_settings(ValkeySettings())


def test_from_settings_with_tls_scheme() -> None:
    provider = ValkeyConfigProvider.from_settings(
        ValkeySettings(url="valkeys://u@h:6380")
    )
    try:
        kwargs = provider._client.connection_pool.connection_kwargs
        assert kwargs.get("ssl_cert_reqs", "required") == "required"
        assert provider._client.connection_pool.connection_class.__name__.startswith(
            "SSL"
        )
    finally:
        provider.close()
