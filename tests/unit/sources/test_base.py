import dataclasses

import httpx
import pytest
import respx

from cvestream.errors import CvestreamError
from cvestream.http import HttpClient
from cvestream.sources.base import (
    FetchResult,
    SourceContractError,
    SourceError,
    SourcePayloadError,
    SourceTransportError,
    get_json,
)

URL = "https://example.test/data"


async def _no_sleep(_delay: float) -> None:
    return None


def _client() -> HttpClient:
    return HttpClient(sleep=_no_sleep, random=lambda: 0.0, max_attempts=2)


@pytest.mark.parametrize(
    "error", [SourceTransportError, SourcePayloadError, SourceContractError]
)
def test_error_hierarchy(error: type[SourceError]) -> None:
    assert issubclass(error, SourceError)
    assert issubclass(error, CvestreamError)
    assert error.__name__.endswith("Error")


def test_source_error_keeps_source_and_message() -> None:
    error = SourcePayloadError("euvd", "not JSON")
    assert error.source == "euvd"
    assert str(error) == "not JSON"


def test_fetch_result_is_frozen_and_defaults() -> None:
    result = FetchResult(records=(), next_cursor=None)
    assert result.skipped == 0
    field = "skipped"
    with pytest.raises(dataclasses.FrozenInstanceError):
        setattr(result, field, 1)


@pytest.mark.anyio
async def test_get_json_returns_the_decoded_object(
    respx_mock: respx.MockRouter,
) -> None:
    respx_mock.get(URL).respond(200, content=b'{"items": [1, 2]}')
    async with _client() as http:
        assert await get_json(http, "euvd", URL) == {"items": [1, 2]}


@pytest.mark.anyio
async def test_get_json_passes_params(respx_mock: respx.MockRouter) -> None:
    route = respx_mock.get(URL).respond(200, content=b"[]")
    async with _client() as http:
        await get_json(http, "euvd", URL, params={"page": 3})
    assert route.calls.last.request.url.params["page"] == "3"


@pytest.mark.anyio
async def test_too_large_becomes_payload_error(respx_mock: respx.MockRouter) -> None:
    respx_mock.get(URL).respond(200, content=b"[" + b"1," * 100 + b"1]")
    async with _client() as http:
        with pytest.raises(SourcePayloadError) as excinfo:
            await get_json(http, "euvd", URL, max_bytes=50)
    assert excinfo.value.source == "euvd"
    assert excinfo.value.__cause__ is not None


@pytest.mark.anyio
@pytest.mark.parametrize("status", [404, 503])
async def test_status_error_becomes_transport_error(
    respx_mock: respx.MockRouter, status: int
) -> None:
    respx_mock.get(URL).respond(status)
    async with _client() as http:
        with pytest.raises(SourceTransportError) as excinfo:
            await get_json(http, "cisa_kev", URL)
    assert excinfo.value.source == "cisa_kev"


@pytest.mark.anyio
async def test_network_error_becomes_transport_error(
    respx_mock: respx.MockRouter,
) -> None:
    respx_mock.get(URL).mock(side_effect=httpx.ConnectError("refused"))
    async with _client() as http:
        with pytest.raises(SourceTransportError):
            await get_json(http, "euvd", URL)


@pytest.mark.anyio
@pytest.mark.parametrize("body", [b"<html>", b"", b'{"items": [', b"\xff\xfe\x00"])
async def test_invalid_json_becomes_payload_error(
    respx_mock: respx.MockRouter, body: bytes
) -> None:
    respx_mock.get(URL).respond(200, content=body)
    async with _client() as http:
        with pytest.raises(SourcePayloadError):
            await get_json(http, "euvd", URL)
