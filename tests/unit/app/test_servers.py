import asyncio
import signal
import socket
from datetime import UTC, datetime
from types import MappingProxyType

import httpx
import pytest
import uvicorn

from cvestream.app.feed_cache import FeedCache, FeedCacheHolder
from cvestream.app.public_api import build_public_app
from cvestream.app.servers import (
    EmbeddedServer,
    ListenerError,
    ManagedServer,
    build_server,
)
from cvestream.config.provider import ConfigSnapshot
from cvestream.domain.endpoint import Endpoint, ProductEntry
from cvestream.settings import ListenerSettings

NOW = datetime(2026, 10, 26, 20, 0, tzinfo=UTC)
TOKEN = "t" * 43


async def _app(_scope: object, _receive: object, _send: object) -> None:
    return None


def _listener(port: int = 8080) -> ListenerSettings:
    return ListenerSettings(host="127.0.0.1", port=port)


def test_build_server_configures_uvicorn_as_a_library() -> None:
    server = build_server(_app, _listener(18080))
    config = server.config
    assert isinstance(server, EmbeddedServer)
    assert config.access_log is False
    assert config.log_config is None
    assert config.server_header is False
    assert config.proxy_headers is False
    assert config.lifespan == "off"
    assert config.host == "127.0.0.1"
    assert config.port == 18080
    assert config.timeout_graceful_shutdown == 5


def test_the_overridden_method_exists_in_uvicorn() -> None:
    # EmbeddedServer replaces this method: a rename in uvicorn must fail here.
    assert callable(uvicorn.Server.capture_signals)


def test_capture_signals_installs_nothing() -> None:
    server = build_server(_app, _listener())
    before = {sig: signal.getsignal(sig) for sig in (signal.SIGTERM, signal.SIGINT)}
    with server.capture_signals():
        during = {sig: signal.getsignal(sig) for sig in before}
    after = {sig: signal.getsignal(sig) for sig in before}
    assert before == during == after


class _FakeServer(EmbeddedServer):
    def __init__(self, behaviour: str) -> None:
        super().__init__(uvicorn.Config(_app, log_config=None))
        self.behaviour = behaviour

    async def serve(self, sockets: list[socket.socket] | None = None) -> None:
        if self.behaviour == "bind-failure":
            raise SystemExit(1)
        if self.behaviour == "never-starts":
            while not self.should_exit:
                await asyncio.sleep(0.001)
            return
        self.started = True
        while not self.should_exit:
            await asyncio.sleep(0.001)


@pytest.mark.anyio
async def test_start_waits_for_the_socket_and_stop_ends_the_task() -> None:
    server = _FakeServer("ok")
    managed = ManagedServer("public", server)
    assert managed.task is None
    await managed.start()
    assert server.started
    assert managed.task is not None
    assert not managed.task.done()
    await managed.stop()
    assert managed.task.done()
    assert server.should_exit


@pytest.mark.anyio
async def test_a_bind_failure_is_a_listener_error_not_a_system_exit() -> None:
    managed = ManagedServer("private", _FakeServer("bind-failure"))
    with pytest.raises(ListenerError, match="private listener failed to start"):
        await managed.start()


@pytest.mark.anyio
async def test_a_server_that_never_starts_times_out(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("cvestream.app.servers.STARTUP_TIMEOUT_SECONDS", 0.05)
    server = _FakeServer("never-starts")
    managed = ManagedServer("public", server)
    with pytest.raises(ListenerError, match="did not start in time"):
        await managed.start()
    assert server.should_exit
    await managed.stop()


@pytest.mark.anyio
async def test_stop_before_start_is_a_no_op() -> None:
    await ManagedServer("public", _FakeServer("ok")).stop()


def _holder() -> FeedCacheHolder:
    endpoint = Endpoint("team-php", "PHP team", (ProductEntry("php"),))
    snapshot = ConfigSnapshot("r", (endpoint,), MappingProxyType({TOKEN: "team-php"}))
    return FeedCacheHolder(FeedCache.build(snapshot, {}, NOW))


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


@pytest.mark.enable_socket
@pytest.mark.allow_hosts(["127.0.0.1"])
@pytest.mark.anyio
async def test_a_real_listener_serves_feeds_and_stops_cleanly() -> None:
    port = _free_port()
    managed = ManagedServer(
        "public", build_server(build_public_app(_holder()), _listener(port))
    )
    await managed.start()
    try:
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}") as http:
            got = await http.get(f"/feeds/{TOKEN}.xml")
            head = await http.head(f"/feeds/{TOKEN}.xml")
            missing = await http.get("/feeds/nope.xml")
        assert got.status_code == 200
        assert got.headers["content-type"] == "application/rss+xml; charset=utf-8"
        assert "server" not in got.headers
        assert head.status_code == 200
        assert head.content == b""
        assert head.headers["content-length"] == got.headers["content-length"]
        assert missing.status_code == 404
        assert missing.content == b""
    finally:
        await managed.stop()


@pytest.mark.enable_socket
@pytest.mark.allow_hosts(["127.0.0.1"])
@pytest.mark.anyio
async def test_a_port_already_in_use_is_a_listener_error() -> None:
    with socket.socket() as taken:
        taken.bind(("127.0.0.1", 0))
        taken.listen()
        port = int(taken.getsockname()[1])
        managed = ManagedServer("public", build_server(_app, _listener(port)))
        with pytest.raises(ListenerError):
            await managed.start()
