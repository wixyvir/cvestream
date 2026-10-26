"""One pipeline run: store, correlate, match, decide, in a single transaction.

The run writes the source records, the publication rows of its decisions and the
source cursors in one `State.transaction()`: a failure anywhere rolls all of it
back (no publication without its cursor, no cursor without its publications).
The pipeline never retries and never catches: the next cycle is the retry.
Synchronous code only; the daemon calls it through `asyncio.to_thread`.
"""

import time
from collections.abc import Sequence
from dataclasses import dataclass

import structlog

from cvestream.clock import Clock
from cvestream.config.provider import ConfigSnapshot
from cvestream.domain.matcher import match
from cvestream.domain.model import Vulnerability
from cvestream.domain.publication import (
    BACKFILL_WINDOW,
    PUBLICATION_WINDOW,
    PublicationDecision,
    decide,
)
from cvestream.pipeline.correlate import build_vulnerability, correlate, normalize_all
from cvestream.sources.base import FetchResult
from cvestream.state.db import State

log = structlog.get_logger()


@dataclass(frozen=True, slots=True)
class SourceBatch:
    # Adapter name, the cursor key: `euvd`, `cisa_kev`.
    source: str
    result: FetchResult


@dataclass(frozen=True, slots=True)
class RunResult:
    decisions: tuple[PublicationDecision, ...]
    # Records received, including the ones skipped for lack of an identity.
    records: int
    # Records inserted or with new content.
    changed: int
    # Records without any usable alias (not the adapters' own `skipped`).
    skipped: int
    # Vulnerabilities whose content changed or that were merged.
    touched: int


def load_cursors(state: State) -> dict[str, str | None]:
    """Stored cursor per adapter name. Read-only: writes nothing."""
    with state.transaction() as repository:
        return repository.cursors()


def run(
    state: State,
    snapshot: ConfigSnapshot,
    batches: Sequence[SourceBatch],
    clock: Clock,
) -> RunResult:
    """Apply `batches` and decide what each enabled endpoint publishes now."""
    started = time.monotonic()
    now = clock.now()
    decided: list[PublicationDecision] = []
    with state.transaction() as repository:
        correlation = correlate(
            repository,
            (record for batch in batches for record in batch.result.records),
            now,
        )
        for batch in batches:
            # No cursor from the adapter (KEV catalogues): leave it untouched.
            if batch.result.next_cursor is not None:
                repository.set_cursor(batch.source, batch.result.next_cursor, now)

        endpoints = [e for e in snapshot.endpoints if e.enabled]
        # Read before any decision: an endpoint whose first publications happen
        # in this run is a backfill for the whole run.
        with_history = repository.endpoints_with_publications(e.id for e in endpoints)
        recent = repository.filter_active_since(
            correlation.touched, now - PUBLICATION_WINDOW
        )
        needs_backfill = any(e.id not in with_history for e in endpoints)
        backfill = (
            repository.active_since(now - BACKFILL_WINDOW) if needs_backfill else []
        )
        wanted = sorted({*recent, *backfill})
        vulnerabilities: dict[int, Vulnerability] = {
            vulnerability_id: build_vulnerability(normalize_all(records))
            for vulnerability_id, records in repository.load_records(wanted).items()
        }
        history = repository.publication_history(
            [e.id for e in endpoints], vulnerabilities.keys()
        )
        for endpoint in endpoints:
            candidates = recent if endpoint.id in with_history else backfill
            for vulnerability_id in sorted(candidates):
                vulnerability = vulnerabilities[vulnerability_id]
                matched = match(vulnerability, endpoint)
                if matched is None:
                    continue
                decision = decide(
                    matched,
                    vulnerability,
                    history.get((endpoint.id, vulnerability_id), []),
                )
                if decision is None:
                    continue
                repository.add_publication(vulnerability_id, decision, now)
                decided.append(decision)
                log.info(
                    "publish.decided",
                    endpoint_id=decision.endpoint_id,
                    vuln_id=decision.primary_id,
                    kind=decision.kind,
                )
    result = RunResult(
        decisions=tuple(decided),
        records=correlation.stored + correlation.skipped,
        changed=correlation.changed,
        skipped=correlation.skipped,
        touched=len(correlation.touched),
    )
    log.info(
        "pipeline.completed",
        count=len(result.decisions),
        records=result.records,
        changed=result.changed,
        touched=result.touched,
        duration_ms=round((time.monotonic() - started) * 1000),
    )
    return result
