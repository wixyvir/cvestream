"""Guardrail (NFR-3, SM-4): nothing is posted twice, even across restarts.

The batches come from the real adapters run against the recorded EUVD and CISA
KEV fixtures; the state is a SQLite file that survives each simulated restart.
"""

import xml.etree.ElementTree as ET
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import MappingProxyType

import pytest
import respx
from sqlalchemy import select

from cvestream.config.provider import ConfigSnapshot
from cvestream.domain.endpoint import Endpoint, ProductEntry, Rules
from cvestream.domain.model import KevCatalog, KevEntry, Sourced, SourceRecord
from cvestream.http import HttpClient
from cvestream.pipeline.run import RunResult, SourceBatch, run
from cvestream.sources.base import FetchResult
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
ENDPOINT = Endpoint(
    id="team-browser", name="Browsers", products=(ProductEntry(name="Chrome"),)
)
SNAPSHOT = ConfigSnapshot(
    revision="r", endpoints=(ENDPOINT,), tokens=MappingProxyType({})
)
GROUPED_ENDPOINT = replace(ENDPOINT, rules=Rules(group_by_product=True))
GROUPED_SNAPSHOT = ConfigSnapshot(
    revision="r", endpoints=(GROUPED_ENDPOINT,), tokens=MappingProxyType({})
)


async def _no_sleep(_delay: float) -> None:
    return None


async def _fetch(
    respx_mock: respx.MockRouter, clock: FakeClock
) -> tuple[SourceBatch, SourceBatch]:
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
    return SourceBatch("euvd", euvd), SourceBatch("cisa_kev", cisa)


def _publications(state: State) -> set[tuple[str, str]]:
    with state.transaction() as repository:
        rows = repository._connection.execute(
            select(schema.publication.c.guid, schema.publication.c.kind)
        ).all()
    return {(row.guid, row.kind) for row in rows}


def _group_guids(state: State) -> set[str]:
    with state.transaction() as repository:
        rows = repository._connection.execute(
            select(schema.publication.c.group_guid).where(
                schema.publication.c.group_guid.is_not(None)
            )
        ).all()
    return {row.group_guid for row in rows}


def _feed_guids(result: RunResult) -> list[str]:
    root = ET.fromstring(result.feeds["team-browser"].xml)  # noqa: S314
    return [item.findtext("guid") or "" for item in root.findall("channel/item")]


def _kinds(result: RunResult) -> list[str]:
    return sorted(d.kind for d in result.decisions)


def _restart(state: State, url: str) -> State:
    """A new process on the same database file."""
    state.dispose()
    return State(url)


@pytest.mark.anyio
async def test_nothing_is_posted_twice_across_restarts(
    respx_mock: respx.MockRouter, fake_clock: FakeClock, tmp_path: Path
) -> None:
    fake_clock.set(NOW)
    url = f"sqlite:///{tmp_path / 'state.db'}"
    state = State(url)
    state.migrate()
    euvd, cisa = await _fetch(respx_mock, fake_clock)

    # run 1: the endpoint is new, so it is backfilled with what it matches
    first = run(state, SNAPSHOT, [euvd, cisa], fake_clock)
    assert first.decisions
    assert set(_kinds(first)) == {"new"}
    published = _publications(state)
    assert len(published) == len(first.decisions)
    assert (f"cvestream:team-browser:{CHROME_CVE}:new", "new") in published

    # restart, then the very same input: nothing new
    state = _restart(state, url)
    state.migrate()
    again = run(state, SNAPSHOT, [euvd, cisa], fake_clock)
    assert again.decisions == ()
    assert _publications(state) == published

    # a day later, fetched again (fresh observed_at everywhere): still nothing
    fake_clock.advance(timedelta(days=1))
    euvd, cisa = await _fetch(respx_mock, fake_clock)
    later = run(state, SNAPSHOT, [euvd, cisa], fake_clock)
    assert later.decisions == ()
    assert later.changed == 0
    assert _publications(state) == published

    # A published CVE becomes known-exploited. No recorded KEV entry shares a CVE
    # with the recorded EUVD search records, so the KEV record is built here.
    exploited = SourceRecord(
        source="cisa_kev",
        record_id=CHROME_CVE,
        observed_at=fake_clock.now(),
        aliases=(CHROME_CVE,),
        kev=(
            Sourced(
                KevEntry(KevCatalog.CISA_KEV, added_at=fake_clock.now()),
                "cisa_kev",
                fake_clock.now(),
            ),
        ),
    )
    kev_batch = SourceBatch(
        "cisa_kev", FetchResult(records=(exploited,), next_cursor=None)
    )
    escalated = run(state, SNAPSHOT, [kev_batch], fake_clock)
    assert _kinds(escalated) == ["kev"]
    assert escalated.decisions[0].guid == f"cvestream:team-browser:{CHROME_CVE}:kev"
    published = _publications(state)
    assert len(published) == len(first.decisions) + 1

    # restart again and rerun everything: exactly once means once
    state = _restart(state, url)
    fake_clock.advance(timedelta(minutes=15))
    for batches in ([kev_batch], [euvd, cisa], [euvd, cisa, kev_batch]):
        assert run(state, SNAPSHOT, batches, fake_clock).decisions == ()
    assert _publications(state) == published
    state.dispose()


@pytest.mark.anyio
async def test_nothing_is_posted_twice_across_restarts_when_grouped(
    respx_mock: respx.MockRouter, fake_clock: FakeClock, tmp_path: Path
) -> None:
    fake_clock.set(NOW)
    url = f"sqlite:///{tmp_path / 'grouped.db'}"
    state = State(url)
    state.migrate()
    euvd, cisa = await _fetch(respx_mock, fake_clock)

    # run 1: the three Chrome records of the recorded page are exactly the
    # threshold, so they publish as one group item (each member keeps its row)
    first = run(state, GROUPED_SNAPSHOT, [euvd, cisa], fake_clock)
    assert set(_kinds(first)) == {"new"}
    assert len(first.decisions) == 3
    published = _publications(state)
    assert len(published) == 3
    (group_guid,) = _group_guids(state)
    assert group_guid.startswith("cvestream:team-browser:group-new:")
    assert _feed_guids(first) == [group_guid]
    assert first.feeds["team-browser"].item_count == 1

    # restart, then the very same input: nothing new, same feed bytes
    state = _restart(state, url)
    state.migrate()
    again = run(state, GROUPED_SNAPSHOT, [euvd, cisa], fake_clock)
    assert again.decisions == ()
    assert _publications(state) == published
    assert again.feeds["team-browser"].xml == first.feeds["team-browser"].xml
    assert again.feeds["team-browser"].etag == first.feeds["team-browser"].etag

    # A published CVE becomes known-exploited: a single kev item on its own,
    # the stored group is not regrouped.
    fake_clock.advance(timedelta(minutes=15))
    exploited = SourceRecord(
        source="cisa_kev",
        record_id=CHROME_CVE,
        observed_at=fake_clock.now(),
        aliases=(CHROME_CVE,),
        kev=(
            Sourced(
                KevEntry(KevCatalog.CISA_KEV, added_at=fake_clock.now()),
                "cisa_kev",
                fake_clock.now(),
            ),
        ),
    )
    kev_batch = SourceBatch(
        "cisa_kev", FetchResult(records=(exploited,), next_cursor=None)
    )
    escalated = run(state, GROUPED_SNAPSHOT, [kev_batch], fake_clock)
    assert _kinds(escalated) == ["kev"]
    assert escalated.decisions[0].guid == f"cvestream:team-browser:{CHROME_CVE}:kev"
    published = _publications(state)
    assert len(published) == 4
    assert _group_guids(state) == {group_guid}
    assert _feed_guids(escalated) == [
        f"cvestream:team-browser:{CHROME_CVE}:kev",
        group_guid,
    ]
    assert escalated.feeds["team-browser"].item_count == 2

    # restart again and rerun everything: exactly once means once
    state = _restart(state, url)
    fake_clock.advance(timedelta(minutes=15))
    for batches in ([kev_batch], [euvd, cisa], [euvd, cisa, kev_batch]):
        assert run(state, GROUPED_SNAPSHOT, batches, fake_clock).decisions == ()
    assert _publications(state) == published
    assert _group_guids(state) == {group_guid}
    state.dispose()
