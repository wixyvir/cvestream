"""The daemon: startup order, shutdown order and the one-shot poll.

Startup: logging is configured by the CLI, then the state is migrated, the
private listener starts, the configuration is loaded, the stored feeds become
the in-memory cache, the worker and one loop per source start, the public
listener starts and `/readyz` turns 200. Shutdown: `/readyz` turns 503, the
source loops stop, the current pipeline run finishes (it is never cancelled),
both servers stop, the HTTP client and the state close.

`serve` and `poll_once` return the process exit code; the CLI only passes it on.
"""

import asyncio
import random as random_module
import signal
import sys
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

import structlog

from cvestream.app.feed_cache import FeedCache, FeedCacheHolder
from cvestream.app.private_api import RuntimeStatus, SourceStatus, build_private_app
from cvestream.app.public_api import build_public_app
from cvestream.app.scheduler import (
    PipelineWorker,
    fetch_outcome,
    source_loop,
    supervise,
)
from cvestream.app.servers import (
    ASGIApp,
    Listener,
    ListenerError,
    ManagedServer,
    build_server,
)
from cvestream.clock import Clock, SystemClock
from cvestream.config.provider import (
    ConfigSnapshot,
    ConfigUnavailableError,
    ConfigValidationError,
)
from cvestream.config.yaml_provider import YamlConfigProvider
from cvestream.http import HttpClient
from cvestream.pipeline.run import SourceOutcome, cycle, load_cursors, load_feeds
from cvestream.settings import ListenerSettings, Settings, SourcesSettings, state_url
from cvestream.sources.registry import ScheduledSource, build_sources
from cvestream.state.db import State

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_USER_ERROR = 2
EXIT_CONFIG_UNAVAILABLE = 3

type Sleep = Callable[[float], Awaitable[None]]
type ServerFactory = Callable[[str, ASGIApp, ListenerSettings], Listener]
type SourcesFactory = Callable[
    [SourcesSettings, HttpClient, Clock], tuple[ScheduledSource, ...]
]

log = structlog.get_logger()


def _managed_server(name: str, app: ASGIApp, listener: ListenerSettings) -> Listener:
    return ManagedServer(name, build_server(app, listener))


def _report(message: str) -> None:
    """The one stderr line of a failed startup."""
    sys.stderr.write(f"{message}\n")


async def _load_snapshot(
    provider: YamlConfigProvider,
) -> tuple[ConfigSnapshot | None, int]:
    """The configuration, or the exit code of the failure (already reported)."""
    try:
        return await asyncio.to_thread(provider.load), EXIT_OK
    except ConfigUnavailableError as exc:
        log.error("config.unavailable", error_type=type(exc).__name__)
        _report(str(exc))
        return None, EXIT_CONFIG_UNAVAILABLE
    except ConfigValidationError as exc:
        log.error("config.rejected", errors=list(exc.errors))
        _report(str(exc))
        return None, EXIT_USER_ERROR


@dataclass
class _Running:
    """What has been started so far, so a failure at any step can undo it."""

    state: State
    status: RuntimeStatus
    stop: asyncio.Event
    servers: list[Listener] = field(default_factory=list)
    loops: list[asyncio.Task[None]] = field(default_factory=list)
    worker: asyncio.Task[None] | None = None
    http: HttpClient | None = None


async def _shutdown(running: _Running) -> None:
    running.status.ready = False
    running.status.stopping = True
    log.info("app.stopping")
    running.stop.set()
    for task in running.loops:
        task.cancel()
    await asyncio.gather(*running.loops, return_exceptions=True)
    if running.worker is not None:
        # Never cancelled: a run in its thread must finish before the state closes.
        (outcome,) = await asyncio.gather(running.worker, return_exceptions=True)
        if isinstance(outcome, Exception):
            log.error(
                "app.failed", worker="pipeline", error_type=type(outcome).__name__
            )
    for server in reversed(running.servers):
        await server.stop()
    if running.http is not None:
        await running.http.aclose()
    running.state.dispose()
    log.info("app.stopped")


def _loop_factory(
    scheduled: ScheduledSource,
    worker: PipelineWorker,
    state: State,
    status: RuntimeStatus,
    clock: Clock,
    random: Callable[[], float],
    sleep: Sleep,
) -> Callable[[Callable[[], None]], Awaitable[None]]:
    def factory(iteration_done: Callable[[], None]) -> Awaitable[None]:
        return source_loop(
            scheduled,
            worker,
            state,
            status,
            clock,
            random=random,
            sleep=sleep,
            iteration_done=iteration_done,
        )

    return factory


async def _wait_for_stop(running: _Running) -> int:
    """Wait for `stop`; a server or the worker ending by itself is a failure."""
    watched: dict[asyncio.Future[None], str] = {
        server.task: f"{server.name} listener"
        for server in running.servers
        if server.task is not None
    }
    if running.worker is not None:
        watched[running.worker] = "pipeline worker"
    stopper = asyncio.ensure_future(running.stop.wait())
    try:
        await asyncio.wait({stopper, *watched}, return_when=asyncio.FIRST_COMPLETED)
    finally:
        stopper.cancel()
    if running.stop.is_set():
        return EXIT_OK
    for task, name in watched.items():
        if task.done():
            error = None if task.cancelled() else task.exception()
            log.error(
                "app.failed",
                component=name,
                error_type=type(error).__name__ if error else None,
            )
    return EXIT_FAILED


async def _start(
    settings: Settings,
    running: _Running,
    clock: Clock,
    random: Callable[[], float],
    sleep: Sleep,
    server_factory: ServerFactory,
    sources_factory: SourcesFactory,
) -> int:
    state, status, stop = running.state, running.status, running.stop
    holder = FeedCacheHolder()
    private = server_factory(
        "private", build_private_app(status, holder, clock), settings.http.private
    )
    public = server_factory("public", build_public_app(holder), settings.http.public)

    await asyncio.to_thread(state.migrate)
    await private.start()
    running.servers.append(private)

    provider = YamlConfigProvider(settings.endpoints_file)
    snapshot, failure = await _load_snapshot(provider)
    if snapshot is None:
        return failure

    feeds = await asyncio.to_thread(load_feeds, state)
    holder.current = FeedCache.build(snapshot, feeds, clock.now())

    running.http = HttpClient()
    sources = sources_factory(settings.sources, running.http, clock)
    worker = PipelineWorker(state, provider, snapshot, holder, clock)
    running.worker = asyncio.create_task(worker.work(stop))
    for scheduled in sources:
        name = scheduled.adapter.name
        status.sources[name] = SourceStatus(name, scheduled.interval)
        factory = _loop_factory(scheduled, worker, state, status, clock, random, sleep)
        running.loops.append(
            asyncio.create_task(supervise(name, factory, sleep=sleep, stop=stop))
        )
    log.info("app.started", sources=[s.adapter.name for s in sources])

    await public.start()
    running.servers.append(public)
    status.ready = True
    log.info("app.ready")
    return await _wait_for_stop(running)


async def serve(
    settings: Settings,
    *,
    clock: Clock | None = None,
    stop: asyncio.Event | None = None,
    random: Callable[[], float] = random_module.random,
    sleep: Sleep = asyncio.sleep,
    server_factory: ServerFactory = _managed_server,
    sources_factory: SourcesFactory = build_sources,
) -> int:
    """Run the daemon until SIGTERM, SIGINT or `stop`; return the exit code.

    `SettingsError` and `StateError` are not caught: they are user errors the
    CLI reports in one line.
    """
    clock = clock or SystemClock()
    # Signals are the runtime's own business unless a test injects `stop`.
    signals = (signal.SIGTERM, signal.SIGINT) if stop is None else ()
    stop = stop or asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in signals:
        loop.add_signal_handler(sig, stop.set)
    running = _Running(
        state=State(state_url(settings)),
        status=RuntimeStatus(started_at=clock.now()),
        stop=stop,
    )
    code = EXIT_OK
    try:
        code = await _start(
            settings, running, clock, random, sleep, server_factory, sources_factory
        )
    except ListenerError as exc:
        log.error("app.failed", error_type=type(exc).__name__)
        _report(str(exc))
        code = EXIT_FAILED
    finally:
        await _shutdown(running)
        for sig in signals:
            loop.remove_signal_handler(sig)
    return code


async def poll_once(
    settings: Settings,
    *,
    clock: Clock | None = None,
    sources_factory: SourcesFactory = build_sources,
) -> int:
    """Fetch every enabled source once and run one cycle; return the exit code.

    A failed source is in the `cycle.summary`, not in the exit code. A cycle
    exception propagates (the CLI turns it into exit code 1).
    """
    clock = clock or SystemClock()
    state = State(state_url(settings))
    try:
        await asyncio.to_thread(state.migrate)
        provider = YamlConfigProvider(settings.endpoints_file)
        snapshot, failure = await _load_snapshot(provider)
        if snapshot is None:
            return failure
        cursors = await asyncio.to_thread(load_cursors, state)
        outcomes: list[SourceOutcome] = []
        async with HttpClient() as http:
            for scheduled in sources_factory(settings.sources, http, clock):
                adapter = scheduled.adapter
                outcomes.append(await fetch_outcome(adapter, cursors.get(adapter.name)))
        await asyncio.to_thread(cycle, state, provider, snapshot, outcomes, clock)
        return EXIT_OK
    finally:
        state.dispose()
