from datetime import UTC, datetime

from cvestream.domain.model import SourceRecord
from cvestream.pipeline.normalize import normalize

NOW = datetime(2026, 10, 26, tzinfo=UTC)


def _record(record_id: str, *aliases: str) -> SourceRecord:
    return SourceRecord(
        source="euvd", record_id=record_id, observed_at=NOW, aliases=aliases
    )


def test_aliases_are_normalized_and_include_the_record_id() -> None:
    result = normalize(_record("euvd-2026-1", " cve-2026-1001 ", "GHSA-AAAA-BBBB-CCCC"))
    assert result is not None
    assert result.aliases == {"EUVD-2026-1", "CVE-2026-1001", "GHSA-AAAA-BBBB-CCCC"}
    assert result.primary_id == "CVE-2026-1001"


def test_the_record_is_kept_unchanged() -> None:
    record = _record("euvd-2026-1", "cve-2026-1")
    result = normalize(record)
    assert result is not None
    assert result.record is record


def test_urls_and_blank_aliases_are_dropped() -> None:
    result = normalize(
        _record("EUVD-2026-1", "https://example.com/CVE-2026-1001", "", "   ")
    )
    assert result is not None
    assert result.aliases == {"EUVD-2026-1"}


def test_no_usable_alias_is_none() -> None:
    assert normalize(_record("https://example.com/x", "", " ")) is None
    assert normalize(_record("  ")) is None


def test_primary_id_prefers_cve_then_euvd_then_advisory() -> None:
    euvd = normalize(_record("EUVD-2026-2", "GHSA-AAAA-BBBB-CCCC"))
    advisory = normalize(_record("CERTFR-2026-ALE-001"))
    assert euvd is not None
    assert advisory is not None
    assert euvd.primary_id == "EUVD-2026-2"
    assert advisory.primary_id == "CERTFR-2026-ALE-001"
