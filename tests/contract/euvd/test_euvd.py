import copy
import json
import re
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

import httpx
import pytest
import respx
from structlog.testing import capture_logs

from cvestream.clock import SystemClock
from cvestream.domain.model import (
    AffectedProduct,
    CvssScore,
    KevCatalog,
    KevEntry,
    Sourced,
)
from cvestream.http import HttpClient
from cvestream.sources import euvd
from cvestream.sources.base import (
    SourceAdapter,
    SourceContractError,
    SourcePayloadError,
    SourceTransportError,
)
from cvestream.sources.euvd import BASE_URL, EuvdAdapter, parse_euvd_date

if TYPE_CHECKING:
    from tests.conftest import FakeClock

FIXTURES = Path(__file__).parents[2] / "fixtures" / "euvd"
SEARCH_URL = f"{BASE_URL}/search"
DUMP_URL = f"{BASE_URL}/kev/dump"
NOW = datetime(2026, 10, 26, 20, 0, 0, tzinfo=UTC)

type Page = dict[str, Any]


class Sleeps:
    """Async `sleep` stand-in recording delays."""

    def __init__(self) -> None:
        self.delays: list[float] = []

    async def __call__(self, delay: float) -> None:
        self.delays.append(delay)


def _raw(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


def _json(name: str) -> Any:
    return json.loads(_raw(name))


def _dump(page: Page) -> bytes:
    return json.dumps(page).encode()


def _mock_dump(respx_mock: respx.MockRouter, body: bytes | None = None) -> respx.Route:
    content = _raw("euvd_kev_dump.json") if body is None else body
    return respx_mock.get(DUMP_URL).respond(200, content=content)


def _mock_pages(
    respx_mock: respx.MockRouter, pages: dict[int, bytes | Page]
) -> respx.Route:
    """Serve `/search` pages by their `page` parameter; any other page fails."""

    def answer(request: httpx.Request) -> httpx.Response:
        number = int(request.url.params["page"])
        body = pages[number]
        content = body if isinstance(body, bytes) else _dump(body)
        return httpx.Response(200, content=content)

    return respx_mock.get(SEARCH_URL).mock(side_effect=answer)


def _whole(body: bytes | Page) -> Page:
    """A recorded page served as the whole result: `total` is its item count.

    Paging stops only on `total` (or on an item older than the cursor), so a
    single page standing for the whole result must say so.
    """
    page: Page = json.loads(body) if isinstance(body, bytes) else copy.deepcopy(body)
    page["total"] = len(page["items"])
    return page


def _requested_pages(route: respx.Route) -> list[int]:
    return [int(call.request.url.params["page"]) for call in route.calls]


@pytest.fixture
def sleeps() -> Sleeps:
    return Sleeps()


@pytest.fixture
def clock(fake_clock: FakeClock) -> FakeClock:
    fake_clock.set(NOW)
    return fake_clock


@pytest.fixture
def make_adapter(
    clock: FakeClock, sleeps: Sleeps
) -> Callable[..., tuple[EuvdAdapter, HttpClient]]:
    def build(
        page_size: int = 100, page_pause: float = euvd.PAGE_PAUSE
    ) -> tuple[EuvdAdapter, HttpClient]:
        http = HttpClient(sleep=Sleeps(), random=lambda: 0.0, max_attempts=2)
        adapter = EuvdAdapter(
            http, clock, page_size=page_size, page_pause=page_pause, sleep=sleeps
        )
        return adapter, http

    return build


# --- adapter contract -------------------------------------------------------


def test_adapter_satisfies_the_protocol(clock: FakeClock) -> None:
    adapter: SourceAdapter = EuvdAdapter(HttpClient(), clock)
    assert adapter.name == "euvd"


# --- mapping ---------------------------------------------------------------


@pytest.mark.anyio
async def test_normal_record_is_fully_mapped(
    respx_mock: respx.MockRouter,
    make_adapter: Callable[..., tuple[EuvdAdapter, HttpClient]],
    clock: FakeClock,
) -> None:
    _mock_dump(respx_mock)
    _mock_pages(respx_mock, {0: _whole(_raw("euvd_search_page0.json"))})
    adapter, http = make_adapter(page_size=100)
    async with http:
        result = await adapter.fetch("2026-10-20T00:00:00Z")
    record = next(r for r in result.records if r.record_id == "EUVD-2026-105619")
    now = clock.now()
    assert record.source == "euvd"
    assert record.observed_at == now
    assert record.aliases == (
        "EUVD-2026-105619",
        "GHSA-2jq2-hg39-chq7",
        "CVE-2026-113279",
    )
    assert record.title is None
    assert record.description == Sourced(
        "Memory corruption in Accessibility in Google Chrome prior to 156.0.8078.47 "
        "allowed a remote attacker to potentially execute arbitrary code inside the "
        "sandbox via a crafted HTML page. (Chromium security severity: Critical)",
        "euvd",
        now,
    )
    assert record.scores == (
        Sourced(
            CvssScore(8.8, "3.1", "CVSS:3.1/AV:N/AC:L/PR:N/UI:R/S:U/C:H/I:H/A:H"),
            "euvd",
            now,
        ),
    )
    assert record.epss == Sourced(0.0046, "euvd", now)
    assert record.affected == (
        Sourced(
            AffectedProduct("Chrome", "Google", "156.0.8078.47 <156.0.8078.47"),
            "euvd",
            now,
        ),
    )
    assert record.kev == ()
    assert record.references == (
        "https://chromereleases.googleblog.com/2026/10/stable-channel-update-for-desktop_02018666648.html",
        "https://issues.chromium.org/issues/570093145",
    )
    assert record.url == "https://euvd.enisa.europa.eu/vulnerability/EUVD-2026-105619"
    assert record.published_at == datetime(2026, 10, 20, 21, 1, 44, tzinfo=UTC)
    assert record.updated_at == datetime(2026, 10, 20, 23, 41, 12, tzinfo=UTC)


@pytest.mark.anyio
async def test_unscored_record_has_an_unknown_score(
    respx_mock: respx.MockRouter,
    make_adapter: Callable[..., tuple[EuvdAdapter, HttpClient]],
    clock: FakeClock,
) -> None:
    _mock_dump(respx_mock)
    _mock_pages(respx_mock, {0: _whole(_raw("euvd_no_score.json"))})
    adapter, http = make_adapter()
    async with http:
        result = await adapter.fetch("2026-10-20T00:00:00Z")
    search = [r for r in result.records if r.source == "euvd"]
    assert len(search) == 3
    for record in search:
        assert record.scores == (Sourced(None, "euvd", clock.now()),)


@pytest.mark.anyio
async def test_placeholder_version_becomes_unknown_and_the_product_is_kept(
    respx_mock: respx.MockRouter,
    make_adapter: Callable[..., tuple[EuvdAdapter, HttpClient]],
    clock: FakeClock,
) -> None:
    _mock_dump(respx_mock)
    _mock_pages(respx_mock, {0: _whole(_raw("euvd_version_na.json"))})
    adapter, http = make_adapter()
    async with http:
        result = await adapter.fetch("2026-10-26T00:00:00Z")
    search = [r for r in result.records if r.source == "euvd"]
    assert [r.record_id for r in search] == ["EUVD-2026-95399", "EUVD-2026-95398"]
    assert search[0].affected == (
        Sourced(
            AffectedProduct("Microsoft Partner Center", "Microsoft", None),
            "euvd",
            clock.now(),
        ),
    )
    assert search[1].affected[0].value.version_text is None


@pytest.mark.anyio
async def test_missing_and_placeholder_products_are_dropped_but_records_kept(
    respx_mock: respx.MockRouter,
    make_adapter: Callable[..., tuple[EuvdAdapter, HttpClient]],
) -> None:
    _mock_dump(respx_mock)
    _mock_pages(respx_mock, {0: _whole(_raw("euvd_no_product.json"))})
    adapter, http = make_adapter()
    async with http:
        result = await adapter.fetch("2026-10-20T00:00:00Z")
    search = [r for r in result.records if r.source == "euvd"]
    assert [r.record_id for r in search] == [
        "EUVD-2026-104983",
        "EUVD-2025-211112",
        "EUVD-2025-211129",
    ]
    assert all(r.affected == () for r in search)
    assert result.skipped == 0


@pytest.mark.anyio
async def test_zero_epss_is_unknown(
    respx_mock: respx.MockRouter,
    make_adapter: Callable[..., tuple[EuvdAdapter, HttpClient]],
) -> None:
    _mock_dump(respx_mock)
    _mock_pages(respx_mock, {0: _whole(_raw("euvd_epss_zero.json"))})
    adapter, http = make_adapter()
    async with http:
        result = await adapter.fetch("2026-10-26T00:00:00Z")
    search = [r for r in result.records if r.source == "euvd"]
    assert len(search) == 2
    assert all(r.epss is None for r in search)
    assert all(r.scores[0].value is not None for r in search)


@pytest.mark.anyio
async def test_exploited_since_is_ignored(
    respx_mock: respx.MockRouter,
    make_adapter: Callable[..., tuple[EuvdAdapter, HttpClient]],
) -> None:
    page = _json("euvd_exploited.json")
    assert "exploitedSince" in page["items"][0]
    _mock_dump(respx_mock)
    _mock_pages(respx_mock, {0: _whole(page)})
    adapter, http = make_adapter()
    async with http:
        result = await adapter.fetch("2026-10-26T00:00:00Z")
    record = next(r for r in result.records if r.source == "euvd")
    assert record.kev == ()
    assert len(record.affected) == 19


def _edited(name: str, edit: Callable[[Page], None]) -> Page:
    page: Page = copy.deepcopy(_json(name))
    edit(page)
    return page


@pytest.mark.anyio
async def test_placeholders_and_odd_scores_become_unknown(
    respx_mock: respx.MockRouter,
    make_adapter: Callable[..., tuple[EuvdAdapter, HttpClient]],
    clock: FakeClock,
) -> None:
    def edit(page: Page) -> None:
        first, second, third = page["items"]
        first["description"] = "n/a"
        first["baseScore"] = "n/a"
        first["epss"] = "-"
        second["baseScore"] = 11.0
        second["epss"] = 150
        third["baseScore"] = 0.0  # a real CVSS "none" keeps its version
        third["baseScoreVersion"] = "3.1"
        third["baseScoreVector"] = " - "

    _mock_dump(respx_mock)
    _mock_pages(respx_mock, {0: _whole(_edited("euvd_search_page0.json", edit))})
    adapter, http = make_adapter()
    async with http:
        result = await adapter.fetch("2026-10-20T00:00:00Z")
    first, second, third = (r for r in result.records if r.source == "euvd")
    assert first.description is None
    assert first.scores == (Sourced(None, "euvd", clock.now()),)
    assert first.epss is None
    assert second.scores == (Sourced(None, "euvd", clock.now()),)
    assert second.epss is None
    assert third.scores == (Sourced(CvssScore(0.0, "3.1", None), "euvd", clock.now()),)


@pytest.mark.anyio
async def test_scores_are_rounded_to_one_decimal(
    respx_mock: respx.MockRouter,
    make_adapter: Callable[..., tuple[EuvdAdapter, HttpClient]],
) -> None:
    def edit(page: Page) -> None:
        page["items"][0]["baseScore"] = 7.34

    _mock_dump(respx_mock)
    _mock_pages(respx_mock, {0: _whole(_edited("euvd_search_page0.json", edit))})
    adapter, http = make_adapter()
    async with http:
        result = await adapter.fetch("2026-10-20T00:00:00Z")
    record = next(r for r in result.records if r.record_id == "EUVD-2026-105619")
    score = record.scores[0].value
    assert score is not None
    assert score.score == 7.3


@pytest.mark.anyio
async def test_unparseable_publication_date_keeps_the_record(
    respx_mock: respx.MockRouter,
    make_adapter: Callable[..., tuple[EuvdAdapter, HttpClient]],
) -> None:
    def edit(page: Page) -> None:
        page["items"][0]["datePublished"] = "yesterday"
        del page["items"][1]["datePublished"]

    _mock_dump(respx_mock)
    _mock_pages(respx_mock, {0: _whole(_edited("euvd_search_page0.json", edit))})
    adapter, http = make_adapter()
    async with http:
        result = await adapter.fetch("2026-10-20T00:00:00Z")
    first, second, *_ = (r for r in result.records if r.source == "euvd")
    assert first.published_at is None
    assert second.published_at is None
    assert first.updated_at is not None
    assert result.skipped == 0


# --- date parsing -----------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Oct 26, 2026, 6:36:49 PM", datetime(2026, 10, 26, 18, 36, 49, tzinfo=UTC)),
        ("Oct 20, 2026, 12:50:04 AM", datetime(2026, 10, 20, 0, 50, 4, tzinfo=UTC)),
        ("Oct 13, 2026, 12:00:00 AM", datetime(2026, 10, 13, 0, 0, 0, tzinfo=UTC)),
        ("Oct 13, 2026, 12:30:00 PM", datetime(2026, 10, 13, 12, 30, 0, tzinfo=UTC)),
        ("Jan 5, 2026, 1:02:03 AM", datetime(2026, 1, 5, 1, 2, 3, tzinfo=UTC)),
        ("Dec 31, 2026, 11:59:59 PM", datetime(2026, 12, 31, 23, 59, 59, tzinfo=UTC)),
        # a narrow no-break space before AM/PM, as recent Java formatters emit
        ("Jan 5, 2026, 1:02:03\u202fPM", datetime(2026, 1, 5, 13, 2, 3, tzinfo=UTC)),
        (" Jan 5, 2026, 1:02:03 PM ", datetime(2026, 1, 5, 13, 2, 3, tzinfo=UTC)),
    ],
)
def test_parse_euvd_date(text: str, expected: datetime) -> None:
    parsed = parse_euvd_date(text)
    assert parsed == expected
    assert parsed is not None
    assert parsed.tzinfo is UTC


@pytest.mark.parametrize(
    "text",
    [
        "",
        "garbage",
        "2026-10-26T18:36:49Z",
        "Foo 5, 2026, 1:02:03 AM",
        "Feb 30, 2026, 1:02:03 AM",
        "Jan 5, 2026, 13:02:03 PM",
        "Jan 5, 2026, 0:00:00 AM",
        "Jan 5, 2026, 1:61:03 AM",
        "Jan 5, 2026, 1:02:03",
        "Jan 5 2026, 1:02:03 AM",
    ],
)
def test_parse_euvd_date_rejects_bad_input(text: str) -> None:
    assert parse_euvd_date(text) is None


# --- cursor and paging -------------------------------------------------------


@pytest.mark.anyio
async def test_first_fetch_starts_fourteen_days_back(
    respx_mock: respx.MockRouter,
    make_adapter: Callable[..., tuple[EuvdAdapter, HttpClient]],
    clock: FakeClock,
) -> None:
    _mock_dump(respx_mock)
    route = _mock_pages(respx_mock, {0: {"items": [], "total": 0}})
    adapter, http = make_adapter()
    async with http:
        result = await adapter.fetch(None)
    params = route.calls.last.request.url.params
    assert (
        params["fromUpdatedDate"]
        == (clock.now() - timedelta(days=14)).date().isoformat()
    )
    assert params["fromUpdatedDate"] == "2026-10-12"
    assert params["page"] == "0"
    assert params["size"] == "100"
    assert result.next_cursor == "2026-10-12T20:00:00Z"


@pytest.mark.anyio
async def test_cursor_sets_the_utc_day_of_the_query(
    respx_mock: respx.MockRouter,
    make_adapter: Callable[..., tuple[EuvdAdapter, HttpClient]],
) -> None:
    _mock_dump(respx_mock)
    route = _mock_pages(respx_mock, {0: {"items": [], "total": 0}})
    adapter, http = make_adapter()
    async with http:
        # 23:30 at UTC-5 is already the next day in UTC
        await adapter.fetch("2026-10-20T23:30:00-05:00")
    assert route.calls.last.request.url.params["fromUpdatedDate"] == "2026-10-21"


@pytest.mark.anyio
async def test_records_older_than_the_cursor_are_dropped_and_paging_stops_early(
    respx_mock: respx.MockRouter,
    make_adapter: Callable[..., tuple[EuvdAdapter, HttpClient]],
    sleeps: Sleeps,
) -> None:
    _mock_dump(respx_mock)
    route = _mock_pages(
        respx_mock,
        {0: _raw("euvd_search_page0.json"), 1: _raw("euvd_search_page1.json")},
    )
    adapter, http = make_adapter(page_size=3)
    async with http:
        result = await adapter.fetch("2026-10-20T23:41:11Z")
    search = [r.record_id for r in result.records if r.source == "euvd"]
    # records equal to the cursor are re-emitted on purpose
    assert search == [
        "EUVD-2026-105619",
        "EUVD-2026-105627",
        "EUVD-2026-105623",
        "EUVD-2026-105621",
    ]
    assert _requested_pages(route) == [0, 1]  # page 1 holds an older record: stop
    assert sleeps.delays == [euvd.PAGE_PAUSE]
    assert result.next_cursor == "2026-10-20T23:41:12Z"


def _synthetic_page(number: int, total: int, size: int = 3) -> Page:
    """`size` descending records on Oct 21 (newer than every recorded fixture)."""
    template = _json("euvd_search_page0.json")["items"][0]
    items = []
    for offset in range(size):
        position = number * size + offset
        item = copy.deepcopy(template)
        item["id"] = f"EUVD-2026-{900_000 + position}"
        item["dateUpdated"] = f"Oct 21, 2026, 11:{59 - position:02d}:00 PM"
        items.append(item)
    return {"items": items, "total": total}


@pytest.mark.anyio
async def test_pages_are_paced_by_the_configured_pause(
    respx_mock: respx.MockRouter,
    make_adapter: Callable[..., tuple[EuvdAdapter, HttpClient]],
    sleeps: Sleeps,
) -> None:
    _mock_dump(respx_mock)
    _mock_pages(
        respx_mock,
        {0: _raw("euvd_search_page0.json"), 1: _raw("euvd_search_page1.json")},
    )
    adapter, http = make_adapter(page_size=3, page_pause=4.0)
    async with http:
        await adapter.fetch("2026-10-20T23:41:11Z")
    assert sleeps.delays == [4.0]


@pytest.mark.anyio
async def test_a_short_page_does_not_stop_paging(
    respx_mock: respx.MockRouter,
    make_adapter: Callable[..., tuple[EuvdAdapter, HttpClient]],
    sleeps: Sleeps,
) -> None:
    # The server applies a smaller page size than requested (it caps `size`
    # silently): every page is short, only `total` tells where the end is.
    pages: dict[int, bytes | Page] = {
        n: _synthetic_page(n, 6, size=2) for n in range(3)
    }
    _mock_dump(respx_mock)
    route = _mock_pages(respx_mock, pages)
    adapter, http = make_adapter(page_size=3)
    async with http:
        result = await adapter.fetch("2026-10-20T00:00:00Z")
    assert len([r for r in result.records if r.source == "euvd"]) == 6
    assert _requested_pages(route) == [0, 1, 2]
    assert sleeps.delays == [euvd.PAGE_PAUSE, euvd.PAGE_PAUSE]


@pytest.mark.anyio
async def test_paging_stops_on_a_short_page_that_reaches_total(
    respx_mock: respx.MockRouter,
    make_adapter: Callable[..., tuple[EuvdAdapter, HttpClient]],
    sleeps: Sleeps,
) -> None:
    _mock_dump(respx_mock)
    route = _mock_pages(
        respx_mock,
        {
            0: _synthetic_page(0, 7),
            1: _synthetic_page(1, 7),
            2: _whole(_raw("euvd_search_last_page.json")) | {"total": 7},
        },
    )
    adapter, http = make_adapter(page_size=3)
    async with http:
        result = await adapter.fetch("2026-10-20T00:00:00Z")
    assert len([r for r in result.records if r.source == "euvd"]) == 7
    assert _requested_pages(route) == [0, 1, 2]
    assert sleeps.delays == [euvd.PAGE_PAUSE, euvd.PAGE_PAUSE]


@pytest.mark.anyio
async def test_an_empty_page_before_total_keeps_the_records_and_the_cursor(
    respx_mock: respx.MockRouter,
    make_adapter: Callable[..., tuple[EuvdAdapter, HttpClient]],
) -> None:
    # A stale or erroneous page must not fail the fetch, nor move the cursor
    # past the items announced by `total` but never served.
    _mock_dump(respx_mock)
    route = _mock_pages(
        respx_mock, {0: _synthetic_page(0, 9), 1: {"items": [], "total": 9}}
    )
    adapter, http = make_adapter(page_size=3)
    with capture_logs() as logs:
        async with http:
            result = await adapter.fetch("2026-10-20T00:00:00Z")
    assert len([r for r in result.records if r.source == "euvd"]) == 3
    assert result.next_cursor == "2026-10-20T00:00:00Z"
    assert _requested_pages(route) == [0, 1]
    assert [
        (entry["source"], entry["count"], entry["total"])
        for entry in logs
        if entry["event"] == "source.paging_incomplete"
    ] == [("euvd", 3, 9)]


@pytest.mark.anyio
async def test_paging_stops_when_total_is_reached(
    respx_mock: respx.MockRouter,
    make_adapter: Callable[..., tuple[EuvdAdapter, HttpClient]],
) -> None:
    boundary = _json("euvd_total_boundary.json")
    assert len(boundary["items"]) == 3
    assert (16 + 1) * 3 == boundary["total"]  # recorded page 16 is the last one
    pages: dict[int, bytes | Page] = {
        n: _synthetic_page(n, boundary["total"]) for n in range(16)
    }
    pages[16] = boundary  # a full page: only `total` tells that it is the last
    _mock_dump(respx_mock)
    route = _mock_pages(respx_mock, pages)
    adapter, http = make_adapter(page_size=3)
    async with http:
        result = await adapter.fetch("2026-10-20T00:00:00Z")
    assert _requested_pages(route) == list(range(17))
    assert len([r for r in result.records if r.source == "euvd"]) == 17 * 3


@pytest.mark.anyio
async def test_duplicates_across_pages_are_emitted_once(
    respx_mock: respx.MockRouter,
    make_adapter: Callable[..., tuple[EuvdAdapter, HttpClient]],
) -> None:
    page0 = _json("euvd_search_page0.json")
    page1 = _json("euvd_search_page1.json")
    shifted: Page = {
        "items": [page0["items"][2], page1["items"][0], page1["items"][1]],
        "total": page0["total"],
    }
    _mock_dump(respx_mock)
    _mock_pages(respx_mock, {0: page0, 1: shifted, 2: {"items": [], "total": 6}})
    adapter, http = make_adapter(page_size=3)
    async with http:
        result = await adapter.fetch("2026-10-20T23:41:10Z")
    ids = [r.record_id for r in result.records if r.source == "euvd"]
    assert ids == [
        "EUVD-2026-105619",
        "EUVD-2026-105627",
        "EUVD-2026-105623",
        "EUVD-2026-105621",
        "EUVD-2026-105424",
    ]


@pytest.mark.anyio
async def test_insertions_while_paging_are_not_an_order_violation(
    respx_mock: respx.MockRouter,
    make_adapter: Callable[..., tuple[EuvdAdapter, HttpClient]],
) -> None:
    # Three records inserted on top between page 0 and page 1 shift the whole
    # of page 0 onto page 1: its first item is newer than the last one seen.
    page0 = _json("euvd_search_page0.json")
    page1 = _json("euvd_search_page1.json")
    _mock_dump(respx_mock)
    route = _mock_pages(
        respx_mock, {0: page0, 1: page0, 2: page1, 3: {"items": [], "total": 9}}
    )
    adapter, http = make_adapter(page_size=3)
    async with http:
        result = await adapter.fetch("2026-10-20T23:41:10Z")
    ids = [r.record_id for r in result.records if r.source == "euvd"]
    assert ids == [
        "EUVD-2026-105619",
        "EUVD-2026-105627",
        "EUVD-2026-105623",
        "EUVD-2026-105621",
        "EUVD-2026-105424",
        "EUVD-2026-105422",
    ]
    assert _requested_pages(route) == [0, 1, 2, 3]


@pytest.mark.anyio
async def test_future_update_date_does_not_park_the_cursor_ahead(
    respx_mock: respx.MockRouter,
    make_adapter: Callable[..., tuple[EuvdAdapter, HttpClient]],
    clock: FakeClock,
) -> None:
    page = _json("euvd_search_last_page.json")
    page["items"][0]["dateUpdated"] = "Jan 1, 2099, 1:00:00 AM"
    _mock_dump(respx_mock)
    _mock_pages(respx_mock, {0: _whole(page)})
    adapter, http = make_adapter(page_size=3)
    async with http:
        result = await adapter.fetch("2026-10-20T00:00:00Z")
    assert [r.record_id for r in result.records if r.source == "euvd"] == [
        "EUVD-2026-104698"
    ]
    assert result.next_cursor == "2026-10-26T20:00:00Z"  # the fetch time
    assert clock.now() == NOW


@pytest.mark.anyio
async def test_next_cursor_is_the_newest_update_not_the_local_clock(
    respx_mock: respx.MockRouter,
    make_adapter: Callable[..., tuple[EuvdAdapter, HttpClient]],
    clock: FakeClock,
) -> None:
    _mock_dump(respx_mock)
    _mock_pages(respx_mock, {0: _whole(_raw("euvd_search_last_page.json"))})
    adapter, http = make_adapter(page_size=3)
    clock.set(NOW + timedelta(days=3))  # far ahead, yet within the 14-day window
    async with http:
        result = await adapter.fetch("2026-10-20T00:00:00Z")
    assert result.next_cursor == "2026-10-20T00:50:04Z"


@pytest.mark.anyio
async def test_without_new_records_the_cursor_does_not_move(
    respx_mock: respx.MockRouter,
    make_adapter: Callable[..., tuple[EuvdAdapter, HttpClient]],
) -> None:
    _mock_dump(respx_mock)
    _mock_pages(respx_mock, {0: _whole(_raw("euvd_search_last_page.json"))})
    adapter, http = make_adapter(page_size=3)
    async with http:
        result = await adapter.fetch("2026-10-21T00:00:00Z")
    assert [r for r in result.records if r.source == "euvd"] == []
    assert result.next_cursor == "2026-10-21T00:00:00Z"


@pytest.mark.anyio
@pytest.mark.parametrize(
    "cursor",
    ["not a date", "", "2026-10-20T23:41:11", "2026-10-20", "2099-01-01T00:00:00Z"],
)
async def test_invalid_cursor_falls_back_to_the_initial_window(
    respx_mock: respx.MockRouter,
    make_adapter: Callable[..., tuple[EuvdAdapter, HttpClient]],
    cursor: str,
) -> None:
    _mock_dump(respx_mock)
    route = _mock_pages(respx_mock, {0: {"items": [], "total": 0}})
    adapter, http = make_adapter()
    with capture_logs() as logs:
        async with http:
            result = await adapter.fetch(cursor)
    assert route.calls.last.request.url.params["fromUpdatedDate"] == "2026-10-12"
    assert result.next_cursor == "2026-10-12T20:00:00Z"
    assert any(
        entry["event"] == "source.cursor_invalid" and entry["source"] == "euvd"
        for entry in logs
    )


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("cursor", "clamped"),
    [
        ("2026-08-01T00:00:00Z", True),  # a long outage
        ("2026-10-12T19:59:59Z", True),  # one second beyond the window
        ("2026-10-12T20:00:00Z", False),  # exactly now - 14 days
    ],
)
async def test_a_cursor_older_than_the_window_is_clamped(
    respx_mock: respx.MockRouter,
    make_adapter: Callable[..., tuple[EuvdAdapter, HttpClient]],
    cursor: str,
    clamped: bool,
) -> None:
    _mock_dump(respx_mock)
    route = _mock_pages(respx_mock, {0: {"items": [], "total": 0}})
    adapter, http = make_adapter()
    with capture_logs() as logs:
        async with http:
            result = await adapter.fetch(cursor)
    assert route.calls.last.request.url.params["fromUpdatedDate"] == "2026-10-12"
    assert result.next_cursor == "2026-10-12T20:00:00Z"
    warnings = [entry for entry in logs if entry["event"] == "source.cursor_clamped"]
    assert len(warnings) == int(clamped)
    assert all(entry["source"] == "euvd" for entry in warnings)
    assert not any(entry["event"] == "source.cursor_invalid" for entry in logs)


@pytest.mark.anyio
async def test_page_cap_is_a_contract_error(
    respx_mock: respx.MockRouter,
    make_adapter: Callable[..., tuple[EuvdAdapter, HttpClient]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(euvd, "MAX_PAGES", 3)
    _mock_dump(respx_mock)
    route = _mock_pages(respx_mock, {n: _synthetic_page(n, 999_999) for n in range(3)})
    adapter, http = make_adapter(page_size=3)
    async with http:
        with pytest.raises(SourceContractError):
            await adapter.fetch("2026-10-20T00:00:00Z")
    assert _requested_pages(route) == [0, 1, 2]


# --- EU KEV ----------------------------------------------------------------


@pytest.mark.anyio
async def test_kev_dump_yields_eu_kev_records_only_for_eukev_entries(
    respx_mock: respx.MockRouter,
    make_adapter: Callable[..., tuple[EuvdAdapter, HttpClient]],
    clock: FakeClock,
) -> None:
    _mock_dump(respx_mock)
    _mock_pages(respx_mock, {0: {"items": [], "total": 0}})
    adapter, http = make_adapter()
    async with http:
        result = await adapter.fetch(None)
    ids = [r.record_id for r in result.records]
    assert ids == [
        "EUVD-2023-44176",
        "EUVD-2020-12346",
        "EUVD-2025-24160",
        "EUVD-2026-72025",
        "EUVD-2026-72024",
    ]  # CVE-2021-22555 is CISA-only: not an EU KEV entry
    assert all(r.source == "eu_kev" for r in result.records)
    named = result.records[3]
    assert named.aliases == ("EUVD-2026-72025", "CVE-2026-67277")
    assert named.title == Sourced("MikroTrick", "eu_kev", clock.now())
    assert named.kev == (
        Sourced(
            KevEntry(KevCatalog.EU_KEV, None, datetime(2026, 9, 5, tzinfo=UTC)),
            "eu_kev",
            clock.now(),
        ),
    )
    assert named.url == "https://euvd.enisa.europa.eu/vulnerability/EUVD-2026-72025"
    assert named.observed_at == clock.now()
    plain = result.records[0]
    assert plain.title is None
    assert plain.scores == ()


@pytest.mark.anyio
async def test_kev_records_come_before_search_records(
    respx_mock: respx.MockRouter,
    make_adapter: Callable[..., tuple[EuvdAdapter, HttpClient]],
) -> None:
    _mock_dump(respx_mock)
    _mock_pages(respx_mock, {0: _whole(_raw("euvd_search_last_page.json"))})
    adapter, http = make_adapter(page_size=3)
    async with http:
        result = await adapter.fetch("2026-10-20T00:00:00Z")
    assert [r.source for r in result.records] == ["eu_kev"] * 5 + ["euvd"]


@pytest.mark.anyio
async def test_kev_dump_is_loaded_on_every_fetch(
    respx_mock: respx.MockRouter,
    make_adapter: Callable[..., tuple[EuvdAdapter, HttpClient]],
) -> None:
    dump = _mock_dump(respx_mock)
    _mock_pages(respx_mock, {0: {"items": [], "total": 0}})
    adapter, http = make_adapter()
    async with http:
        await adapter.fetch(None)
        await adapter.fetch("2026-10-20T00:00:00Z")
    assert dump.call_count == 2


@pytest.mark.anyio
async def test_kev_entries_without_ids_are_skipped_and_odd_dates_tolerated(
    respx_mock: respx.MockRouter,
    make_adapter: Callable[..., tuple[EuvdAdapter, HttpClient]],
) -> None:
    dump: list[object] = [
        {"sources": ["eukev_kev"], "dateAdded": "2026-09-05"},  # no ID at all
        {"cveId": "CVE-2026-10001", "sources": ["eukev_kev"], "dateAdded": "soon"},
        {"cveId": "CVE-2026-10002", "euvdId": "n/a", "sources": ["eukev_kev"]},
        {"cveId": "CVE-2026-10003", "sources": "eukev_kev"},  # wrong type
        "not an object",
    ]
    _mock_dump(respx_mock, json.dumps(dump).encode())
    _mock_pages(respx_mock, {0: {"items": [], "total": 0}})
    adapter, http = make_adapter()
    async with http:
        result = await adapter.fetch(None)
    assert [r.record_id for r in result.records] == [
        "CVE-2026-10001",
        "CVE-2026-10002",
    ]
    first = result.records[0]
    assert first.aliases == ("CVE-2026-10001",)
    assert first.url is None
    assert first.kev[0].value.added_at is None
    assert result.skipped == 3


@pytest.mark.anyio
async def test_kev_entries_with_malformed_ids_fall_back_or_are_skipped(
    respx_mock: respx.MockRouter,
    make_adapter: Callable[..., tuple[EuvdAdapter, HttpClient]],
) -> None:
    dump: list[object] = [
        # a malformed EUVD ID is not an identity: the CVE ID is used instead
        {
            "euvdId": "https://euvd/x",
            "cveId": "CVE-2025-25231",
            "sources": ["eukev_kev"],
        },
        {"euvdId": " euvd-2025-24160 ", "cveId": "garbage", "sources": ["eukev_kev"]},
        {"euvdId": "EUVD-x", "cveId": "CVE-1", "sources": ["eukev_kev"]},
    ]
    _mock_dump(respx_mock, json.dumps(dump).encode())
    _mock_pages(respx_mock, {0: {"items": [], "total": 0}})
    adapter, http = make_adapter()
    async with http:
        result = await adapter.fetch(None)
    assert [(r.record_id, r.aliases, r.url) for r in result.records] == [
        ("CVE-2025-25231", ("CVE-2025-25231",), None),
        (
            "EUVD-2025-24160",
            ("EUVD-2025-24160",),
            "https://euvd.enisa.europa.eu/vulnerability/EUVD-2025-24160",
        ),
    ]
    assert result.skipped == 1


@pytest.mark.anyio
async def test_dump_where_every_entry_is_rejected_is_a_contract_error(
    respx_mock: respx.MockRouter,
    make_adapter: Callable[..., tuple[EuvdAdapter, HttpClient]],
) -> None:
    # e.g. `euvdId`/`cveId` renamed upstream: every entry fails, which is drift
    dump = [
        {"vulnId": entry["euvdId"], "sources": entry["sources"]}
        for entry in _json("euvd_kev_dump.json")
    ]
    _mock_dump(respx_mock, json.dumps(dump).encode())
    adapter, http = make_adapter()
    async with http:
        with pytest.raises(SourceContractError, match="no entry passed validation"):
            await adapter.fetch(None)


# --- malformed payloads and failures ----------------------------------------


@pytest.mark.anyio
async def test_search_body_that_is_not_json_is_a_payload_error(
    respx_mock: respx.MockRouter,
    make_adapter: Callable[..., tuple[EuvdAdapter, HttpClient]],
) -> None:
    _mock_dump(respx_mock)
    truncated = _raw("euvd_search_page0.json")[:200]
    _mock_pages(respx_mock, {0: truncated})
    adapter, http = make_adapter()
    async with http:
        with pytest.raises(SourcePayloadError) as excinfo:
            await adapter.fetch("2026-10-20T00:00:00Z")
    assert excinfo.value.source == "euvd"


@pytest.mark.anyio
@pytest.mark.parametrize(
    "edit",
    [
        lambda page: page.pop("items"),
        lambda page: page.update(items="oops"),
        lambda page: page.update(items={"a": 1}),
        lambda page: page.pop("total"),
        lambda page: page.update(total="many"),
    ],
    ids=["no-items", "items-string", "items-object", "no-total", "total-string"],
)
async def test_unexpected_search_shape_is_a_contract_error(
    respx_mock: respx.MockRouter,
    make_adapter: Callable[..., tuple[EuvdAdapter, HttpClient]],
    edit: Callable[[Page], object],
) -> None:
    page: Page = copy.deepcopy(_json("euvd_search_page0.json"))
    edit(page)
    _mock_dump(respx_mock)
    _mock_pages(respx_mock, {0: page})
    adapter, http = make_adapter()
    async with http:
        with pytest.raises(SourceContractError):
            await adapter.fetch("2026-10-20T00:00:00Z")


@pytest.mark.anyio
async def test_search_top_level_list_is_a_contract_error(
    respx_mock: respx.MockRouter,
    make_adapter: Callable[..., tuple[EuvdAdapter, HttpClient]],
) -> None:
    _mock_dump(respx_mock)
    _mock_pages(respx_mock, {0: b"[]"})
    adapter, http = make_adapter()
    async with http:
        with pytest.raises(SourceContractError):
            await adapter.fetch("2026-10-20T00:00:00Z")


@pytest.mark.anyio
async def test_ascending_order_is_a_contract_error(
    respx_mock: respx.MockRouter,
    make_adapter: Callable[..., tuple[EuvdAdapter, HttpClient]],
) -> None:
    page: Page = copy.deepcopy(_json("euvd_search_page0.json"))
    page["items"].reverse()
    _mock_dump(respx_mock)
    _mock_pages(respx_mock, {0: page})
    adapter, http = make_adapter(page_size=3)
    async with http:
        with pytest.raises(SourceContractError, match="sorted by dateUpdated"):
            await adapter.fetch("2026-10-20T00:00:00Z")


@pytest.mark.anyio
async def test_records_that_fail_validation_are_skipped_and_counted(
    respx_mock: respx.MockRouter,
    make_adapter: Callable[..., tuple[EuvdAdapter, HttpClient]],
) -> None:
    def edit(page: Page) -> None:
        page["items"][0]["dateUpdated"] = "garbage"
        del page["items"][1]["id"]

    _mock_dump(respx_mock)
    _mock_pages(respx_mock, {0: _whole(_edited("euvd_search_page0.json", edit))})
    adapter, http = make_adapter(page_size=10)
    async with http:
        result = await adapter.fetch("2026-10-20T00:00:00Z")
    search = [r.record_id for r in result.records if r.source == "euvd"]
    assert search == ["EUVD-2026-105623"]
    assert result.skipped == 2


@pytest.mark.anyio
async def test_unusable_ids_and_wrong_types_are_skipped(
    respx_mock: respx.MockRouter,
    make_adapter: Callable[..., tuple[EuvdAdapter, HttpClient]],
) -> None:
    def edit(page: Page) -> None:
        page["items"][0]["id"] = "CVE-2026-1"  # not an EUVD ID
        page["items"][1]["id"] = "n/a"
        page["items"][2]["baseScore"] = {"unexpected": True}
        page["items"].append("not an object")
        page["items"].append(None)

    _mock_dump(respx_mock)
    _mock_pages(respx_mock, {0: _whole(_edited("euvd_search_page0.json", edit))})
    adapter, http = make_adapter(page_size=10)
    async with http:
        result = await adapter.fetch("2026-10-20T00:00:00Z")
    assert [r for r in result.records if r.source == "euvd"] == []
    assert result.skipped == 5


@pytest.mark.anyio
async def test_id_is_stripped_before_the_pattern_check(
    respx_mock: respx.MockRouter,
    make_adapter: Callable[..., tuple[EuvdAdapter, HttpClient]],
) -> None:
    def edit(page: Page) -> None:
        page["items"][0]["id"] = " EUVD-2026-105619 \n"

    _mock_dump(respx_mock)
    _mock_pages(respx_mock, {0: _whole(_edited("euvd_search_page0.json", edit))})
    adapter, http = make_adapter(page_size=10)
    async with http:
        result = await adapter.fetch("2026-10-20T00:00:00Z")
    assert result.records[5].record_id == "EUVD-2026-105619"


@pytest.mark.anyio
@pytest.mark.parametrize("body", [b"[]", b'{"cveId": "CVE-1"}', b'"text"'])
async def test_dump_that_is_empty_or_not_a_list_is_a_contract_error(
    respx_mock: respx.MockRouter,
    make_adapter: Callable[..., tuple[EuvdAdapter, HttpClient]],
    body: bytes,
) -> None:
    _mock_dump(respx_mock, body)
    adapter, http = make_adapter()
    async with http:
        with pytest.raises(SourceContractError):
            await adapter.fetch(None)


@pytest.mark.anyio
async def test_dump_that_is_not_json_is_a_payload_error(
    respx_mock: respx.MockRouter,
    make_adapter: Callable[..., tuple[EuvdAdapter, HttpClient]],
) -> None:
    _mock_dump(respx_mock, b"<html>maintenance</html>")
    adapter, http = make_adapter()
    async with http:
        with pytest.raises(SourcePayloadError):
            await adapter.fetch(None)


@pytest.mark.anyio
async def test_search_failure_returns_no_result_even_though_the_dump_loaded(
    respx_mock: respx.MockRouter,
    make_adapter: Callable[..., tuple[EuvdAdapter, HttpClient]],
) -> None:
    dump = _mock_dump(respx_mock)
    search = respx_mock.get(SEARCH_URL).respond(503)
    adapter, http = make_adapter()
    async with http:
        with pytest.raises(SourceTransportError) as excinfo:
            await adapter.fetch("2026-10-20T00:00:00Z")
    assert excinfo.value.source == "euvd"
    assert dump.call_count == 1
    assert search.call_count == 2  # max_attempts of the test client


@pytest.mark.anyio
async def test_a_failure_on_a_later_page_loses_the_whole_fetch(
    respx_mock: respx.MockRouter,
    make_adapter: Callable[..., tuple[EuvdAdapter, HttpClient]],
) -> None:
    _mock_dump(respx_mock)

    def answer(request: httpx.Request) -> httpx.Response:
        if request.url.params["page"] == "0":
            return httpx.Response(200, content=_raw("euvd_search_page0.json"))
        return httpx.Response(500)

    respx_mock.get(SEARCH_URL).mock(side_effect=answer)
    adapter, http = make_adapter(page_size=3)
    async with http:
        with pytest.raises(SourceTransportError):
            await adapter.fetch("2026-10-20T00:00:00Z")


@pytest.mark.anyio
async def test_dump_failure_is_a_transport_error(
    respx_mock: respx.MockRouter,
    make_adapter: Callable[..., tuple[EuvdAdapter, HttpClient]],
) -> None:
    respx_mock.get(DUMP_URL).mock(side_effect=httpx.ConnectError("refused"))
    adapter, http = make_adapter()
    async with http:
        with pytest.raises(SourceTransportError):
            await adapter.fetch(None)


# --- live ------------------------------------------------------------------


@pytest.mark.live
@pytest.mark.enable_socket
@pytest.mark.anyio
async def test_live_euvd_fetch_matches_the_recorded_shape() -> None:
    clock = SystemClock()
    cursor = (clock.now() - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    async with HttpClient() as http:
        result = await EuvdAdapter(http, clock).fetch(cursor)
    search = [r for r in result.records if r.source == "euvd"]
    assert search
    for record in search:
        assert re.fullmatch(r"EUVD-\d{4}-\d+", record.record_id)
        assert record.updated_at is not None
        assert record.updated_at.tzinfo is UTC
        assert record.aliases[0] == record.record_id
    kev = [r for r in result.records if r.source == "eu_kev"]
    assert kev
    assert all(r.kev[0].value.catalog is KevCatalog.EU_KEV for r in kev)
    assert result.next_cursor is not None
