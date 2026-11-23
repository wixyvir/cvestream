from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import insert, select
from sqlalchemy.exc import IntegrityError

from cvestream.domain.matcher import PassThrough
from cvestream.domain.model import Sourced, SourceRecord
from cvestream.domain.publication import PublicationDecision
from cvestream.domain.severity import Band
from cvestream.state import schema
from cvestream.state.codec import content_hash
from cvestream.state.db import State
from cvestream.state.repository import (
    Repository,
    StoredFeed,
    StoredRecord,
    _upsert,
)

NOW = datetime(2026, 10, 26, 12, 0, tzinfo=UTC)


def _record(
    source: str = "euvd", record_id: str = "EUVD-2026-1", title: str = "t"
) -> SourceRecord:
    return SourceRecord(
        source=source,
        record_id=record_id,
        observed_at=NOW,
        aliases=("CVE-2026-1",),
        title=Sourced(title, source, NOW),
    )


def _decision(
    guid: str = "cvestream:e:CVE-2026-1:new", endpoint_id: str = "e"
) -> PublicationDecision:
    return PublicationDecision(
        endpoint_id=endpoint_id,
        primary_id="CVE-2026-1",
        kind="new",
        guid=guid,
        band=Band.HIGH,
        is_known_exploited=False,
        products=("php", "nginx"),
        labels=frozenset({PassThrough.VERSIONS_UNKNOWN, PassThrough.SCORE_UNKNOWN}),
    )


def _vulnerability(repo: Repository, activity: datetime = NOW) -> int:
    vulnerability_id = repo.create_vulnerability(activity)
    repo.insert_source_record(
        vulnerability_id, _record(record_id=f"R{vulnerability_id}"), activity, NOW
    )
    repo.refresh_vulnerability(vulnerability_id, "CVE-2026-1", NOW, changed=True)
    return vulnerability_id


def test_upsert_inserts_then_updates(state: State) -> None:
    with state.transaction() as repo:
        repo.set_cursor("euvd", "c1", NOW)
        repo.set_cursor("euvd", "c2", NOW + timedelta(hours=1))
        repo.set_cursor("cisa_kev", None, NOW)
        assert repo.cursors() == {"euvd": "c2", "cisa_kev": None}
    with state.transaction() as repo:
        rows = repo._connection.execute(select(schema.source_cursor)).all()
    assert len(rows) == 2
    euvd = next(r for r in rows if r.source == "euvd")
    assert euvd.updated_at == NOW + timedelta(hours=1)


def test_upsert_do_nothing_keeps_the_existing_row(state: State) -> None:
    with state.transaction() as repo:
        first = repo.create_vulnerability(NOW)
        second = repo.create_vulnerability(NOW)
        repo.add_aliases(first, ["CVE-2026-1"])
        repo.add_aliases(second, ["CVE-2026-1", "CVE-2026-2"])
        assert repo.vulnerability_ids_for_aliases(["CVE-2026-1"]) == {first}
        assert repo.vulnerability_ids_for_aliases(["CVE-2026-2"]) == {second}


def test_upsert_refuses_unknown_dialects(
    state: State, monkeypatch: pytest.MonkeyPatch
) -> None:
    with state.transaction() as repo:
        connection = repo._connection
        monkeypatch.setattr(connection.dialect, "name", "oracle")
        with pytest.raises(schema.StateError, match="oracle"):
            _upsert(connection, schema.alias, {}, keys=["alias"])
        monkeypatch.undo()


def test_vulnerability_ids_for_aliases(state: State) -> None:
    with state.transaction() as repo:
        one = repo.create_vulnerability(NOW)
        two = repo.create_vulnerability(NOW)
        repo.add_aliases(one, ["CVE-2026-1", "EUVD-2026-1"])
        repo.add_aliases(two, ["CVE-2026-2"])
        assert repo.vulnerability_ids_for_aliases(["CVE-2026-1", "EUVD-2026-1"]) == {
            one
        }
        assert repo.vulnerability_ids_for_aliases(["CVE-2026-1", "CVE-2026-2"]) == {
            one,
            two,
        }
        assert repo.vulnerability_ids_for_aliases(["CVE-2000-1"]) == set()
        assert repo.vulnerability_ids_for_aliases([]) == set()


def test_ids_lists_are_chunked(state: State) -> None:
    with state.transaction() as repo:
        ids = [repo.create_vulnerability(NOW) for _ in range(3)]
        aliases = [f"CVE-2026-{n}" for n in range(1, 1201)]
        repo.add_aliases(ids[0], aliases)
        assert repo.vulnerability_ids_for_aliases(aliases) == {ids[0]}


def test_source_record_insert_find_update_and_mark_seen(state: State) -> None:
    record = _record()
    with state.transaction() as repo:
        vulnerability_id = repo.create_vulnerability(NOW)
        assert repo.find_source_record("euvd", "EUVD-2026-1") is None
        repo.insert_source_record(vulnerability_id, record, NOW, NOW)
        stored = repo.find_source_record("euvd", "EUVD-2026-1")
        assert stored == StoredRecord(
            id=stored.id if stored else 0,
            vulnerability_id=vulnerability_id,
            content_hash=content_hash(record),
        )
        assert stored is not None
        later = NOW + timedelta(hours=1)
        repo.mark_seen(stored, later)
        changed = _record(title="other")
        repo.update_source_record(
            stored, vulnerability_id, changed, NOW + timedelta(days=1), later
        )
        after = repo.find_source_record("euvd", "EUVD-2026-1")
        assert after is not None
        assert after.content_hash == content_hash(changed)
        assert repo.load_records([vulnerability_id]) == {vulnerability_id: [changed]}
        row = repo._connection.execute(select(schema.source_record)).one()
        assert row.first_seen_at == NOW
        assert row.changed_at == later
        assert row.last_seen_at == later


def test_mark_seen_keeps_the_stored_record(state: State) -> None:
    record = _record()
    with state.transaction() as repo:
        vulnerability_id = repo.create_vulnerability(NOW)
        repo.insert_source_record(vulnerability_id, record, NOW, NOW)
        stored = repo.find_source_record("euvd", "EUVD-2026-1")
        assert stored is not None
        repo.mark_seen(stored, NOW + timedelta(hours=3))
        row = repo._connection.execute(select(schema.source_record)).one()
        assert row.changed_at == NOW
        assert row.last_seen_at == NOW + timedelta(hours=3)
        assert row.record_json["observed_at"] == "2026-10-26T12:00:00.000000Z"


def test_source_record_is_unique_per_source_and_record_id(state: State) -> None:
    with pytest.raises(IntegrityError), state.transaction() as repo:
        vulnerability_id = repo.create_vulnerability(NOW)
        repo.insert_source_record(vulnerability_id, _record(), NOW, NOW)
        repo.insert_source_record(vulnerability_id, _record(), NOW, NOW)


def test_load_records_are_ordered_by_source_then_record_id(state: State) -> None:
    with state.transaction() as repo:
        vulnerability_id = repo.create_vulnerability(NOW)
        for source, record_id in [("euvd", "B"), ("cisa_kev", "Z"), ("euvd", "A")]:
            repo.insert_source_record(
                vulnerability_id, _record(source, record_id), NOW, NOW
            )
        loaded = repo.load_records([vulnerability_id])[vulnerability_id]
        assert [(r.source, r.record_id) for r in loaded] == [
            ("cisa_kev", "Z"),
            ("euvd", "A"),
            ("euvd", "B"),
        ]
        assert repo.load_records([]) == {}


def test_refresh_vulnerability_takes_the_latest_activity(state: State) -> None:
    with state.transaction() as repo:
        vulnerability_id = repo.create_vulnerability(NOW)
        repo.insert_source_record(
            vulnerability_id, _record(record_id="A"), NOW - timedelta(days=3), NOW
        )
        repo.insert_source_record(
            vulnerability_id, _record(record_id="B"), NOW - timedelta(days=1), NOW
        )
        later = NOW + timedelta(minutes=5)
        repo.refresh_vulnerability(vulnerability_id, "CVE-2026-9", later, changed=True)
        row = repo._connection.execute(select(schema.vulnerability)).one()
        assert row.primary_id == "CVE-2026-9"
        assert row.activity_at == NOW - timedelta(days=1)
        assert row.changed_at == later
        repo.refresh_vulnerability(
            vulnerability_id, "CVE-2026-9", later + timedelta(hours=1), changed=False
        )
        row = repo._connection.execute(select(schema.vulnerability)).one()
        assert row.changed_at == later


def test_active_since_includes_the_boundary(state: State) -> None:
    since = NOW - timedelta(days=7)
    with state.transaction() as repo:
        exactly = _vulnerability(repo, since)
        before = _vulnerability(repo, since - timedelta(microseconds=1))
        after = _vulnerability(repo, since + timedelta(hours=1))
        assert repo.active_since(since) == [exactly, after]
        assert repo.filter_active_since([exactly, before, after], since) == [
            exactly,
            after,
        ]
        assert repo.filter_active_since([before], since) == []
        assert repo.filter_active_since([], since) == []


def test_merge_repoints_everything_and_deletes_the_absorbed(state: State) -> None:
    with state.transaction() as repo:
        survivor = repo.create_vulnerability(NOW)
        absorbed = repo.create_vulnerability(NOW)
        repo.add_aliases(survivor, ["CVE-2026-1"])
        repo.add_aliases(absorbed, ["EUVD-2026-1"])
        repo.insert_source_record(survivor, _record(record_id="A"), NOW, NOW)
        repo.insert_source_record(absorbed, _record(record_id="B"), NOW, NOW)
        repo.add_publication(absorbed, _decision("g-absorbed"), NOW)
        repo.add_publication(survivor, _decision("g-survivor"), NOW)

        repo.merge_vulnerabilities(survivor, [absorbed, survivor])

        assert repo.vulnerability_ids_for_aliases(["CVE-2026-1", "EUVD-2026-1"]) == {
            survivor
        }
        records = repo.load_records([survivor, absorbed])
        assert set(records) == {survivor}
        assert len(records[survivor]) == 2
        history = repo.publication_history(["e"], [survivor, absorbed])
        assert set(history) == {("e", survivor)}
        assert {h.guid for h in history[("e", survivor)]} == {
            "g-absorbed",
            "g-survivor",
        }
        remaining = repo._connection.execute(select(schema.vulnerability.c.id)).all()
        assert [row[0] for row in remaining] == [survivor]


def test_publication_round_trip_and_history_grouping(state: State) -> None:
    with state.transaction() as repo:
        one = _vulnerability(repo)
        two = _vulnerability(repo)
        repo.add_publication(one, _decision("g1", "team-a"), NOW)
        repo.add_publication(one, _decision("g2", "team-b"), NOW)
        repo.add_publication(two, _decision("g3", "team-a"), NOW)
        history = repo.publication_history(["team-a", "team-b"], [one, two])
        assert {key: [h.guid for h in rows] for key, rows in history.items()} == {
            ("team-a", one): ["g1"],
            ("team-b", one): ["g2"],
            ("team-a", two): ["g3"],
        }
        past = history[("team-a", one)][0]
        assert past.kind == "new"
        assert past.band is Band.HIGH
        assert past.is_known_exploited is False
        assert repo.publication_history([], [one]) == {}
        assert repo.publication_history(["team-a"], [two]) == {
            ("team-a", two): history[("team-a", two)]
        }
        row = repo._connection.execute(
            select(schema.publication).where(schema.publication.c.guid == "g1")
        ).one()
        assert row.products_json == ["php", "nginx"]
        assert row.labels_json == ["score_unknown", "versions_unknown"]
        assert row.published_at == NOW
        assert row.primary_id == "CVE-2026-1"
        assert row.band == "high"


def test_endpoints_with_publications(state: State) -> None:
    with state.transaction() as repo:
        one = _vulnerability(repo)
        repo.add_publication(one, _decision("g1", "team-a"), NOW)
        assert repo.endpoints_with_publications(["team-a", "team-b"]) == {"team-a"}
        assert repo.endpoints_with_publications([]) == set()


def test_the_same_guid_cannot_be_published_twice(state: State) -> None:
    with pytest.raises(IntegrityError), state.transaction() as repo:
        one = _vulnerability(repo)
        repo.add_publication(one, _decision("same"), NOW)
        repo.add_publication(one, _decision("same"), NOW)


def test_publication_guid_exists_across_endpoints_and_vulnerabilities(
    state: State,
) -> None:
    with state.transaction() as repo:
        one = _vulnerability(repo)
        assert repo.publication_guid_exists("g1") is False
        repo.add_publication(one, _decision("g1", "team-a"), NOW)
        assert repo.publication_guid_exists("g1") is True
        assert repo.publication_guid_exists("g2") is False


def test_cursors_store_none_as_null(state: State) -> None:
    with state.transaction() as repo:
        repo.set_cursor("cisa_kev", None, NOW)
        assert repo.cursors() == {"cisa_kev": None}
        row = repo._connection.execute(select(schema.source_cursor.c.cursor)).one()
        assert row.cursor is None


def test_publications_since_groups_by_endpoint_newest_first(state: State) -> None:
    with state.transaction() as repo:
        first = _vulnerability(repo)
        second = _vulnerability(repo)
        repo.add_publication(
            first, _decision("cvestream:e:CVE-2026-1:new"), NOW - timedelta(days=2)
        )
        repo.add_publication(
            second, _decision("cvestream:e:CVE-2026-2:new"), NOW - timedelta(days=1)
        )
        repo.add_publication(
            second,
            _decision("cvestream:other:CVE-2026-2:new", endpoint_id="other"),
            NOW,
        )
        found = repo.publications_since(
            ["e", "other", "absent"], NOW - timedelta(days=3)
        )
    assert set(found) == {"e", "other"}
    assert [p.guid for p in found["e"]] == [
        "cvestream:e:CVE-2026-2:new",
        "cvestream:e:CVE-2026-1:new",
    ]
    assert [p.vulnerability_id for p in found["e"]] == [second, first]


def test_publications_since_round_trips_the_decision_columns(state: State) -> None:
    with state.transaction() as repo:
        vulnerability_id = _vulnerability(repo)
        repo.add_publication(vulnerability_id, _decision(), NOW)
        (stored,) = repo.publications_since(["e"], NOW)["e"]
    assert stored.band is Band.HIGH
    assert stored.kind == "new"
    assert stored.primary_id == "CVE-2026-1"
    assert stored.is_known_exploited is False
    assert stored.products == ("php", "nginx")
    assert stored.labels == {PassThrough.VERSIONS_UNKNOWN, PassThrough.SCORE_UNKNOWN}
    assert stored.published_at == NOW


def test_publications_since_includes_the_boundary(state: State) -> None:
    since = NOW - timedelta(days=14)
    with state.transaction() as repo:
        vulnerability_id = _vulnerability(repo)
        repo.add_publication(vulnerability_id, _decision("g-at"), since)
        repo.add_publication(
            vulnerability_id, _decision("g-before"), since - timedelta(microseconds=1)
        )
        found = repo.publications_since(["e"], since)
    assert [p.guid for p in found["e"]] == ["g-at"]


def test_publications_since_breaks_ties_by_newest_row(state: State) -> None:
    with state.transaction() as repo:
        vulnerability_id = _vulnerability(repo)
        repo.add_publication(vulnerability_id, _decision("g-1"), NOW)
        repo.add_publication(vulnerability_id, _decision("g-2"), NOW)
        found = repo.publications_since(["e"], NOW)
    assert [p.guid for p in found["e"]] == ["g-2", "g-1"]


def test_publications_since_with_no_endpoint(state: State) -> None:
    with state.transaction() as repo:
        assert repo.publications_since([], NOW) == {}


def _feed(endpoint_id: str = "e", etag: str = '"a"', xml: str = "<rss/>") -> StoredFeed:
    return StoredFeed(endpoint_id, xml, etag, 2, NOW, NOW - timedelta(hours=1))


def test_save_feed_inserts_then_updates(state: State) -> None:
    with state.transaction() as repo:
        assert repo.feeds() == {}
        repo.save_feed(_feed())
        repo.save_feed(_feed("other"))
        updated = StoredFeed(
            "e", "<rss>2</rss>", '"b"', 5, NOW + timedelta(hours=1), NOW
        )
        repo.save_feed(updated)
        feeds = repo.feeds()
    assert set(feeds) == {"e", "other"}
    assert feeds["e"] == updated
    assert feeds["other"] == _feed("other")


def test_feeds_are_committed_with_the_transaction(state: State) -> None:
    with state.transaction() as repo:
        repo.save_feed(_feed())
    with state.transaction() as repo:
        assert repo.feeds() == {"e": _feed()}


def test_a_failed_transaction_rolls_the_feed_back(state: State) -> None:
    with pytest.raises(RuntimeError), state.transaction() as repo:
        repo.save_feed(_feed())
        raise RuntimeError("boom")
    with state.transaction() as repo:
        assert repo.feeds() == {}


def test_publications_since_round_trips_is_fresh(state: State) -> None:
    with state.transaction() as repo:
        one = _vulnerability(repo)
        two = _vulnerability(repo)
        three = _vulnerability(repo)
        repo.add_publication(one, replace(_decision("g-true"), is_fresh=True), NOW)
        repo.add_publication(two, replace(_decision("g-false"), is_fresh=False), NOW)
        # a row from before the column existed: NULL
        repo._connection.execute(
            insert(schema.publication).values(
                endpoint_id="e",
                vulnerability_id=three,
                kind="new",
                guid="g-null",
                primary_id="CVE-2026-1",
                band="high",
                is_known_exploited=False,
                products_json=[],
                labels_json=[],
                published_at=NOW,
            )
        )
        stored = {
            p.guid: p for ps in repo.publications_since(["e"], NOW).values() for p in ps
        }
    assert stored["g-true"].is_fresh is True
    assert stored["g-false"].is_fresh is False
    assert stored["g-null"].is_fresh is None


def test_publications_since_round_trips_the_group_guid(state: State) -> None:
    with state.transaction() as repo:
        one = _vulnerability(repo)
        two = _vulnerability(repo)
        three = _vulnerability(repo)
        group_guid = "cvestream:e:group-new:0123456789abcdef"
        repo.add_publication(
            one,
            replace(_decision("g-grouped"), endpoint_id="e"),
            NOW,
            group_guid=group_guid,
        )
        repo.add_publication(two, _decision("g-single"), NOW)
        # a row from before the column existed: NULL
        repo._connection.execute(
            insert(schema.publication).values(
                endpoint_id="e",
                vulnerability_id=three,
                kind="new",
                guid="g-null",
                primary_id="CVE-2026-1",
                band="high",
                is_known_exploited=False,
                is_fresh=True,
                products_json=[],
                labels_json=[],
                published_at=NOW,
            )
        )
        stored = {
            p.guid: p for ps in repo.publications_since(["e"], NOW).values() for p in ps
        }
    assert stored["g-grouped"].group_guid == group_guid
    assert stored["g-single"].group_guid is None
    assert stored["g-null"].group_guid is None
