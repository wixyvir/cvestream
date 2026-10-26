import asyncio
import os
import signal
import threading
from collections.abc import Awaitable, Callable
from datetime import timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
import structlog
from structlog.testing import capture_logs

from cvestream.app.runtime import poll_once, serve
from cvestream.app.servers import ASGIApp, ListenerError
from cvestream.clock import Clock
from cvestream.domain.model import AffectedProduct, CvssScore, Sourced, SourceRecord
from cvestream.http import HttpClient
from cvestream.pipeline import run as pipeline_run
from cvestream.settings import (
    ListenerSettings,
    Settings,
    SourcesSettings,
    load_settings,
)
from cvestream.sources.base import (
    FetchResult,
    SourceAdapter,
    SourceError,
    SourceTransportError,
)
from cvestream.sources.registry import ScheduledSource
from cvestream.state.db import State, StateError
from tests.conftest import FakeClock

TOKEN = "t" * 43
VALID = f"""\
version: 1
endpoints:
  - id: team-php
    name: PHP team
    token: {TOKEN}
    products:
      - php
"""
log = structlog.get_logger()


class _Adapter:
    def __init__(self, name: str, *script: FetchResult | SourceError) -> None:
        self.name = name
        self._script = list(script)
        self.cursors: list[str | None] = []

    async def fetch(self, cursor: str | None) -> FetchResult:
        self.cursors.append(cursor)
        step = self._script.pop(0) if self._script else FetchResult((), None)
        if isinstance(step, Exception):
            raise step
        return step


def _record(clock: Clock) -> SourceRecord:
    now = clock.now()
    return SourceRecord(
        source="euvd",
        record_id="EUVD-2026-1",
        observed_at=now,
        aliases=("CVE-2026-1001",),
        description=Sourced("A flaw", "euvd", now),
        scores=(Sourced(CvssScore(8.0, "3.1"), "euvd", now),),
        affected=(Sourced(AffectedProduct("php"), "euvd", now),),
        updated_at=now - timedelta(hours=1),
    )


class _Hang:
    """A source whose fetch never returns: no cycle ever runs."""

    name = "euvd"

    async def fetch(self, cursor: str | None) -> FetchResult:
        await asyncio.Event().wait()
        raise AssertionError


class _Listener:
    """A server that records its lifecycle and probes `/readyz` at each step."""

    def __init__(
        self,
        name: str,
        app: ASGIApp,
        apps: dict[str, ASGIApp],
        *,
        fail_start: bool = False,
    ) -> None:
        self.name = name
        self._apps = apps
        self._fail_start = fail_start
        self._task: asyncio.Future[None] | None = None
        self.ended = asyncio.Event()
        apps[name] = app

    @property
    def task(self) -> asyncio.Future[None] | None:
        return self._task

    async def _probe(self) -> int:
        transport = httpx.ASGITransport(app=self._apps["private"])
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as http:
            return (await http.get("/readyz")).status_code

    async def start(self) -> None:
        if self._fail_start:
            msg = f"{self.name} listener failed to start"
            raise ListenerError(msg)
        log.info("test.listener_started", name=self.name, readyz=await self._probe())
        self._task = asyncio.ensure_future(self._wait())

    async def _wait(self) -> None:
        await self.ended.wait()

    async def stop(self) -> None:
        log.info("test.listener_stopped", name=self.name)
        self.ended.set()


class _Harness:
    def __init__(self, tmp_path: Path, clock: FakeClock) -> None:
        self.tmp_path = tmp_path
        self.clock = clock
        self.endpoints = tmp_path / "endpoints.yaml"
        self.endpoints.write_text(VALID, encoding="utf-8")
        self.apps: dict[str, ASGIApp] = {}
        self.listeners: dict[str, _Listener] = {}
        self.fail_start: set[str] = set()
        self.adapters: tuple[SourceAdapter, ...] = ()
        self.stop = asyncio.Event()
        self.settings: Settings = self.load_settings()

    def load_settings(self, **extra: str) -> Settings:
        environ = {
            "CVESTREAM_DATA_DIR": str(self.tmp_path / "data"),
            "CVESTREAM_ENDPOINTS_FILE": str(self.endpoints),
            **extra,
        }
        return load_settings(environ)

    def server_factory(
        self, name: str, app: ASGIApp, _listener: ListenerSettings
    ) -> _Listener:
        listener = _Listener(name, app, self.apps, fail_start=name in self.fail_start)
        self.listeners[name] = listener
        return listener

    def sources_factory(
        self, _settings: SourcesSettings, _http: HttpClient, _clock: Clock
    ) -> tuple[ScheduledSource, ...]:
        return tuple(ScheduledSource(a, timedelta(minutes=10)) for a in self.adapters)

    async def get(self, listener: str, path: str) -> httpx.Response:
        transport = httpx.ASGITransport(app=self.apps[listener])
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as http:
            return await http.get(path)

    def serve(self, *, own_signals: bool = False) -> Awaitable[int]:
        return serve(
            self.settings,
            clock=self.clock,
            stop=None if own_signals else self.stop,
            sleep=_block,
            random=lambda: 0.0,
            server_factory=self.server_factory,
            sources_factory=self.sources_factory,
        )


async def _block(_delay: float) -> None:
    await asyncio.Event().wait()


@pytest.fixture
def harness(tmp_path: Path, fake_clock: FakeClock) -> _Harness:
    return _Harness(tmp_path, fake_clock)


async def _until(predicate: Callable[[], bool]) -> None:
    for _ in range(2000):
        if predicate():
            return
        await asyncio.sleep(0.001)
    raise AssertionError("condition not reached")


def _order(logs: list[Any]) -> list[str]:
    wanted = {
        "state.migrated",
        "test.listener_started",
        "test.listener_stopped",
        "app.started",
        "app.ready",
        "app.stopping",
        "app.stopped",
    }
    out: list[str] = []
    for entry in logs:
        if entry["event"] in wanted:
            name = entry.get("name")
            out.append(f"{entry['event']}:{name}" if name else entry["event"])
    return out


@pytest.mark.anyio
async def test_the_startup_order(harness: _Harness, fake_clock: FakeClock) -> None:
    harness.adapters = (_Adapter("euvd", FetchResult((_record(fake_clock),), "c1")),)
    with capture_logs() as logs:
        task = asyncio.ensure_future(harness.serve())
        await _until(lambda: any(e["event"] == "app.ready" for e in logs))
        assert (await harness.get("private", "/readyz")).status_code == 200
        status = (await harness.get("private", "/status")).json()
        harness.stop.set()
        assert await task == 0
    order = _order(logs)
    assert order[:5] == [
        "state.migrated",
        "test.listener_started:private",
        "app.started",
        "test.listener_started:public",
        "app.ready",
    ]
    started = {
        e["name"]: e["readyz"] for e in logs if e["event"] == "test.listener_started"
    }
    # not ready when either listener starts, ready only after `app.ready`
    assert started == {"private": 503, "public": 503}
    assert "euvd" in status["sources"]
    assert status["endpoints"] == {
        "team-php": {"item_count": 0, "last_render_at": None}
    }


@pytest.mark.anyio
async def test_the_feed_is_served_once_the_first_cycle_ran(
    harness: _Harness, fake_clock: FakeClock
) -> None:
    harness.adapters = (_Adapter("euvd", FetchResult((_record(fake_clock),), "c1")),)
    with capture_logs() as logs:
        task = asyncio.ensure_future(harness.serve())
        await _until(lambda: any(e["event"] == "cycle.summary" for e in logs))
        response = await harness.get("public", f"/feeds/{TOKEN}.xml")
        status = (await harness.get("private", "/status")).json()
        harness.stop.set()
        await task
    assert response.status_code == 200
    assert b"CVE-2026-1001" in response.content
    assert status["sources"]["euvd"]["cursor"] == "c1"
    assert status["endpoints"]["team-php"]["item_count"] == 1
    # no token anywhere in the logs of a whole run
    assert TOKEN not in str(logs)


@pytest.mark.anyio
async def test_an_empty_channel_is_served_before_the_first_cycle(
    harness: _Harness,
) -> None:
    # a source that never returns: no cycle ever runs
    harness.adapters = (_Hang(),)
    with capture_logs() as logs:
        task = asyncio.ensure_future(harness.serve())
        await _until(lambda: any(e["event"] == "app.ready" for e in logs))
        response = await harness.get("public", f"/feeds/{TOKEN}.xml")
        harness.stop.set()
        await task
    assert response.status_code == 200
    assert b"<item>" not in response.content


@pytest.mark.anyio
async def test_a_restart_serves_the_stored_feed_before_any_fetch(
    harness: _Harness, fake_clock: FakeClock
) -> None:
    harness.adapters = (_Adapter("euvd", FetchResult((_record(fake_clock),), "c1")),)
    with capture_logs() as logs:
        task = asyncio.ensure_future(harness.serve())
        await _until(lambda: any(e["event"] == "cycle.summary" for e in logs))
        first = (await harness.get("public", f"/feeds/{TOKEN}.xml")).content
        harness.stop.set()
        await task

    harness.adapters = (_Hang(),)
    harness.stop = asyncio.Event()
    with capture_logs() as logs:
        task = asyncio.ensure_future(harness.serve())
        await _until(lambda: any(e["event"] == "app.ready" for e in logs))
        second = (await harness.get("public", f"/feeds/{TOKEN}.xml")).content
        harness.stop.set()
        await task
    assert second == first
    assert b"CVE-2026-1001" in second


@pytest.mark.anyio
async def test_a_missing_endpoints_file_exits_3(
    harness: _Harness, capsys: pytest.CaptureFixture[str]
) -> None:
    harness.endpoints.unlink()
    with capture_logs() as logs:
        code = await harness.serve()
    assert code == 3
    assert any(e["event"] == "config.unavailable" for e in logs)
    assert "test.listener_started:private" in _order(logs)
    assert "test.listener_stopped:private" in _order(logs)
    assert "test.listener_started:public" not in _order(logs)
    err = capsys.readouterr().err
    assert len(err.splitlines()) == 1
    assert "endpoints.yaml" in err


@pytest.mark.anyio
async def test_an_invalid_endpoints_file_exits_2(
    harness: _Harness, capsys: pytest.CaptureFixture[str]
) -> None:
    harness.endpoints.write_text(
        VALID.replace("team-php", "Team PHP"), encoding="utf-8"
    )
    with capture_logs() as logs:
        code = await harness.serve()
    assert code == 2
    (rejected,) = [e for e in logs if e["event"] == "config.rejected"]
    assert rejected["errors"]
    assert TOKEN not in str(logs)
    err = capsys.readouterr().err
    assert len(err.splitlines()) == 1
    assert TOKEN not in err
    assert "test.listener_stopped:private" in _order(logs)
    assert "test.listener_started:public" not in _order(logs)


@pytest.mark.anyio
async def test_the_state_is_disposed_when_the_config_is_rejected(
    harness: _Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    disposed: list[bool] = []
    original = State.dispose

    def spy(self: State) -> None:
        disposed.append(True)
        original(self)

    monkeypatch.setattr(State, "dispose", spy)
    harness.endpoints.unlink()
    await harness.serve()
    assert disposed == [True]


@pytest.mark.anyio
async def test_a_listener_that_cannot_start_exits_1(
    harness: _Harness, capsys: pytest.CaptureFixture[str]
) -> None:
    harness.fail_start = {"private"}
    with capture_logs() as logs:
        code = await harness.serve()
    assert code == 1
    assert any(e["event"] == "app.failed" for e in logs)
    assert capsys.readouterr().err.strip() == "private listener failed to start"
    assert "app.started" not in _order(logs)


@pytest.mark.anyio
async def test_a_public_listener_that_cannot_start_stops_the_private_one(
    harness: _Harness,
) -> None:
    harness.fail_start = {"public"}
    with capture_logs() as logs:
        code = await harness.serve()
    assert code == 1
    assert "test.listener_stopped:private" in _order(logs)


@pytest.mark.anyio
async def test_a_state_error_propagates_after_cleanup(
    harness: _Harness, tmp_path: Path
) -> None:
    blocker = tmp_path / "blocker"
    blocker.write_text("a file, not a directory", encoding="utf-8")
    harness.settings = harness.load_settings(CVESTREAM_DATA_DIR=str(blocker / "data"))
    with pytest.raises(StateError):
        await harness.serve()
    # nothing was started
    assert not any(listener.task for listener in harness.listeners.values())


@pytest.mark.anyio
async def test_shutdown_order_lets_the_current_run_finish(
    harness: _Harness, fake_clock: FakeClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    harness.adapters = (_Adapter("euvd", FetchResult((_record(fake_clock),), "c1")),)
    entered = threading.Event()
    release = threading.Event()
    original = pipeline_run.run

    def slow(*args: Any, **kwargs: Any) -> Any:
        entered.set()
        assert release.wait(10)
        result = original(*args, **kwargs)
        log.info("test.run_finished")
        return result

    monkeypatch.setattr(pipeline_run, "run", slow)
    with capture_logs() as logs:
        task = asyncio.ensure_future(harness.serve())
        await _until(lambda: any(e["event"] == "app.ready" for e in logs))
        assert await asyncio.to_thread(entered.wait, 10)
        harness.stop.set()
        await _until(lambda: any(e["event"] == "app.stopping" for e in logs))
        # the run is still in its thread: nothing has stopped yet
        assert not any(e["event"] == "test.listener_stopped" for e in logs)
        assert (await harness.get("private", "/readyz")).status_code == 503
        assert not task.done()
        release.set()
        assert await task == 0
    events = [e["event"] for e in logs]
    assert events.index("test.run_finished") < events.index("test.listener_stopped")
    assert events.index("app.stopping") < events.index("test.run_finished")
    assert events[-1] == "app.stopped"
    # the stopped public listener went first
    stopped = [e["name"] for e in logs if e["event"] == "test.listener_stopped"]
    assert stopped == ["public", "private"]


@pytest.mark.anyio
async def test_readyz_turns_503_first_on_shutdown(harness: _Harness) -> None:
    with capture_logs() as logs:
        task = asyncio.ensure_future(harness.serve())
        await _until(lambda: any(e["event"] == "app.ready" for e in logs))
        assert (await harness.get("private", "/readyz")).status_code == 200
        harness.stop.set()
        assert await task == 0
    stopping = next(i for i, e in enumerate(logs) if e["event"] == "app.stopping")
    first_stop = next(
        i for i, e in enumerate(logs) if e["event"] == "test.listener_stopped"
    )
    assert stopping < first_stop


@pytest.mark.anyio
async def test_a_listener_dying_on_its_own_exits_1(harness: _Harness) -> None:
    with capture_logs() as logs:
        task = asyncio.ensure_future(harness.serve())
        await _until(lambda: any(e["event"] == "app.ready" for e in logs))
        harness.listeners["public"].ended.set()
        assert await task == 1
    (failed,) = [e for e in logs if e["event"] == "app.failed"]
    assert failed["component"] == "public listener"


@pytest.mark.anyio
async def test_sigterm_stops_the_daemon_cleanly(harness: _Harness) -> None:
    with capture_logs() as logs:
        task = asyncio.ensure_future(harness.serve(own_signals=True))
        await _until(lambda: any(e["event"] == "app.ready" for e in logs))
        os.kill(os.getpid(), signal.SIGTERM)
        assert await asyncio.wait_for(task, 10) == 0
    assert logs[-1]["event"] == "app.stopped"


@pytest.mark.anyio
async def test_a_failed_source_does_not_stop_the_daemon_or_the_feed(
    harness: _Harness, fake_clock: FakeClock
) -> None:
    harness.adapters = (
        _Adapter("euvd", FetchResult((_record(fake_clock),), "c1")),
        _Adapter("cisa_kev", SourceTransportError("cisa_kev", "down")),
    )
    with capture_logs() as logs:
        task = asyncio.ensure_future(harness.serve())
        await _until(
            lambda: (
                sum(e["event"] == "cycle.summary" for e in logs) >= 1
                and any(e["event"] == "source.failed" for e in logs)
            )
        )
        await _until(lambda: harness.listeners["public"].task is not None)
        response = await harness.get("public", f"/feeds/{TOKEN}.xml")
        status = (await harness.get("private", "/status")).json()
        harness.stop.set()
        assert await task == 0
    assert response.status_code == 200
    assert status["sources"]["cisa_kev"]["last_error"]["type"] == "SourceTransportError"
    assert status["sources"]["cisa_kev"]["last_success_at"] is None


@pytest.mark.anyio
async def test_poll_once_logs_one_summary_and_exits_0_even_when_a_source_failed(
    harness: _Harness, fake_clock: FakeClock
) -> None:
    harness.adapters = (
        _Adapter("euvd", FetchResult((_record(fake_clock),), "c1")),
        _Adapter("cisa_kev", SourceTransportError("cisa_kev", "down")),
    )
    with capture_logs() as logs:
        code = await poll_once(
            harness.settings, clock=fake_clock, sources_factory=harness.sources_factory
        )
    assert code == 0
    (summary,) = [e for e in logs if e["event"] == "cycle.summary"]
    assert summary["sources"] == {
        "euvd": {"result": "ok", "records": 1, "skipped": 0},
        "cisa_kev": {"result": "failed", "error_type": "SourceTransportError"},
    }
    assert summary["published"] == {"team-php": 1}
    assert any(e["event"] == "state.migrated" for e in logs)


@pytest.mark.anyio
async def test_poll_once_starts_from_the_stored_cursors(
    harness: _Harness, fake_clock: FakeClock
) -> None:
    harness.adapters = (_Adapter("euvd", FetchResult((_record(fake_clock),), "c1")),)
    await poll_once(
        harness.settings, clock=fake_clock, sources_factory=harness.sources_factory
    )
    await poll_once(
        harness.settings, clock=fake_clock, sources_factory=harness.sources_factory
    )
    assert harness.adapters[0].cursors == [None, "c1"]


@pytest.mark.anyio
@pytest.mark.parametrize(("content", "code"), [(None, 3), ("version: [1\n", 2)])
async def test_poll_once_exit_codes_for_a_bad_configuration(
    harness: _Harness,
    fake_clock: FakeClock,
    capsys: pytest.CaptureFixture[str],
    content: str | None,
    code: int,
) -> None:
    if content is None:
        harness.endpoints.unlink()
    else:
        harness.endpoints.write_text(content, encoding="utf-8")
    result = await poll_once(
        harness.settings, clock=fake_clock, sources_factory=harness.sources_factory
    )
    assert result == code
    assert len(capsys.readouterr().err.splitlines()) == 1


@pytest.mark.anyio
async def test_poll_once_lets_a_cycle_failure_propagate(
    harness: _Harness, fake_clock: FakeClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("disk full")

    monkeypatch.setattr(pipeline_run, "run", boom)
    with pytest.raises(RuntimeError, match="disk full"):
        await poll_once(
            harness.settings, clock=fake_clock, sources_factory=harness.sources_factory
        )
