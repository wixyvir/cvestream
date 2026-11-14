"""The `cvestream-ui` ACL user (story 3.4) against a real server.

Proves each grant and each refusal of the administration-UI user, and the two
safety nets for UI writes (the forced reload, `config.rejected`). Needs
`VALKEY_TEST_ADDRESS` (see `valkey_support`); skipped without it, a failure on
CI.
"""

from collections.abc import Iterator
from typing import Any

import pytest
from structlog.testing import capture_logs
from valkey import Valkey
from valkey.exceptions import ExecAbortError, NoPermissionError, ResponseError

from cvestream.config import keys
from cvestream.config.provider import ConfigUnavailableError, ConfigValidationError
from cvestream.config.valkey_provider import ValkeyConfigProvider
from cvestream.pipeline.run import reload_config
from tests.integration import valkey_support as support
from tests.integration.test_valkey_provider import _document, _php, _seed

pytestmark = [pytest.mark.valkey, pytest.mark.allow_hosts(support.ALLOWED_HOSTS)]

# A key of the namespace that no test seeds: commands that take a second key
# name it, so that only the command, never the key pattern, can refuse them.
SPARE_KEY = "cvestream:v1:spare"

# Every command the UI user must never run (AC 1 and the Dev Notes never-grant
# list), as raw argument lists. `execute_command` is used throughout because
# valkey-py has no method for many of them.
FORBIDDEN_CALLS: list[tuple[str, ...]] = [
    ("KEYS", "*"),
    ("FLUSHALL",),
    ("FLUSHDB",),
    ("CONFIG", "GET", "*"),
    ("ACL", "LIST"),
    ("MODULE", "LIST"),
    ("EVAL", "return 1", "0"),
    ("EVAL_RO", "return 1", "0"),
    ("EVALSHA", "0" * 64, "0"),
    ("EVALSHA_RO", "0" * 64, "0"),
    ("SCRIPT", "LOAD", "return 1"),
    ("FUNCTION", "LIST"),
    ("FCALL", "f", "0"),
    ("FCALL_RO", "f", "0"),
    ("SHUTDOWN",),
    ("REPLICAOF", "NO", "ONE"),
    ("SLAVEOF", "NO", "ONE"),
    ("MIGRATE", "127.0.0.1", "6379", keys.SCHEMA, "0", "5000"),
    ("MONITOR",),
    ("SLOWLOG", "GET"),
    ("LATENCY", "HISTORY", "x"),
    ("CLIENT", "LIST"),
    ("CLIENT", "KILL", "TYPE", "normal"),
    ("CLIENT", "NO-EVICT", "on"),
    ("RENAME", keys.SCHEMA, SPARE_KEY),
    ("RENAMENX", keys.SCHEMA, SPARE_KEY),
    ("MOVE", keys.SCHEMA, "1"),
    ("COPY", keys.SCHEMA, SPARE_KEY),
    ("RESTORE", SPARE_KEY, "0", ""),
    ("DUMP", keys.SCHEMA),
    ("SWAPDB", "0", "1"),
    ("EXPIRE", keys.SCHEMA, "10"),
    ("PEXPIRE", keys.SCHEMA, "10"),
    ("EXPIREAT", keys.SCHEMA, "9999999999"),
    ("PEXPIREAT", keys.SCHEMA, "9999999999"),
    ("PERSIST", keys.SCHEMA),
    ("GETEX", keys.SCHEMA),
    ("SAVE",),
    ("BGSAVE",),
    ("BGREWRITEAOF",),
    ("CLUSTER", "INFO"),
    ("FAILOVER",),
    ("SYNC",),
    ("PSYNC", "?", "-1"),
    ("REPLCONF", "listening-port", "0"),
]


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


def _clean_namespace(connection: Valkey) -> None:
    """`SCAN cvestream:v1:*` + `DEL`: residue of a crashed test would break the
    `SCAN` grant assertion, and the admin cleanup has no `SCAN`."""
    cursor = 0
    while True:
        scanned = connection.scan(cursor, match="cvestream:v1:*")
        assert isinstance(scanned, tuple)  # valkey-py's stubs include Awaitable
        cursor, found = scanned
        if found:
            connection.delete(*found)
        if cursor == 0:
            return


@pytest.fixture
def ui(admin: Valkey) -> Iterator[Valkey]:
    """A `cvestream-ui` client; the namespace is empty before and after."""
    connection = support.client(support.UI)
    _clean_namespace(connection)
    yield connection
    _clean_namespace(connection)
    connection.close()


def _refused(operation: Any) -> None:
    with pytest.raises((NoPermissionError, ExecAbortError)):
        operation()


def _command_refused(operation: Any) -> None:
    """Refused for the command itself, not for a key it names."""
    with pytest.raises(NoPermissionError, match="no permissions to run the"):
        operation()


def _scan_all(connection: Valkey, match: str) -> set[bytes]:
    found: set[bytes] = set()
    cursor = 0
    while True:
        scanned = connection.scan(cursor, match=match)
        assert isinstance(scanned, tuple)  # valkey-py's stubs include Awaitable
        cursor, names = scanned
        found |= set(names)
        if cursor == 0:
            return found


# --- grants -------------------------------------------------------------------


def test_the_ui_reads_and_writes_the_whole_namespace(
    admin: Valkey, ui: Valkey, writer: ValkeyConfigProvider
) -> None:
    _seed(writer, _php())
    token = keys.token("team-php")
    assert ui.ping()
    assert ui.execute_command("SELECT", "0")
    # strings, tokens included
    assert ui.get(keys.SCHEMA) == b"1"
    value = support.get(ui, token)
    assert value is not None
    assert ui.mget([keys.SCHEMA, token]) == [b"1", value]
    assert ui.exists(keys.SCHEMA, token) == 2
    ui.set(keys.SCHEMA, "1")
    ui.delete(keys.SCHEMA)
    admin.set(keys.SCHEMA, "1")
    ui.set(token, "t" * 43)
    assert admin.get(token) == b"t" * 43
    ui.delete(token)
    assert admin.get(token) is None
    ui.set(token, value)
    # sets
    ui.sadd(keys.ENDPOINTS, "x")
    assert support.members(ui, keys.ENDPOINTS) == {b"team-php", b"x"}
    ui.srem(keys.ENDPOINTS, "x")
    # the revision
    before = int(support.get(ui, keys.REVISION) or 0)
    assert ui.incr(keys.REVISION) == before + 1


def test_the_ui_runs_every_browse_command_of_its_acl_line(
    admin: Valkey, ui: Valkey, writer: ValkeyConfigProvider
) -> None:
    _seed(writer, _php())
    assert _scan_all(ui, "cvestream:v1:*") == {
        keys.SCHEMA.encode(),
        keys.REVISION.encode(),
        keys.ENDPOINTS.encode(),
        keys.endpoint("team-php").encode(),
        keys.products("team-php").encode(),
        keys.token("team-php").encode(),
    }
    assert ui.type(keys.SCHEMA) == b"string"
    assert ui.type(keys.ENDPOINTS) == b"set"
    assert ui.ttl(keys.SCHEMA) == -1
    assert ui.pttl(keys.SCHEMA) == -1
    assert ui.strlen(keys.SCHEMA) == 1
    assert ui.getrange(keys.SCHEMA, 0, -1) == b"1"
    assert ui.scard(keys.ENDPOINTS) == 1
    assert ui.sismember(keys.ENDPOINTS, "team-php")
    sscanned: set[bytes] = set()
    cursor = 0
    while True:
        scanned = ui.sscan(keys.ENDPOINTS, cursor)
        assert isinstance(scanned, tuple)  # valkey-py's stubs include Awaitable
        cursor, members = scanned
        sscanned |= set(members)
        if cursor == 0:
            break
    assert sscanned == {b"team-php"}
    assert isinstance(ui.dbsize(), int)
    info = ui.info()  # parsed by valkey-py into a dict
    assert isinstance(info, dict)
    assert "valkey_version" in info or "redis_version" in info
    assert isinstance(
        ui.execute_command("MEMORY", "USAGE", keys.SCHEMA, "SAMPLES", "0"), int
    )
    ui.execute_command("CLIENT", "SETNAME", "cvestream-ui-test")
    ui.execute_command("CLIENT", "SETINFO", "LIB-NAME", "cvestream-test")


def test_the_full_write_protocol_works_as_the_ui(
    admin: Valkey, ui: Valkey, writer: ValkeyConfigProvider
) -> None:
    _seed(writer, _php())
    with ui.pipeline() as pipe:
        pipe.watch(keys.REVISION)
        pipe.multi()
        pipe.set(keys.products("team-php"), '["go"]')
        pipe.incr(keys.REVISION)
        pipe.execute()
    assert admin.get(keys.products("team-php")) == b'["go"]'


def test_the_ui_can_abandon_a_transaction(
    admin: Valkey, ui: Valkey, writer: ValkeyConfigProvider
) -> None:
    # One raw connection: the transaction state lives on it, and the client's
    # reply callbacks would turn `QUEUED` into `False`.
    _seed(writer, _php())
    before = admin.get(keys.REVISION)
    connection = ui.connection_pool.get_connection("MULTI")
    try:
        for command, reply in (
            (("WATCH", keys.REVISION), b"OK"),
            (("UNWATCH",), b"OK"),
            (("MULTI",), b"OK"),
            (("SET", keys.products("team-php"), '["go"]'), b"QUEUED"),
            (("DISCARD",), b"OK"),
        ):
            connection.send_command(*command)
            assert connection.read_response() == reply, command
    finally:
        ui.connection_pool.release(connection)
    assert admin.get(keys.products("team-php")) != b'["go"]'
    assert admin.get(keys.REVISION) == before


def test_set_with_an_expiry_is_granted_despite_the_expire_ban(
    admin: Valkey, ui: Valkey, writer: ValkeyConfigProvider
) -> None:
    # An ACL cannot filter arguments: `SET ... EX` gives a key a TTL although
    # `EXPIRE` and friends are refused (docs/operations.md says never to).
    _seed(writer, _php())
    products = keys.products("team-php")
    ui.set(products, '["go"]', ex=3600)
    ttl = ui.ttl(products)
    assert isinstance(ttl, int)  # valkey-py's stubs include Awaitable
    assert 0 < ttl <= 3600
    _command_refused(lambda: ui.execute_command("PERSIST", products))
    ui.set(products, '["go"]')  # a plain SET clears the TTL
    assert ui.ttl(products) == -1


# --- refusals -----------------------------------------------------------------


@pytest.mark.parametrize("call", FORBIDDEN_CALLS, ids=lambda c: " ".join(c))
def test_the_ui_is_refused_every_forbidden_command(
    admin: Valkey, ui: Valkey, writer: ValkeyConfigProvider, call: tuple[str, ...]
) -> None:
    _seed(writer, _php())
    _command_refused(lambda: ui.execute_command(*call))


def test_a_forbidden_command_queued_in_multi_fails_the_transaction(
    admin: Valkey, ui: Valkey, writer: ValkeyConfigProvider
) -> None:
    _seed(writer, _php())
    before = admin.get(keys.REVISION)

    def attempt() -> None:
        with ui.pipeline() as pipe:
            pipe.watch(keys.REVISION)
            pipe.multi()
            pipe.set(keys.products("team-php"), '["go"]')
            pipe.execute_command("CONFIG", "GET", "*")
            pipe.incr(keys.REVISION)
            pipe.execute()

    _refused(attempt)
    assert admin.get(keys.products("team-php")) != b'["go"]'
    assert admin.get(keys.REVISION) == before


def test_the_ui_is_refused_debug_even_though_the_server_disables_it(
    admin: Valkey, ui: Valkey, writer: ValkeyConfigProvider
) -> None:
    # DEBUG is compiled out by `enable-debug-command` before the ACL is even
    # consulted; the ACL line grants no `+debug` either (unit-tested).
    _seed(writer, _php())
    with pytest.raises(ResponseError, match="DEBUG command not allowed"):
        ui.execute_command("DEBUG", "SLEEP", "0")


@pytest.mark.parametrize("key", ["other", "cvestream:v2:schema"])
def test_the_ui_cannot_touch_a_key_outside_the_namespace(
    admin: Valkey, ui: Valkey, writer: ValkeyConfigProvider, key: str
) -> None:
    _seed(writer, _php())
    _refused(lambda: ui.get(key))
    _refused(lambda: ui.set(key, "1"))
    _refused(lambda: ui.type(key))
    _refused(lambda: ui.ttl(key))
    _refused(lambda: ui.delete(key))


# --- the safety nets for UI writes (AC 4) --------------------------------------


def test_a_ui_edit_without_incr_is_picked_up_by_the_forced_reload(
    admin: Valkey,
    ui: Valkey,
    daemon: ValkeyConfigProvider,
    writer: ValkeyConfigProvider,
) -> None:
    _seed(writer, _php())
    snapshot = daemon.load()
    ui.set(keys.products("team-php"), '["go"]')  # no INCR: a UI write
    assert reload_config(daemon, snapshot) is snapshot
    forced = reload_config(daemon, snapshot, force=True)
    assert [p.name for p in forced.endpoints[0].products] == ["go"]


def test_an_invalid_ui_edit_is_rejected_and_the_last_valid_kept(
    admin: Valkey,
    ui: Valkey,
    daemon: ValkeyConfigProvider,
    writer: ValkeyConfigProvider,
) -> None:
    _seed(writer, _php())
    snapshot = daemon.load()
    ui.set(keys.products("team-php"), "[oops")
    ui.incr(keys.REVISION)
    with capture_logs() as logs:
        kept = reload_config(daemon, snapshot)
    assert kept is snapshot
    (entry,) = [e for e in logs if e["event"] == "config.rejected"]
    assert entry["errors"] == ["cvestream:v1:endpoint:team-php:products: invalid JSON"]


def test_deleting_the_schema_rejects_the_store_until_repaired(
    admin: Valkey,
    ui: Valkey,
    daemon: ValkeyConfigProvider,
    writer: ValkeyConfigProvider,
) -> None:
    _seed(writer, _php())
    snapshot = daemon.load()
    ui.delete(keys.SCHEMA)
    ui.incr(keys.REVISION)
    with pytest.raises(ConfigValidationError, match="not seeded"):
        daemon.load()
    with capture_logs() as logs:
        kept = reload_config(daemon, snapshot)
    assert kept is snapshot
    (entry,) = [e for e in logs if e["event"] == "config.rejected"]
    assert entry["errors"] == [
        f"{keys.SCHEMA}: missing, the store is not seeded (see docs/config-contract.md)"
    ]
    ui.set(keys.SCHEMA, "1")  # repair
    assert daemon.load().endpoints


def test_deleting_the_revision_reads_as_zero_and_stays_valid(
    admin: Valkey,
    ui: Valkey,
    daemon: ValkeyConfigProvider,
    writer: ValkeyConfigProvider,
) -> None:
    _seed(writer, _php())
    snapshot = daemon.load()
    ui.delete(keys.REVISION)
    assert daemon.revision() == "0"
    reloaded = reload_config(daemon, snapshot)
    assert reloaded.revision == "0"
    assert [e.id for e in reloaded.endpoints] == ["team-php"]
    assert ui.incr(keys.REVISION) == 1  # the next writer starts again from 1


def test_deleting_the_endpoints_set_is_a_valid_empty_configuration(
    admin: Valkey,
    ui: Valkey,
    daemon: ValkeyConfigProvider,
    writer: ValkeyConfigProvider,
) -> None:
    # Not rejected: the ids come from the set, so every endpoint (and feed)
    # disappears at the next reload; the per-endpoint keys stay, orphaned.
    # A daemon guard is deferred (deferred-work.md); recovery is config import.
    _seed(writer, _php())
    backup = writer.document()  # `cvestream config export`
    snapshot = daemon.load()
    ui.delete(keys.ENDPOINTS)
    ui.incr(keys.REVISION)
    with capture_logs() as logs:
        reloaded = reload_config(daemon, snapshot)
    assert reloaded.endpoints == ()
    assert not [e for e in logs if e["event"] == "config.rejected"]
    assert admin.get(keys.token("team-php")) is not None
    writer.replace(backup)  # `cvestream config import`
    assert [e.id for e in reload_config(daemon, reloaded).endpoints] == ["team-php"]


def test_a_non_integer_revision_breaks_every_write_until_repaired(
    admin: Valkey,
    ui: Valkey,
    daemon: ValkeyConfigProvider,
    writer: ValkeyConfigProvider,
) -> None:
    _seed(writer, _php())
    snapshot = daemon.load()
    ui.set(keys.REVISION, "not-an-integer")
    with pytest.raises(ResponseError):  # INCR on a non-integer value
        admin.incr(keys.REVISION)
    with pytest.raises(ConfigValidationError, match="not an integer"):
        daemon.revision()
    with capture_logs() as logs:
        kept = reload_config(daemon, snapshot, force=True)
    assert kept is snapshot
    assert any(e["event"] == "config.rejected" for e in logs)
    # `cvestream config import` cannot repair it: its INCR runs inside MULTI
    # (deferred-work.md). Only a SET of an integer does.
    with pytest.raises(ConfigUnavailableError):
        writer.replace(_document(_php()))
    ui.set(keys.REVISION, "5")
    assert daemon.revision() == "5"
    assert reload_config(daemon, snapshot).revision == "5"
