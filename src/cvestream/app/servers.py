"""The two embedded uvicorn servers.

uvicorn is used as a library: it must not install its own signal handlers (the
runtime owns SIGTERM and SIGINT), must not reconfigure logging (structlog owns
it) and must not write an access log (the request path holds the feed token).
"""

import asyncio
from collections.abc import Awaitable, Callable, Generator, MutableMapping
from contextlib import contextmanager
from typing import Any, Protocol

import structlog
import uvicorn

from cvestream.errors import CvestreamError
from cvestream.settings import ListenerSettings

type ASGIApp = Callable[
    [
        MutableMapping[str, Any],
        Callable[[], Awaitable[MutableMapping[str, Any]]],
        Callable[[MutableMapping[str, Any]], Awaitable[None]],
    ],
    Awaitable[None],
]

# `Server.started` is polled: uvicorn has no startup callback.
STARTUP_POLL_SECONDS = 0.01
STARTUP_TIMEOUT_SECONDS = 10.0
GRACEFUL_SHUTDOWN_SECONDS = 5

log = structlog.get_logger()


class ListenerError(CvestreamError):
    """A listener could not start (the port is taken, for instance)."""


class EmbeddedServer(uvicorn.Server):
    """uvicorn without its signal handling.

    `uvicorn.Server.serve()` installs SIGINT and SIGTERM handlers and raises the
    captured signal again after shutdown; two servers and the runtime's own
    handlers would fight over them.
    """

    @contextmanager
    def capture_signals(self) -> Generator[None]:
        yield


def build_server(app: ASGIApp, listener: ListenerSettings) -> EmbeddedServer:
    return EmbeddedServer(
        uvicorn.Config(
            app,
            host=listener.host,
            port=listener.port,
            log_config=None,
            access_log=False,
            server_header=False,
            proxy_headers=False,
            lifespan="off",
            timeout_graceful_shutdown=GRACEFUL_SHUTDOWN_SECONDS,
        )
    )


class Listener(Protocol):
    """What the runtime needs from a server: start, stop, and its task."""

    name: str

    @property
    def task(self) -> asyncio.Future[None] | None: ...

    async def start(self) -> None: ...

    async def stop(self) -> None: ...


class ManagedServer:
    """One `EmbeddedServer` running as a task of the event loop."""

    def __init__(self, name: str, server: EmbeddedServer) -> None:
        self.name = name
        self._server = server
        self._task: asyncio.Task[None] | None = None

    @property
    def task(self) -> asyncio.Task[None] | None:
        return self._task

    async def _serve(self) -> None:
        try:
            await self._server.serve()
        except SystemExit:
            # uvicorn calls `sys.exit(1)` when it cannot bind. Inside a task,
            # SystemExit would tear the whole event loop down.
            msg = f"{self.name} listener failed to start"
            raise ListenerError(msg) from None

    async def start(self) -> None:
        """Return once the socket is bound; raise `ListenerError` otherwise."""
        task = asyncio.create_task(self._serve())
        self._task = task
        waited = 0.0
        while not self._server.started and not task.done():
            if waited >= STARTUP_TIMEOUT_SECONDS:
                self._server.should_exit = True
                msg = f"{self.name} listener did not start in time"
                raise ListenerError(msg)
            await asyncio.sleep(STARTUP_POLL_SECONDS)
            waited += STARTUP_POLL_SECONDS
        if task.done():
            task.result()  # raises ListenerError (or whatever killed it)
            msg = f"{self.name} listener stopped at startup"
            raise ListenerError(msg)

    async def stop(self) -> None:
        self._server.should_exit = True
        task = self._task
        if task is None:
            return
        try:
            await task
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - the server is going away
            log.error("app.failed", listener=self.name, error_type=type(exc).__name__)
