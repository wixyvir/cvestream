from dataclasses import replace
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from cvestream.domain.model import (
    AffectedProduct,
    CvssScore,
    KevCatalog,
    KevEntry,
    Sourced,
    SourceRecord,
)
from cvestream.domain.publication import PublicationDecision
from cvestream.domain.severity import Band
from cvestream.pipeline.correlate import (
    CorrelationResult,
    activity_time,
    build_vulnerability,
    correlate,
    merge_keys,
    normalize_all,
)
from cvestream.pipeline.normalize import normalize
from cvestream.state import schema
from cvestream.state.db import State

NOW = datetime(2026, 10, 26, 12, 0, tzinfo=UTC)


def _euvd(
    record_id: str = "EUVD-2026-1",
    aliases: tuple[str, ...] = ("CVE-2026-1001",),
    score: float | None = 7.5,
    url: str | None = "https://euvd.example/1",
    observed_at: datetime = NOW,
) -> SourceRecord:
    return SourceRecord(
        source="euvd",
        record_id=record_id,
        observed_at=observed_at,
        aliases=aliases,
        title=Sourced("t", "euvd", observed_at),
        scores=(
            (
                Sourced(
                    CvssScore(score) if score is not None else None, "euvd", observed_at
                ),
            )
        ),
        affected=(Sourced(AffectedProduct("php"), "euvd", observed_at),),
        url=url,
        updated_at=NOW - timedelta(days=1),
    )


def _cisa(cve: str = "CVE-2026-1001", observed_at: datetime = NOW) -> SourceRecord:
    return SourceRecord(
        source="cisa_kev",
        record_id=cve,
        observed_at=observed_at,
        kev=(
            Sourced(
                KevEntry(KevCatalog.CISA_KEV, added_at=NOW - timedelta(days=2)),
                "cisa_kev",
                observed_at,
            ),
        ),
        scores=(Sourced(None, "cisa_kev", observed_at),),
    )


def _vulnerabilities(state: State) -> list[tuple[int, str]]:
    with state.transaction() as repo:
        rows = repo._connection.execute(
            select(schema.vulnerability.c.id, schema.vulnerability.c.primary_id)
        ).all()
    return [(row.id, row.primary_id) for row in rows]


def _source_records(state: State) -> list[tuple[str, str, int]]:
    with state.transaction() as repo:
        rows = repo._connection.execute(
            select(
                schema.source_record.c.source,
                schema.source_record.c.record_id,
                schema.source_record.c.vulnerability_id,
            ).order_by(schema.source_record.c.id)
        ).all()
    return [(r.source, r.record_id, r.vulnerability_id) for r in rows]


def _run(
    state: State, *records: SourceRecord, now: datetime = NOW
) -> CorrelationResult:
    with state.transaction() as repo:
        return correlate(repo, records, now)


def test_two_sources_sharing_a_cve_make_one_vulnerability(state: State) -> None:
    with state.transaction() as repo:
        result = correlate(repo, [_euvd(), _cisa()], NOW)
        loaded = repo.load_records(result.touched)
        (records,) = loaded.values()
    assert len(_vulnerabilities(state)) == 1
    assert len(_source_records(state)) == 2
    assert result.stored == 2
    assert result.changed == 2
    vulnerability = build_vulnerability(normalize_all(records))
    assert vulnerability.primary_id == "CVE-2026-1001"
    assert [(r.source, r.record_id) for r in vulnerability.records] == [
        ("cisa_kev", "CVE-2026-1001"),
        ("euvd", "EUVD-2026-1"),
    ]
    assert vulnerability.is_known_exploited
    # every score is kept, including "this source says unscored"
    assert [s.value for s in vulnerability.scores] == [None, CvssScore(7.5)]
    assert vulnerability.aliases == {"CVE-2026-1001", "EUVD-2026-1"}


def test_the_order_of_arrival_does_not_matter(state: State, tmp_path) -> None:
    other = State(f"sqlite:///{tmp_path / 'other.db'}")
    other.migrate()
    _run(state, _euvd(), _cisa())
    _run(other, _cisa(), _euvd())
    assert len(_vulnerabilities(other)) == 1
    assert [p for _, p in _vulnerabilities(other)] == ["CVE-2026-1001"]
    assert sorted((s, r) for s, r, _ in _source_records(other)) == sorted(
        (s, r) for s, r, _ in _source_records(state)
    )
    other.dispose()


def test_a_url_change_is_not_a_new_vulnerability(state: State) -> None:
    _run(state, _euvd(url="https://old.example/1"))
    result = _run(state, _euvd(url="https://new.example/1"))
    assert len(_vulnerabilities(state)) == 1
    assert len(_source_records(state)) == 1
    assert result.changed == 1


def test_a_shared_ghsa_does_not_merge(state: State) -> None:
    _run(
        state,
        _euvd("EUVD-2026-1", ("CVE-2026-1001", "GHSA-AAAA-BBBB-CCCC")),
        _euvd("EUVD-2026-2", ("CVE-2026-1002", "GHSA-AAAA-BBBB-CCCC")),
    )
    assert len(_vulnerabilities(state)) == 2


def test_a_record_without_merge_keys_is_never_merged(state: State) -> None:
    advisory = SourceRecord(
        source="certfr_alerte",
        record_id="CERTFR-2026-ALE-001",
        observed_at=NOW,
        aliases=("GHSA-AAAA-BBBB-CCCC",),
    )
    other = replace(advisory, source="cert_eu", record_id="CERTFR-2026-ALE-001")
    _run(state, advisory, other)
    assert len(_vulnerabilities(state)) == 2
    # found again through its (source, record_id)
    _run(state, advisory)
    assert len(_vulnerabilities(state)) == 2


def test_a_new_cve_alias_merges_two_vulnerabilities(state: State) -> None:

    first = _euvd("EUVD-2026-1", ("CVE-2026-1001",))
    second = _euvd("EUVD-2026-2", ())
    _run(state, first, second)
    assert [p for _, p in _vulnerabilities(state)] == ["CVE-2026-1001", "EUVD-2026-2"]
    older, newer = (i for i, _ in _vulnerabilities(state))
    decision = PublicationDecision(
        endpoint_id="e",
        primary_id="EUVD-2026-2",
        kind="new",
        guid="cvestream:e:EUVD-2026-2:new",
        band=Band.HIGH,
        is_known_exploited=False,
        products=("php",),
        labels=frozenset(),
    )
    with state.transaction() as repo:
        repo.add_publication(newer, decision, NOW)

    # the EUVD-only record gains the CVE that the other vulnerability holds
    result = _run(state, _euvd("EUVD-2026-2", ("CVE-2026-1001",), score=9.8))

    assert _vulnerabilities(state) == [(older, "CVE-2026-1001")]
    assert {v for _, _, v in _source_records(state)} == {older}
    with state.transaction() as repo:
        history = repo.publication_history(["e"], [older, newer])
    assert list(history) == [("e", older)]
    assert result.touched == {older}


def test_the_same_content_redelivered_does_not_touch_the_vulnerability(
    state: State,
) -> None:
    _run(state, _euvd(observed_at=NOW))
    later = NOW + timedelta(hours=1)
    result = _run(state, _euvd(observed_at=later), now=later)
    assert result.touched == frozenset()
    assert result.changed == 0
    with state.transaction() as repo:
        row = repo._connection.execute(select(schema.source_record)).one()
        vulnerability = repo._connection.execute(select(schema.vulnerability)).one()
    assert row.last_seen_at == later
    assert row.changed_at == NOW
    assert row.record_json["observed_at"] == "2026-10-26T12:00:00.000000Z"
    assert vulnerability.changed_at == NOW


def test_a_changed_score_touches_the_vulnerability(state: State) -> None:
    _run(state, _euvd(score=5.0))
    later = NOW + timedelta(hours=1)
    result = _run(state, _euvd(score=9.0), now=later)
    assert len(result.touched) == 1
    assert result.changed == 1
    with state.transaction() as repo:
        vulnerability = repo._connection.execute(select(schema.vulnerability)).one()
    assert vulnerability.changed_at == later


def test_a_record_without_a_usable_alias_is_skipped(state: State) -> None:
    result = _run(state, _euvd("https://example.com/x", ()), _euvd())
    assert result.skipped == 1
    assert result.stored == 1
    assert len(_vulnerabilities(state)) == 1


def test_the_primary_id_follows_the_aliases(state: State) -> None:
    _run(state, _euvd("EUVD-2026-1", ()))
    assert [p for _, p in _vulnerabilities(state)] == ["EUVD-2026-1"]
    _run(state, _euvd("EUVD-2026-1", ("CVE-2026-1007",)))
    assert [p for _, p in _vulnerabilities(state)] == ["CVE-2026-1007"]


def test_aliases_are_append_only(state: State) -> None:
    _run(state, _euvd("EUVD-2026-1", ("CVE-2026-1001",)))
    _run(state, _euvd("EUVD-2026-1", ()))
    assert len(_vulnerabilities(state)) == 1
    # the dropped alias still finds the vulnerability
    _run(state, _cisa("CVE-2026-1001"))
    assert len(_vulnerabilities(state)) == 1


def test_activity_is_the_latest_source_time(state: State) -> None:
    _run(state, _euvd())  # updated 1 day ago
    _run(state, _cisa())  # KEV added 2 days ago
    with state.transaction() as repo:
        row = repo._connection.execute(select(schema.vulnerability)).one()
    assert row.activity_at == NOW - timedelta(days=1)


def test_activity_time_cases() -> None:
    base = SourceRecord(source="s", record_id="CVE-2026-1001", observed_at=NOW)
    updated = NOW - timedelta(days=1)
    published = NOW - timedelta(days=5)
    kev_old = Sourced(
        KevEntry(KevCatalog.CISA_KEV, added_at=NOW - timedelta(days=30)), "s", NOW
    )
    kev_new = Sourced(
        KevEntry(KevCatalog.EU_KEV, added_at=NOW - timedelta(days=9)), "s", NOW
    )
    kev_undated = Sourced(KevEntry(KevCatalog.EU_KEV), "s", NOW)
    assert (
        activity_time(replace(base, updated_at=updated, published_at=published))
        == updated
    )
    assert activity_time(replace(base, published_at=published)) == published
    assert activity_time(replace(base, kev=(kev_old, kev_new, kev_undated))) == (
        NOW - timedelta(days=9)
    )
    assert activity_time(replace(base, kev=(kev_undated,))) is None
    assert activity_time(base) is None


def test_a_record_with_no_source_time_is_active_when_first_seen(state: State) -> None:
    record = SourceRecord(source="s", record_id="CVE-2026-1001", observed_at=NOW)
    _run(state, record)
    with state.transaction() as repo:
        row = repo._connection.execute(select(schema.vulnerability)).one()
    assert row.activity_at == NOW


def test_merge_keys_are_cve_and_euvd_only() -> None:
    normalized = normalize(
        _euvd("EUVD-2026-1", ("CVE-2026-1001", "GHSA-AAAA-BBBB-CCCC"))
    )
    assert normalized is not None
    assert merge_keys(normalized) == {"EUVD-2026-1", "CVE-2026-1001"}


def test_build_vulnerability_keeps_every_value() -> None:
    kev_catalog = SourceRecord(
        source="eu_kev",
        record_id="EUVD-2026-1",
        observed_at=NOW,
        aliases=("CVE-2026-1001",),
        references=("https://a.example", "https://b.example"),
        kev=(Sourced(KevEntry(KevCatalog.EU_KEV), "eu_kev", NOW),),
        published_at=NOW - timedelta(days=10),
        updated_at=NOW - timedelta(days=3),
    )
    euvd = replace(
        _euvd(),
        references=("https://a.example", "https://a.example"),
        published_at=NOW - timedelta(days=4),
        updated_at=NOW - timedelta(days=1),
        description=Sourced("d", "euvd", NOW),
    )
    cisa = replace(_cisa(), references=("https://a.example",))
    records = [normalize(r) for r in (euvd, kev_catalog, cisa)]
    vulnerability = build_vulnerability([n for n in records if n is not None])
    # The earliest publication and the newest update.
    assert vulnerability.published_at == NOW - timedelta(days=10)
    assert vulnerability.updated_at == NOW - timedelta(days=1)
    assert [(r.value, r.source) for r in vulnerability.references] == [
        ("https://a.example", "cisa_kev"),
        ("https://a.example", "eu_kev"),
        ("https://b.example", "eu_kev"),
        # duplicated inside one record: kept once
        ("https://a.example", "euvd"),
    ]
    assert len(vulnerability.kev) == 2
    assert len(vulnerability.scores) == 2
    assert [d.value for d in vulnerability.descriptions] == ["d"]
    assert [t.source for t in vulnerability.titles] == ["euvd"]


def test_a_vulnerability_without_source_dates_has_none() -> None:
    normalized = normalize(_cisa())
    assert normalized is not None
    vulnerability = build_vulnerability([normalized])
    assert vulnerability.published_at is None
    assert vulnerability.updated_at is None
