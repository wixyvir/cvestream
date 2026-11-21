"""The `cycle.summary` event: what one pipeline cycle did (NFR-4).

One per cycle: the result of every source of the cycle, the number published
per enabled endpoint (zero included), how many published items were let
through on unknown data (recall over volume, FR-25), and what an endpoint's
`require_score` held back.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

import structlog

from cvestream.config.provider import ConfigSnapshot
from cvestream.domain.matcher import PassThrough

if TYPE_CHECKING:
    from cvestream.pipeline.run import RunResult, SourceOutcome

log = structlog.get_logger()


@dataclass(frozen=True, slots=True)
class SourceReport:
    result: Literal["ok", "failed"]
    # `ok` only: records the adapter returned.
    records: int | None = None
    # `ok` only: the adapter's count of records it dropped.
    skipped: int | None = None
    # `failed` only: the `SourceError` subclass name.
    error_type: str | None = None


@dataclass(frozen=True, slots=True)
class CycleSummary:
    sources: Mapping[str, SourceReport]
    # Every enabled endpoint, zero included, in config order.
    published: Mapping[str, int]
    # Decisions let through on an unknown score / unknown versions.
    score_unknown: int
    versions_unknown: int
    # Per enabled endpoint with require_score: vulnerabilities it held back.
    held_back: Mapping[str, int]
    duration_ms: int


def summarize(
    outcomes: Sequence[SourceOutcome],
    snapshot: ConfigSnapshot,
    result: RunResult,
    duration_ms: int,
) -> CycleSummary:
    sources: dict[str, SourceReport] = {}
    # Two outcomes of one source in a cycle (a drained backlog): the last wins.
    for outcome in outcomes:
        if outcome.result is None:
            sources[outcome.source] = SourceReport(
                "failed", error_type=outcome.error_type
            )
        else:
            sources[outcome.source] = SourceReport(
                "ok",
                records=len(outcome.result.records),
                skipped=outcome.result.skipped,
            )
    published = {e.id: 0 for e in snapshot.endpoints if e.enabled}
    for decision in result.decisions:
        published[decision.endpoint_id] = published.get(decision.endpoint_id, 0) + 1
    return CycleSummary(
        sources=sources,
        published=published,
        score_unknown=sum(
            PassThrough.SCORE_UNKNOWN in d.labels for d in result.decisions
        ),
        versions_unknown=sum(
            PassThrough.VERSIONS_UNKNOWN in d.labels for d in result.decisions
        ),
        held_back=result.held_back,
        duration_ms=duration_ms,
    )


def log_summary(summary: CycleSummary) -> None:
    log.info(
        "cycle.summary",
        sources={
            name: {
                key: value
                for key, value in (
                    ("result", report.result),
                    ("records", report.records),
                    ("skipped", report.skipped),
                    ("error_type", report.error_type),
                )
                if value is not None
            }
            for name, report in summary.sources.items()
        },
        published=dict(summary.published),
        score_unknown=summary.score_unknown,
        versions_unknown=summary.versions_unknown,
        held_back=dict(summary.held_back),
        count=sum(summary.published.values()),
        duration_ms=summary.duration_ms,
    )
