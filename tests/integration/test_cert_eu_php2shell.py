"""PHP2Shell replayed from CERT-EU to EUVD through the whole pipeline.

CERT-EU 2026-018 was published about 7.5 hours before EUVD had the PHP records
(SM-3). The real adapters run on the recorded payloads (respx), the state is a
SQLite file, and the clock is moved between the two sources.
"""

import json
import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from pathlib import Path
from types import MappingProxyType
from typing import Any

import pytest
import respx
from sqlalchemy import select
from structlog.testing import capture_logs

from cvestream.config.provider import ConfigSnapshot
from cvestream.domain.endpoint import Endpoint, ProductEntry
from cvestream.domain.publication import PublicationDecision
from cvestream.domain.severity import Band
from cvestream.http import HttpClient
from cvestream.pipeline.run import SourceOutcome, cycle
from cvestream.sources.cert_eu import JSON_URL, RSS_URL, CertEuAdapter
from cvestream.sources.euvd import BASE_URL, EuvdAdapter
from cvestream.state import schema
from cvestream.state.db import State
from tests.conftest import FakeClock

FIXTURES = Path(__file__).parents[1] / "fixtures"
CERT_EU_NOW = datetime(2026, 10, 23, 15, 0, 0, tzinfo=UTC)
EUVD_NOW = datetime(2026, 10, 30, 19, 0, 0, tzinfo=UTC)
# Just before the revised 2026-018 (`pubDate` 14:36:26Z).
CERT_EU_CURSOR = "2026-10-23T14:36:25Z"
# Just before the two recorded EUVD records (`dateUpdated` 6:31:35 PM UTC).
EUVD_CURSOR = "2026-10-30T18:31:34Z"
CVES = ("CVE-2026-109618", "CVE-2026-109619", "CVE-2026-115560")
ENDPOINT = Endpoint(id="team-php", name="PHP team", products=(ProductEntry("php"),))
SNAPSHOT = ConfigSnapshot(
    revision="r", endpoints=(ENDPOINT,), tokens=MappingProxyType({})
)


class _Provider:
    """A provider whose configuration never changes."""

    def revision(self) -> str:
        return SNAPSHOT.revision

    def load(self) -> ConfigSnapshot:
        return SNAPSHOT


async def _no_sleep(_delay: float) -> None:
    return None


def _http() -> HttpClient:
    return HttpClient(sleep=_no_sleep, random=lambda: 0.0, max_attempts=2)


def _recorded(*parts: str) -> bytes:
    return FIXTURES.joinpath(*parts).read_bytes()


async def _cert_eu(
    respx_mock: respx.MockRouter, clock: FakeClock, cursor: str | None
) -> tuple[SourceOutcome, int]:
    """The CERT-EU outcome and the number of advisory requests it made."""
    respx_mock.get(RSS_URL).respond(
        200, content=_recorded("cert_eu", "cert_eu_rss.xml")
    )
    route = respx_mock.get(JSON_URL.format(serial="2026-018")).respond(
        200,
        content=_recorded("cert_eu", "cert_eu_2026_018_multi_cve_revised.json"),
    )
    async with _http() as http:
        result = await CertEuAdapter(http, clock).fetch(cursor)
    return SourceOutcome("cert_eu", result), route.call_count


async def _euvd(respx_mock: respx.MockRouter, clock: FakeClock) -> SourceOutcome:
    first: dict[str, Any] = json.loads(
        _recorded("euvd", "euvd_php_branch_wildcard.json")
    )
    second: dict[str, Any] = json.loads(
        _recorded("euvd", "euvd_php2shell_second_cve.json")
    )
    # `dateUpdated` descending, as the real API sorts
    page = {"items": [*second["items"], *first["items"]], "total": 2}
    respx_mock.get(f"{BASE_URL}/kev/dump").respond(
        200, content=_recorded("euvd", "euvd_kev_dump.json")
    )
    respx_mock.get(f"{BASE_URL}/search").respond(200, content=json.dumps(page).encode())
    async with _http() as http:
        result = await EuvdAdapter(http, clock, sleep=_no_sleep).fetch(EUVD_CURSOR)
    return SourceOutcome("euvd", result)


def _php2shell_vulnerabilities(state: State) -> list[str]:
    """Primary IDs of the PHP2Shell vulnerabilities (the KEV dump adds others)."""
    with state.transaction() as repository:
        rows = repository._connection.execute(
            select(schema.vulnerability.c.primary_id).where(
                schema.vulnerability.c.primary_id.in_(CVES)
            )
        ).all()
    return sorted(row.primary_id for row in rows)


def _by_primary_id(decisions: tuple[PublicationDecision, ...]) -> dict[str, Any]:
    return {d.primary_id: d for d in decisions}


@pytest.mark.anyio
async def test_php2shell_goes_from_cert_eu_to_euvd(
    respx_mock: respx.MockRouter, fake_clock: FakeClock, tmp_path: Path
) -> None:
    state = State(f"sqlite:///{tmp_path / 'state.db'}")
    state.migrate()
    provider = _Provider()
    with capture_logs() as logs:
        # Run 1: CERT-EU alone. One `new` item per CVE, severity unknown.
        fake_clock.set(CERT_EU_NOW)
        cert_eu, requests = await _cert_eu(respx_mock, fake_clock, CERT_EU_CURSOR)
        assert requests == 1
        first = cycle(state, provider, SNAPSHOT, [cert_eu], fake_clock).run
        assert [d.kind for d in first.decisions] == ["new"] * 3
        assert {d.band for d in first.decisions} == {Band.UNKNOWN}
        assert {d.guid for d in first.decisions} == {
            f"cvestream:team-php:{cve}:new" for cve in CVES
        }
        assert _php2shell_vulnerabilities(state) == sorted(CVES)

        # Run 2: EUVD catches up on two of the three CVEs. Each is merged into
        # the CERT-EU vulnerability and published once more, critical.
        respx_mock.clear()
        fake_clock.set(EUVD_NOW)
        euvd = await _euvd(respx_mock, fake_clock)
        second = cycle(state, provider, SNAPSHOT, [euvd], fake_clock).run
        updates = _by_primary_id(second.decisions)
        assert sorted(updates) == ["CVE-2026-109618", "CVE-2026-109619"]
        assert {d.kind for d in updates.values()} == {"band-critical"}
        assert {d.band for d in updates.values()} == {Band.CRITICAL}
        assert updates["CVE-2026-109618"].guid == (
            "cvestream:team-php:CVE-2026-109618:band-critical"
        )
        # three CVEs stay three vulnerabilities: no merge across CVEs, EUVD merged
        assert _php2shell_vulnerabilities(state) == sorted(CVES)
        titles = _titles(second.feeds["team-php"].xml)
        # The title names a product only from EUVD data, never from CERT-EU prose.
        assert sorted(titles) == [
            "CVE-2026-109618 in PHP Group PHP: severity unknown",
            "CVE-2026-109619 in PHP Group PHP: severity unknown",
            "CVE-2026-115560 in php: severity unknown",
            "[UPDATE] CVE-2026-109618 in PHP Group PHP: severity raised to critical",
            "[UPDATE] CVE-2026-109619 in PHP Group PHP: severity raised to critical",
        ]

        # Run 3: the same CERT-EU revision again. Its `pubDate` is not newer than
        # the cursor, so no advisory is requested and nothing is decided.
        respx_mock.clear()
        fake_clock.set(EUVD_NOW)
        assert cert_eu.result is not None
        unchanged, requests = await _cert_eu(
            respx_mock, fake_clock, cert_eu.result.next_cursor
        )
        assert requests == 0
        assert (
            cycle(state, provider, SNAPSHOT, [unchanged], fake_clock).run.decisions
            == ()
        )

        # Run 4: the same records fed again (an unchanged content hash).
        again = cycle(state, provider, SNAPSHOT, [cert_eu], fake_clock).run
        assert again.decisions == ()
    assert _php2shell_vulnerabilities(state) == sorted(CVES)
    assert not [e for e in logs if e["event"] == "publish.skipped"]
    state.dispose()


def _titles(xml: bytes) -> list[str]:
    root = ET.fromstring(xml)  # noqa: S314 - our own output
    return [t for item in root.findall("channel/item") if (t := item.findtext("title"))]
