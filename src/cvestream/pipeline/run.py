"""One pipeline run: store, correlate, match, decide, in a single transaction.

The run writes the source records, the publication rows of its decisions and the
source cursors in one `State.transaction()`: a failure anywhere rolls all of it
back (no publication without its cursor, no cursor without its publications).
The same transaction renders every enabled endpoint's feed into `feed_cache`, so
a feed never shows a publication whose cursor was rolled back. `cycle()` wraps a
run with the configuration reload and the `cycle.summary` event.
The pipeline never retries and never catches: the next cycle is the retry.
Synchronous code only; the daemon calls it through `asyncio.to_thread`.
"""

import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType

import structlog

from cvestream.clock import Clock
from cvestream.config.provider import (
    ConfigProvider,
    ConfigSnapshot,
    ConfigUnavailableError,
    ConfigValidationError,
)
from cvestream.domain.matcher import DropReason, match_outcome
from cvestream.domain.model import Vulnerability
from cvestream.domain.publication import (
    BACKFILL_WINDOW,
    PUBLICATION_WINDOW,
    PublicationDecision,
    decide,
    group_decisions,
)
from cvestream.pipeline.correlate import build_vulnerability, correlate, normalize_all
from cvestream.pipeline.render import (
    FeedEntry,
    RenderedFeed,
    count_items,
    etag_of,
    render_feed,
)
from cvestream.pipeline.summary import log_summary, summarize
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
    # Per enabled endpoint with require_score: vulnerabilities it held back.
    held_back: Mapping[str, int]


@dataclass(frozen=True, slots=True)
class SourceOutcome:
    """What one source fetch came to: a result, or a failure."""

    # Adapter name, the cursor key.
    source: str
    # `None`: the fetch failed.
    result: FetchResult | None
    # `SourceError` subclass name when it failed.
    error_type: str | None = None


@dataclass(frozen=True, slots=True)
class CycleResult:
    # The snapshot this cycle used (maybe reloaded).
    snapshot: ConfigSnapshot
    run: RunResult


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
        held_back = {e.id: 0 for e in endpoints if e.rules.require_score}
        for endpoint in endpoints:
            candidates = recent if endpoint.id in with_history else backfill
            # Decide and check every candidate first, then write: grouping
            # (story 5.2) needs the whole run's accepted decisions of this
            # endpoint before any row is written, all in this one transaction.
            accepted: list[tuple[int, PublicationDecision]] = []
            run_guids: set[str] = set()
            for vulnerability_id in sorted(candidates):
                vulnerability = vulnerabilities[vulnerability_id]
                outcome = match_outcome(vulnerability, endpoint)
                if isinstance(outcome, DropReason):
                    if outcome is DropReason.REQUIRE_SCORE:
                        held_back[endpoint.id] += 1
                    continue
                decision = decide(
                    outcome,
                    vulnerability,
                    history.get((endpoint.id, vulnerability_id), []),
                    now,
                )
                if decision is None:
                    continue
                # Safety net: `decide()` checks the GUID against the history of
                # this vulnerability only. Two vulnerabilities sharing an
                # advisory-only primary ID build the same GUID, and inserting it
                # twice would roll back this run and every next one. Skip it,
                # whether the GUID is already stored or accepted earlier in
                # this run (rows are written below, after the grouping).
                if (
                    repository.publication_guid_exists(decision.guid)
                    or decision.guid in run_guids
                ):
                    log.warning(
                        "publish.skipped",
                        reason="guid_collision",
                        endpoint_id=decision.endpoint_id,
                        vuln_id=decision.primary_id,
                        kind=decision.kind,
                    )
                    continue
                run_guids.add(decision.guid)
                accepted.append((vulnerability_id, decision))
            group_of: dict[str, str] = {}
            if endpoint.rules.group_by_product:
                for group in group_decisions([d for _, d in accepted]):
                    for member in group.members:
                        group_of[member.guid] = group.guid
                    log.info(
                        "publish.grouped",
                        endpoint_id=group.endpoint_id,
                        family=group.family,
                        count=len(group.members),
                        part=group.part,
                        parts=group.parts,
                        group_guid=group.guid,
                    )
            for vulnerability_id, decision in accepted:
                group_guid = group_of.get(decision.guid)
                repository.add_publication(
                    vulnerability_id, decision, now, group_guid=group_guid
                )
                decided.append(decision)
                fields: dict[str, object] = {
                    "endpoint_id": decision.endpoint_id,
                    "vuln_id": decision.primary_id,
                    "kind": decision.kind,
                }
                if group_guid is not None:
                    fields["group_guid"] = group_guid
                log.info("publish.decided", **fields)

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
                    is_fresh=p.is_fresh,
                    group_guid=p.group_guid,
                )
                for p in publications.get(endpoint.id, [])
            ]
            xml = render_feed(endpoint, entries)
            etag = etag_of(xml)
            old = previous.get(endpoint.id)
            changed_at = old.changed_at if old is not None and old.etag == etag else now
            # A group counts as one rendered item, not one per member.
            item_count = count_items(entries)
            repository.save_feed(
                StoredFeed(
                    endpoint.id,
                    xml.decode("utf-8"),
                    etag,
                    item_count,
                    now,
                    changed_at,
                )
            )
            feeds[endpoint.id] = RenderedFeed(
                endpoint.id, xml, etag, item_count, now, changed_at
            )
    result = RunResult(
        decisions=tuple(decided),
        records=correlation.stored + correlation.skipped,
        changed=correlation.changed,
        skipped=correlation.skipped,
        touched=len(correlation.touched),
        feeds=MappingProxyType(feeds),
        held_back=MappingProxyType(held_back),
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


def reload_config(provider: ConfigProvider, current: ConfigSnapshot) -> ConfigSnapshot:
    """The snapshot to use now: a valid new one, else `current`.

    An invalid or unreadable configuration keeps the last valid one and is
    logged. Never logs the snapshot or its tokens.
    """
    try:
        revision = provider.revision()
        if revision == current.revision:
            return current
        snapshot = provider.load()
    except ConfigValidationError as exc:
        log.warning("config.rejected", errors=list(exc.errors))
        return current
    except ConfigUnavailableError as exc:
        log.warning("config.unavailable", error_type=type(exc).__name__)
        return current
    log.info("config.reloaded", count=len(snapshot.endpoints))
    return snapshot


def cycle(
    state: State,
    provider: ConfigProvider,
    snapshot: ConfigSnapshot,
    outcomes: Sequence[SourceOutcome],
    clock: Clock,
) -> CycleResult:
    """One pipeline cycle: reload the config, run, log `cycle.summary`."""
    started = time.monotonic()
    snapshot = reload_config(provider, snapshot)
    batches = [
        SourceBatch(o.source, o.result) for o in outcomes if o.result is not None
    ]
    result = run(state, snapshot, batches, clock)
    duration_ms = round((time.monotonic() - started) * 1000)
    log_summary(summarize(outcomes, snapshot, result, duration_ms))
    return CycleResult(snapshot=snapshot, run=result)
