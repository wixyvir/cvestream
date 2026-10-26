"""One pipeline run: store, correlate, match, decide, in a single transaction.

The run writes the source records, the publication rows of its decisions and the
source cursors in one `State.transaction()`: a failure anywhere rolls all of it
back (no publication without its cursor, no cursor without its publications).
The same transaction renders every enabled endpoint's feed into `feed_cache`, so
a feed never shows a publication whose cursor was rolled back.
The pipeline never retries and never catches: the next cycle is the retry.
Synchronous code only; the daemon calls it through `asyncio.to_thread`.
"""

import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType

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
from cvestream.pipeline.render import FeedEntry, RenderedFeed, etag_of, render_feed
from cvestream.sources.base import FetchResult
from cvestream.state.db import State
from cvestream.state.repository import StoredFeed

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
    # The feed of every enabled endpoint, rendered by this run.
    feeds: Mapping[str, RenderedFeed]


def load_cursors(state: State) -> dict[str, str | None]:
    """Stored cursor per adapter name. Read-only: writes nothing."""
    with state.transaction() as repository:
        return repository.cursors()


def load_feeds(state: State) -> dict[str, RenderedFeed]:
    """The stored feeds, by endpoint id. Read-only: writes nothing."""
    with state.transaction() as repository:
        stored = repository.feeds()
    return {
        endpoint_id: RenderedFeed(
            endpoint_id=feed.endpoint_id,
            xml=feed.xml.encode("utf-8"),
            etag=feed.etag,
            item_count=feed.item_count,
            rendered_at=feed.rendered_at,
            changed_at=feed.changed_at,
        )
        for endpoint_id, feed in stored.items()
    }


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
    feeds: dict[str, RenderedFeed] = {}
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
                # Safety net: `decide()` checks the GUID against the history of
                # this vulnerability only. Two vulnerabilities sharing an
                # advisory-only primary ID build the same GUID, and inserting it
                # twice would roll back this run and every next one. Skip it.
                if repository.publication_guid_exists(decision.guid):
                    log.warning(
                        "publish.skipped",
                        reason="guid_collision",
                        endpoint_id=decision.endpoint_id,
                        vuln_id=decision.primary_id,
                        kind=decision.kind,
                    )
                    continue
                repository.add_publication(vulnerability_id, decision, now)
                decided.append(decision)
                log.info(
                    "publish.decided",
                    endpoint_id=decision.endpoint_id,
                    vuln_id=decision.primary_id,
                    kind=decision.kind,
                )

        # Every enabled endpoint is rendered at every run: the 14-day window
        # slides even when nothing is published, and a rename shows at once.
        publications = repository.publications_since(
            [e.id for e in endpoints], now - PUBLICATION_WINDOW
        )
        missing = {
            p.vulnerability_id for ps in publications.values() for p in ps
        } - vulnerabilities.keys()
        vulnerabilities |= {
            vulnerability_id: build_vulnerability(normalize_all(records))
            for vulnerability_id, records in repository.load_records(missing).items()
        }
        previous = repository.feeds()
        for endpoint in endpoints:
            entries = [
                FeedEntry(
                    guid=p.guid,
                    kind=p.kind,
                    primary_id=p.primary_id,
                    band=p.band,
                    is_known_exploited=p.is_known_exploited,
                    products=p.products,
                    labels=p.labels,
                    published_at=p.published_at,
                    vulnerability=vulnerabilities[p.vulnerability_id],
                )
                for p in publications.get(endpoint.id, [])
            ]
            xml = render_feed(endpoint, entries)
            etag = etag_of(xml)
            old = previous.get(endpoint.id)
            changed_at = old.changed_at if old is not None and old.etag == etag else now
            repository.save_feed(
                StoredFeed(
                    endpoint.id,
                    xml.decode("utf-8"),
                    etag,
                    len(entries),
                    now,
                    changed_at,
                )
            )
            feeds[endpoint.id] = RenderedFeed(
                endpoint.id, xml, etag, len(entries), now, changed_at
            )
    result = RunResult(
        decisions=tuple(decided),
        records=correlation.stored + correlation.skipped,
        changed=correlation.changed,
        skipped=correlation.skipped,
        touched=len(correlation.touched),
        feeds=MappingProxyType(feeds),
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
