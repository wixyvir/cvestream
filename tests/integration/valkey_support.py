"""Shared helpers of the tests that talk to a real Valkey server.

`VALKEY_TEST_ADDRESS=<ip>:<port>` names a disposable server started with the
repo's `deploy/valkey/` files (README, "Running the tests"). It is deliberately
not a `CVESTREAM_*` variable: settings read those.
"""

import os
from pathlib import Path

import pytest
from pydantic import SecretStr
from valkey import Valkey

from cvestream.config import keys
from cvestream.config.valkey_provider import ValkeyConfigProvider
from cvestream.settings import ValkeySettings

ADDRESS_ENV = "VALKEY_TEST_ADDRESS"
SECRETS = Path(__file__).resolve().parents[2] / "deploy" / "valkey" / "dev-secrets"
DAEMON = "cvestream-daemon"
ADMIN = "cvestream-admin"
CONNECTOR = "connector-example"
# Ids the tests write: the admin cleanup has no SCAN, so it deletes these too.
KNOWN_IDS = ("team-php", "team-go", "other", "x")

ADDRESS = os.environ.get(ADDRESS_ENV, "")
HOST, _, _PORT = ADDRESS.partition(":")
# pytest-socket matches the host literally: an IP, never a name.
ALLOWED_HOSTS = [HOST or "127.0.0.1"]
PORT = int(_PORT) if _PORT.isdigit() else 6379


def require_server() -> None:
    """Skip without a server; on CI (`CI` set) a missing server is a failure."""
    if ADDRESS:
        return
    message = f"{ADDRESS_ENV} not set (start a disposable valkey, see README)"
    if os.environ.get("CI"):
        pytest.fail(message)
    pytest.skip(message)


def password(user: str) -> str:
    return (SECRETS / f"{user}.password").read_text(encoding="utf-8")


def url(user: str) -> str:
    return f"valkey://{user}@{HOST}:{PORT}/0"


def client(user: str, secret: str | None = None) -> Valkey:
    return Valkey(
        host=HOST,
        port=PORT,
        username=user,
        password=password(user) if secret is None else secret,
        socket_timeout=5,
        socket_connect_timeout=5,
    )


def settings(user: str, secret: str | None = None) -> ValkeySettings:
    return ValkeySettings(
        url=url(user),
        password=SecretStr(password(user) if secret is None else secret),
    )


def provider(user: str, secret: str | None = None) -> ValkeyConfigProvider:
    return ValkeyConfigProvider.from_settings(settings(user, secret))


def members(connection: Valkey, key: str) -> set[bytes]:
    """`SMEMBERS` with a narrow type (valkey-py's stubs include `Awaitable`)."""
    result = connection.smembers(key)
    assert isinstance(result, set | list)
    return set(result)


def get(connection: Valkey, key: str) -> bytes | None:
    """`GET` with a narrow type."""
    result = connection.get(key)
    assert result is None or isinstance(result, bytes)
    return result


def wipe(admin: Valkey) -> None:
    """Delete every key the tests write (the admin has no `KEYS` or `SCAN`)."""
    ids = {*KNOWN_IDS, *(m.decode() for m in members(admin, keys.ENDPOINTS))}
    names = [keys.ENDPOINTS, keys.SCHEMA, keys.REVISION]
    for endpoint_id in ids:
        names += [
            keys.endpoint(endpoint_id),
            keys.products(endpoint_id),
            keys.token(endpoint_id),
        ]
    admin.delete(*names)
