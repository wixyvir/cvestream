"""The public listener: `GET` and `HEAD /feeds/{token}.xml` and nothing else (FR-20).

Handlers read only the in-memory feed cache. The token is the access control:
an unknown, revoked or disabled one is a `404` with an empty body, never `401`
or `403`. Nothing here logs a token, a path or a request URL.
"""

import time
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime

import structlog
from starlette.applications import Starlette
from starlette.exceptions import HTTPException
from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import Route

from cvestream.app.feed_cache import CachedFeed, FeedCacheHolder
from cvestream.pipeline.render import MEDIA_TYPE

CACHE_CONTROL = "max-age=300"

log = structlog.get_logger()


def _not_found(request: Request) -> Response:
    log.info("feed.not_found", method=request.method)
    return Response(status_code=404)


async def _handle_not_found(request: Request, _exc: Exception) -> Response:
    return _not_found(request)


async def _handle_method_not_allowed(_request: Request, exc: Exception) -> Response:
    headers = exc.headers if isinstance(exc, HTTPException) and exc.headers else None
    return Response(status_code=405, headers=headers)


def _etag_matches(header: str, etag: str) -> bool:
    if header.strip() == "*":
        return True
    candidates = (c.strip().removeprefix("W/") for c in header.split(","))
    return etag.removeprefix("W/") in candidates


def _modified_since(header: str, changed_at: datetime) -> bool:
    """True when the feed has not changed after the date in `header`."""
    try:
        since = parsedate_to_datetime(header)
    except TypeError, ValueError:
        return False
    if since.tzinfo is None or since.utcoffset() is None:
        return False
    return changed_at.astimezone(UTC).replace(microsecond=0) <= since


def _not_modified(request: Request, cached: CachedFeed) -> bool:
    # `If-None-Match` takes precedence: `If-Modified-Since` is then ignored.
    if_none_match = request.headers.get("if-none-match")
    if if_none_match is not None:
        return _etag_matches(if_none_match, cached.etag)
    if_modified_since = request.headers.get("if-modified-since")
    if if_modified_since is not None:
        return _modified_since(if_modified_since, cached.changed_at)
    return False


def build_public_app(holder: FeedCacheHolder) -> Starlette:
    async def feed(request: Request) -> Response:
        started = time.monotonic()
        cached = holder.current.get(request.path_params["token"])
        if cached is None:
            return _not_found(request)
        headers = {
            "ETag": cached.etag,
            "Last-Modified": cached.last_modified,
            "Cache-Control": CACHE_CONTROL,
        }
        if _not_modified(request, cached):
            response = Response(status_code=304, headers=headers)
        elif request.method == "HEAD":
            # uvicorn drops the body of a HEAD response, ASGI test transports
            # do not: send none, with the length of the full body.
            headers["Content-Length"] = str(len(cached.body))
            response = Response(status_code=200, headers=headers, media_type=MEDIA_TYPE)
        else:
            response = Response(
                cached.body, status_code=200, headers=headers, media_type=MEDIA_TYPE
            )
        log.info(
            "feed.served",
            endpoint_id=cached.endpoint_id,
            method=request.method,
            status=response.status_code,
            duration_ms=round((time.monotonic() - started) * 1000),
        )
        return response

    return Starlette(
        routes=[Route("/feeds/{token}.xml", feed, methods=["GET"])],
        exception_handlers={404: _handle_not_found, 405: _handle_method_not_allowed},
    )
