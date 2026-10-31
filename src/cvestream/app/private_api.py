"""The private listener: health, readiness and status (never published).

Handlers read only in-memory objects. No token and no feed path appears in any
body. Errors are `{"error": {"code", "message"}}`; success has no envelope.
"""

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import StrEnum

from starlette.applications import Starlette
from starlette.exceptions import HTTPException
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from cvestream.app.feed_cache import FeedCacheHolder
from cvestream.clock import Clock

# FR-6: a source is stale after this many intervals without a success.
STALE_AFTER_INTERVALS = 3


class SourceActivity(StrEnum):
    """What a source loop is doing right now."""

    # Waiting for its next fetch (or not started yet).
    IDLE = "idle"
    FETCHING = "fetching"
    # Fetched (or failed); waiting for the pipeline run that carries it.
    PROCESSING = "processing"
    # The loop crashed; the supervisor restarts it at `next_fetch_at`.
    CRASHED = "crashed"


@dataclass
class SourceStatus:
    """Mutated only on the event loop."""

    name: str
    interval: timedelta
    last_success_at: datetime | None = None
    last_error_type: str | None = None
    last_error_at: datetime | None = None
    cursor: str | None = None
    activity: SourceActivity = SourceActivity.IDLE
    activity_since: datetime | None = None
    # Set only while `idle` or `crashed`.
    next_fetch_at: datetime | None = None

    def enter(
        self,
        activity: SourceActivity,
        now: datetime,
        next_fetch_at: datetime | None = None,
    ) -> None:
        self.activity = activity
        self.activity_since = now
        self.next_fetch_at = next_fetch_at


@dataclass
class RuntimeStatus:
    """Mutated only on the event loop."""

    started_at: datetime
    ready: bool = False
    # Set when shutdown starts: `/readyz` says why it is not ready.
    stopping: bool = False
    sources: dict[str, SourceStatus] = field(default_factory=dict)

    def is_stale(self, source: SourceStatus, now: datetime) -> bool:
        """No success for more than 3 intervals (from the start until the first)."""
        last = source.last_success_at or self.started_at
        return now - last > STALE_AFTER_INTERVALS * source.interval


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _iso_or_none(value: datetime | None) -> str | None:
    return _iso(value) if value is not None else None


def _error(status_code: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(
        {"error": {"code": code, "message": message}}, status_code=status_code
    )


async def _handle_not_found(_request: Request, _exc: Exception) -> Response:
    return _error(404, "not_found", "no such endpoint")


async def _handle_method_not_allowed(_request: Request, exc: Exception) -> Response:
    response = _error(405, "method_not_allowed", "method not allowed")
    if isinstance(exc, HTTPException) and exc.headers:
        response.headers.update(exc.headers)
    return response


def build_private_app(
    status: RuntimeStatus, holder: FeedCacheHolder, clock: Clock
) -> Starlette:
    async def healthz(_request: Request) -> Response:
        return JSONResponse({"status": "ok"})

    async def readyz(_request: Request) -> Response:
        if status.stopping:
            return _error(503, "not_ready", "shutting down")
        if not status.ready:
            return _error(503, "not_ready", "configuration or feed cache not loaded")
        return JSONResponse({"status": "ready"})

    async def report(_request: Request) -> Response:
        now = clock.now()
        sources = {
            name: {
                "activity": source.activity.value,
                "activity_since": _iso_or_none(source.activity_since),
                "next_fetch_at": _iso_or_none(source.next_fetch_at),
                "interval_seconds": int(source.interval.total_seconds()),
                "last_success_at": (
                    _iso(source.last_success_at) if source.last_success_at else None
                ),
                "last_error": (
                    {
                        "type": source.last_error_type,
                        "at": _iso(source.last_error_at),
                    }
                    if source.last_error_type and source.last_error_at
                    else None
                ),
                "cursor": source.cursor,
                "stale": status.is_stale(source, now),
            }
            for name, source in status.sources.items()
        }
        endpoints = {
            endpoint_id: {
                "item_count": feed.item_count,
                "last_render_at": _iso(feed.rendered_at) if feed.rendered_at else None,
            }
            for endpoint_id, feed in holder.current.by_endpoint.items()
        }
        return JSONResponse({"sources": sources, "endpoints": endpoints})

    app = Starlette(
        routes=[
            Route("/healthz", healthz, methods=["GET"]),
            Route("/readyz", readyz, methods=["GET"]),
            Route("/status", report, methods=["GET"]),
        ],
        exception_handlers={404: _handle_not_found, 405: _handle_method_not_allowed},
    )
    # No 307 from `/healthz/` to `/healthz`: an unknown path is a JSON 404.
    app.router.redirect_slashes = False
    return app
