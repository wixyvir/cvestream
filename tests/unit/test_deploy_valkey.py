"""The Valkey server files are checked without a server."""

import fnmatch
import hashlib
import json
from pathlib import Path

import pytest

DEPLOY = Path(__file__).resolve().parents[2] / "deploy" / "valkey"
USERS = ("cvestream-daemon", "cvestream-admin", "cvestream-ui", "connector-example")
# A key no connector may reach.
A_TOKEN_KEY = "cvestream:v1:token:team-php"  # noqa: S105 (a key name, not a secret)

# Commands no shipped user may ever run (story 3.4), with every subcommand: the
# dangerous server commands, scripting, the EXPIRE family (a TTL on a config key
# would make it vanish; `SET ... EX` stays possible, see docs/config-contract.md),
# the commands that move keys out of the contract layout or stream the dataset,
# and `CLIENT` (its subcommands list or kill other clients) but for the
# allow-listed subcommands below.
FORBIDDEN_COMMANDS = {
    "keys",
    "flushall",
    "flushdb",
    "config",
    "acl",
    "debug",
    "module",
    "eval",
    "evalsha",
    "eval_ro",
    "evalsha_ro",
    "script",
    "function",
    "fcall",
    "fcall_ro",
    "shutdown",
    "replicaof",
    "slaveof",
    "migrate",
    "monitor",
    "slowlog",
    "latency",
    "client",
    "rename",
    "renamenx",
    "move",
    "copy",
    "swapdb",
    "restore",
    "dump",
    "expire",
    "pexpire",
    "expireat",
    "pexpireat",
    "persist",
    "getex",
    "save",
    "bgsave",
    "bgrewriteaof",
    "cluster",
    "failover",
    "sync",
    "psync",
    "replconf",
}
# The only subcommands of a forbidden command that a user may get: client
# libraries send them after `AUTH` (valkey-py `SETINFO`, ioredis `SETNAME`).
ALLOWED_SUBCOMMANDS = {"client|setinfo", "client|setname"}
# Story 3.4 adds `cvestream-ui` and must leave the other lines as they are
# (AC 1): changing one of them is deliberate and updates this table too.
UNCHANGED_LINES = {
    "default": "user default off resetpass resetkeys resetchannels -@all",
    "cvestream-daemon": (
        "user cvestream-daemon on "
        "#46f4efafd071de07d56f12258421edcb70b39bad7e3199cb250fc693329567e3 "
        "resetchannels %R~cvestream:v1:* -@all +get +mget +smembers +exists "
        "+ping +select +client|setinfo"
    ),
    "cvestream-admin": (
        "user cvestream-admin on "
        "#5f558d6e4f84fae21673ad9ea14f3a312ef3f4304b59a51ceb0da3a9290da7a3 "
        "resetchannels ~cvestream:v1:* -@all +get +mget +smembers +exists "
        "+set +del +sadd +srem +incr +incrby +watch +unwatch +multi +exec "
        "+discard +ping +select +client|setinfo"
    ),
    "connector-example": (
        "user connector-example on "
        "#174808d04c670f4b8e094fe329f5237bb3b149a8f41c1936349c38678c85b48f "
        "resetchannels %R~cvestream:v1:schema %R~cvestream:v1:endpoints "
        "%R~cvestream:v1:endpoint:* %W~cvestream:v1:endpoint:*:products "
        "%RW~cvestream:v1:revision -@all +get +mget +smembers +exists +set "
        "+incr +incrby +watch +unwatch +multi +exec +discard +ping +select "
        "+client|setinfo"
    ),
}


def _acl_lines() -> dict[str, list[str]]:
    users: dict[str, list[str]] = {}
    for line in (DEPLOY / "users.acl").read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        words = line.split()
        assert words[0] == "user", f"only `user` lines are valid in an ACL file: {line}"
        users[words[1]] = words[2:]
    return users


def _commands(rules: list[str]) -> set[str]:
    """The granted commands, lower-cased: ACL rules are case-insensitive."""
    return {rule[1:].lower() for rule in rules if rule.startswith("+")}


def _patterns(rules: list[str]) -> list[tuple[str, str]]:
    """(permission, glob) pairs: `~p` and `%RW~p` are read-write."""
    found: list[tuple[str, str]] = []
    for rule in rules:
        if rule.startswith("%") and "~" in rule:
            perm, _, glob = rule[1:].partition("~")
            found.append((perm, glob))
        elif rule.startswith("~"):
            found.append(("RW", rule[1:]))
    return found


def test_the_acl_file_has_only_user_lines_and_no_comment() -> None:
    # Valkey 9.1 refuses an ACL file with any other line (a `#` comment too).
    text = (DEPLOY / "users.acl").read_text(encoding="utf-8")
    for line in text.splitlines():
        assert not line.strip() or line.startswith("user ")
        assert not line.lstrip().startswith("#")


def test_the_acl_file_defines_every_user_and_disables_default() -> None:
    users = _acl_lines()
    assert set(users) == {"default", *USERS}
    assert users["default"][0] == "off"
    for name in USERS:
        assert users[name][0] == "on"


@pytest.mark.parametrize("name", USERS)
def test_each_hash_is_the_sha256_of_its_dev_secret(name: str) -> None:
    secret = (DEPLOY / "dev-secrets" / f"{name}.password").read_text(encoding="utf-8")
    assert secret == secret.strip()  # one line, no trailing newline
    hashes = [rule for rule in _acl_lines()[name] if rule.startswith("#")]
    assert hashes == ["#" + hashlib.sha256(secret.encode()).hexdigest()]


def test_no_plaintext_password_and_no_blanket_access() -> None:
    for name, rules in _acl_lines().items():
        for rule in rules:
            assert not rule.startswith(">"), f"{name}: plaintext password"
            assert rule not in {"allkeys", "~*", "%RW~*", "allcommands", "+@all"}
            assert rule not in {"nopass", "allchannels", "&*"}


def test_the_daemon_is_read_only() -> None:
    rules = _acl_lines()["cvestream-daemon"]
    assert _patterns(rules) == [("R", "cvestream:v1:*")]
    commands = {rule for rule in rules if rule.startswith("+")}
    assert not commands & {
        "+set",
        "+del",
        "+sadd",
        "+srem",
        "+incr",
        "+multi",
        "+watch",
    }


def test_the_admin_is_limited_to_the_namespace() -> None:
    rules = _acl_lines()["cvestream-admin"]
    assert _patterns(rules) == [("RW", "cvestream:v1:*")]
    assert "-@all" in rules
    commands = {rule for rule in rules if rule.startswith("+")}
    # No category and nothing that lists, scans or configures the server.
    assert not {c for c in commands if c.startswith("+@")}
    assert not commands & {"+keys", "+scan", "+flushall", "+flushdb", "+config"}


def test_the_connector_reaches_no_token_key() -> None:
    rules = _acl_lines()["connector-example"]
    patterns = _patterns(rules)
    assert patterns
    for _, glob in patterns:
        assert not fnmatch.fnmatchcase(A_TOKEN_KEY, glob)
        assert not fnmatch.fnmatchcase("cvestream:v1:token:x", glob)


def test_the_connector_writes_only_products_and_revision() -> None:
    patterns = _patterns(_acl_lines()["connector-example"])
    writable = [glob for perm, glob in patterns if "W" in perm]
    assert sorted(writable) == [
        "cvestream:v1:endpoint:*:products",
        "cvestream:v1:revision",
    ]
    for key in ("cvestream:v1:endpoint:team-php", "cvestream:v1:endpoints"):
        assert not any(fnmatch.fnmatchcase(key, glob) for glob in writable)
    assert fnmatch.fnmatchcase("cvestream:v1:endpoint:team-php:products", writable[0])


def test_the_ui_user_is_limited_to_the_namespace() -> None:
    rules = _acl_lines()["cvestream-ui"]
    assert _patterns(rules) == [("RW", "cvestream:v1:*")]


@pytest.mark.parametrize("name", USERS)
def test_no_user_gets_a_category_or_a_forbidden_command(name: str) -> None:
    rules = _acl_lines()[name]
    assert rules.index("-@all") < min(
        index for index, rule in enumerate(rules) if rule.startswith("+")
    )
    for command in _commands(rules):
        assert not command.startswith("@"), f"{name}: +{command} is a category"
        if command in ALLOWED_SUBCOMMANDS:
            continue
        parent = command.partition("|")[0]
        assert parent not in FORBIDDEN_COMMANDS, f"{name}: +{command} is forbidden"


def test_only_the_ui_user_has_scan() -> None:
    # Categories are refused above, so the explicit grant is the only way in.
    for name, rules in _acl_lines().items():
        assert ("scan" in _commands(rules)) == (name == "cvestream-ui"), name


def test_the_other_users_lines_are_unchanged() -> None:
    lines = {
        line.split()[1]: line
        for line in (DEPLOY / "users.acl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    }
    for name, expected in UNCHANGED_LINES.items():
        assert lines[name] == expected, name


def test_the_redisinsight_pre_setup_file_matches_the_dev_secret_and_the_acl() -> None:
    databases = json.loads(
        (DEPLOY / "dev-secrets" / "cvestream-ui.redisinsight.json").read_text(
            encoding="utf-8"
        )
    )
    password = (DEPLOY / "dev-secrets" / "cvestream-ui.password").read_text(
        encoding="utf-8"
    )
    assert len(databases) == 1
    (database,) = databases
    assert database == {
        "id": "cvestream",
        "name": "cvestream",
        "host": "valkey",
        "port": 6379,
        "username": "cvestream-ui",
        "password": password,
    }
    assert "cvestream-ui" in _acl_lines()


def test_the_connector_reads_what_the_read_protocol_needs() -> None:
    readable = [glob for perm, glob in _patterns(_acl_lines()["connector-example"])]
    for key in (
        "cvestream:v1:schema",
        "cvestream:v1:revision",
        "cvestream:v1:endpoints",
        "cvestream:v1:endpoint:team-php",
        "cvestream:v1:endpoint:team-php:products",
    ):
        assert any(fnmatch.fnmatchcase(key, glob) for glob in readable), key


def test_the_server_configuration() -> None:
    lines = [
        line.strip()
        for line in (DEPLOY / "valkey.conf").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    assert "appendonly yes" in lines
    assert "appendfsync everysec" in lines
    assert "aclfile /usr/local/etc/valkey/users.acl" in lines
    assert "dir /data" in lines
    # `user` directives and `aclfile` are mutually exclusive
    assert not any(line.startswith("user ") for line in lines)
