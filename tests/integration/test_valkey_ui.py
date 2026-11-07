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
from cvestream.config.valkey_provider import ValkeyConfigProvider
from cvestream.pipeline.run import reload_config
from tests.integration import valkey_support as support
from tests.integration.test_valkey_provider import _php, _seed

pytestmark = [pytest.mark.valkey, pytest.mark.allow_hosts(support.ALLOWED_HOSTS)]

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
    ("EVALSHA", "0" * 64, "0"),
    ("SCRIPT", "LOAD", "return 1"),
    ("FUNCTION", "LIST"),
    ("FCALL", "f", "0"),
    ("SHUTDOWN",),
    ("REPLICAOF", "NO", "ONE"),
    ("SLAVEOF", "NO", "ONE"),
    ("MIGRATE", "127.0.0.1", "6379", keys.SCHEMA, "0", "5000"),
    ("MONITOR",),
    ("SLOWLOG", "GET"),
    ("LATENCY", "HISTORY", "x"),
    ("CLIENT", "LIST"),
    ("CLIENT", "KILL", "TYPE", "normal"),
    ("RENAME", keys.SCHEMA, "x"),
    ("RENAMENX", keys.SCHEMA, "x"),
    ("MOVE", keys.SCHEMA, "1"),
    ("COPY", keys.SCHEMA, "x"),
    ("RESTORE", "x", "0", ""),
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


# --- grants -------------------------------------------------------------------


def test_the_ui_reads_and_writes_the_whole_namespace(
    admin: Valkey, ui: Valkey, writer: ValkeyConfigProvider
) -> None:
    _seed(writer, _php())
    # strings, tokens included
    assert ui.get(keys.SCHEMA) == b"1"
    assert ui.get(keys.token("team-php")) is not None
    ui.set(keys.SCHEMA, "1")
    ui.delete(keys.SCHEMA)
    admin.set(keys.SCHEMA, "1")
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
    scanned = ui.scan(0, match="cvestream:v1:*")
    assert isinstance(scanned, tuple)  # valkey-py's stubs include Awaitable
    cursor, found = scanned
    assert cursor == 0
    assert set(found) == {
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


# --- refusals -----------------------------------------------------------------


@pytest.mark.parametrize("call", FORBIDDEN_CALLS, ids=lambda c: " ".join(c))
def test_the_ui_is_refused_every_forbidden_command(
    admin: Valkey, ui: Valkey, writer: ValkeyConfigProvider, call: tuple[str, ...]
) -> None:
    _seed(writer, _php())
    _refused(lambda: ui.execute_command(*call))


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


def test_deleting_a_structural_key_is_granted_but_rejects_the_snapshot(
    admin: Valkey,
    ui: Valkey,
    daemon: ValkeyConfigProvider,
    writer: ValkeyConfigProvider,
) -> None:
    from cvestream.config.provider import ConfigValidationError

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
    assert entry["errors"] == ["the store is not seeded"] or "not seeded" in str(
        entry["errors"]
    )
    admin.set(keys.SCHEMA, "1")  # repair
    assert daemon.load().endpoints


def test_a_non_integer_revision_breaks_every_write_until_repaired(
    admin: Valkey,
    ui: Valkey,
    daemon: ValkeyConfigProvider,
    writer: ValkeyConfigProvider,
) -> None:
    from cvestream.config.provider import ConfigValidationError

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
    # repair: SET an integer (or `cvestream config import`)
    ui.set(keys.REVISION, "5")
    assert daemon.revision() == "5"
    assert reload_config(daemon, snapshot).revision == "5"
