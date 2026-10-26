"""Outbound HTTP.

The only place that talks to the network and the only place that retries.
Proxy variables are ignored (`trust_env=False`) and TLS is verified against the
system trust store. Retries happen on timeouts, 5xx and 429 only; every other
failure surfaces at once and the next poll cycle is the retry.
"""

import asyncio
import random as random_module
import ssl
from collections.abc import Awaitable, Callable, Mapping
from datetime import UTC
from email.utils import parsedate_to_datetime
from types import TracebackType
from typing import Self

import httpx
import structlog

from cvestream import __version__
from cvestream.clock import Clock, SystemClock
from cvestream.errors import CvestreamError

DEFAULT_TIMEOUT = httpx.Timeout(connect=10.0, read=30.0, write=10.0, pool=10.0)
DEFAULT_MAX_BYTES = 16 * 1024 * 1024  # CISA KEV is ~1.8 MB, an EUVD page ~0.4 MB
MAX_ATTEMPTS = 4  # 1 try + 3 retries
BACKOFF_BASE = 1.0  # seconds
BACKOFF_CAP = 30.0  # seconds, per wait
MAX_RETRY_AFTER = 60.0  # a longer Retry-After: give up, the next cycle retries
USER_AGENT = f"cvestream/{__version__} (+https://github.com/wixyvir/cvestream)"

log = structlog.get_logger()


class HttpError(CvestreamError):
    """An outbound request failed."""


class HttpTransportError(HttpError):
    """No usable response: timeout after retries, connection, TLS or protocol error."""


class HttpStatusError(HttpError):
    """The final response had a non-2xx status."""

    def __init__(self, message: str, *, status_code: int) -> None:
        super().__init__(message)
        self.status_code = status_code


class HttpResponseTooLargeError(HttpError):
    """The response body is larger than the allowed maximum."""


class _RetryableError(Exception):
    """Internal: one attempt failed in a way that may be retried."""

    def __init__(
        self,
        final: HttpError,
        *,
        status: int | None,
        retry_after: float | None,
    ) -> None:
        super().__init__(str(final))
        self.final = final
        self.status = status
        self.retry_after = retry_after


class HttpClient:
    """One shared `httpx.AsyncClient` with retries and a response size limit."""

    def __init__(
        self,
        *,
        timeout: httpx.Timeout = DEFAULT_TIMEOUT,
        max_attempts: int = MAX_ATTEMPTS,
        clock: Clock | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        random: Callable[[], float] = random_module.random,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._max_attempts = max_attempts
        self._clock = clock or SystemClock()
        self._sleep = sleep
        self._random = random
        # httpx defaults to certifi; an SSLContext makes it the system store.
        self._client = httpx.AsyncClient(
            trust_env=False,
            verify=ssl.create_default_context(),
            timeout=timeout,
            follow_redirects=True,
            headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
            transport=transport,
        )

    @property
    def httpx_client(self) -> httpx.AsyncClient:
        return self._client

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._client.aclose()

    async def get(
        self,
        url: str,
        *,
        params: Mapping[str, str | int] | None = None,
        max_bytes: int = DEFAULT_MAX_BYTES,
    ) -> bytes:
        """GET `url` and return the body, retrying timeouts, 5xx and 429."""
        attempt = 1
        while True:
            try:
                return await self._attempt(url, params, max_bytes)
            except _RetryableError as failure:
                delay = self._retry_delay(failure, attempt)
                if delay is None or attempt >= self._max_attempts:
                    raise failure.final from failure.final.__cause__
                log.warning(
                    "http.retried",
                    url=url,
                    attempt=attempt,
                    status=failure.status,
                    delay=delay,
                    error_type=type(failure.final).__name__,
                )
                await self._sleep(delay)
                attempt += 1

    def _retry_delay(self, failure: _RetryableError, attempt: int) -> float | None:
        """Seconds to wait before the next attempt; `None` means give up."""
        if failure.retry_after is not None:
            if failure.retry_after > MAX_RETRY_AFTER:
                return None
            return failure.retry_after
        ceiling = min(BACKOFF_CAP, BACKOFF_BASE * 2 ** (attempt - 1))
        return self._random() * ceiling

    async def _attempt(
        self, url: str, params: Mapping[str, str | int] | None, max_bytes: int
    ) -> bytes:
        try:
            async with self._client.stream("GET", url, params=params) as response:
                status = response.status_code
                if status == 429 or status >= 500:
                    raise _RetryableError(
                        HttpStatusError(
                            f"GET {url}: HTTP {status}", status_code=status
                        ),
                        status=status,
                        retry_after=self._parse_retry_after(
                            response.headers.get("Retry-After")
                        ),
                    )
                if not response.is_success:
                    msg = f"GET {url}: HTTP {status}"
                    raise HttpStatusError(msg, status_code=status)
                return await _read_body(response, url, max_bytes)
        except httpx.TimeoutException as exc:
            final = HttpTransportError(f"GET {url}: {type(exc).__name__}")
            final.__cause__ = exc
            raise _RetryableError(final, status=None, retry_after=None) from exc
        except httpx.RequestError as exc:
            msg = f"GET {url}: {type(exc).__name__}"
            raise HttpTransportError(msg) from exc

    def _parse_retry_after(self, value: str | None) -> float | None:
        if value is None:
            return None
        text = value.strip()
        if text.isascii() and text.isdigit():
            return float(text)
        try:
            when = parsedate_to_datetime(text)
        except TypeError, ValueError:
            return None
        if when.tzinfo is None:
            when = when.replace(tzinfo=UTC)
        return max(0.0, (when - self._clock.now()).total_seconds())


async def _read_body(response: httpx.Response, url: str, max_bytes: int) -> bytes:
    declared = response.headers.get("Content-Length")
    if declared is not None and declared.isascii() and declared.isdigit():
        if int(declared) > max_bytes:
            msg = f"GET {url}: Content-Length {declared} exceeds {max_bytes} bytes"
            raise HttpResponseTooLargeError(msg)
    body = bytearray()
    async for chunk in response.aiter_bytes():
        body.extend(chunk)
        if len(body) > max_bytes:
            msg = f"GET {url}: body exceeds {max_bytes} bytes"
            raise HttpResponseTooLargeError(msg)
    return bytes(body)
