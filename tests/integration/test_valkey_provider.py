"""The Valkey provider against a real server with the repo's ACL file.

Needs `VALKEY_TEST_ADDRESS` (see `valkey_support`); skipped without it, a
failure on CI.
"""

import json
from collections.abc import Iterator
from typing import Any

import pytest
from structlog.testing import capture_logs
from valkey import Valkey
from valkey.exceptions import (
    AuthenticationError,
    ExecAbortError,
    NoPermissionError,
)

from cvestream.config import keys
from cvestream.config.models import ConfigDocument, EndpointConfig, ProductEntryConfig
from cvestream.config.provider import (
    ConfigAccessDeniedError,
    ConfigSnapshot,
    ConfigValidationError,
)
from cvestream.config.valkey_provider import ValkeyConfigProvider
from cvestream.pipeline.run import reload_config
from tests.integration import valkey_support as support

pytestmark = [pytest.mark.valkey, pytest.mark.allow_hosts(support.ALLOWED_HOSTS)]

TOKEN_A = "a" * 43
TOKEN_B = "b" * 43
SECRET = "S" * 43


def _php(token: str = TOKEN_A) -> EndpointConfig:
    return EndpointConfig(
        id="team-php",
        name="PHP team",
        token=token,
        min_cvss=7.5,
        kev_only=False,
        products=[
            "php >= 8.2",
            ProductEntryConfig(name="symfony", aliases=["sf"], match_description=False),
        ],
    )


def _go() -> EndpointConfig:
    return EndpointConfig(id="team-go", name="Go", token=TOKEN_B, products=["go"])


def _document(*endpoints: EndpointConfig) -> ConfigDocument:
    return ConfigDocument(version=1, endpoints=list(endpoints))


@pytest.fixture
def daemon() -> Iterator[ValkeyConfigProvider]:
    provider = support.provider(support.DAEMON)
    yield provider
    provider.close()


@pytest.fixture
def writer() -> Iterator[ValkeyConfigProvider]:
    provider = support.provider(support.ADMIN)
    yield provider
    provider.close()


@pytest.fixture
def connector() -> Iterator[Valkey]:
    connection = support.client(support.CONNECTOR)
    yield connection
    connection.close()


@pytest.fixture
def daemon_client() -> Iterator[Valkey]:
    connection = support.client(support.DAEMON)
    yield connection
    connection.close()


def _seed(writer: ValkeyConfigProvider, *endpoints: EndpointConfig) -> str:
    return writer.update(lambda _: _document(*endpoints))


def _refused(operation: Any) -> None:
    with pytest.raises((NoPermissionError, ExecAbortError)):
        operation()


# --- 1, 2: authentication ----------------------------------------------------


def test_an_unauthenticated_client_is_refused(admin: Valkey) -> None:
    anonymous = Valkey(host=support.HOST, port=support.PORT, socket_timeout=5)
    try:
        with pytest.raises(AuthenticationError):  # NOAUTH
            anonymous.get(keys.REVISION)
    finally:
        anonymous.close()


def test_the_default_user_is_disabled(admin: Valkey) -> None:
    for secret in ("anything", ""):
        client = Valkey(
            host=support.HOST,
            port=support.PORT,
            username="default",
            password=secret,
            socket_timeout=5,
        )
        try:
            with pytest.raises(AuthenticationError):
                client.get(keys.REVISION)
        finally:
            client.close()


def test_a_wrong_password_is_access_denied(admin: Valkey) -> None:
    provider = support.provider(support.DAEMON, "not-the-password")
    try:
        with pytest.raises(ConfigAccessDeniedError) as excinfo:
            provider.load()
        with pytest.raises(ConfigAccessDeniedError):
            provider.revision()
    finally:
        provider.close()
    assert str(excinfo.value) == (
        "valkey: authentication failed for user 'cvestream-daemon'"
    )
    assert "not-the-password" not in str(excinfo.value)


def test_an_unknown_user_is_access_denied(admin: Valkey) -> None:
    provider = support.provider("nobody", "x")
    try:
        with pytest.raises(ConfigAccessDeniedError):
            provider.revision()
    finally:
        provider.close()


# --- 3, 4, 5: the ACL users --------------------------------------------------


def test_the_daemon_reads_the_namespace_and_writes_nothing(
    admin: Valkey, daemon_client: Valkey, writer: ValkeyConfigProvider
) -> None:
    _seed(writer, _php())
    assert daemon_client.get(keys.REVISION) is not None
    assert support.members(daemon_client, keys.ENDPOINTS) == {b"team-php"}
    assert daemon_client.get(keys.token("team-php")) == TOKEN_A.encode()
    _refused(lambda: daemon_client.set(keys.REVISION, "1"))
    _refused(lambda: daemon_client.delete(keys.SCHEMA))
    _refused(lambda: daemon_client.sadd(keys.ENDPOINTS, "x"))
    _refused(lambda: daemon_client.incr(keys.REVISION))
    _refused(lambda: daemon_client.execute_command("MULTI"))
    _refused(lambda: daemon_client.get("other-key"))
    _refused(lambda: daemon_client.keys("*"))


def test_the_admin_writes_the_namespace_and_nothing_else(admin: Valkey) -> None:
    admin.set(keys.SCHEMA, "1")
    admin.sadd(keys.ENDPOINTS, "x")
    assert admin.incr(keys.REVISION) == 1
    admin.delete(keys.SCHEMA)
    _refused(lambda: admin.set("other", "1"))
    _refused(lambda: admin.get("other"))
    _refused(lambda: admin.set("cvestream:v2:schema", "1"))
    _refused(lambda: admin.flushall())
    _refused(lambda: admin.config_get("*"))
    _refused(lambda: admin.keys("*"))
    _refused(lambda: admin.scan(0))


def test_the_connector_edits_products_and_the_revision_only(
    admin: Valkey, connector: Valkey, writer: ValkeyConfigProvider
) -> None:
    _seed(writer, _php())
    assert connector.get(keys.SCHEMA) == b"1"
    assert support.members(connector, keys.ENDPOINTS) == {b"team-php"}
    assert json.loads(support.get(connector, keys.endpoint("team-php")) or b"")["name"]
    assert connector.get(keys.products("team-php")) is not None
    assert connector.get(keys.REVISION) is not None
    assert connector.set(keys.products("team-php"), "[]")
    assert int(support.get(connector, keys.REVISION) or 0) >= 0
    connector.incr(keys.REVISION)

    # the full write protocol works
    with connector.pipeline() as pipe:
        pipe.watch(keys.REVISION)
        pipe.multi()
        pipe.set(keys.products("team-php"), '["go"]')
        pipe.incr(keys.REVISION)
        pipe.execute()
    assert admin.get(keys.products("team-php")) == b'["go"]'


def test_the_connector_cannot_touch_tokens_or_endpoints(
    admin: Valkey, connector: Valkey, writer: ValkeyConfigProvider
) -> None:
    _seed(writer, _php())
    token = keys.token("team-php")
    _refused(lambda: connector.get(token))
    _refused(lambda: connector.mget([token]))
    _refused(lambda: connector.set(token, "x" * 43))
    _refused(lambda: connector.set(keys.endpoint("team-php"), "{}"))
    _refused(lambda: connector.sadd(keys.ENDPOINTS, "x"))
    _refused(lambda: connector.delete(keys.products("team-php")))
    _refused(lambda: connector.set(keys.SCHEMA, "2"))
    _refused(lambda: connector.set("other", "1"))
    assert admin.get(token) == TOKEN_A.encode()


def test_a_token_write_queued_in_a_transaction_fails_the_transaction(
    admin: Valkey, connector: Valkey, writer: ValkeyConfigProvider
) -> None:
    _seed(writer, _php())
    before = admin.get(keys.REVISION)

    def attempt() -> None:
        with connector.pipeline() as pipe:
            pipe.watch(keys.REVISION)
            pipe.multi()
            pipe.set(keys.products("team-php"), '["evil"]')
            pipe.set(keys.token("team-php"), "stolen")
            pipe.incr(keys.REVISION)
            pipe.execute()

    _refused(attempt)
    assert admin.get(keys.token("team-php")) == TOKEN_A.encode()
    assert admin.get(keys.products("team-php")) != b'["evil"]'
    assert admin.get(keys.REVISION) == before


# --- 6: the provider end to end ----------------------------------------------


def test_the_daemon_loads_what_the_admin_wrote(
    admin: Valkey, daemon: ValkeyConfigProvider, writer: ValkeyConfigProvider
) -> None:
    revision = _seed(writer, _php(), _go())
    snapshot = daemon.load()
    assert snapshot.revision == revision == daemon.revision()
    assert [e.id for e in snapshot.endpoints] == ["team-go", "team-php"]
    php = next(e for e in snapshot.endpoints if e.id == "team-php")
    assert php.name == "PHP team"
    assert php.rules.min_cvss == 7.5
    assert [p.name for p in php.products] == ["php", "symfony"]
    assert php.products[0].min_version is not None
    assert php.products[1].aliases == ("sf",)
    assert php.products[1].match_description is False
    assert snapshot.tokens == {TOKEN_A: "team-php", TOKEN_B: "team-go"}
    assert snapshot.endpoint_for_token(TOKEN_B) is not None


def test_the_stored_layout_is_the_documented_contract(
    admin: Valkey, writer: ValkeyConfigProvider
) -> None:
    _seed(writer, _php())
    assert admin.get(keys.SCHEMA) == b"1"
    assert admin.get(keys.REVISION) == b"1"
    assert support.members(admin, keys.ENDPOINTS) == {b"team-php"}
    assert admin.get(keys.token("team-php")) == TOKEN_A.encode()  # raw, not JSON
    body = json.loads(support.get(admin, keys.endpoint("team-php")) or b"")
    assert body == {
        "enabled": True,
        "kev_only": False,
        "min_cvss": 7.5,
        "name": "PHP team",
    }
    products = json.loads(support.get(admin, keys.products("team-php")) or b"")
    assert products[0] == "php >= 8.2"
    assert products[1]["name"] == "symfony"


def test_the_revision_of_an_empty_store_is_zero(
    admin: Valkey, daemon: ValkeyConfigProvider
) -> None:
    assert daemon.revision() == "0"


def test_the_daemon_cannot_write(admin: Valkey, daemon: ValkeyConfigProvider) -> None:
    with pytest.raises(ConfigAccessDeniedError):
        daemon.update(lambda _: _document(_php()))
    assert admin.get(keys.REVISION) is None


# --- 7: hot reload through a connector ---------------------------------------


def test_a_connector_edit_is_loaded_and_an_invalid_one_is_rejected(
    admin: Valkey,
    connector: Valkey,
    daemon: ValkeyConfigProvider,
    writer: ValkeyConfigProvider,
) -> None:
    _seed(writer, _php())
    snapshot = daemon.load()
    # a valid edit: new products, revision bumped
    connector.set(keys.products("team-php"), '["go", "rust >= 1.80"]')
    connector.incr(keys.REVISION)
    assert daemon.revision() != snapshot.revision
    reloaded = reload_config(daemon, snapshot)
    assert reloaded is not snapshot
    (endpoint,) = reloaded.endpoints
    assert [p.name for p in endpoint.products] == ["go", "rust"]
    # an invalid edit: the key is named, the last valid snapshot is kept
    connector.set(keys.products("team-php"), "[oops, " + SECRET)
    connector.incr(keys.REVISION)
    with pytest.raises(ConfigValidationError) as excinfo:
        daemon.load()
    assert excinfo.value.errors == (
        "cvestream:v1:endpoint:team-php:products: invalid JSON",
    )
    assert SECRET not in str(excinfo.value)
    with capture_logs() as logs:
        kept = reload_config(daemon, reloaded)
    assert kept is reloaded
    (entry,) = [e for e in logs if e["event"] == "config.rejected"]
    assert entry["errors"] == ["cvestream:v1:endpoint:team-php:products: invalid JSON"]


def test_a_forgotten_revision_is_picked_up_by_the_forced_reload(
    admin: Valkey,
    connector: Valkey,
    daemon: ValkeyConfigProvider,
    writer: ValkeyConfigProvider,
) -> None:
    _seed(writer, _php())
    snapshot = daemon.load()
    connector.set(keys.products("team-php"), '["go"]')  # no INCR: a buggy writer
    assert reload_config(daemon, snapshot) is snapshot
    forced = reload_config(daemon, snapshot, force=True)
    assert [p.name for p in forced.endpoints[0].products] == ["go"]


# --- 8, 9, 10: write conflicts, validation, unseeded store -------------------


def test_a_concurrent_writer_makes_update_retry(
    admin: Valkey, writer: ValkeyConfigProvider
) -> None:
    _seed(writer, _php())
    before = int(support.get(admin, keys.REVISION) or 0)
    rival = support.client(support.ADMIN)
    calls: list[int] = []

    def change(document: ConfigDocument) -> ConfigDocument:
        calls.append(1)
        if len(calls) == 1:
            rival.incr(keys.REVISION)  # between WATCH and EXEC
        return _document(*document.endpoints, _go())

    try:
        revision = writer.update(change)
    finally:
        rival.close()
    assert len(calls) == 2
    assert revision == str(before + 2)
    assert admin.get(keys.REVISION) == str(before + 2).encode()
    assert support.members(admin, keys.ENDPOINTS) == {b"team-php", b"team-go"}


def test_update_gives_up_after_five_conflicts(
    admin: Valkey, writer: ValkeyConfigProvider
) -> None:
    from cvestream.config.provider import ConfigConflictError

    rival = support.client(support.ADMIN)
    calls: list[int] = []

    def change(document: ConfigDocument) -> ConfigDocument:
        calls.append(1)
        rival.incr(keys.REVISION)
        return _document(_php())

    try:
        with pytest.raises(ConfigConflictError, match="gave up after 5 attempts"):
            writer.update(change)
    finally:
        rival.close()
    assert len(calls) == 5
    assert support.members(admin, keys.ENDPOINTS) == set()


def test_an_invalid_update_writes_nothing(
    admin: Valkey, daemon: ValkeyConfigProvider, writer: ValkeyConfigProvider
) -> None:
    revision = _seed(writer, _php())
    duplicate = EndpointConfig(id="team-go", name="Go", token=TOKEN_A)
    with pytest.raises(ConfigValidationError) as excinfo:
        writer.update(
            lambda doc: ConfigDocument.model_construct(
                version=1, endpoints=[*doc.endpoints, duplicate]
            )
        )
    assert "same token" in str(excinfo.value)
    assert TOKEN_A not in str(excinfo.value)
    assert admin.get(keys.REVISION) == revision.encode()
    assert support.members(admin, keys.ENDPOINTS) == {b"team-php"}
    assert daemon.load().revision == revision


def test_an_unseeded_store_is_rejected(
    admin: Valkey, daemon: ValkeyConfigProvider
) -> None:
    with pytest.raises(ConfigValidationError) as excinfo:
        daemon.load()
    assert "not seeded" in str(excinfo.value)


def test_the_first_update_seeds_the_store(
    admin: Valkey, daemon: ValkeyConfigProvider, writer: ValkeyConfigProvider
) -> None:
    assert writer.update(lambda doc: doc) == "1"
    snapshot = daemon.load()
    assert snapshot.endpoints == ()
    assert snapshot.revision == "1"


def test_removing_an_endpoint_deletes_its_keys(
    admin: Valkey, daemon: ValkeyConfigProvider, writer: ValkeyConfigProvider
) -> None:
    _seed(writer, _php(), _go())
    writer.update(
        lambda doc: _document(*(e for e in doc.endpoints if e.id != "team-go"))
    )
    assert support.members(admin, keys.ENDPOINTS) == {b"team-php"}
    for key in (
        keys.endpoint("team-go"),
        keys.products("team-go"),
        keys.token("team-go"),
    ):
        assert admin.get(key) is None
    assert [e.id for e in daemon.load().endpoints] == ["team-php"]


def test_a_store_changed_by_hand_is_read_back_by_update(
    admin: Valkey, writer: ValkeyConfigProvider
) -> None:
    _seed(writer, _php())
    admin.set(keys.products("team-php"), '["go"]')
    seen: list[ConfigDocument] = []

    def change(document: ConfigDocument) -> ConfigDocument:
        seen.append(document)
        return document

    writer.update(change)
    assert seen[0].endpoints[0].products == ["go"]


def test_a_wrong_type_names_the_key(
    admin: Valkey, daemon: ValkeyConfigProvider, writer: ValkeyConfigProvider
) -> None:
    _seed(writer, _php())
    admin.delete(keys.endpoint("team-php"))
    admin.sadd(keys.endpoint("team-php"), "member")  # a set where a string belongs
    with pytest.raises(ConfigValidationError) as excinfo:
        daemon.load()
    assert excinfo.value.errors == ("cvestream:v1:endpoint:team-php: wrong type",)


def test_an_invalid_endpoint_id_in_the_set_is_rejected(
    admin: Valkey, daemon: ValkeyConfigProvider, writer: ValkeyConfigProvider
) -> None:
    _seed(writer, _php())
    admin.sadd(keys.ENDPOINTS, "Not A Slug")
    with pytest.raises(ConfigValidationError, match="not a valid endpoint id"):
        daemon.load()


def test_the_snapshot_matches_what_the_yaml_provider_would_build(
    admin: Valkey, daemon: ValkeyConfigProvider, writer: ValkeyConfigProvider
) -> None:
    from cvestream.config.provider import build_snapshot

    document = _document(_php(), _go())
    writer.update(lambda _: document)
    expected: ConfigSnapshot = build_snapshot(
        document.model_dump(mode="json"), revision="1", source="yaml"
    )
    loaded = daemon.load()
    assert set(loaded.endpoints) == set(expected.endpoints)
    assert dict(loaded.tokens) == dict(expected.tokens)
