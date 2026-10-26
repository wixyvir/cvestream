from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from email.utils import format_datetime
from types import MappingProxyType

import httpx
import pytest
from structlog.testing import capture_logs

from cvestream.app.feed_cache import FeedCache, FeedCacheHolder
from cvestream.app.public_api import build_public_app
from cvestream.config.provider import ConfigSnapshot
from cvestream.domain.endpoint import Endpoint, ProductEntry
from cvestream.pipeline.render import MEDIA_TYPE, RenderedFeed, etag_of, render_feed

NOW = datetime(2026, 10, 26, 20, 0, tzinfo=UTC)
CHANGED = datetime(2026, 10, 26, 18, 30, 15, 123456, tzinfo=UTC)
TOKEN = "t" * 43
TOKEN_OFF = "o" * 43
TOKEN_NEW = "n" * 43
PHP = Endpoint("team-php", "PHP team", (ProductEntry("php"),))
OFF = Endpoint("off", "Off", (ProductEntry("php"),), enabled=False)


def _snapshot(
    *pairs: tuple[str, str], endpoints: tuple[Endpoint, ...] = (PHP, OFF)
) -> ConfigSnapshot:
    return ConfigSnapshot(
        "r",
        endpoints,
        MappingProxyType(dict(pairs) or {TOKEN: "team-php", TOKEN_OFF: "off"}),
    )


def _cache(snapshot: ConfigSnapshot | None = None) -> FeedCache:
    xml = render_feed(PHP, ())
    feed = RenderedFeed("team-php", xml, etag_of(xml), 0, NOW, CHANGED)
    return FeedCache.build(snapshot or _snapshot(), {"team-php": feed}, NOW)


@pytest.fixture
def holder() -> FeedCacheHolder:
    return FeedCacheHolder(_cache())


@pytest.fixture
async def client(holder: FeedCacheHolder) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=build_public_app(holder))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as http:
        yield http


def _expected(holder: FeedCacheHolder) -> tuple[bytes, str]:
    cached = holder.current.get(TOKEN)
    assert cached is not None
    return cached.body, cached.etag


@pytest.mark.anyio
async def test_get_serves_the_feed_with_its_caching_headers(
    client: httpx.AsyncClient, holder: FeedCacheHolder
) -> None:
    response = await client.get(f"/feeds/{TOKEN}.xml")
    body, etag = _expected(holder)
    assert response.status_code == 200
    assert response.headers["content-type"] == MEDIA_TYPE
    assert response.headers["content-type"] == "application/rss+xml; charset=utf-8"
    assert response.headers["etag"] == etag
    assert response.headers["last-modified"] == "Mon, 26 Oct 2026 18:30:15 GMT"
    assert response.headers["cache-control"] == "max-age=300"
    assert response.headers["content-length"] == str(len(body))
    assert response.content == body


@pytest.mark.anyio
async def test_head_has_the_headers_of_get_and_no_body(
    client: httpx.AsyncClient, holder: FeedCacheHolder
) -> None:
    got = await client.get(f"/feeds/{TOKEN}.xml")
    head = await client.head(f"/feeds/{TOKEN}.xml")
    assert head.status_code == 200
    assert head.content == b""
    for name in (
        "content-type",
        "etag",
        "last-modified",
        "cache-control",
        "content-length",
    ):
        assert head.headers[name] == got.headers[name]


@pytest.mark.anyio
@pytest.mark.parametrize(
    "template",
    ["{etag}", "W/{etag}", '"other", {etag}', "*", '"other" , W/{etag} '],
)
async def test_if_none_match_that_matches_gives_304(
    client: httpx.AsyncClient, holder: FeedCacheHolder, template: str
) -> None:
    _, etag = _expected(holder)
    response = await client.get(
        f"/feeds/{TOKEN}.xml", headers={"If-None-Match": template.format(etag=etag)}
    )
    assert response.status_code == 304
    assert response.content == b""
    assert response.headers["etag"] == etag
    assert response.headers["last-modified"] == "Mon, 26 Oct 2026 18:30:15 GMT"
    assert response.headers["cache-control"] == "max-age=300"
    assert "content-type" not in response.headers


@pytest.mark.anyio
async def test_if_none_match_that_differs_gives_200(client: httpx.AsyncClient) -> None:
    response = await client.get(
        f"/feeds/{TOKEN}.xml", headers={"If-None-Match": '"nope"'}
    )
    assert response.status_code == 200


@pytest.mark.anyio
async def test_head_with_a_matching_etag_is_304(
    client: httpx.AsyncClient, holder: FeedCacheHolder
) -> None:
    _, etag = _expected(holder)
    response = await client.head(f"/feeds/{TOKEN}.xml", headers={"If-None-Match": etag})
    assert response.status_code == 304


@pytest.mark.anyio
async def test_if_none_match_wins_over_if_modified_since(
    client: httpx.AsyncClient,
) -> None:
    later = format_datetime(CHANGED + timedelta(days=1), usegmt=True)
    response = await client.get(
        f"/feeds/{TOKEN}.xml",
        headers={"If-None-Match": '"nope"', "If-Modified-Since": later},
    )
    assert response.status_code == 200


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("delta", "status"),
    [
        (timedelta(0), 304),
        (timedelta(seconds=-1), 200),
        (timedelta(days=1), 304),
        (timedelta(days=-1), 200),
    ],
)
async def test_if_modified_since_alone(
    client: httpx.AsyncClient, delta: timedelta, status: int
) -> None:
    # `Last-Modified` has second precision: the same second is "not modified"
    since = format_datetime(CHANGED.replace(microsecond=0) + delta, usegmt=True)
    response = await client.get(
        f"/feeds/{TOKEN}.xml", headers={"If-Modified-Since": since}
    )
    assert response.status_code == status


@pytest.mark.anyio
@pytest.mark.parametrize(
    "value", ["garbage", "", "Mon, 26 Oct 2026 18:30:15", "2026-10-26"]
)
async def test_an_unusable_if_modified_since_is_ignored(
    client: httpx.AsyncClient, value: str
) -> None:
    response = await client.get(
        f"/feeds/{TOKEN}.xml", headers={"If-Modified-Since": value}
    )
    assert response.status_code == 200


@pytest.mark.anyio
@pytest.mark.parametrize(
    "path",
    [
        "/feeds/unknown.xml",
        f"/feeds/{TOKEN_OFF}.xml",
        "/",
        "/feeds/x",
        "/feeds/",
        f"/feeds/{TOKEN}.rss",
        f"/feeds/{TOKEN}",
        f"/feeds/{TOKEN}.xml/extra",
        "/healthz",
        "/status",
    ],
)
async def test_everything_else_is_an_empty_404(
    client: httpx.AsyncClient, path: str
) -> None:
    response = await client.get(path)
    assert response.status_code == 404
    assert response.content == b""


@pytest.mark.anyio
@pytest.mark.parametrize("method", ["POST", "PUT", "DELETE", "PATCH"])
async def test_other_methods_on_the_feed_path_are_an_empty_405(
    client: httpx.AsyncClient, method: str
) -> None:
    response = await client.request(method, f"/feeds/{TOKEN}.xml")
    assert response.status_code == 405
    assert response.content == b""
    assert "GET" in response.headers["allow"]


@pytest.mark.anyio
async def test_a_swapped_holder_serves_the_new_content_and_a_rotated_token_404s(
    client: httpx.AsyncClient, holder: FeedCacheHolder
) -> None:
    before = await client.get(f"/feeds/{TOKEN}.xml")
    assert before.status_code == 200
    rotated = _snapshot((TOKEN_NEW, "team-php"))
    other = Endpoint("team-php", "Renamed", (ProductEntry("php"),))
    xml = render_feed(other, ())
    feed = RenderedFeed("team-php", xml, etag_of(xml), 0, NOW, NOW)
    holder.current = FeedCache.build(rotated, {"team-php": feed}, NOW)
    assert (await client.get(f"/feeds/{TOKEN}.xml")).status_code == 404
    after = await client.get(f"/feeds/{TOKEN_NEW}.xml")
    assert after.status_code == 200
    assert after.content == xml
    assert after.headers["etag"] != before.headers["etag"]


@pytest.mark.anyio
async def test_requests_log_the_endpoint_never_the_token_or_the_path(
    client: httpx.AsyncClient,
) -> None:
    with capture_logs() as logs:
        await client.get(f"/feeds/{TOKEN}.xml")
        await client.head(f"/feeds/{TOKEN}.xml")
        await client.get(f"/feeds/{TOKEN}.xml", headers={"If-None-Match": "*"})
        await client.get("/feeds/unknown-token-value.xml")
        await client.get("/elsewhere")
    served = [entry for entry in logs if entry["event"] == "feed.served"]
    missing = [entry for entry in logs if entry["event"] == "feed.not_found"]
    assert [(e["method"], e["status"]) for e in served] == [
        ("GET", 200),
        ("HEAD", 200),
        ("GET", 304),
    ]
    assert all(e["endpoint_id"] == "team-php" for e in served)
    assert all(isinstance(e["duration_ms"], int) for e in served)
    assert [e["method"] for e in missing] == ["GET", "GET"]
    assert all(set(e) == {"event", "log_level", "method"} for e in missing)
    text = str(logs)
    assert TOKEN not in text
    assert "unknown-token-value" not in text
    assert "/feeds/" not in text
    assert "elsewhere" not in text


@pytest.mark.anyio
async def test_the_served_body_is_the_rss_document(
    client: httpx.AsyncClient,
) -> None:
    response = await client.get(f"/feeds/{TOKEN}.xml")
    assert response.content.startswith(b"<?xml")
    assert b"<rss" in response.content
