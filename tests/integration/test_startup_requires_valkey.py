"""`config_provider: valkey` needs Valkey to start: exit code 3.

The closed-port tests need no server and always run; the access tests are
marked `valkey`. Time is fake: the backoff never really sleeps.
"""

import asyncio
import socket
from pathlib import Path
from typing import Any

import pytest
from structlog.testing import capture_logs

from cvestream.app.runtime import poll_once, serve
from cvestream.app.servers import ASGIApp
from cvestream.settings import ListenerSettings, Settings, load_settings
from tests.integration import valkey_support as support


class _Listener:
    """A listener that does nothing: only the startup order matters here."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.started = False
        self._task: asyncio.Future[None] | None = None

    @property
    def task(self) -> asyncio.Future[None] | None:
        return self._task

    async def start(self) -> None:
        self.started = True
        self._task = asyncio.ensure_future(self._wait())

    async def _wait(self) -> None:
        await asyncio.Event().wait()

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()


class _Time:
    def __init__(self) -> None:
        self.now = 0.0
        self.waits: list[float] = []

    def monotonic(self) -> float:
        return self.now

    async def sleep(self, delay: float) -> None:
        self.waits.append(delay)
        self.now += delay


def _closed_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _settings(tmp_path: Path, url: str, password: str | None = None) -> Settings:
    environ = {
        "CVESTREAM_DATA_DIR": str(tmp_path / "data"),
        "CVESTREAM_CONFIG_PROVIDER": "valkey",
        "CVESTREAM_VALKEY__URL": url,
        "CVESTREAM_VALKEY__STARTUP_TIMEOUT": "PT3S",
    }
    if password is not None:
        secret = tmp_path / "password"
        secret.write_text(password, encoding="utf-8")
        environ["CVESTREAM_VALKEY__PASSWORD_FILE"] = str(secret)
    return load_settings(environ)


async def _serve(settings: Settings, clock: _Time) -> tuple[int, dict[str, _Listener]]:
    listeners: dict[str, _Listener] = {}

    def factory(name: str, _app: ASGIApp, _settings: ListenerSettings) -> _Listener:
        listeners[name] = _Listener(name)
        return listeners[name]

    code = await serve(
        settings,
        stop=asyncio.Event(),
        sleep=clock.sleep,
        monotonic=clock.monotonic,
        server_factory=factory,
        sources_factory=lambda *_args: (),
    )
    return code, listeners


@pytest.mark.anyio
@pytest.mark.allow_hosts(["127.0.0.1"])
async def test_a_closed_port_exits_3_after_the_startup_timeout(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    port = _closed_port()
    settings = _settings(
        tmp_path, f"valkey://cvestream-daemon@127.0.0.1:{port}/0", "pw"
    )
    clock = _Time()
    with capture_logs() as logs:
        code, listeners = await _serve(settings, clock)
    assert code == 3
    assert clock.waits == [1, 2]  # 1 s, then 2 s (cut to the 3 s deadline)
    events = [e for e in logs if e["event"] == "config.unavailable"]
    assert [e["log_level"] for e in events] == ["warning", "warning", "error"]
    assert {e["error_type"] for e in events} == {"ConfigUnavailableError"}
    # liveness was up, readiness never turned on, the public listener never started
    assert listeners["private"].started
    assert "public" not in listeners or not listeners["public"].started
    err = capsys.readouterr().err
    assert err.splitlines() == [
        f"valkey: cannot reach 127.0.0.1:{port}: ConnectionError"
    ]
    assert "pw" not in err


@pytest.mark.anyio
@pytest.mark.allow_hosts(["127.0.0.1"])
async def test_poll_once_exits_3_on_a_closed_port(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    settings = _settings(
        tmp_path, f"valkey://cvestream-daemon@127.0.0.1:{_closed_port()}/0"
    )
    clock = _Time()
    code = await poll_once(
        settings,
        sleep=clock.sleep,
        monotonic=clock.monotonic,
        sources_factory=lambda *_a: (),
    )
    assert code == 3
    assert clock.waits == [1, 2]
    assert "cannot reach" in capsys.readouterr().err


@pytest.mark.valkey
@pytest.mark.anyio
@pytest.mark.allow_hosts(support.ALLOWED_HOSTS)
@pytest.mark.parametrize(
    ("user", "secret"),
    [(support.DAEMON, "wrong-password"), ("default", "anything")],
)
async def test_an_authentication_failure_exits_3_at_once(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    user: str,
    secret: str,
) -> None:
    support.require_server()
    settings = _settings(
        tmp_path, f"valkey://{user}@{support.HOST}:{support.PORT}/0", secret
    )
    clock = _Time()
    with capture_logs() as logs:
        code, _ = await _serve(settings, clock)
    assert code == 3
    assert clock.waits == []  # not retried
    (event,) = [e for e in logs if e["event"] == "config.unavailable"]
    assert event["log_level"] == "error"
    assert event["error_type"] == "ConfigAccessDeniedError"
    err = capsys.readouterr().err
    assert err.splitlines() == [f"valkey: authentication failed for user '{user}'"]
    assert secret not in err


@pytest.mark.valkey
@pytest.mark.anyio
@pytest.mark.allow_hosts(support.ALLOWED_HOSTS)
async def test_an_unseeded_store_exits_2(
    tmp_path: Path, admin: Any, capsys: pytest.CaptureFixture[str]
) -> None:
    settings = _settings(
        tmp_path,
        support.url(support.DAEMON),
        support.password(support.DAEMON),
    )
    clock = _Time()
    with capture_logs() as logs:
        code, _ = await _serve(settings, clock)
    assert code == 2
    assert clock.waits == []
    assert any(e["event"] == "config.rejected" for e in logs)
    assert "not seeded" in capsys.readouterr().err
