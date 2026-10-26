from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta, timezone
from types import MappingProxyType

import httpx
import pytest

from cvestream.app.feed_cache import FeedCache, FeedCacheHolder
from cvestream.app.private_api import RuntimeStatus, SourceStatus, build_private_app
from cvestream.config.provider import ConfigSnapshot
from cvestream.domain.endpoint import Endpoint, ProductEntry
from cvestream.pipeline.render import RenderedFeed, etag_of, render_feed
from tests.conftest import FakeClock

START = datetime(2026, 10, 26, 12, 0, tzinfo=UTC)
TOKEN = "t" * 43
PHP = Endpoint("team-php", "PHP team", (ProductEntry("php"),))
GO = Endpoint("team-go", "Go team", (ProductEntry("go"),))


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock(START)


@pytest.fixture
def status() -> RuntimeStatus:
    return RuntimeStatus(started_at=START)


@pytest.fixture
def holder() -> FeedCacheHolder:
    xml = render_feed(PHP, ())
    feed = RenderedFeed("team-php", xml, etag_of(xml), 4, START, START)
    snapshot = ConfigSnapshot(
        "r",
        (PHP, GO),
        MappingProxyType({TOKEN: "team-php", "g" * 43: "team-go"}),
    )
    return FeedCacheHolder(FeedCache.build(snapshot, {"team-php": feed}, START))


@pytest.fixture
async def client(
    status: RuntimeStatus, holder: FeedCacheHolder, clock: FakeClock
) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=build_private_app(status, holder, clock))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as http:
        yield http


@pytest.mark.anyio
async def test_healthz_is_ok(client: httpx.AsyncClient) -> None:
    response = await client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


@pytest.mark.anyio
async def test_readyz_is_503_until_the_runtime_is_ready(
    client: httpx.AsyncClient, status: RuntimeStatus
) -> None:
    response = await client.get("/readyz")
    assert response.status_code == 503
    assert response.json() == {
        "error": {
            "code": "not_ready",
            "message": "configuration or feed cache not loaded",
        }
    }
    status.ready = True
    response = await client.get("/readyz")
    assert response.status_code == 200
    assert response.json() == {"status": "ready"}


@pytest.mark.anyio
async def test_readyz_is_503_again_during_shutdown(
    client: httpx.AsyncClient, status: RuntimeStatus
) -> None:
    status.ready = True
    status.stopping = True
    response = await client.get("/readyz")
    assert response.status_code == 503
    assert response.json()["error"] == {
        "code": "not_ready",
        "message": "shutting down",
    }


@pytest.mark.anyio
async def test_readyz_does_not_depend_on_sources(
    client: httpx.AsyncClient, status: RuntimeStatus
) -> None:
    status.ready = True
    status.sources["euvd"] = SourceStatus("euvd", timedelta(minutes=15))
    status.sources["euvd"].last_error_type = "SourceTransportError"
    status.sources["euvd"].last_error_at = START
    assert (await client.get("/readyz")).status_code == 200


@pytest.mark.anyio
async def test_status_shape(
    client: httpx.AsyncClient, status: RuntimeStatus, clock: FakeClock
) -> None:
    status.sources["euvd"] = SourceStatus(
        "euvd",
        timedelta(minutes=15),
        last_success_at=START - timedelta(minutes=5),
        last_error_type="SourceTransportError",
        last_error_at=datetime(
            2026, 10, 26, 13, 0, tzinfo=timezone(timedelta(hours=1))
        ),
        cursor="2026-10-26T11:00:00Z",
    )
    status.sources["cisa_kev"] = SourceStatus("cisa_kev", timedelta(hours=1))
    response = await client.get("/status")
    assert response.status_code == 200
    assert response.json() == {
        "sources": {
            "euvd": {
                "interval_seconds": 900,
                "last_success_at": "2026-10-26T11:55:00Z",
                "last_error": {
                    "type": "SourceTransportError",
                    "at": "2026-10-26T12:00:00Z",
                },
                "cursor": "2026-10-26T11:00:00Z",
                "stale": False,
            },
            "cisa_kev": {
                "interval_seconds": 3600,
                "last_success_at": None,
                "last_error": None,
                "cursor": None,
                "stale": False,
            },
        },
        "endpoints": {
            "team-php": {"item_count": 4, "last_render_at": "2026-10-26T12:00:00Z"},
            "team-go": {"item_count": 0, "last_render_at": None},
        },
    }


@pytest.mark.anyio
async def test_status_staleness_follows_the_clock(
    client: httpx.AsyncClient, status: RuntimeStatus, clock: FakeClock
) -> None:
    status.sources["euvd"] = SourceStatus("euvd", timedelta(minutes=10))
    # never succeeded: counted from the start of the daemon
    clock.advance(timedelta(minutes=30))
    body = (await client.get("/status")).json()
    assert body["sources"]["euvd"]["stale"] is False
    clock.advance(timedelta(seconds=1))
    body = (await client.get("/status")).json()
    assert body["sources"]["euvd"]["stale"] is True
    status.sources["euvd"].last_success_at = clock.now()
    body = (await client.get("/status")).json()
    assert body["sources"]["euvd"]["stale"] is False


@pytest.mark.anyio
async def test_status_has_no_token_and_no_feed_path(
    client: httpx.AsyncClient, status: RuntimeStatus
) -> None:
    status.ready = True
    for path in ("/status", "/healthz", "/readyz", "/nothing"):
        text = (await client.get(path)).text
        assert TOKEN not in text
        assert "/feeds/" not in text
        assert ".xml" not in text


@pytest.mark.anyio
@pytest.mark.parametrize("path", [f"/feeds/{TOKEN}.xml", "/healthz/", "/status/"])
async def test_unknown_path_is_a_json_404(client: httpx.AsyncClient, path: str) -> None:
    response = await client.get(path)
    assert response.status_code == 404
    assert response.json() == {
        "error": {"code": "not_found", "message": "no such endpoint"}
    }


@pytest.mark.anyio
async def test_a_wrong_method_is_a_json_405(client: httpx.AsyncClient) -> None:
    response = await client.post("/status")
    assert response.status_code == 405
    assert response.json()["error"]["code"] == "method_not_allowed"
    assert "GET" in response.headers["allow"]


def test_is_stale_boundary(status: RuntimeStatus) -> None:
    source = SourceStatus("euvd", timedelta(minutes=10), last_success_at=START)
    assert status.is_stale(source, START + timedelta(minutes=30)) is False
    assert status.is_stale(source, START + timedelta(minutes=30, seconds=1)) is True
