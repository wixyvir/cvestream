import copy
import json
import re
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

import httpx
import pytest
import respx

from cvestream.clock import SystemClock
from cvestream.domain.model import KevCatalog, KevEntry, Sourced
from cvestream.http import HttpClient
from cvestream.sources.base import (
    SourceAdapter,
    SourceContractError,
    SourcePayloadError,
    SourceTransportError,
)
from cvestream.sources.cisa_kev import CATALOG_PAGE, CATALOG_URL, CisaKevAdapter

if TYPE_CHECKING:
    from tests.conftest import FakeClock

FIXTURE = Path(__file__).parents[2] / "fixtures" / "cisa_kev" / "cisa_kev_catalog.json"

type Catalog = dict[str, Any]


def _catalog() -> Catalog:
    catalog: Catalog = copy.deepcopy(json.loads(FIXTURE.read_bytes()))
    return catalog


def _serve(respx_mock: respx.MockRouter, body: bytes | Catalog) -> respx.Route:
    content = body if isinstance(body, bytes) else json.dumps(body).encode()
    return respx_mock.get(CATALOG_URL).respond(200, content=content)


@pytest.fixture
def make_adapter(
    fake_clock: FakeClock,
) -> Callable[[], tuple[CisaKevAdapter, HttpClient]]:
    def build() -> tuple[CisaKevAdapter, HttpClient]:
        async def no_sleep(_delay: float) -> None:
            return None

        http = HttpClient(sleep=no_sleep, random=lambda: 0.0, max_attempts=2)
        return CisaKevAdapter(http, fake_clock), http

    return build


def test_adapter_satisfies_the_protocol(fake_clock: FakeClock) -> None:
    adapter: SourceAdapter = CisaKevAdapter(HttpClient(), fake_clock)
    assert adapter.name == "cisa_kev"


@pytest.mark.anyio
async def test_known_ransomware_entry_is_fully_mapped(
    respx_mock: respx.MockRouter,
    make_adapter: Callable[[], tuple[CisaKevAdapter, HttpClient]],
    fake_clock: FakeClock,
) -> None:
    _serve(respx_mock, FIXTURE.read_bytes())
    adapter, http = make_adapter()
    async with http:
        result = await adapter.fetch(None)
    assert [r.record_id for r in result.records] == [
        "CVE-2026-114625",
        "CVE-2021-22555",
        "CVE-2023-3519",
        "CVE-2020-1472",
    ]
    assert result.skipped == 0
    now = fake_clock.now()
    record = result.records[2]
    assert record.source == "cisa_kev"
    assert record.observed_at == now
    assert record.aliases == ("CVE-2023-3519",)
    assert record.title is not None
    assert record.title.value.startswith("Citrix NetScaler ADC")
    assert record.title.source == "cisa_kev"
    assert record.description is not None
    assert record.description.observed_at == now
    assert record.kev == (
        Sourced(
            KevEntry(
                KevCatalog.CISA_KEV,
                known_ransomware_use=True,
                added_at=datetime(2023, 7, 19, tzinfo=UTC),
            ),
            "cisa_kev",
            now,
        ),
    )
    assert record.url == CATALOG_PAGE
    # CISA gives coarse vendor/product labels without versions: no affected products
    assert record.affected == ()
    assert record.scores == ()
    assert record.epss is None


@pytest.mark.anyio
async def test_unknown_ransomware_use_is_none_never_false(
    respx_mock: respx.MockRouter,
    make_adapter: Callable[[], tuple[CisaKevAdapter, HttpClient]],
) -> None:
    _serve(respx_mock, FIXTURE.read_bytes())
    adapter, http = make_adapter()
    async with http:
        result = await adapter.fetch(None)
    flags = {r.record_id: r.kev[0].value.known_ransomware_use for r in result.records}
    assert flags == {
        "CVE-2026-114625": None,
        "CVE-2021-22555": None,
        "CVE-2023-3519": True,
        "CVE-2020-1472": True,
    }
    assert False not in flags.values()


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("value", "expected"),
    [("Known", True), ("known", True), (" KNOWN ", True), ("Unknown", None)],
)
async def test_ransomware_flag_values(
    respx_mock: respx.MockRouter,
    make_adapter: Callable[[], tuple[CisaKevAdapter, HttpClient]],
    value: str,
    expected: bool | None,
) -> None:
    catalog = _catalog()
    catalog["vulnerabilities"][0]["knownRansomwareCampaignUse"] = value
    _serve(respx_mock, catalog)
    adapter, http = make_adapter()
    async with http:
        result = await adapter.fetch(None)
    assert result.records[0].kev[0].value.known_ransomware_use is expected


@pytest.mark.anyio
@pytest.mark.parametrize("missing", ["absent", "none", "other"])
async def test_missing_or_odd_ransomware_flag_is_unknown(
    respx_mock: respx.MockRouter,
    make_adapter: Callable[[], tuple[CisaKevAdapter, HttpClient]],
    missing: str,
) -> None:
    catalog = _catalog()
    entry = catalog["vulnerabilities"][2]  # a "Known" entry
    if missing == "absent":
        del entry["knownRansomwareCampaignUse"]
    elif missing == "none":
        entry["knownRansomwareCampaignUse"] = None
    else:
        entry["knownRansomwareCampaignUse"] = "Maybe"
    _serve(respx_mock, catalog)
    adapter, http = make_adapter()
    async with http:
        result = await adapter.fetch(None)
    assert result.records[2].kev[0].value.known_ransomware_use is None


@pytest.mark.anyio
async def test_next_cursor_is_the_catalog_version(
    respx_mock: respx.MockRouter,
    make_adapter: Callable[[], tuple[CisaKevAdapter, HttpClient]],
) -> None:
    _serve(respx_mock, FIXTURE.read_bytes())
    adapter, http = make_adapter()
    async with http:
        result = await adapter.fetch(None)
    assert result.next_cursor == "2026.10.23"


@pytest.mark.anyio
@pytest.mark.parametrize("version", ["missing", "empty", "number"])
async def test_next_cursor_is_none_without_a_usable_version(
    respx_mock: respx.MockRouter,
    make_adapter: Callable[[], tuple[CisaKevAdapter, HttpClient]],
    version: str,
) -> None:
    catalog = _catalog()
    if version == "missing":
        del catalog["catalogVersion"]
    elif version == "empty":
        catalog["catalogVersion"] = "  "
    else:
        catalog["catalogVersion"] = 2026
    _serve(respx_mock, catalog)
    adapter, http = make_adapter()
    async with http:
        result = await adapter.fetch(None)
    assert result.next_cursor is None
    assert len(result.records) == 4


@pytest.mark.anyio
async def test_the_cursor_argument_is_ignored(
    respx_mock: respx.MockRouter,
    make_adapter: Callable[[], tuple[CisaKevAdapter, HttpClient]],
) -> None:
    route = _serve(respx_mock, FIXTURE.read_bytes())
    adapter, http = make_adapter()
    async with http:
        first = await adapter.fetch(None)
        second = await adapter.fetch("2026.10.23")
    assert route.call_count == 2
    assert [str(call.request.url) for call in route.calls] == [CATALOG_URL] * 2
    assert first.records == second.records


@pytest.mark.anyio
async def test_entries_without_a_usable_cve_id_are_skipped_and_counted(
    respx_mock: respx.MockRouter,
    make_adapter: Callable[[], tuple[CisaKevAdapter, HttpClient]],
) -> None:
    catalog = _catalog()
    vulnerabilities = catalog["vulnerabilities"]
    del vulnerabilities[0]["cveID"]
    vulnerabilities[1]["cveID"] = "not-a-cve"
    vulnerabilities.append("not an object")
    vulnerabilities.append({"cveID": 12345})
    _serve(respx_mock, catalog)
    adapter, http = make_adapter()
    async with http:
        result = await adapter.fetch(None)
    assert [r.record_id for r in result.records] == ["CVE-2023-3519", "CVE-2020-1472"]
    assert result.skipped == 4


@pytest.mark.anyio
async def test_catalogue_where_every_entry_is_rejected_is_a_contract_error(
    respx_mock: respx.MockRouter,
    make_adapter: Callable[[], tuple[CisaKevAdapter, HttpClient]],
) -> None:
    # e.g. `cveID` renamed upstream: every entry fails, which is drift
    catalog = _catalog()
    for entry in catalog["vulnerabilities"]:
        entry["cveId"] = entry.pop("cveID")
    _serve(respx_mock, catalog)
    adapter, http = make_adapter()
    async with http:
        with pytest.raises(SourceContractError, match="no entry passed validation"):
            await adapter.fetch(None)


@pytest.mark.anyio
async def test_cve_id_is_stripped_and_upper_cased(
    respx_mock: respx.MockRouter,
    make_adapter: Callable[[], tuple[CisaKevAdapter, HttpClient]],
) -> None:
    catalog = _catalog()
    catalog["vulnerabilities"][0]["cveID"] = " cve-2026-114625 "
    _serve(respx_mock, catalog)
    adapter, http = make_adapter()
    async with http:
        result = await adapter.fetch(None)
    assert result.records[0].record_id == "CVE-2026-114625"
    assert result.records[0].aliases == ("CVE-2026-114625",)


@pytest.mark.anyio
async def test_odd_dates_and_missing_text_do_not_drop_the_entry(
    respx_mock: respx.MockRouter,
    make_adapter: Callable[[], tuple[CisaKevAdapter, HttpClient]],
) -> None:
    catalog = _catalog()
    entry = catalog["vulnerabilities"][0]
    entry["dateAdded"] = "last week"
    del entry["vulnerabilityName"]
    entry["shortDescription"] = "n/a"
    _serve(respx_mock, catalog)
    adapter, http = make_adapter()
    async with http:
        result = await adapter.fetch(None)
    record = result.records[0]
    assert record.kev[0].value.added_at is None
    assert record.title is None
    assert record.description is None
    assert result.skipped == 0


@pytest.mark.anyio
@pytest.mark.parametrize(
    "edit",
    [
        lambda catalog: catalog.pop("vulnerabilities"),
        lambda catalog: catalog.update(vulnerabilities="oops"),
        lambda catalog: catalog.update(vulnerabilities=[]),
    ],
    ids=["missing", "not-a-list", "empty"],
)
async def test_unexpected_shape_is_a_contract_error(
    respx_mock: respx.MockRouter,
    make_adapter: Callable[[], tuple[CisaKevAdapter, HttpClient]],
    edit: Callable[[Catalog], object],
) -> None:
    catalog = _catalog()
    edit(catalog)
    _serve(respx_mock, catalog)
    adapter, http = make_adapter()
    async with http:
        with pytest.raises(SourceContractError) as excinfo:
            await adapter.fetch(None)
    assert excinfo.value.source == "cisa_kev"


@pytest.mark.anyio
async def test_top_level_list_is_a_contract_error(
    respx_mock: respx.MockRouter,
    make_adapter: Callable[[], tuple[CisaKevAdapter, HttpClient]],
) -> None:
    _serve(respx_mock, b"[]")
    adapter, http = make_adapter()
    async with http:
        with pytest.raises(SourceContractError):
            await adapter.fetch(None)


@pytest.mark.anyio
@pytest.mark.parametrize(
    "body", [b"", b"<html>maintenance</html>", FIXTURE.read_bytes()[:300]]
)
async def test_invalid_json_is_a_payload_error(
    respx_mock: respx.MockRouter,
    make_adapter: Callable[[], tuple[CisaKevAdapter, HttpClient]],
    body: bytes,
) -> None:
    _serve(respx_mock, body)
    adapter, http = make_adapter()
    async with http:
        with pytest.raises(SourcePayloadError) as excinfo:
            await adapter.fetch(None)
    assert excinfo.value.source == "cisa_kev"


@pytest.mark.anyio
async def test_http_failure_is_a_transport_error(
    respx_mock: respx.MockRouter,
    make_adapter: Callable[[], tuple[CisaKevAdapter, HttpClient]],
) -> None:
    respx_mock.get(CATALOG_URL).respond(503)
    adapter, http = make_adapter()
    async with http:
        with pytest.raises(SourceTransportError):
            await adapter.fetch(None)


@pytest.mark.anyio
async def test_network_failure_is_a_transport_error(
    respx_mock: respx.MockRouter,
    make_adapter: Callable[[], tuple[CisaKevAdapter, HttpClient]],
) -> None:
    respx_mock.get(CATALOG_URL).mock(side_effect=httpx.ConnectError("refused"))
    adapter, http = make_adapter()
    async with http:
        with pytest.raises(SourceTransportError):
            await adapter.fetch(None)


@pytest.mark.live
@pytest.mark.enable_socket
@pytest.mark.anyio
async def test_live_cisa_kev_catalogue_matches_the_recorded_shape() -> None:
    async with HttpClient() as http:
        result = await CisaKevAdapter(http, SystemClock()).fetch(None)
    assert len(result.records) > 1000
    assert result.skipped == 0
    for record in result.records:
        assert re.fullmatch(r"CVE-\d{4}-\d{4,}", record.record_id)
        assert record.kev[0].value.catalog is KevCatalog.CISA_KEV
    assert any(r.kev[0].value.known_ransomware_use is True for r in result.records)
    assert result.next_cursor is not None
