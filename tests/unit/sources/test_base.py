import dataclasses
from datetime import UTC, datetime

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
    clean_text,
    get_bytes,
    get_json,
    parse_calendar_date,
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
@pytest.mark.parametrize(
    "body",
    [
        b"<html>",
        b"",
        b'{"items": [',
        b"\xff\xfe\x00",
        b"[" * 1_000_000 + b"]" * 1_000_000,
    ],
    ids=["html", "empty", "truncated", "bad-utf8", "deeply-nested"],
)
async def test_invalid_json_becomes_payload_error(
    respx_mock: respx.MockRouter, body: bytes
) -> None:
    respx_mock.get(URL).respond(200, content=body)
    async with _client() as http:
        with pytest.raises(SourcePayloadError):
            await get_json(http, "euvd", URL)


@pytest.mark.parametrize("value", ["", "  ", "n/a", "N/A", "na", "-", " - ", "Unknown"])
def test_clean_text_turns_placeholders_into_none(value: str) -> None:
    assert clean_text(value) is None


@pytest.mark.parametrize("value", [None, 3, 1.5, ["x"], {"a": 1}])
def test_clean_text_turns_non_text_into_none(value: object) -> None:
    assert clean_text(value) is None


def test_clean_text_strips_real_text() -> None:
    assert clean_text("  PHP Group \n") == "PHP Group"
    assert clean_text("n/a-ish") == "n/a-ish"


def test_parse_calendar_date_is_midnight_utc() -> None:
    assert parse_calendar_date("2026-10-23") == datetime(2026, 10, 23, tzinfo=UTC)
    assert parse_calendar_date(" 2023-07-19 ") == datetime(2023, 7, 19, tzinfo=UTC)


@pytest.mark.parametrize(
    "value",
    [
        None,
        20261023,
        "",
        "2026-13-01",
        "2026-02-30",
        "23/10/2026",
        "2026-10-23T10:00:00Z",
    ],
)
def test_parse_calendar_date_rejects_everything_else(value: object) -> None:
    assert parse_calendar_date(value) is None


@pytest.mark.anyio
async def test_get_bytes_returns_the_raw_body(respx_mock: respx.MockRouter) -> None:
    respx_mock.get(URL).respond(200, content=b"<rss/>")
    async with _client() as http:
        assert await get_bytes(http, "cert_eu", URL) == b"<rss/>"


@pytest.mark.anyio
async def test_get_bytes_passes_params(respx_mock: respx.MockRouter) -> None:
    route = respx_mock.get(URL).respond(200, content=b"x")
    async with _client() as http:
        await get_bytes(http, "cert_eu", URL, params={"page": 3})
    assert route.calls.last.request.url.params["page"] == "3"


@pytest.mark.anyio
async def test_get_bytes_too_large_is_a_payload_error(
    respx_mock: respx.MockRouter,
) -> None:
    respx_mock.get(URL).respond(200, content=b"x" * 100)
    async with _client() as http:
        with pytest.raises(SourcePayloadError) as excinfo:
            await get_bytes(http, "cert_eu", URL, max_bytes=50)
    assert excinfo.value.source == "cert_eu"
    assert excinfo.value.__cause__ is not None


@pytest.mark.anyio
async def test_get_bytes_http_error_is_a_transport_error(
    respx_mock: respx.MockRouter,
) -> None:
    respx_mock.get(URL).respond(404)
    async with _client() as http:
        with pytest.raises(SourceTransportError) as excinfo:
            await get_bytes(http, "cert_eu", URL)
    assert excinfo.value.source == "cert_eu"
