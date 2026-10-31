import json
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from cvestream.domain.model import (
    AffectedProduct,
    CvssScore,
    KevCatalog,
    KevEntry,
    Sourced,
    SourceRecord,
)
from cvestream.state.codec import (
    content_hash,
    record_from_json,
    record_to_json,
)
from cvestream.state.schema import StateError

T0 = datetime(2026, 10, 26, 12, 0, 0, 123456, tzinfo=UTC)


def _full(observed_at: datetime = T0) -> SourceRecord:
    def s[T](value: T, source: str = "euvd") -> Sourced[T]:
        return Sourced(value, source, observed_at)

    return SourceRecord(
        source="euvd",
        record_id="EUVD-2026-1",
        observed_at=observed_at,
        aliases=("EUVD-2026-1", "CVE-2026-1", "GHSA-xxxx-yyyy-zzzz"),
        title=s("Title"),
        description=s("Description"),
        scores=(
            s(CvssScore(9.8, "3.1", "AV:N/AC:L")),
            s(None, "cisa_kev"),
        ),
        affected=(
            s(AffectedProduct("php", "php", "< 8.2.1")),
            s(AffectedProduct("nginx", None, None)),
        ),
        kev=(
            s(KevEntry(KevCatalog.CISA_KEV, True, T0 - timedelta(days=2)), "cisa_kev"),
            s(KevEntry(KevCatalog.EU_KEV, None, None), "eu_kev"),
        ),
        references=("https://example.com/a", "https://example.com/b"),
        url="https://euvd.example/EUVD-2026-1",
        published_at=T0 - timedelta(days=3),
        updated_at=T0 - timedelta(days=1),
        epss=s(0.25),
    )


def _minimal() -> SourceRecord:
    return SourceRecord(source="cisa_kev", record_id="CVE-2026-2", observed_at=T0)


def test_full_round_trip() -> None:
    record = _full()
    assert record_from_json(record_to_json(record)) == record


def test_minimal_round_trip() -> None:
    record = _minimal()
    assert record_from_json(record_to_json(record)) == record


def test_unknown_stays_null() -> None:
    data = record_to_json(_minimal())
    assert data["title"] is None
    assert data["epss"] is None
    assert data["published_at"] is None
    assert data["scores"] == []


def test_tuples_are_rebuilt_as_tuples() -> None:
    record = record_from_json(record_to_json(_full()))
    assert isinstance(record.aliases, tuple)
    assert isinstance(record.scores, tuple)
    assert isinstance(record.kev[0].value.catalog, KevCatalog)


def test_json_is_serializable() -> None:
    json.dumps(record_to_json(_full()))


def test_unknown_version_is_refused() -> None:
    data = record_to_json(_minimal())
    data["v"] = 99
    with pytest.raises(StateError, match="codec version"):
        record_from_json(data)


def test_missing_field_is_refused() -> None:
    data = record_to_json(_minimal())
    del data["source"]
    with pytest.raises(StateError):
        record_from_json(data)


def test_malformed_field_is_refused() -> None:
    data = record_to_json(_minimal())
    data["aliases"] = "CVE-2026-2"
    with pytest.raises(StateError):
        record_from_json(data)


def test_content_hash_ignores_every_observed_at() -> None:
    later = T0 + timedelta(hours=5)
    assert content_hash(_full(T0)) == content_hash(_full(later))


@pytest.mark.parametrize(
    "change",
    [
        lambda r: replace(
            r, scores=(Sourced(CvssScore(9.9), "euvd", T0), *r.scores[1:])
        ),
        lambda r: replace(
            r, affected=(Sourced(AffectedProduct("php", "php", "< 8.2.2"), "euvd", T0),)
        ),
        lambda r: replace(r, kev=r.kev[:1]),
        lambda r: replace(r, aliases=(*r.aliases, "CVE-2026-99")),
        lambda r: replace(r, url="https://euvd.example/other"),
        lambda r: replace(r, description=Sourced("Other", "euvd", T0)),
        lambda r: replace(r, epss=None),
    ],
)
def test_content_hash_changes_with_content(
    change: Callable[[SourceRecord], SourceRecord],
) -> None:
    record = _full()
    assert content_hash(change(record)) != content_hash(record)


def test_content_hash_is_hex_sha256() -> None:
    digest = content_hash(_minimal())
    assert len(digest) == 64
    int(digest, 16)
