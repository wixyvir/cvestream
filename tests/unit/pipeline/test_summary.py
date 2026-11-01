from datetime import UTC, datetime
from types import MappingProxyType

from structlog.testing import capture_logs

from cvestream.config.provider import ConfigSnapshot
from cvestream.domain.endpoint import Endpoint, ProductEntry
from cvestream.domain.matcher import PassThrough
from cvestream.domain.model import SourceRecord
from cvestream.domain.publication import PublicationDecision
from cvestream.domain.severity import Band
from cvestream.pipeline.run import RunResult, SourceOutcome
from cvestream.pipeline.summary import (
    CycleSummary,
    SourceReport,
    log_summary,
    summarize,
)
from cvestream.sources.base import FetchResult

NOW = datetime(2026, 10, 26, tzinfo=UTC)


def _endpoint(endpoint_id: str, *, enabled: bool = True) -> Endpoint:
    return Endpoint(
        id=endpoint_id,
        name=endpoint_id,
        products=(ProductEntry("php"),),
        enabled=enabled,
    )


def _snapshot(*endpoints: Endpoint) -> ConfigSnapshot:
    return ConfigSnapshot("r", endpoints, MappingProxyType({}))


def _decision(
    endpoint_id: str, *labels: PassThrough, guid: str | None = None
) -> PublicationDecision:
    return PublicationDecision(
        endpoint_id=endpoint_id,
        primary_id="CVE-2026-1",
        kind="new",
        guid=guid or f"cvestream:{endpoint_id}:CVE-2026-1:new",
        band=Band.HIGH,
        is_known_exploited=False,
        products=("php",),
        labels=frozenset(labels),
    )


def _result(*decisions: PublicationDecision) -> RunResult:
    return RunResult(
        decisions=decisions,
        records=0,
        changed=0,
        skipped=0,
        touched=0,
        feeds=MappingProxyType({}),
    )


def _record(record_id: str) -> SourceRecord:
    return SourceRecord(source="euvd", record_id=record_id, observed_at=NOW)


def test_sources_report_ok_with_counts_and_failed_with_the_error_type() -> None:
    outcomes = [
        SourceOutcome(
            "euvd", FetchResult((_record("a"), _record("b")), "c", skipped=3)
        ),
        SourceOutcome("cisa_kev", None, "SourceTransportError"),
    ]
    summary = summarize(outcomes, _snapshot(), _result(), 12)
    assert summary.sources == {
        "euvd": SourceReport("ok", records=2, skipped=3),
        "cisa_kev": SourceReport("failed", error_type="SourceTransportError"),
    }
    assert summary.duration_ms == 12


def test_the_last_outcome_of_a_source_wins() -> None:
    outcomes = [
        SourceOutcome("euvd", None, "SourcePayloadError"),
        SourceOutcome("euvd", FetchResult((), None)),
    ]
    summary = summarize(outcomes, _snapshot(), _result(), 0)
    assert summary.sources == {"euvd": SourceReport("ok", records=0, skipped=0)}


def test_every_enabled_endpoint_is_counted_zero_included() -> None:
    snapshot = _snapshot(
        _endpoint("a"), _endpoint("off", enabled=False), _endpoint("b")
    )
    result = _result(
        _decision("b"), _decision("b", guid="g2"), _decision("b", guid="g3")
    )
    summary = summarize([], snapshot, result, 0)
    assert list(summary.published) == ["a", "b"]
    assert summary.published == {"a": 0, "b": 3}


def test_pass_throughs_are_counted_per_label() -> None:
    result = _result(
        _decision("a", PassThrough.SCORE_UNKNOWN, guid="g1"),
        _decision("a", PassThrough.VERSIONS_UNKNOWN, guid="g2"),
        _decision(
            "a", PassThrough.SCORE_UNKNOWN, PassThrough.VERSIONS_UNKNOWN, guid="g3"
        ),
        _decision("a", guid="g4"),
    )
    summary = summarize([], _snapshot(_endpoint("a")), result, 0)
    assert summary.score_unknown == 2
    assert summary.versions_unknown == 2


def test_log_summary_writes_only_the_fields_that_apply() -> None:
    summary = CycleSummary(
        sources={
            "euvd": SourceReport("ok", records=2, skipped=0),
            "cisa_kev": SourceReport("failed", error_type="SourceTransportError"),
        },
        published={"a": 0, "b": 2},
        score_unknown=1,
        versions_unknown=0,
        duration_ms=7,
    )
    with capture_logs() as logs:
        log_summary(summary)
    assert len(logs) == 1
    entry = logs[0]
    assert entry["event"] == "cycle.summary"
    assert entry["log_level"] == "info"
    assert entry["sources"] == {
        "euvd": {"result": "ok", "records": 2, "skipped": 0},
        "cisa_kev": {"result": "failed", "error_type": "SourceTransportError"},
    }
    assert entry["published"] == {"a": 0, "b": 2}
    assert entry["score_unknown"] == 1
    assert entry["versions_unknown"] == 0
    assert entry["count"] == 2
    assert entry["duration_ms"] == 7
