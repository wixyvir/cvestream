"""Valkey provider logic that needs no server (the server tests are marked `valkey`)."""

import json
import ssl
from collections.abc import Iterator
from typing import Any

import pytest
from pydantic import SecretStr
from valkey import Valkey
from valkey.connection import SSLConnection
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
from valkey.exceptions import ConnectionError as ValkeyConnectionError
from valkey.exceptions import TimeoutError as ValkeyTimeoutError

from cvestream.config import keys
from cvestream.config.models import ConfigDocument, EndpointConfig
from cvestream.config.provider import (
    ConfigAccessDeniedError,
    ConfigConflictError,
    ConfigError,
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
        assert kwargs["ssl_cert_reqs"] == "required"
        # valkey-py's default is False: the host name must be checked too.
        assert kwargs["ssl_check_hostname"] is True
        assert provider._client.connection_pool.connection_class.__name__.startswith(
            "SSL"
        )
    finally:
        provider.close()


def test_tls_connections_check_the_host_name() -> None:
    """The SSL context of a real connection object verifies chain and name."""
    provider = ValkeyConfigProvider.from_settings(
        ValkeySettings(url="valkeys://u@h:6380")
    )
    try:
        connection = provider._client.connection_pool.make_connection()
        assert isinstance(connection, SSLConnection)
        assert connection.check_hostname is True
        assert connection.cert_reqs == ssl.CERT_REQUIRED
    finally:
        provider.close()


def test_plain_connections_take_no_tls_options() -> None:
    provider = ValkeyConfigProvider.from_settings(
        ValkeySettings(url="valkey://u@h:6379")
    )
    try:
        kwargs = provider._client.connection_pool.connection_kwargs
        assert not any(name.startswith("ssl_") for name in kwargs)
        # Building a plain connection must not choke on a stray `ssl_*` argument.
        provider._client.connection_pool.make_connection()
    finally:
        provider.close()


# --- the write protocol on a fake client ---------------------------------------


class _FakePipeline:
    """A just-enough WATCH/MULTI/EXEC pipeline over a dict."""

    def __init__(self, client: _FakeClient) -> None:
        self._client = client
        self.queued: list[tuple[str, tuple[Any, ...]]] = []
        self.multi_started = False

    def __enter__(self) -> _FakePipeline:
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def watch(self, *_: str) -> None:
        self._client.commands.append("watch")

    def get(self, key: str) -> bytes | None:
        self._client.commands.append(f"get {key}")
        return self._client.strings.get(key)

    def smembers(self, key: str) -> list[bytes]:
        self._client.commands.append(f"smembers {key}")
        return [
            m.encode() if isinstance(m, str) else m
            for m in self._client.sets.get(key, [])
        ]

    def multi(self) -> None:
        self.multi_started = True

    def _queue(self, name: str, *args: Any) -> None:
        assert self.multi_started
        self.queued.append((name, args))

    def set(self, key: str, value: str) -> None:
        self._queue("set", key, value)

    def sadd(self, key: str, *members: str) -> None:
        self._queue("sadd", key, *members)

    def srem(self, key: str, *members: str) -> None:
        self._queue("srem", key, *members)

    def delete(self, *names: str) -> None:
        self._queue("delete", *names)

    def incr(self, key: str) -> None:
        self._queue("incr", key)

    def execute(self) -> list[object]:
        client = self._client
        client.executions += 1
        if client.execute_errors:
            raise client.execute_errors.pop(0)
        results: list[object] = []
        for name, args in self.queued:
            client.commands.append(f"{name} {' '.join(map(str, args))}")
            if name == "set":
                client.strings[args[0]] = args[1].encode()
            elif name == "sadd":
                client.sets.setdefault(args[0], set()).update(args[1:])
            elif name == "srem":
                client.sets.get(args[0], set()).difference_update(args[1:])
            elif name == "delete":
                for key in args:
                    client.strings.pop(key, None)
                    client.sets.pop(key, None)
            elif name == "incr":
                value = int(client.strings.get(args[0], b"0")) + 1
                client.strings[args[0]] = str(value).encode()
                results.append(value)
                continue
            results.append(1)
        return results


class _FakeClient:
    def __init__(self) -> None:
        self.strings: dict[str, bytes] = {}
        self.sets: dict[str, set[Any]] = {}
        self.commands: list[str] = []
        self.executions = 0
        self.execute_errors: list[Exception] = []

    def pipeline(self, transaction: bool = True) -> _FakePipeline:
        return _FakePipeline(self)


def _fake_provider() -> tuple[ValkeyConfigProvider, _FakeClient]:
    client = _FakeClient()
    return ValkeyConfigProvider(client, user="cvestream-admin"), client  # ty: ignore[invalid-argument-type]


def _seed(client: _FakeClient, **tokens: str) -> None:
    client.strings[keys.SCHEMA] = b"1"
    client.strings[keys.REVISION] = b"4"
    client.sets[keys.ENDPOINTS] = set(tokens)
    for endpoint_id, token in tokens.items():
        client.strings[keys.endpoint(endpoint_id)] = _json({"name": endpoint_id})
        client.strings[keys.products(endpoint_id)] = _json(["php"])
        client.strings[keys.token(endpoint_id)] = token.encode()


def _document(*ids: str) -> ConfigDocument:
    return ConfigDocument(
        version=1,
        endpoints=[
            EndpointConfig(id=endpoint_id, name=endpoint_id.upper(), token=str(n) * 43)
            for n, endpoint_id in enumerate(ids, start=1)
        ],
    )


def _rename(document: ConfigDocument) -> ConfigDocument:
    first = document.endpoints[0].model_copy(update={"name": "Renamed"})
    return document.model_copy(update={"endpoints": [first, *document.endpoints[1:]]})


def test_update_seeds_an_unseeded_store() -> None:
    provider, client = _fake_provider()
    revision = provider.update(
        lambda doc: doc.model_copy(update={"endpoints": _document("x").endpoints})
    )
    assert revision == "1"
    assert client.strings[keys.SCHEMA] == b"1"
    assert client.sets[keys.ENDPOINTS] == {"x"}
    assert client.strings[keys.token("x")] == b"1" * 43


def test_update_rewrites_and_grows_the_revision_by_one() -> None:
    provider, client = _fake_provider()
    _seed(client, a=TOKEN_A)
    assert provider.update(_rename) == "5"
    assert json.loads(client.strings[keys.endpoint("a")])["name"] == "Renamed"


def test_update_refuses_an_invalid_store_and_writes_nothing() -> None:
    provider, client = _fake_provider()
    _seed(client, a=TOKEN_A)
    client.strings[keys.products("a")] = b"{not json"
    with pytest.raises(ConfigValidationError):
        provider.update(_rename)
    assert client.executions == 0


@pytest.mark.parametrize(
    "error",
    [
        ConfigError("nope"),
        ValueError("nope"),
        OSError("nope"),
        ValkeyConnectionError("nope"),
        ResponseError("NOPERM nope"),
    ],
    ids=["config", "value", "os", "valkey-connection", "valkey-response"],
)
def test_an_error_raised_by_change_reaches_the_caller_unchanged(
    error: Exception,
) -> None:
    provider, client = _fake_provider()
    _seed(client, a=TOKEN_A)

    def change(_: ConfigDocument) -> ConfigDocument:
        raise error

    with pytest.raises(type(error)) as excinfo:
        provider.update(change)
    assert excinfo.value is error
    assert client.executions == 0


def test_replace_writes_a_document_over_an_invalid_store() -> None:
    provider, client = _fake_provider()
    _seed(client, a=TOKEN_A, b=TOKEN_B)
    client.strings[keys.products("a")] = b"{not json"
    client.strings[keys.endpoint("b")] = b"[]"
    client.sets[keys.ENDPOINTS].add("Not A Slug")
    client.sets[keys.ENDPOINTS].add(b"\xff")

    revision = provider.replace(_document("a", "c"))

    assert revision == "5"
    assert client.sets[keys.ENDPOINTS] == {"a", "c"}
    assert keys.endpoint("b") not in client.strings
    assert keys.products("b") not in client.strings
    assert keys.token("b") not in client.strings
    assert json.loads(client.strings[keys.products("a")]) == []
    assert client.strings[keys.token("c")] == b"2" * 43
    # the stored values were never read
    assert not [c for c in client.commands if c.startswith("get ")]


def test_replace_seeds_an_unseeded_store() -> None:
    provider, client = _fake_provider()
    assert provider.replace(_document("x")) == "1"
    assert client.strings[keys.SCHEMA] == b"1"
    assert client.sets[keys.ENDPOINTS] == {"x"}


def test_replace_with_no_endpoint_empties_the_store() -> None:
    provider, client = _fake_provider()
    _seed(client, a=TOKEN_A)
    provider.replace(ConfigDocument(version=1, endpoints=[]))
    assert keys.ENDPOINTS not in client.sets
    assert keys.endpoint("a") not in client.strings
    assert client.strings[keys.REVISION] == b"5"


def test_replace_validates_before_connecting() -> None:
    provider, client = _fake_provider()
    duplicate = _document("a", "b")
    duplicate.endpoints[1].token = duplicate.endpoints[0].token
    with pytest.raises(ConfigValidationError, match="same token"):
        provider.replace(duplicate)
    assert client.commands == []


def test_replace_only_uses_commands_of_the_admin_acl() -> None:
    provider, client = _fake_provider()
    _seed(client, a=TOKEN_A)
    provider.replace(_document("a", "b"))
    used = {c.split()[0] for c in client.commands}
    assert used <= {"watch", "smembers", "set", "sadd", "delete", "incr"}


def test_a_lost_race_is_retried() -> None:
    provider, client = _fake_provider()
    _seed(client, a=TOKEN_A)
    client.execute_errors = [WatchError("Watched variable changed.")]
    assert provider.update(_rename) == "5"
    assert client.executions == 2


def test_replace_retries_a_lost_race() -> None:
    provider, client = _fake_provider()
    client.execute_errors = [WatchError("Watched variable changed.")] * 2
    assert provider.replace(_document("x")) == "1"
    assert client.executions == 3


def test_endless_lost_races_give_up() -> None:
    provider, client = _fake_provider()
    _seed(client, a=TOKEN_A)
    client.execute_errors = [WatchError("Watched variable changed.")] * 5
    with pytest.raises(ConfigConflictError, match="gave up after 5 attempts"):
        provider.update(_rename)
    assert client.executions == 5


@pytest.mark.parametrize(
    ("cause", "name"),
    [
        (ValkeyConnectionError("secret-host dropped"), "ConnectionError"),
        (ValkeyTimeoutError("slow"), "TimeoutError"),
    ],
)
def test_a_connection_lost_while_watching_is_unavailable_not_a_conflict(
    cause: Exception, name: str
) -> None:
    provider, client = _fake_provider()
    _seed(client, a=TOKEN_A)

    def drop() -> WatchError:
        try:
            try:
                raise cause
            except ValkeyError:
                raise WatchError(
                    "A ConnectionError occurred on while watching one or more keys"
                ) from None
        except WatchError as exc:
            return exc

    client.execute_errors = [drop()]
    with pytest.raises(ConfigUnavailableError) as excinfo:
        provider.update(_rename)
    assert not isinstance(excinfo.value, ConfigConflictError)
    assert str(excinfo.value) == f"valkey: cannot reach valkey:6379: {name}"
    assert client.executions == 1


def test_document_is_the_validated_stored_document() -> None:
    provider = _ScriptedProvider(["3", "3"], _raw(a=_endpoint()))
    document = provider.document()
    assert [e.id for e in document.endpoints] == ["a"]
    assert document.endpoints[0].token == TOKEN_A


def test_document_retries_like_load() -> None:
    provider = _ScriptedProvider(["1", "2", "2", "2"], _raw(a=_endpoint()))
    provider.document()
    assert provider.raw_reads == 2


def test_document_names_the_endpoint_in_model_errors() -> None:
    provider = _ScriptedProvider(
        ["1", "1"], _raw(a=_endpoint(body={"name": "x", "min_cvss": 11}))
    )
    with pytest.raises(ConfigValidationError) as excinfo:
        provider.document()
    assert excinfo.value.errors[0].startswith("endpoint:a.min_cvss: ")


def test_document_of_an_unseeded_store_is_a_validation_error() -> None:
    provider = _ScriptedProvider(["0", "0"], RawConfig(None, {}))
    with pytest.raises(ConfigValidationError, match="not seeded"):
        provider.document()
