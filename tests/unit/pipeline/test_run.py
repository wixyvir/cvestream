import xml.etree.ElementTree as ET
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import MappingProxyType

import pytest
from sqlalchemy import Table, select
from structlog.testing import capture_logs

from cvestream.config.provider import ConfigSnapshot
from cvestream.domain.endpoint import Endpoint, ProductEntry, Rules
from cvestream.domain.model import (
    AffectedProduct,
    CvssScore,
    KevCatalog,
    KevEntry,
    Sourced,
    SourceRecord,
)
from cvestream.domain.publication import PublicationDecision
from cvestream.pipeline.run import (
    RunResult,
    SourceBatch,
    load_cursors,
    load_feeds,
    run,
)
from cvestream.sources.base import FetchResult
from cvestream.state import schema
from cvestream.state.db import State
from cvestream.state.repository import Repository, StoredFeed
from tests.conftest import FakeClock

CVE = "CVE-2026-1001"
# FakeClock's start: a source time is fixed, whatever the polling time
START = datetime(2026, 1, 1, tzinfo=UTC)


def _endpoint(
    endpoint_id: str = "team-php", product: str = "php", *, enabled: bool = True
) -> Endpoint:
    return Endpoint(
        id=endpoint_id,
        name=endpoint_id,
        products=(ProductEntry(name=product),),
        enabled=enabled,
    )


def _snapshot(*endpoints: Endpoint) -> ConfigSnapshot:
    return ConfigSnapshot(
        revision="r", endpoints=endpoints, tokens=MappingProxyType({})
    )


def _euvd(
    clock: FakeClock,
    *,
    record_id: str = "EUVD-2026-1",
    cve: str | None = CVE,
    score: float | None = 5.0,
    product: str | None = "php",
    description: str = "A flaw",
    updated_ago: timedelta = timedelta(hours=1),
) -> SourceRecord:
    now = clock.now()
    return SourceRecord(
        source="euvd",
        record_id=record_id,
        observed_at=now,
        aliases=(cve,) if cve else (),
        title=Sourced("title", "euvd", now),
        description=Sourced(description, "euvd", now),
        scores=(Sourced(CvssScore(score) if score is not None else None, "euvd", now),),
        affected=((Sourced(AffectedProduct(product), "euvd", now),) if product else ()),
        updated_at=START - updated_ago,
    )


def _kev(
    clock: FakeClock,
    cve: str = CVE,
    name: str | None = None,
    description: str | None = None,
) -> SourceRecord:
    now = clock.now()
    return SourceRecord(
        source="cisa_kev",
        record_id=cve,
        observed_at=now,
        title=Sourced(name, "cisa_kev", now) if name else None,
        description=Sourced(description, "cisa_kev", now) if description else None,
        kev=(
            Sourced(
                KevEntry(KevCatalog.CISA_KEV, added_at=now - timedelta(minutes=5)),
                "cisa_kev",
                now,
            ),
        ),
    )


def _batch(*records: SourceRecord, cursor: str | None = None) -> SourceBatch:
    return SourceBatch("euvd", FetchResult(records=records, next_cursor=cursor))


def _kinds(result: RunResult) -> list[tuple[str, str]]:
    return [(d.endpoint_id, d.kind) for d in result.decisions]


def _count(state: State, table: Table) -> int:
    with state.transaction() as repo:
        return len(repo._connection.execute(select(table)).all())


def test_first_match_is_new_with_the_stored_guid(
    state: State, fake_clock: FakeClock
) -> None:
    result = run(state, _snapshot(_endpoint()), [_batch(_euvd(fake_clock))], fake_clock)
    assert _kinds(result) == [("team-php", "new")]
    with state.transaction() as repo:
        row = repo._connection.execute(select(schema.publication)).one()
    assert row.guid == f"cvestream:team-php:{CVE}:new"
    assert row.published_at == fake_clock.now()
    assert row.primary_id == CVE
    assert row.products_json == ["php"]
    assert result.records == 1
    assert result.changed == 1
    assert result.touched == 1


def test_rerunning_the_same_batches_decides_nothing(
    state: State, fake_clock: FakeClock
) -> None:
    snapshot = _snapshot(_endpoint())
    batches = [_batch(_euvd(fake_clock))]
    run(state, snapshot, batches, fake_clock)
    fake_clock.advance(timedelta(minutes=15))
    again = run(state, snapshot, [_batch(_euvd(fake_clock))], fake_clock)
    assert again.decisions == ()
    assert again.changed == 0
    assert _count(state, schema.publication) == 1


def test_known_exploited_publishes_one_kev_update(
    state: State, fake_clock: FakeClock
) -> None:
    snapshot = _snapshot(_endpoint())
    run(state, snapshot, [_batch(_euvd(fake_clock))], fake_clock)
    fake_clock.advance(timedelta(minutes=15))
    result = run(state, snapshot, [_batch(_kev(fake_clock))], fake_clock)
    assert _kinds(result) == [("team-php", "kev")]
    fake_clock.advance(timedelta(minutes=15))
    assert run(state, snapshot, [_batch(_kev(fake_clock))], fake_clock).decisions == ()


def test_a_kev_only_record_waits_for_product_data_without_description_matching(
    state: State, fake_clock: FakeClock
) -> None:
    strict = Endpoint(
        id="team-php-strict",
        name="strict",
        products=(ProductEntry(name="php", match_description=False),),
    )
    snapshot = _snapshot(_endpoint(), strict)
    kev_only = _kev(
        fake_clock, description="This can be chained to implant PHP webshells."
    )
    first = run(state, snapshot, [_batch(kev_only)], fake_clock)
    # The default entry still matches the description; the strict one waits.
    assert _kinds(first) == [("team-php", "new")]
    fake_clock.advance(timedelta(minutes=15))
    # FR-26: EUVD brings the product. No score, so no band change on team-php.
    late = run(state, snapshot, [_batch(_euvd(fake_clock, score=None))], fake_clock)
    assert _kinds(late) == [("team-php-strict", "new")]


def test_a_score_rise_across_a_band_publishes_one_update(
    state: State, fake_clock: FakeClock
) -> None:
    snapshot = _snapshot(_endpoint())
    run(state, snapshot, [_batch(_euvd(fake_clock, score=5.0))], fake_clock)
    fake_clock.advance(timedelta(minutes=15))
    result = run(state, snapshot, [_batch(_euvd(fake_clock, score=9.8))], fake_clock)
    assert _kinds(result) == [("team-php", "band-critical")]


def test_changes_that_do_not_republish(state: State, fake_clock: FakeClock) -> None:
    snapshot = _snapshot(_endpoint())
    run(state, snapshot, [_batch(_euvd(fake_clock, score=7.1))], fake_clock)
    fake_clock.advance(timedelta(minutes=15))
    within_band = _euvd(fake_clock, score=8.5)
    assert run(state, snapshot, [_batch(within_band)], fake_clock).decisions == ()
    fake_clock.advance(timedelta(minutes=15))
    new_source = SourceRecord(
        source="eu_kev_like",
        record_id="EUVD-2026-1",
        observed_at=fake_clock.now(),
        aliases=(CVE,),
        updated_at=fake_clock.now(),
    )
    assert run(state, snapshot, [_batch(new_source)], fake_clock).decisions == ()
    fake_clock.advance(timedelta(minutes=15))
    edited = _euvd(fake_clock, score=8.5, description="A flaw, edited in php")
    assert run(state, snapshot, [_batch(edited)], fake_clock).decisions == ()
    fake_clock.advance(timedelta(minutes=15))
    dropped = _euvd(fake_clock, score=3.0)
    assert run(state, snapshot, [_batch(dropped)], fake_clock).decisions == ()


def test_late_product_data_is_published_as_new(
    state: State, fake_clock: FakeClock
) -> None:
    snapshot = _snapshot(_endpoint())
    # FR-26: stored, but nothing says php yet
    first = run(state, snapshot, [_batch(_euvd(fake_clock, product=None))], fake_clock)
    assert first.decisions == ()
    fake_clock.advance(timedelta(minutes=15))
    late = run(state, snapshot, [_batch(_euvd(fake_clock, product="php"))], fake_clock)
    assert _kinds(late) == [("team-php", "new")]


def test_late_description_data_is_published_as_new(
    state: State, fake_clock: FakeClock
) -> None:
    snapshot = _snapshot(_endpoint())
    run(
        state,
        snapshot,
        [_batch(_euvd(fake_clock, product=None, description="unspecified"))],
        fake_clock,
    )
    fake_clock.advance(timedelta(minutes=15))
    result = run(
        state,
        snapshot,
        [_batch(_euvd(fake_clock, product=None, description="flaw in php"))],
        fake_clock,
    )
    assert _kinds(result) == [("team-php", "new")]


def test_late_kev_status_on_an_unmatched_endpoint_is_new(
    state: State, fake_clock: FakeClock
) -> None:
    only_kev = Endpoint(
        id="kev-team",
        name="kev",
        products=(ProductEntry(name="php"),),
        rules=replace(_endpoint().rules, kev_only=True),
    )
    snapshot = _snapshot(only_kev)
    assert run(state, snapshot, [_batch(_euvd(fake_clock))], fake_clock).decisions == ()
    fake_clock.advance(timedelta(minutes=15))
    result = run(state, snapshot, [_batch(_kev(fake_clock))], fake_clock)
    assert _kinds(result) == [("kev-team", "new")]


def test_a_new_endpoint_is_backfilled_with_the_last_seven_days(
    state: State, fake_clock: FakeClock
) -> None:
    recent = _euvd(
        fake_clock, record_id="EUVD-2026-1", cve=CVE, updated_ago=timedelta(days=6)
    )
    old = _euvd(
        fake_clock,
        record_id="EUVD-2026-2",
        cve="CVE-2026-1002",
        updated_ago=timedelta(days=8),
    )
    team_a = _endpoint("team-a")
    first = run(state, _snapshot(team_a), [_batch(recent, old)], fake_clock)
    assert _kinds(first) == [("team-a", "new")]

    fake_clock.advance(timedelta(minutes=15))
    team_b = _endpoint("team-b")
    second = run(state, _snapshot(team_a, team_b), [_batch()], fake_clock)
    # B gets what it matches within 7 days, nothing older; A gets nothing
    assert _kinds(second) == [("team-b", "new")]
    assert second.decisions[0].primary_id == CVE

    fake_clock.advance(timedelta(minutes=15))
    third = run(state, _snapshot(team_a, team_b), [_batch()], fake_clock)
    assert third.decisions == ()


def test_the_first_run_publishes_the_last_seven_days_only(
    state: State, fake_clock: FakeClock
) -> None:
    ten_days = _euvd(fake_clock, updated_ago=timedelta(days=10))
    result = run(state, _snapshot(_endpoint()), [_batch(ten_days)], fake_clock)
    assert result.decisions == ()


def test_an_endpoint_without_a_match_is_backfilled_again_next_run(
    state: State, fake_clock: FakeClock
) -> None:
    # no publication row yet: still a backfill, so late data is not missed
    endpoint = _endpoint("team-go", product="go")
    run(state, _snapshot(endpoint), [_batch(_euvd(fake_clock))], fake_clock)
    fake_clock.advance(timedelta(minutes=15))
    result = run(
        state,
        _snapshot(endpoint),
        [_batch(_euvd(fake_clock, product="go"))],
        fake_clock,
    )
    assert _kinds(result) == [("team-go", "new")]


def test_a_disabled_endpoint_decides_nothing(
    state: State, fake_clock: FakeClock
) -> None:
    snapshot = _snapshot(_endpoint(enabled=False))
    result = run(state, snapshot, [_batch(_euvd(fake_clock))], fake_clock)
    assert result.decisions == ()
    assert _count(state, schema.publication) == 0


def test_an_old_vulnerability_touched_today_is_not_published(
    state: State, fake_clock: FakeClock
) -> None:
    snapshot = _snapshot(_endpoint())
    # the endpoint has history: it is not a backfill any more
    run(state, snapshot, [_batch(_euvd(fake_clock))], fake_clock)
    fake_clock.advance(timedelta(minutes=15))
    old = _euvd(
        fake_clock,
        record_id="EUVD-2015-9",
        cve="CVE-2015-1234",
        updated_ago=timedelta(days=20),
    )
    run(state, snapshot, [_batch(old)], fake_clock)
    fake_clock.advance(timedelta(minutes=15))
    # a cosmetic change (renamed entry) of the old record
    renamed = replace(old, title=Sourced("renamed", "euvd", fake_clock.now()))
    result = run(state, snapshot, [_batch(renamed)], fake_clock)
    assert result.touched == 1
    assert result.decisions == ()


def test_cursors_are_stored_only_for_batches_that_have_one(
    state: State, fake_clock: FakeClock
) -> None:
    snapshot = _snapshot(_endpoint())
    kev_batch = SourceBatch("cisa_kev", FetchResult(records=(), next_cursor=None))
    run(
        state,
        snapshot,
        [_batch(_euvd(fake_clock), cursor="cursor-1"), kev_batch],
        fake_clock,
    )
    assert load_cursors(state) == {"euvd": "cursor-1"}
    run(state, snapshot, [_batch(cursor=None)], fake_clock)
    assert load_cursors(state) == {"euvd": "cursor-1"}
    run(state, snapshot, [_batch(cursor="cursor-2")], fake_clock)
    assert load_cursors(state) == {"euvd": "cursor-2"}


def test_a_failure_rolls_everything_back(
    state: State, fake_clock: FakeClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    snapshot = _snapshot(_endpoint("a"), _endpoint("b"))
    original = Repository.add_publication
    calls: list[str] = []

    def flaky(
        self: Repository,
        vulnerability_id: int,
        decision: PublicationDecision,
        published_at: datetime,
    ) -> None:
        calls.append(decision.endpoint_id)
        if len(calls) == 2:
            raise RuntimeError("disk full")
        original(self, vulnerability_id, decision, published_at)

    monkeypatch.setattr(Repository, "add_publication", flaky)
    with pytest.raises(RuntimeError, match="disk full"):
        run(
            state,
            snapshot,
            [_batch(_euvd(fake_clock), cursor="cursor-1")],
            fake_clock,
        )
    assert calls == ["a", "b"]
    assert _count(state, schema.publication) == 0
    assert _count(state, schema.source_record) == 0
    assert _count(state, schema.source_cursor) == 0
    assert _count(state, schema.vulnerability) == 0


ADVISORY = "CERTFR-2026-ALE-012"


def _advisory_revision(clock: FakeClock, revision: int) -> SourceRecord:
    # Advisory IDs never merge: two revisions without a CVE or EUVD alias are
    # two vulnerabilities with the same primary ID, hence the same GUID.
    return replace(
        _euvd(clock, cve=None),
        source="certfr_alerte",
        record_id=f"{ADVISORY}-R{revision}",
        aliases=(ADVISORY,),
    )


def test_a_guid_collision_in_one_run_is_skipped_and_logged(
    state: State, fake_clock: FakeClock
) -> None:
    batch = _batch(_advisory_revision(fake_clock, 1), _advisory_revision(fake_clock, 2))
    with capture_logs() as logs:
        result = run(state, _snapshot(_endpoint()), [batch], fake_clock)
    assert _kinds(result) == [("team-php", "new")]
    assert _count(state, schema.vulnerability) == 2
    assert _count(state, schema.publication) == 1
    skipped = [entry for entry in logs if entry["event"] == "publish.skipped"]
    assert skipped == [
        {
            "event": "publish.skipped",
            "log_level": "warning",
            "reason": "guid_collision",
            "endpoint_id": "team-php",
            "vuln_id": ADVISORY,
            "kind": "new",
        }
    ]


def test_a_guid_collision_with_an_earlier_run_is_skipped(
    state: State, fake_clock: FakeClock
) -> None:
    snapshot = _snapshot(_endpoint())
    run(state, snapshot, [_batch(_advisory_revision(fake_clock, 1))], fake_clock)
    fake_clock.advance(timedelta(minutes=15))
    again = run(
        state, snapshot, [_batch(_advisory_revision(fake_clock, 2))], fake_clock
    )
    assert again.decisions == ()
    assert again.changed == 1
    assert _count(state, schema.publication) == 1


def test_decisions_and_completion_are_logged(
    state: State, fake_clock: FakeClock
) -> None:
    with capture_logs() as logs:
        run(state, _snapshot(_endpoint()), [_batch(_euvd(fake_clock))], fake_clock)
    events = {entry["event"]: entry for entry in logs}
    assert events["publish.decided"]["endpoint_id"] == "team-php"
    assert events["publish.decided"]["vuln_id"] == CVE
    assert events["publish.decided"]["kind"] == "new"
    completed = events["pipeline.completed"]
    assert completed["count"] == 1
    assert completed["records"] == 1
    assert "duration_ms" in completed


def _guids(xml: bytes) -> list[str]:
    root = ET.fromstring(xml)  # noqa: S314 - our own output
    return [item.findtext("guid") or "" for item in root.findall("channel/item")]


def test_every_enabled_endpoint_gets_a_stored_feed(
    state: State, fake_clock: FakeClock
) -> None:
    snapshot = _snapshot(
        _endpoint("team-php"),
        _endpoint("team-go", "go"),
        _endpoint("off", enabled=False),
    )
    result = run(state, snapshot, [_batch(_euvd(fake_clock))], fake_clock)
    assert list(result.feeds) == ["team-php", "team-go"]
    with state.transaction() as repo:
        stored = repo.feeds()
    assert set(stored) == {"team-php", "team-go"}
    php = result.feeds["team-php"]
    assert php.item_count == 1
    assert _guids(php.xml) == [f"cvestream:team-php:{CVE}:new"]
    assert stored["team-php"].xml.encode("utf-8") == php.xml
    assert stored["team-php"].etag == php.etag
    assert php.rendered_at == fake_clock.now()
    assert php.changed_at == fake_clock.now()
    # no publication: a valid empty channel
    assert result.feeds["team-go"].item_count == 0
    assert _guids(result.feeds["team-go"].xml) == []


def test_an_unchanged_rerun_keeps_the_etag_and_moves_the_render_time(
    state: State, fake_clock: FakeClock
) -> None:
    snapshot = _snapshot(_endpoint())
    first = run(state, snapshot, [_batch(_euvd(fake_clock))], fake_clock).feeds
    fake_clock.advance(timedelta(minutes=15))
    again = run(state, snapshot, [_batch(_euvd(fake_clock))], fake_clock).feeds
    assert again["team-php"].etag == first["team-php"].etag
    assert again["team-php"].xml == first["team-php"].xml
    assert again["team-php"].changed_at == first["team-php"].changed_at
    assert again["team-php"].rendered_at == fake_clock.now()
    with state.transaction() as repo:
        stored = repo.feeds()["team-php"]
    assert stored.changed_at == first["team-php"].changed_at
    assert stored.rendered_at == fake_clock.now()


def test_a_new_decision_changes_the_etag_and_the_change_time(
    state: State, fake_clock: FakeClock
) -> None:
    snapshot = _snapshot(_endpoint())
    first = run(state, snapshot, [_batch(_euvd(fake_clock))], fake_clock).feeds
    fake_clock.advance(timedelta(minutes=15))
    second = run(state, snapshot, [_batch(_kev(fake_clock))], fake_clock).feeds
    assert second["team-php"].etag != first["team-php"].etag
    assert second["team-php"].changed_at == fake_clock.now()
    assert second["team-php"].item_count == 2
    # newest first
    assert _guids(second["team-php"].xml) == [
        f"cvestream:team-php:{CVE}:kev",
        f"cvestream:team-php:{CVE}:new",
    ]


def test_the_body_follows_the_current_data_and_the_title_the_decision(
    state: State, fake_clock: FakeClock
) -> None:
    snapshot = _snapshot(_endpoint())
    run(state, snapshot, [_batch(_euvd(fake_clock, score=7.1))], fake_clock)
    fake_clock.advance(timedelta(minutes=15))
    feed = run(
        state, snapshot, [_batch(_euvd(fake_clock, score=7.4))], fake_clock
    ).feeds["team-php"]
    item = ET.fromstring(feed.xml).find("channel/item")  # noqa: S314
    assert item is not None
    assert item.findtext("title") == f"{CVE} in php: severity high"
    assert "CVSS 7.4" in (item.findtext("description") or "")


def test_a_publication_older_than_fourteen_days_leaves_the_feed(
    state: State, fake_clock: FakeClock
) -> None:
    snapshot = _snapshot(_endpoint())
    first = run(state, snapshot, [_batch(_euvd(fake_clock))], fake_clock).feeds
    assert first["team-php"].item_count == 1
    fake_clock.advance(timedelta(days=14) - timedelta(minutes=1))
    inside = run(state, snapshot, [_batch()], fake_clock).feeds
    assert inside["team-php"].item_count == 1
    assert inside["team-php"].etag == first["team-php"].etag
    fake_clock.advance(timedelta(minutes=2))
    outside = run(state, snapshot, [_batch()], fake_clock).feeds
    assert outside["team-php"].item_count == 0
    assert outside["team-php"].etag != first["team-php"].etag
    assert outside["team-php"].changed_at == fake_clock.now()


def test_a_renamed_endpoint_changes_its_channel_title(
    state: State, fake_clock: FakeClock
) -> None:
    first = run(state, _snapshot(_endpoint()), [_batch()], fake_clock).feeds
    renamed = replace(_endpoint(), name="PHP team")
    fake_clock.advance(timedelta(minutes=1))
    second = run(state, _snapshot(renamed), [_batch()], fake_clock).feeds
    assert second["team-php"].etag != first["team-php"].etag
    assert b"cvestream: PHP team" in second["team-php"].xml


def test_a_failure_after_rendering_rolls_the_feeds_back(
    state: State, fake_clock: FakeClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    snapshot = _snapshot(_endpoint("a"), _endpoint("b"))
    original = Repository.save_feed
    saved: list[str] = []

    def flaky(self: Repository, feed: StoredFeed) -> None:
        saved.append(feed.endpoint_id)
        if len(saved) == 2:
            raise RuntimeError("disk full")
        original(self, feed)

    monkeypatch.setattr(Repository, "save_feed", flaky)
    with pytest.raises(RuntimeError, match="disk full"):
        run(state, snapshot, [_batch(_euvd(fake_clock), cursor="c")], fake_clock)
    assert saved == ["a", "b"]
    assert _count(state, schema.feed_cache) == 0
    assert _count(state, schema.publication) == 0
    assert _count(state, schema.source_cursor) == 0


def test_load_feeds_returns_what_run_stored(
    state: State, fake_clock: FakeClock
) -> None:
    assert load_feeds(state) == {}
    result = run(state, _snapshot(_endpoint()), [_batch(_euvd(fake_clock))], fake_clock)
    assert load_feeds(state) == dict(result.feeds)


def test_the_feeds_of_a_disabled_endpoint_are_not_rendered_again(
    state: State, fake_clock: FakeClock
) -> None:
    run(state, _snapshot(_endpoint()), [_batch(_euvd(fake_clock))], fake_clock)
    fake_clock.advance(timedelta(minutes=15))
    result = run(state, _snapshot(_endpoint(enabled=False)), [_batch()], fake_clock)
    assert result.feeds == {}
    # the stored row stays (harmless), with its last render time
    assert load_feeds(state)["team-php"].rendered_at == START


def test_an_unscored_vulnerability_held_back_by_require_score_is_new_once_scored(
    state: State, fake_clock: FakeClock
) -> None:
    rules = Rules(require_score=True, min_cvss=7.0)
    snapshot = _snapshot(replace(_endpoint(), rules=rules))
    # Run 1: a first, scored vulnerability is published as new
    first = run(
        state,
        snapshot,
        [
            _batch(
                _euvd(
                    fake_clock, record_id="EUVD-2026-0", cve="CVE-2026-1000", score=9.8
                )
            )
        ],
        fake_clock,
    )
    assert _kinds(first) == [("team-php", "new")]
    # Run 2: an unscored vulnerability is held back
    fake_clock.advance(timedelta(minutes=15))
    result = run(
        state,
        snapshot,
        [
            _batch(
                _euvd(
                    fake_clock, record_id="EUVD-2026-1", cve="CVE-2026-1001", score=None
                )
            )
        ],
        fake_clock,
    )
    assert result.decisions == ()
    # Run 3: the same record with a score is published as new
    fake_clock.advance(timedelta(minutes=15))
    result = run(
        state,
        snapshot,
        [
            _batch(
                _euvd(
                    fake_clock, record_id="EUVD-2026-1", cve="CVE-2026-1001", score=9.8
                )
            )
        ],
        fake_clock,
    )
    assert _kinds(result) == [("team-php", "new")]
    assert result.decisions[0].primary_id == "CVE-2026-1001"
    with state.transaction() as repo:
        row = repo._connection.execute(
            select(schema.publication).where(schema.publication.c.primary_id == CVE)
        ).one()
    assert row.guid == f"cvestream:team-php:{CVE}:new"
    # Run 4: a changed record is re-matched; decide() returns nothing
    fake_clock.advance(timedelta(minutes=15))
    result = run(
        state,
        snapshot,
        [
            _batch(
                _euvd(
                    fake_clock,
                    record_id="EUVD-2026-1",
                    cve="CVE-2026-1001",
                    score=9.8,
                    description="A flaw, updated",
                )
            )
        ],
        fake_clock,
    )
    assert result.changed == 1
    assert result.decisions == ()


def test_an_unscored_vulnerability_held_back_by_require_score_is_new_once_exploited(
    state: State, fake_clock: FakeClock
) -> None:
    rules = Rules(require_score=True, min_cvss=7.0)
    snapshot = _snapshot(replace(_endpoint(), rules=rules))
    # Run 1: a first, scored vulnerability is published as new
    first = run(
        state,
        snapshot,
        [
            _batch(
                _euvd(
                    fake_clock, record_id="EUVD-2026-0", cve="CVE-2026-1000", score=9.8
                )
            )
        ],
        fake_clock,
    )
    assert _kinds(first) == [("team-php", "new")]
    # Run 2: an unscored vulnerability is held back
    fake_clock.advance(timedelta(minutes=15))
    result = run(
        state,
        snapshot,
        [
            _batch(
                _euvd(
                    fake_clock, record_id="EUVD-2026-1", cve="CVE-2026-1001", score=None
                )
            )
        ],
        fake_clock,
    )
    assert result.decisions == ()
    # Run 3: the same vulnerability becomes KEV and is published as new
    fake_clock.advance(timedelta(minutes=15))
    result = run(
        state,
        snapshot,
        [_batch(_kev(fake_clock, cve="CVE-2026-1001"))],
        fake_clock,
    )
    assert _kinds(result) == [("team-php", "new")]
    assert result.decisions[0].primary_id == "CVE-2026-1001"
    with state.transaction() as repo:
        row = repo._connection.execute(
            select(schema.publication).where(schema.publication.c.primary_id == CVE)
        ).one()
    assert row.guid == f"cvestream:team-php:{CVE}:new"
