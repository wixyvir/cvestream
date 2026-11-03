import copy
import json
import re
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest
import respx
from packaging.version import Version
from structlog.testing import capture_logs

from cvestream.clock import SystemClock
from cvestream.domain.endpoint import Endpoint, ProductEntry
from cvestream.domain.matcher import match
from cvestream.http import HttpClient
from cvestream.pipeline.correlate import build_vulnerability, merge_keys
from cvestream.pipeline.normalize import normalize
from cvestream.sources.base import (
    FetchResult,
    SourceAdapter,
    SourceContractError,
    SourcePayloadError,
    SourceTransportError,
    get_json,
)
from cvestream.sources.cert_eu import (
    ADVISORY_URL,
    JSON_URL,
    MAX_RSS_BYTES,
    RSS_URL,
    CertEuAdapter,
    _Advisory,
    _parse_pub_date,
    _records,
)

if TYPE_CHECKING:
    from tests.conftest import FakeClock

FIXTURES = Path(__file__).parents[2] / "fixtures" / "cert_eu"
RSS = (FIXTURES / "cert_eu_rss.xml").read_bytes()
NOW = datetime(2026, 10, 23, 15, 0, 0, tzinfo=UTC)
# Just before the revised 2026-018 (`pubDate` 14:36:26Z).
BEFORE_018 = "2026-10-23T14:36:25Z"
CVES_018 = ["CVE-2026-109618", "CVE-2026-109619", "CVE-2026-115560"]

type Payload = dict[str, Any]
type MakeAdapter = Callable[[], tuple[CertEuAdapter, HttpClient]]


def _advisory(name: str) -> Payload:
    payload: Payload = json.loads((FIXTURES / f"cert_eu_{name}.json").read_bytes())
    return payload


def _derived(serial: str) -> Payload:
    """A single-CVE advisory under another serial (derived from the recorded one)."""
    return {**_advisory("2026_019_single_cve"), "serial_number": serial}


def _rss(keep: Sequence[str], rename: Mapping[str, str] | None = None) -> bytes:
    """The recorded RSS reduced to the given serials (bytes derived, not written)."""
    items = re.findall(rb"<item>.*?</item>", RSS, re.DOTALL)
    start = RSS.index(b"<item>")
    end = RSS.rindex(b"</item>") + len(b"</item>")
    chosen = [i for i in items if any(s.encode() + b":" in i for s in keep)]
    body = b"\n".join(chosen)
    for old, new in (rename or {}).items():
        body = body.replace(old.encode(), new.encode())
    return RSS[:start] + body + RSS[end:]


def _serve(
    respx_mock: respx.MockRouter,
    advisories: Mapping[str, Payload | bytes],
    rss: bytes = RSS,
) -> dict[str, respx.Route]:
    respx_mock.get(RSS_URL).respond(200, content=rss)
    routes = {}
    for serial, payload in advisories.items():
        content = (
            payload if isinstance(payload, bytes) else json.dumps(payload).encode()
        )
        routes[serial] = respx_mock.get(JSON_URL.format(serial=serial)).respond(
            200, content=content
        )
    return routes


@pytest.fixture
def make_adapter(fake_clock: FakeClock) -> MakeAdapter:
    fake_clock.set(NOW)

    def build() -> tuple[CertEuAdapter, HttpClient]:
        async def no_sleep(_delay: float) -> None:
            return None

        http = HttpClient(sleep=no_sleep, random=lambda: 0.0, max_attempts=2)
        return CertEuAdapter(http, fake_clock), http

    return build


async def _fetch(make_adapter: MakeAdapter, cursor: str | None) -> FetchResult:
    adapter, http = make_adapter()
    async with http:
        return await adapter.fetch(cursor)


def test_adapter_satisfies_the_protocol(fake_clock: FakeClock) -> None:
    adapter: SourceAdapter = CertEuAdapter(HttpClient(), fake_clock)
    assert adapter.name == "cert_eu"


# --- 1, 2: one record per CVE, field mapping --------------------------------------


@pytest.mark.anyio
async def test_a_multi_cve_advisory_gives_one_record_per_cve(
    respx_mock: respx.MockRouter, make_adapter: MakeAdapter
) -> None:
    _serve(respx_mock, {"2026-018": _advisory("2026_018_multi_cve_revised")})
    result = await _fetch(make_adapter, BEFORE_018)
    assert [r.record_id for r in result.records] == [f"2026-018:{c}" for c in CVES_018]
    assert result.skipped == 0
    for record, cve in zip(result.records, CVES_018, strict=True):
        assert record.source == "cert_eu"
        assert record.aliases == (cve, "CERT-EU-SA2026-018")
        assert record.url == (
            "https://cert.europa.eu/publications/security-advisories/2026-018/"
        )
        assert record.observed_at == NOW
        assert record.published_at == datetime(2026, 10, 20, tzinfo=UTC)
        assert record.updated_at == datetime(2026, 10, 23, 14, 36, 26, tzinfo=UTC)
        assert record.scores == ()
        assert record.kev == ()
        assert record.epss is None


@pytest.mark.anyio
async def test_field_mapping_of_the_multi_cve_advisory(
    respx_mock: respx.MockRouter, make_adapter: MakeAdapter
) -> None:
    _serve(respx_mock, {"2026-018": _advisory("2026_018_multi_cve_revised")})
    record = (await _fetch(make_adapter, BEFORE_018)).records[0]
    assert record.title is not None
    assert record.title.value == "Critical Vulnerabilities in PHP"
    assert record.title.source == "cert_eu"
    assert record.description is not None
    assert record.description.value.startswith("On 20 October 2026, the PHP project")
    assert "<br" not in record.description.value
    assert "\n[New] On 23 October" in record.description.value
    assert "https://www.php.net/archive/2026.php#2026-10-20-1" in record.references
    assert len(record.references) == len(set(record.references)) == 8
    products = [a.value for a in record.affected]
    assert [p.product for p in products[:5]] == [
        "PHP 8.5 versions prior to 8.5.12",
        "PHP 8.4 versions prior to 8.4.27",
        "PHP 8.3 versions prior to 8.3.36",
        "PHP 8.2 versions prior to 8.2.35",
        "End-of-life branches (PHP 8.1 and older), which no longer receive updates "
        "from the PHP project",
    ]
    assert products[5].product.startswith("Many commercial and open-source products")
    assert len(products) == 6
    assert all(p.vendor is None and p.version_text is None for p in products)
    assert all(a.source == "cert_eu" and a.observed_at == NOW for a in record.affected)


@pytest.mark.anyio
async def test_each_cve_of_an_advisory_is_a_distinct_merge_key_set(
    respx_mock: respx.MockRouter, make_adapter: MakeAdapter
) -> None:
    _serve(respx_mock, {"2026-018": _advisory("2026_018_multi_cve_revised")})
    result = await _fetch(make_adapter, BEFORE_018)
    normalized = [n for r in result.records if (n := normalize(r)) is not None]
    assert [merge_keys(n) for n in normalized] == [frozenset({c}) for c in CVES_018]
    assert [n.primary_id for n in normalized] == CVES_018


# --- 3, 4: affected products ------------------------------------------------------


@pytest.mark.anyio
async def test_a_lead_in_names_the_product_the_bullets_omit(
    respx_mock: respx.MockRouter, make_adapter: MakeAdapter
) -> None:
    _serve(
        respx_mock,
        {"2026-019": _advisory("2026_019_single_cve")},
        rss=_rss(["2026-019"]),
    )
    result = await _fetch(make_adapter, None)
    assert [r.record_id for r in result.records] == ["2026-019:CVE-2026-110264"]
    record = result.records[0]
    assert [a.value.product for a in record.affected] == [
        "The vulnerability CVE-2026-110264 affects the following versions of GitLab "
        "Community and Enterprise Editions: from 7.0 before 18.11.13; "
        "19.0 before 19.0.10; 19.1 before 19.1.9; 19.2 before 19.2.9; "
        "19.3 before 19.3.6; 19.4 before 19.4.3; 19.5 before 19.5.1"
    ]
    vulnerability = build_vulnerability([_normalized(record)])
    endpoint = Endpoint(
        id="team-gitlab",
        name="GitLab",
        products=(
            ProductEntry("gitlab"),
            ProductEntry("gitlab", min_version=Version("19.0")),
        ),
    )
    result_match = match(vulnerability, endpoint)
    assert result_match is not None
    first, second = result_match.matches
    assert first.structured is True
    assert first.versions_unknown is False
    assert second.structured is True
    assert second.versions_unknown is True


def _normalized(record: Any) -> Any:
    normalized = normalize(record)
    assert normalized is not None
    return normalized


@pytest.mark.anyio
async def test_an_advisory_without_cve_is_one_advisory_only_record(
    respx_mock: respx.MockRouter, make_adapter: MakeAdapter
) -> None:
    _serve(
        respx_mock,
        {"2025-029": _advisory("2025_029_no_cve")},
        rss=_rss(["2026-017"], {"2026-017": "2025-029"}),
    )
    result = await _fetch(make_adapter, None)
    assert len(result.records) == 1
    record = result.records[0]
    assert record.record_id == "CERT-EU-SA2025-029"
    assert record.aliases == ("CERT-EU-SA2025-029",)
    normalized = normalize(record)
    assert normalized is not None
    assert normalized.primary_id == "CERT-EU-SA2025-029"
    assert merge_keys(normalized) == frozenset()
    assert [a.value.product for a in record.affected] == [
        "The vulnerability seems to be affecting Gen 7 SonicWall firewalls. The "
        "vendor is investigating, but at the time of this writing, no more details "
        "are available"
    ]
    assert record.url == ADVISORY_URL.format(serial="2025-029")


@pytest.mark.anyio
async def test_per_cve_product_blocks_stay_with_their_cve(
    respx_mock: respx.MockRouter, make_adapter: MakeAdapter
) -> None:
    _serve(
        respx_mock,
        {"2025-008": _advisory("2025_008_cve_blocks")},
        rss=_rss(["2026-017"], {"2026-017": "2025-008"}),
    )
    result = await _fetch(make_adapter, None)
    assert [r.record_id for r in result.records] == [
        "2025-008:CVE-2023-48790",
        "2025-008:CVE-2024-45324",
        "2025-008:CVE-2023-40723",
    ]
    blocks = {
        r.record_id.split(":")[1]: [a.value.product for a in r.affected]
        for r in result.records
    }
    assert all(len(texts) == 1 for texts in blocks.values())
    assert "FortiNDR" in blocks["CVE-2023-48790"][0]
    assert "FortiOS" in blocks["CVE-2024-45324"][0]
    assert "FortiSIEM" not in blocks["CVE-2024-45324"][0]
    assert "FortiSIEM" in blocks["CVE-2023-40723"][0]
    assert "FortiOS" not in blocks["CVE-2023-40723"][0]


@pytest.mark.anyio
async def test_an_advisory_without_the_section_has_no_affected_product(
    respx_mock: respx.MockRouter, make_adapter: MakeAdapter
) -> None:
    payload = _advisory("2026_019_single_cve")
    payload["content_markdown"] = payload["content_markdown"].replace(
        "# Affected Products", "# Something Else"
    )
    _serve(respx_mock, {"2026-019": payload}, rss=_rss(["2026-019"]))
    record = (await _fetch(make_adapter, None)).records[0]
    assert record.affected == ()


@pytest.mark.anyio
async def test_the_section_is_found_under_any_heading_case_and_fences_are_dropped(
    respx_mock: respx.MockRouter, make_adapter: MakeAdapter
) -> None:
    payload = _advisory("2026_019_single_cve")
    payload["content_markdown"] = (
        "# Summary\n\nCVE-2026-110264.\n\n# products AFFECTED\n\n"
        "- **[New]** Foo 1.0 [1, 2]\n"
        "  - nested bar\n"
        "- Baz\n  continued\n\n"
        "```\n# not a heading\n- not a bullet\n```\n\n# Other\n\n- after\n"
    )
    _serve(respx_mock, {"2026-019": payload}, rss=_rss(["2026-019"]))
    record = (await _fetch(make_adapter, None)).records[0]
    assert [a.value.product for a in record.affected] == [
        "Foo 1.0; nested bar",
        "Baz continued",
    ]


@pytest.mark.anyio
@pytest.mark.parametrize(
    "heading",
    ["# **Affected Products**", "# Affected Products:", "# `Products Affected`"],
)
async def test_the_section_heading_may_carry_emphasis_or_a_colon(
    heading: str, respx_mock: respx.MockRouter, make_adapter: MakeAdapter
) -> None:
    payload = _advisory("2026_019_single_cve")
    payload["content_markdown"] = payload["content_markdown"].replace(
        "# Affected Products", heading
    )
    _serve(respx_mock, {"2026-019": payload}, rss=_rss(["2026-019"]))
    record = (await _fetch(make_adapter, None)).records[0]
    assert len(record.affected) == 1
    assert "GitLab" in record.affected[0].value.product


@pytest.mark.anyio
async def test_a_loose_list_stays_one_list_under_its_lead_in(
    respx_mock: respx.MockRouter, make_adapter: MakeAdapter
) -> None:
    payload = _advisory("2026_019_single_cve")
    payload["content_markdown"] = (
        "# Affected Products\n\nCVE-2026-110264 affects Foo:\n\n- 1.0\n\n- 2.0\n"
    )
    _serve(respx_mock, {"2026-019": payload}, rss=_rss(["2026-019"]))
    record = (await _fetch(make_adapter, None)).records[0]
    assert [a.value.product for a in record.affected] == [
        "CVE-2026-110264 affects Foo: 1.0; 2.0"
    ]


# --- 1: CVE extraction ------------------------------------------------------------


@pytest.mark.anyio
async def test_cve_ids_are_case_insensitive_unique_and_whole(
    respx_mock: respx.MockRouter, make_adapter: MakeAdapter
) -> None:
    payload = _advisory("2026_019_single_cve")
    payload["content_markdown"] = (
        "# References\n\n[1] <https://example.test/cve-2026-1234>\n\n"
        "CVE-2026-1234 again, CVE-2026-1234567, XCVE-2026-9999, CVE-2026-9999X "
        "and cve-2026-5678.\n"
    )
    _serve(respx_mock, {"2026-019": payload}, rss=_rss(["2026-019"]))
    result = await _fetch(make_adapter, None)
    assert [r.record_id for r in result.records] == [
        "2026-019:CVE-2026-1234",
        "2026-019:CVE-2026-1234567",
        "2026-019:CVE-2026-5678",
    ]


# --- 5: cursor --------------------------------------------------------------------


@pytest.mark.anyio
async def test_without_cursor_the_start_is_14_days_before_now(
    respx_mock: respx.MockRouter, make_adapter: MakeAdapter, fake_clock: FakeClock
) -> None:
    # Floor 2026-10-23T14:00Z: only 2026-018 (14:36:26Z) is newer.
    fake_clock.set(datetime(2026, 11, 6, 14, 0, 0, tzinfo=UTC))
    routes = _serve(
        respx_mock,
        {
            "2026-018": _advisory("2026_018_multi_cve_revised"),
            "2026-019": _advisory("2026_019_single_cve"),
        },
    )
    result = await _fetch(make_adapter, None)
    assert len(result.records) == 3
    assert routes["2026-018"].call_count == 1
    assert routes["2026-019"].call_count == 0
    assert result.next_cursor == "2026-10-23T14:36:26Z"


@pytest.mark.anyio
async def test_a_cursor_on_the_newest_pub_date_fetches_no_advisory(
    respx_mock: respx.MockRouter, make_adapter: MakeAdapter
) -> None:
    routes = _serve(respx_mock, {"2026-018": _advisory("2026_018_multi_cve_revised")})
    result = await _fetch(make_adapter, "2026-10-23T14:36:26Z")
    assert result.records == ()
    assert result.skipped == 0
    assert result.next_cursor == "2026-10-23T14:36:26Z"
    assert routes["2026-018"].call_count == 0


@pytest.mark.anyio
async def test_next_cursor_is_the_newest_pub_date_as_utc(
    respx_mock: respx.MockRouter, make_adapter: MakeAdapter
) -> None:
    _serve(respx_mock, {"2026-018": _advisory("2026_018_multi_cve_revised")})
    result = await _fetch(make_adapter, BEFORE_018)
    assert result.next_cursor == "2026-10-23T14:36:26Z"


@pytest.mark.anyio
@pytest.mark.parametrize(
    "cursor", ["not a date", "2026-10-23T14:36:25", "2027-01-01T00:00:00Z"]
)
async def test_an_invalid_or_future_cursor_falls_back_to_the_floor(
    respx_mock: respx.MockRouter, make_adapter: MakeAdapter, cursor: str
) -> None:
    # Floor 2026-10-09T15:00Z: 2026-018, 019, 017 and 016 are newer.
    routes = _serve(
        respx_mock,
        {
            "2026-018": _advisory("2026_018_multi_cve_revised"),
            "2026-019": _advisory("2026_019_single_cve"),
            "2026-017": _derived("2026-017"),
            "2026-016": _derived("2026-016"),
        },
    )
    with capture_logs() as logs:
        result = await _fetch(make_adapter, cursor)
    assert [e["event"] for e in logs if e["log_level"] == "warning"] == [
        "source.cursor_invalid"
    ]
    assert all(route.call_count == 1 for route in routes.values())
    assert len(result.records) == 6


@pytest.mark.anyio
async def test_a_cursor_older_than_the_window_is_clamped(
    respx_mock: respx.MockRouter, make_adapter: MakeAdapter
) -> None:
    # Floor 2026-10-09T15:00Z: 2026-015 (10-07) is older and never fetched.
    routes = _serve(
        respx_mock,
        {
            "2026-018": _advisory("2026_018_multi_cve_revised"),
            "2026-019": _advisory("2026_019_single_cve"),
            "2026-017": {
                **_advisory("2026_019_single_cve"),
                "serial_number": "2026-017",
            },
            "2026-016": {
                **_advisory("2026_019_single_cve"),
                "serial_number": "2026-016",
            },
            "2026-015": {
                **_advisory("2026_019_single_cve"),
                "serial_number": "2026-015",
            },
        },
    )
    with capture_logs() as logs:
        await _fetch(make_adapter, "2020-01-01T00:00:00Z")
    assert "source.cursor_clamped" in [e["event"] for e in logs]
    assert routes["2026-016"].call_count == 1
    assert routes["2026-015"].call_count == 0


@pytest.mark.anyio
async def test_a_future_pub_date_is_capped_at_the_fetch_time(
    respx_mock: respx.MockRouter, make_adapter: MakeAdapter
) -> None:
    rss = RSS.replace(
        b"Fri, 23 Oct 2026 16:36:26 CEST", b"Mon, 26 Oct 2026 16:36:26 CEST"
    )
    _serve(respx_mock, {"2026-018": _advisory("2026_018_multi_cve_revised")}, rss=rss)
    result = await _fetch(make_adapter, BEFORE_018)
    assert result.next_cursor == "2026-10-23T15:00:00Z"
    assert len(result.records) == 3


# --- 5: revisions -----------------------------------------------------------------


@pytest.mark.anyio
async def test_a_revision_adds_a_record_and_keeps_the_existing_ids(
    respx_mock: respx.MockRouter, make_adapter: MakeAdapter
) -> None:
    v12 = _advisory("2026_018_multi_cve_revised")
    v11 = copy.deepcopy(v12)
    v11["content_markdown"] = v11["content_markdown"].replace(
        "CVE-2026-115560", "a third vulnerability"
    )
    v11["publish_date"] = "21-10-2026 08:00:00"
    rss_v11 = _rss(["2026-018"]).replace(
        b"Fri, 23 Oct 2026 16:36:26 CEST", b"Wed, 21 Oct 2026 10:00:00 CEST"
    )
    routes = _serve(respx_mock, {"2026-018": v11}, rss=rss_v11)
    first = await _fetch(make_adapter, "2026-10-20T00:00:00Z")
    assert [r.record_id for r in first.records] == [
        "2026-018:CVE-2026-109618",
        "2026-018:CVE-2026-109619",
    ]
    assert first.next_cursor == "2026-10-21T08:00:00Z"
    assert routes["2026-018"].call_count == 1

    respx_mock.clear()
    routes = _serve(respx_mock, {"2026-018": v12}, rss=_rss(["2026-018"]))
    second = await _fetch(make_adapter, first.next_cursor)
    assert [r.record_id for r in second.records] == [f"2026-018:{c}" for c in CVES_018]
    assert second.records[0].record_id == first.records[0].record_id
    assert second.records[1].record_id == first.records[1].record_id
    assert second.next_cursor == "2026-10-23T14:36:26Z"

    # the same revision again: its `pubDate` is not newer, no advisory request
    respx_mock.clear()
    routes = _serve(respx_mock, {"2026-018": v12}, rss=_rss(["2026-018"]))
    third = await _fetch(make_adapter, second.next_cursor)
    assert third.records == ()
    assert routes["2026-018"].call_count == 0


# --- 6: dates ---------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (
            "Fri, 23 Oct 2026 16:36:26 CEST",
            datetime(2026, 10, 23, 14, 36, 26, tzinfo=UTC),
        ),
        ("Mon, 02 Nov 2026 10:00:00 CET", datetime(2026, 11, 2, 9, 0, 0, tzinfo=UTC)),
        (
            "Fri, 23 Oct 2026 16:36:26 +0200",
            datetime(2026, 10, 23, 14, 36, 26, tzinfo=UTC),
        ),
        (
            "Fri, 23 Oct 2026 14:36:26 GMT",
            datetime(2026, 10, 23, 14, 36, 26, tzinfo=UTC),
        ),
        (
            "  Fri, 23 Oct 2026 16:36:26 cest ",
            datetime(2026, 10, 23, 14, 36, 26, tzinfo=UTC),
        ),
        ("Fri, 23 Oct 2026 16:36:26 PDTX", None),
        ("Fri, 23 Oct 2026 16:36:26", None),
        ("yesterday", None),
        ("", None),
        (None, None),
    ],
)
def test_pub_date_zones(text: str | None, expected: datetime | None) -> None:
    assert _parse_pub_date(text) == expected


@pytest.mark.anyio
async def test_an_rss_item_with_an_unknown_zone_is_skipped(
    respx_mock: respx.MockRouter, make_adapter: MakeAdapter
) -> None:
    rss = RSS.replace(b"16:36:26 CEST", b"16:36:26 PDTX")
    routes = _serve(
        respx_mock, {"2026-018": _advisory("2026_018_multi_cve_revised")}, rss=rss
    )
    result = await _fetch(make_adapter, "2026-10-22T09:14:22Z")
    assert result.records == ()
    assert result.skipped == 1
    assert routes["2026-018"].call_count == 0
    assert result.next_cursor == "2026-10-22T09:14:22Z"


@pytest.mark.anyio
async def test_an_unreadable_publish_date_falls_back_to_the_rss_pub_date(
    respx_mock: respx.MockRouter, make_adapter: MakeAdapter
) -> None:
    payload = _advisory("2026_019_single_cve")
    payload["publish_date"] = "22/10/2026"
    _serve(respx_mock, {"2026-019": payload}, rss=_rss(["2026-019"]))
    record = (await _fetch(make_adapter, None)).records[0]
    # Thu, 22 Oct 2026 11:14:22 CEST
    assert record.updated_at == datetime(2026, 10, 22, 9, 14, 22, tzinfo=UTC)


@pytest.mark.anyio
async def test_a_missing_original_date_leaves_published_at_unknown(
    respx_mock: respx.MockRouter, make_adapter: MakeAdapter
) -> None:
    payload = _advisory("2026_019_single_cve")
    payload["content_markdown"] = re.sub(
        r"original_date:.*\n", "", payload["content_markdown"]
    )
    _serve(respx_mock, {"2026-019": payload}, rss=_rss(["2026-019"]))
    record = (await _fetch(make_adapter, None)).records[0]
    assert record.published_at is None
    assert record.updated_at == datetime(2026, 10, 22, 9, 14, 22, tzinfo=UTC)


@pytest.mark.anyio
async def test_the_json_publish_date_is_utc_not_paris_time(
    respx_mock: respx.MockRouter, make_adapter: MakeAdapter
) -> None:
    payload = _advisory("2026_018_multi_cve_revised")
    assert payload["publish_date"] == "23-10-2026 14:36:26"
    _serve(respx_mock, {"2026-018": payload}, rss=_rss(["2026-018"]))
    record = (await _fetch(make_adapter, BEFORE_018)).records[0]
    assert record.updated_at == datetime(2026, 10, 23, 14, 36, 26, tzinfo=UTC)


# --- 4: XML safety ----------------------------------------------------------------

BILLION_LAUGHS = (
    b'<?xml version="1.0"?><!DOCTYPE lolz [<!ENTITY lol "lol">'
    b'<!ENTITY lol2 "&lol;&lol;&lol;&lol;"><!ENTITY lol3 "&lol2;&lol2;&lol2;">]>'
)
XXE = b'<!DOCTYPE r [<!ENTITY x SYSTEM "file:///etc/passwd">]>'


@pytest.mark.anyio
@pytest.mark.parametrize(
    "body",
    [
        BILLION_LAUGHS + RSS,
        XXE + RSS,
        XXE + RSS.replace(b"<title>Latest", b"<title>&x;Latest", 1),
        b"<!DOCTYPE rss>" + RSS,
        RSS[: len(RSS) // 2],
        b"<html><body>not xml",
        b"",
    ],
    ids=[
        "billion-laughs",
        "xxe",
        "xxe-referenced",
        "empty-doctype",
        "truncated",
        "html",
        "empty",
    ],
)
async def test_an_unsafe_or_malformed_rss_is_a_payload_error(
    respx_mock: respx.MockRouter, make_adapter: MakeAdapter, body: bytes
) -> None:
    routes = _serve(
        respx_mock, {"2026-018": _advisory("2026_018_multi_cve_revised")}, rss=body
    )
    with pytest.raises(SourcePayloadError):
        await _fetch(make_adapter, BEFORE_018)
    assert routes["2026-018"].call_count == 0


# --- 7: failures ------------------------------------------------------------------


@pytest.mark.anyio
@pytest.mark.parametrize(
    "rss",
    [
        b'<rss version="2.0"></rss>',
        b'<rss version="2.0"><channel><title>x</title></channel></rss>',
        RSS.replace(b"pubDate>", b"pubDated>"),
        re.sub(rb"\d{4}-\d{3}", b"x", RSS),
    ],
    ids=["no-channel", "no-item", "no-usable-date", "no-usable-serial"],
)
async def test_an_unexpected_rss_shape_is_a_contract_error(
    respx_mock: respx.MockRouter, make_adapter: MakeAdapter, rss: bytes
) -> None:
    _serve(respx_mock, {}, rss=rss)
    with pytest.raises(SourceContractError):
        await _fetch(make_adapter, None)


@pytest.mark.anyio
@pytest.mark.parametrize(
    "broken",
    [
        b"<!DOCTYPE html><html><body>Not found</body></html>",
        {"title": "no content", "serial_number": "2026-019"},
        {"title": "wrong type", "serial_number": "2026-019", "content_markdown": 3},
        {"serial_number": "2026-099", "content_markdown": "CVE-2026-1234"},
        {"serial_number": "2026-019", "content_markdown": "x" * (3 * 1024 * 1024)},
    ],
    ids=["html", "no-content", "wrong-type", "other-serial", "too-large"],
)
async def test_a_broken_advisory_is_skipped_and_held_back(
    respx_mock: respx.MockRouter,
    make_adapter: MakeAdapter,
    broken: bytes | Payload,
) -> None:
    _serve(
        respx_mock,
        {"2026-018": _advisory("2026_018_multi_cve_revised"), "2026-019": broken},
        rss=_rss(["2026-018", "2026-019"]),
    )
    with capture_logs() as logs:
        result = await _fetch(make_adapter, None)
    assert [r.record_id for r in result.records] == [f"2026-018:{c}" for c in CVES_018]
    assert result.skipped == 1
    skipped = [e for e in logs if e["event"] == "source.record_skipped"]
    assert [e["record_id"] for e in skipped] == ["2026-019"]
    # 2026-019 is `2026-10-22T09:14:22Z`: the cursor stays strictly below it
    assert result.next_cursor is not None
    assert result.next_cursor < "2026-10-22T09:14:22Z"
    assert result.next_cursor == "2026-10-09T15:00:00Z"


@pytest.mark.anyio
async def test_the_cursor_keeps_what_is_older_than_a_failed_advisory(
    respx_mock: respx.MockRouter, make_adapter: MakeAdapter
) -> None:
    # 2026-019 (10-22) fails, 2026-017 (10-15) is fine: the cursor reaches 10-15.
    _serve(
        respx_mock,
        {
            "2026-019": b"<html></html>",
            "2026-017": {
                **_advisory("2026_019_single_cve"),
                "serial_number": "2026-017",
            },
        },
        rss=_rss(["2026-019", "2026-017"]),
    )
    result = await _fetch(make_adapter, None)
    assert result.skipped == 1
    assert result.next_cursor == "2026-10-15T09:38:20Z"


@pytest.mark.anyio
async def test_every_advisory_unusable_is_a_contract_error(
    respx_mock: respx.MockRouter, make_adapter: MakeAdapter
) -> None:
    _serve(respx_mock, {"2026-019": b"<html></html>"}, rss=_rss(["2026-019"]))
    with pytest.raises(SourceContractError):
        await _fetch(make_adapter, None)


@pytest.mark.anyio
@pytest.mark.parametrize("status", [500, 403, 404])
async def test_an_rss_error_status_is_a_transport_error(
    respx_mock: respx.MockRouter, make_adapter: MakeAdapter, status: int
) -> None:
    respx_mock.get(RSS_URL).respond(status)
    with pytest.raises(SourceTransportError):
        await _fetch(make_adapter, None)


@pytest.mark.anyio
async def test_an_advisory_404_fails_the_whole_fetch(
    respx_mock: respx.MockRouter, make_adapter: MakeAdapter
) -> None:
    respx_mock.get(RSS_URL).respond(200, content=_rss(["2026-018", "2026-019"]))
    respx_mock.get(JSON_URL.format(serial="2026-018")).respond(
        200, content=json.dumps(_advisory("2026_018_multi_cve_revised")).encode()
    )
    respx_mock.get(JSON_URL.format(serial="2026-019")).respond(404, content=b"<html/>")
    with pytest.raises(SourceTransportError):
        await _fetch(make_adapter, None)


@pytest.mark.anyio
async def test_an_oversized_rss_is_a_payload_error(
    respx_mock: respx.MockRouter, make_adapter: MakeAdapter
) -> None:
    respx_mock.get(RSS_URL).respond(200, content=b"x" * (MAX_RSS_BYTES + 1))
    with pytest.raises(SourcePayloadError):
        await _fetch(make_adapter, None)


# --- live (deselected by default) ---------------------------------------------------


@pytest.mark.live
@pytest.mark.enable_socket
@pytest.mark.anyio
async def test_live_rss_matches_the_recorded_shape() -> None:
    async with HttpClient() as http:
        result = await CertEuAdapter(http, SystemClock()).fetch(None)
    assert result.skipped == 0
    assert result.next_cursor is not None
    cursor = datetime.fromisoformat(result.next_cursor)
    assert cursor.tzinfo is not None
    assert cursor.utcoffset() is not None
    for record in result.records:
        assert re.fullmatch(
            r"\d{4}-\d{3,}:CVE-\d{4}-\d{4,}|CERT-EU-SA\d{4}-\d{3,}", record.record_id
        )
        assert record.scores == ()


async def _live_records(serial: str) -> list[str]:
    async with HttpClient() as http:
        data = await get_json(http, "cert_eu", JSON_URL.format(serial=serial))
    now = datetime.now(UTC)
    records = _records(_Advisory.model_validate(data), serial, now, now)
    return [r.record_id for r in records]


@pytest.mark.live
@pytest.mark.enable_socket
@pytest.mark.anyio
async def test_live_multi_cve_advisory() -> None:
    ids = await _live_records("2026-018")
    assert {f"2026-018:{cve}" for cve in CVES_018} <= set(ids)


@pytest.mark.live
@pytest.mark.enable_socket
@pytest.mark.anyio
async def test_live_single_cve_advisory() -> None:
    assert await _live_records("2026-019") == ["2026-019:CVE-2026-110264"]


@pytest.mark.live
@pytest.mark.enable_socket
@pytest.mark.anyio
async def test_live_advisory_without_cve() -> None:
    assert await _live_records("2025-029") == ["CERT-EU-SA2025-029"]
