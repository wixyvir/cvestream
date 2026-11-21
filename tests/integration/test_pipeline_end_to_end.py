"""From the recorded EUVD and CISA KEV payloads to a feed served over HTTP.

The sources are the real adapters run against the recorded fixtures (respx); the
state is a SQLite file; the feed is read through the public Starlette app with
`httpx.ASGITransport`, so no port is bound.
"""

import re
import xml.etree.ElementTree as ET
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import MappingProxyType

import httpx
import pytest
import respx
from sqlalchemy import select

from cvestream.app.feed_cache import FeedCache, FeedCacheHolder
from cvestream.app.public_api import build_public_app
from cvestream.config.provider import ConfigSnapshot
from cvestream.domain.endpoint import Endpoint, ProductEntry
from cvestream.http import HttpClient
from cvestream.pipeline.run import CycleResult, SourceOutcome, cycle, load_feeds
from cvestream.sources.cisa_kev import CATALOG_URL, CisaKevAdapter
from cvestream.sources.euvd import BASE_URL, EuvdAdapter
from cvestream.state import schema
from cvestream.state.db import State
from tests.conftest import FakeClock

FIXTURES = Path(__file__).parents[1] / "fixtures"
NOW = datetime(2026, 10, 26, 20, 0, 0, tzinfo=UTC)
# Recorded search page: three Chrome records, updated on 2026-10-20.
CURSOR = "2026-10-20T00:00:00Z"
CHROME_CVE = "CVE-2026-113279"
TOKEN = "k3Jx9" + "a" * 38
ENDPOINT = Endpoint(
    id="team-browser", name="Browsers", products=(ProductEntry(name="Chrome"),)
)
SNAPSHOT = ConfigSnapshot(
    revision="r",
    endpoints=(ENDPOINT,),
    tokens=MappingProxyType({TOKEN: "team-browser"}),
)
FEED_PATH = f"/feeds/{TOKEN}.xml"


class _Provider:
    """A provider whose configuration never changes."""

    def revision(self) -> str:
        return SNAPSHOT.revision

    def load(self) -> ConfigSnapshot:
        return SNAPSHOT


async def _no_sleep(_delay: float) -> None:
    return None


async def _outcomes(
    respx_mock: respx.MockRouter, clock: FakeClock
) -> list[SourceOutcome]:
    respx_mock.get(f"{BASE_URL}/kev/dump").respond(
        200, content=(FIXTURES / "euvd" / "euvd_kev_dump.json").read_bytes()
    )
    respx_mock.get(f"{BASE_URL}/search").respond(
        200, content=(FIXTURES / "euvd" / "euvd_search_page0.json").read_bytes()
    )
    respx_mock.get(CATALOG_URL).respond(
        200, content=(FIXTURES / "cisa_kev" / "cisa_kev_catalog.json").read_bytes()
    )
    async with HttpClient(sleep=_no_sleep, random=lambda: 0.0, max_attempts=2) as http:
        euvd = await EuvdAdapter(http, clock, sleep=_no_sleep).fetch(CURSOR)
        cisa = await CisaKevAdapter(http, clock).fetch(None)
    return [SourceOutcome("euvd", euvd), SourceOutcome("cisa_kev", cisa)]


def _client(cache: FeedCache) -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=build_public_app(FeedCacheHolder(cache)))
    return httpx.AsyncClient(transport=transport, base_url="http://test")


def _stored_guids(state: State) -> set[str]:
    with state.transaction() as repository:
        rows = repository._connection.execute(select(schema.publication.c.guid)).all()
    return {row.guid for row in rows}


def _items(body: bytes) -> list[ET.Element]:
    root = ET.fromstring(body)  # noqa: S314 - our own output
    assert root.tag == "rss"
    assert root.attrib == {"version": "2.0"}
    return root.findall("channel/item")


@pytest.mark.anyio
async def test_a_feed_goes_from_recorded_payloads_to_a_served_response(
    respx_mock: respx.MockRouter, fake_clock: FakeClock, tmp_path: Path
) -> None:
    fake_clock.set(NOW)
    url = f"sqlite:///{tmp_path / 'state.db'}"
    state = State(url)
    state.migrate()
    outcomes = await _outcomes(respx_mock, fake_clock)

    result: CycleResult = cycle(state, _Provider(), SNAPSHOT, outcomes, fake_clock)
    assert result.run.decisions
    cache = FeedCache.build(result.snapshot, result.run.feeds, fake_clock.now())

    async with _client(cache) as client:
        response = await client.get(FEED_PATH)
        assert response.status_code == 200
        assert response.headers["content-type"] == "application/rss+xml; charset=utf-8"
        assert response.headers["cache-control"] == "max-age=300"
        etag = response.headers["etag"]
        items = _items(response.content)

        # one item per stored publication, with the stored GUID
        guids = {item.findtext("guid") for item in items}
        assert guids == _stored_guids(state)
        assert len(items) == len(result.run.decisions)
        assert f"cvestream:team-browser:{CHROME_CVE}:new" in guids

        chrome = next(
            i
            for i in items
            if i.findtext("guid") == f"cvestream:team-browser:{CHROME_CVE}:new"
        )
        title = chrome.findtext("title") or ""
        assert title.startswith(f"{CHROME_CVE} in Google Chrome: severity ")
        description = chrome.findtext("description") or ""
        lines = description.split("\n")
        # The first block leads with the facts line, Matched and Description.
        first_block = description.split("</p>")[0]
        assert first_block.startswith("<p>high · CVSS 8.8 · exploited: no · published ")
        assert re.search(r"published \d{4}-\d{2}-\d{2}", first_block)
        assert "<b>Matched:</b>" in first_block
        assert "<b>Description:</b>" in first_block
        # The IDs block comes later, with each source's score.
        assert any(
            line.startswith("<p><b>IDs:</b> ") and CHROME_CVE in line for line in lines
        )
        assert any(line.startswith("<b>CVSS:</b> ") for line in lines)
        # Not known-exploited: no KEV detail line at all.
        assert not any(line.startswith("<b>Known exploited:</b> ") for line in lines)
        assert '<a href="https://euvd.enisa.europa.eu/vulnerability/' in description
        guid = chrome.find("guid")
        assert guid is not None
        assert guid.attrib == {"isPermaLink": "false"}
        assert chrome.findtext("pubDate") == "Mon, 26 Oct 2026 20:00:00 GMT"

        # conditional requests
        not_modified = await client.get(FEED_PATH, headers={"If-None-Match": etag})
        assert not_modified.status_code == 304
        assert not_modified.content == b""
        assert not_modified.headers["etag"] == etag

        head = await client.head(FEED_PATH)
        assert head.status_code == 200
        assert head.content == b""
        assert head.headers["etag"] == etag
        assert head.headers["content-length"] == str(len(response.content))

        # an unknown token and a wrong path are an empty 404
        unknown = await client.get("/feeds/not-a-token.xml")
        assert unknown.status_code == 404
        assert unknown.content == b""

        # nothing in the feed leaks the token
        assert TOKEN not in response.text

    # a restart serves the same bytes from feed_cache, before any fetch
    state.dispose()
    state = State(url)
    state.migrate()
    restarted = FeedCache.build(SNAPSHOT, load_feeds(state), fake_clock.now())
    async with _client(restarted) as client:
        again = await client.get(FEED_PATH)
    assert again.content == response.content
    assert again.headers["etag"] == etag
    assert again.headers["last-modified"] == response.headers["last-modified"]
    state.dispose()


@pytest.mark.anyio
async def test_a_second_cycle_with_the_same_data_keeps_the_etag(
    respx_mock: respx.MockRouter, fake_clock: FakeClock, tmp_path: Path
) -> None:
    fake_clock.set(NOW)
    state = State(f"sqlite:///{tmp_path / 'state.db'}")
    state.migrate()
    first = cycle(
        state,
        _Provider(),
        SNAPSHOT,
        await _outcomes(respx_mock, fake_clock),
        fake_clock,
    )
    fake_clock.advance(timedelta(minutes=15))
    second = cycle(
        state,
        _Provider(),
        SNAPSHOT,
        await _outcomes(respx_mock, fake_clock),
        fake_clock,
    )
    assert second.run.decisions == ()
    before = first.run.feeds["team-browser"]
    after = second.run.feeds["team-browser"]
    assert after.etag == before.etag
    assert after.changed_at == before.changed_at
    assert after.rendered_at > before.rendered_at
    state.dispose()
