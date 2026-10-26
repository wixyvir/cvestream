import dataclasses
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import pytest

from cvestream.domain.model import (
    AffectedProduct,
    CvssScore,
    KevCatalog,
    KevEntry,
    NormalizedRecord,
    RecordRef,
    Sourced,
    SourceRecord,
    Vulnerability,
)

NOW = datetime(2026, 10, 26, tzinfo=UTC)
type VulnerabilityFactory = Callable[..., Vulnerability]


def _instances() -> list[Any]:
    record = SourceRecord(source="euvd", record_id="EUVD-2026-1", observed_at=NOW)
    return [
        Sourced("x", source="euvd", observed_at=NOW),
        CvssScore(score=9.8),
        AffectedProduct(product="PHP"),
        KevEntry(catalog=KevCatalog.CISA_KEV),
        RecordRef(source="euvd", record_id="EUVD-2026-1"),
        record,
        NormalizedRecord(
            record=record, aliases=frozenset({"EUVD-2026-1"}), primary_id="EUVD-2026-1"
        ),
        Vulnerability(primary_id="CVE-2026-1", aliases=frozenset({"CVE-2026-1"})),
    ]


@pytest.mark.parametrize("instance", _instances(), ids=lambda i: type(i).__name__)
def test_model_classes_are_frozen_and_slotted(instance: Any) -> None:
    assert not hasattr(instance, "__dict__")
    field = dataclasses.fields(instance)[0].name
    with pytest.raises(dataclasses.FrozenInstanceError):
        setattr(instance, field, None)


def test_sourced_keeps_none_as_unknown() -> None:
    unscored: Sourced[CvssScore | None] = Sourced(None, source="euvd", observed_at=NOW)
    assert unscored.value is None


def test_replace_creates_a_modified_copy() -> None:
    original = Vulnerability(
        primary_id="EUVD-2026-1", aliases=frozenset({"EUVD-2026-1"})
    )
    changed = dataclasses.replace(original, primary_id="CVE-2026-1")
    assert (original.primary_id, changed.primary_id) == ("EUVD-2026-1", "CVE-2026-1")


def test_optional_fields_default_to_unknown() -> None:
    record = SourceRecord(source="euvd", record_id="EUVD-2026-1", observed_at=NOW)
    assert record.title is None
    assert record.description is None
    assert record.scores == ()
    assert record.published_at is None
    assert AffectedProduct(product="PHP").version_text is None
    assert CvssScore(score=1.0).vector is None


def test_is_known_exploited(make_vulnerability: VulnerabilityFactory) -> None:
    assert make_vulnerability(kev=True).is_known_exploited
    assert not make_vulnerability(kev=False).is_known_exploited


def test_vulnerability_is_hashable(make_vulnerability: VulnerabilityFactory) -> None:
    vulnerability = make_vulnerability(
        description="PHP flaw",
        products=[("PHP", "PHP Group", "8.1.0 <8.2.29")],
        scores=[9.8, None],
        kev=True,
    )
    assert isinstance(hash(vulnerability), int)
