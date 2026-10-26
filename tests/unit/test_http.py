from collections.abc import AsyncIterator
from datetime import timedelta
from email.utils import format_datetime
from typing import TYPE_CHECKING

import httpx
import pytest
import respx

from cvestream import __version__
from cvestream.errors import CvestreamError
from cvestream.http import (
    BACKOFF_BASE,
    DEFAULT_MAX_BYTES,
    MAX_ATTEMPTS,
    MAX_RETRY_AFTER,
    HttpClient,
    HttpError,
    HttpResponseTooLargeError,
    HttpStatusError,
    HttpTransportError,
)

if TYPE_CHECKING:
    from tests.conftest import FakeClock

URL = "https://example.test/data"


class Recorder:
    """Async `sleep` stand-in that records delays and returns at once."""

    def __init__(self) -> None:
        self.delays: list[float] = []

    async def __call__(self, delay: float) -> None:
        self.delays.append(delay)


def _client(
    recorder: Recorder,
    *,
    random_value: float = 1.0,
    clock: FakeClock | None = None,
    max_attempts: int = MAX_ATTEMPTS,
) -> HttpClient:
    return HttpClient(
        sleep=recorder,
        random=lambda: random_value,
        clock=clock,
        max_attempts=max_attempts,
    )


def test_errors_are_cvestream_errors() -> None:
    for error in (HttpTransportError, HttpStatusError, HttpResponseTooLargeError):
        assert issubclass(error, HttpError)
        assert issubclass(error, CvestreamError)
        assert error.__name__.endswith("Error")
    assert HttpStatusError("boom", status_code=404).status_code == 404


@pytest.mark.anyio
async def test_get_returns_body_and_sends_user_agent(
    respx_mock: respx.MockRouter,
) -> None:
    route = respx_mock.get(URL).respond(200, content=b'{"ok": true}')
    async with _client(Recorder()) as http:
        body = await http.get(URL)
    assert body == b'{"ok": true}'
    request = route.calls.last.request
    assert request.headers["user-agent"].startswith(f"cvestream/{__version__}")
    assert request.headers["accept"] == "application/json"


@pytest.mark.anyio
async def test_get_sends_query_params(respx_mock: respx.MockRouter) -> None:
    route = respx_mock.get(URL).respond(200, content=b"{}")
    async with _client(Recorder()) as http:
        await http.get(URL, params={"page": 2, "size": "100"})
    assert dict(route.calls.last.request.url.params) == {"page": "2", "size": "100"}


@pytest.mark.anyio
async def test_client_does_not_trust_the_environment() -> None:
    async with _client(Recorder()) as http:
        assert http.httpx_client.trust_env is False


@pytest.mark.anyio
async def test_proxy_variables_are_ignored(
    respx_mock: respx.MockRouter, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:9")
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:9")
    monkeypatch.setenv("ALL_PROXY", "http://127.0.0.1:9")
    respx_mock.get(URL).respond(200, content=b"ok")
    async with _client(Recorder()) as http:
        assert http.httpx_client.trust_env is False
        assert await http.get(URL) == b"ok"


@pytest.mark.anyio
async def test_503_is_retried_with_jittered_delay(
    respx_mock: respx.MockRouter,
) -> None:
    route = respx_mock.get(URL).mock(
        side_effect=[httpx.Response(503), httpx.Response(200, content=b"ok")]
    )
    recorder = Recorder()
    async with _client(recorder, random_value=0.5) as http:
        assert await http.get(URL) == b"ok"
    assert route.call_count == 2
    assert recorder.delays == [0.5 * BACKOFF_BASE]


@pytest.mark.anyio
async def test_429_honours_retry_after_seconds(
    respx_mock: respx.MockRouter,
) -> None:
    respx_mock.get(URL).mock(
        side_effect=[
            httpx.Response(429, headers={"Retry-After": "7"}),
            httpx.Response(200, content=b"ok"),
        ]
    )
    recorder = Recorder()
    async with _client(recorder) as http:
        assert await http.get(URL) == b"ok"
    assert recorder.delays == [7.0]


@pytest.mark.anyio
async def test_retry_after_http_date_is_relative_to_the_clock(
    respx_mock: respx.MockRouter, fake_clock: FakeClock
) -> None:
    when = fake_clock.now() + timedelta(seconds=12)
    respx_mock.get(URL).mock(
        side_effect=[
            httpx.Response(
                503, headers={"Retry-After": format_datetime(when, usegmt=True)}
            ),
            httpx.Response(200, content=b"ok"),
        ]
    )
    recorder = Recorder()
    async with _client(recorder, clock=fake_clock) as http:
        assert await http.get(URL) == b"ok"
    assert recorder.delays == [12.0]


@pytest.mark.anyio
async def test_retry_after_date_in_the_past_waits_zero(
    respx_mock: respx.MockRouter, fake_clock: FakeClock
) -> None:
    when = fake_clock.now() - timedelta(minutes=5)
    respx_mock.get(URL).mock(
        side_effect=[
            httpx.Response(
                429, headers={"Retry-After": format_datetime(when, usegmt=True)}
            ),
            httpx.Response(200, content=b"ok"),
        ]
    )
    recorder = Recorder()
    async with _client(recorder, clock=fake_clock) as http:
        await http.get(URL)
    assert recorder.delays == [0.0]


@pytest.mark.anyio
async def test_unusable_retry_after_falls_back_to_backoff(
    respx_mock: respx.MockRouter,
) -> None:
    respx_mock.get(URL).mock(
        side_effect=[
            httpx.Response(503, headers={"Retry-After": "soon"}),
            httpx.Response(200, content=b"ok"),
        ]
    )
    recorder = Recorder()
    async with _client(recorder, random_value=1.0) as http:
        await http.get(URL)
    assert recorder.delays == [BACKOFF_BASE]


@pytest.mark.anyio
async def test_retry_after_above_the_limit_gives_up(
    respx_mock: respx.MockRouter,
) -> None:
    route = respx_mock.get(URL).respond(
        429, headers={"Retry-After": str(int(MAX_RETRY_AFTER) * 2)}
    )
    recorder = Recorder()
    async with _client(recorder) as http:
        with pytest.raises(HttpStatusError) as excinfo:
            await http.get(URL)
    assert excinfo.value.status_code == 429
    assert route.call_count == 1
    assert recorder.delays == []


@pytest.mark.anyio
async def test_timeout_is_retried_then_succeeds(
    respx_mock: respx.MockRouter,
) -> None:
    route = respx_mock.get(URL).mock(
        side_effect=[httpx.ReadTimeout("slow"), httpx.Response(200, content=b"ok")]
    )
    recorder = Recorder()
    async with _client(recorder) as http:
        assert await http.get(URL) == b"ok"
    assert route.call_count == 2
    assert len(recorder.delays) == 1


@pytest.mark.anyio
async def test_timeouts_exhausted_raise_transport_error(
    respx_mock: respx.MockRouter,
) -> None:
    route = respx_mock.get(URL).mock(side_effect=httpx.ConnectTimeout("slow"))
    recorder = Recorder()
    async with _client(recorder) as http:
        with pytest.raises(HttpTransportError) as excinfo:
            await http.get(URL)
    assert route.call_count == MAX_ATTEMPTS
    assert len(recorder.delays) == MAX_ATTEMPTS - 1
    assert isinstance(excinfo.value.__cause__, httpx.ConnectTimeout)


@pytest.mark.anyio
async def test_5xx_exhausted_raises_status_error(
    respx_mock: respx.MockRouter,
) -> None:
    route = respx_mock.get(URL).respond(502)
    async with _client(Recorder()) as http:
        with pytest.raises(HttpStatusError) as excinfo:
            await http.get(URL)
    assert excinfo.value.status_code == 502
    assert route.call_count == MAX_ATTEMPTS


@pytest.mark.anyio
async def test_backoff_doubles_and_is_capped(respx_mock: respx.MockRouter) -> None:
    respx_mock.get(URL).respond(500)
    recorder = Recorder()
    async with _client(recorder, random_value=1.0, max_attempts=9) as http:
        with pytest.raises(HttpStatusError):
            await http.get(URL)
    assert recorder.delays == [1.0, 2.0, 4.0, 8.0, 16.0, 30.0, 30.0, 30.0]


@pytest.mark.anyio
async def test_backoff_uses_full_jitter(respx_mock: respx.MockRouter) -> None:
    respx_mock.get(URL).respond(500)
    recorder = Recorder()
    async with _client(recorder, random_value=0.0) as http:
        with pytest.raises(HttpStatusError):
            await http.get(URL)
    assert recorder.delays == [0.0, 0.0, 0.0]


@pytest.mark.anyio
@pytest.mark.parametrize("status", [400, 401, 403, 404, 410])
async def test_other_4xx_are_not_retried(
    respx_mock: respx.MockRouter, status: int
) -> None:
    route = respx_mock.get(URL).respond(status)
    recorder = Recorder()
    async with _client(recorder) as http:
        with pytest.raises(HttpStatusError) as excinfo:
            await http.get(URL)
    assert excinfo.value.status_code == status
    assert route.call_count == 1
    assert recorder.delays == []


@pytest.mark.anyio
async def test_connect_error_is_not_retried(respx_mock: respx.MockRouter) -> None:
    route = respx_mock.get(URL).mock(side_effect=httpx.ConnectError("refused"))
    recorder = Recorder()
    async with _client(recorder) as http:
        with pytest.raises(HttpTransportError) as excinfo:
            await http.get(URL)
    assert route.call_count == 1
    assert recorder.delays == []
    assert isinstance(excinfo.value.__cause__, httpx.ConnectError)


class _ExplodingStream(httpx.AsyncByteStream):
    async def __aiter__(self) -> AsyncIterator[bytes]:
        msg = "the body must not be read"
        raise AssertionError(msg)
        yield b""  # pragma: no cover


@pytest.mark.anyio
async def test_content_length_above_limit_is_refused_without_reading(
    respx_mock: respx.MockRouter,
) -> None:
    respx_mock.get(URL).mock(
        return_value=httpx.Response(
            200, headers={"Content-Length": "101"}, stream=_ExplodingStream()
        )
    )
    async with _client(Recorder()) as http:
        with pytest.raises(HttpResponseTooLargeError):
            await http.get(URL, max_bytes=100)


@pytest.mark.anyio
async def test_streamed_body_above_limit_is_refused(
    respx_mock: respx.MockRouter,
) -> None:
    async def chunks() -> AsyncIterator[bytes]:
        for _ in range(5):
            yield b"x" * 30

    respx_mock.get(URL).mock(return_value=httpx.Response(200, content=chunks()))
    async with _client(Recorder()) as http:
        with pytest.raises(HttpResponseTooLargeError):
            await http.get(URL, max_bytes=100)


@pytest.mark.anyio
async def test_body_of_exactly_the_limit_is_accepted(
    respx_mock: respx.MockRouter,
) -> None:
    respx_mock.get(URL).respond(200, content=b"x" * 100)
    async with _client(Recorder()) as http:
        assert await http.get(URL, max_bytes=100) == b"x" * 100


@pytest.mark.anyio
async def test_too_large_is_not_retried(respx_mock: respx.MockRouter) -> None:
    route = respx_mock.get(URL).respond(200, content=b"x" * 200)
    recorder = Recorder()
    async with _client(recorder) as http:
        with pytest.raises(HttpResponseTooLargeError):
            await http.get(URL, max_bytes=100)
    assert route.call_count == 1
    assert recorder.delays == []


def test_default_limit_is_sixteen_mebibytes() -> None:
    assert DEFAULT_MAX_BYTES == 16 * 1024 * 1024
