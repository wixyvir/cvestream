"""Correlation: merge the records of every source into one vulnerability (FR-7..9).

Identity is `(source, record_id)` plus CVE and EUVD aliases, never a URL. Aliases
are append-only: a source dropping an alias never splits a vulnerability, which
would orphan its publication history.
"""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime

import structlog

from cvestream.domain.identity import AliasKind, alias_kind, primary_id
from cvestream.domain.model import (
    NormalizedRecord,
    RecordRef,
    Sourced,
    SourceRecord,
    Vulnerability,
)
from cvestream.pipeline.normalize import normalize
from cvestream.state.codec import content_hash
from cvestream.state.repository import Repository

log = structlog.get_logger()

# Only CVE and EUVD IDs merge two records (FR-7). GHSA and advisory IDs are kept
# for display, but never merge: a record without any of them is never merged.
MERGE_KINDS = frozenset({AliasKind.CVE, AliasKind.EUVD})


def merge_keys(record: NormalizedRecord) -> frozenset[str]:
    """The aliases of `record` that may merge vulnerabilities."""
    return frozenset(a for a in record.aliases if alias_kind(a) in MERGE_KINDS)


def activity_time(record: SourceRecord) -> datetime | None:
    """When the *source* last touched the record, `None` when it does not say.

    A CISA KEV entry added in 2021 has activity in 2021 even when first loaded
    today, so the first full catalogue load is not 1,800 fresh vulnerabilities.
    """
    if record.updated_at is not None:
        return record.updated_at
    if record.published_at is not None:
        return record.published_at
    added = [k.value.added_at for k in record.kev if k.value.added_at is not None]
    return max(added) if added else None


def build_vulnerability(records: Sequence[NormalizedRecord]) -> Vulnerability:
    """Merge normalized records into one vulnerability, losing no `Sourced` value."""
    ordered = sorted(records, key=lambda n: (n.record.source, n.record.record_id))
    aliases = frozenset(alias for n in ordered for alias in n.aliases)
    references: dict[tuple[str, str], Sourced[str]] = {}
    for n in ordered:
        for reference in n.record.references:
            references.setdefault(
                (reference, n.record.source),
                Sourced(reference, n.record.source, n.record.observed_at),
            )
    published = [
        n.record.published_at for n in ordered if n.record.published_at is not None
    ]
    updated = [n.record.updated_at for n in ordered if n.record.updated_at is not None]
    return Vulnerability(
        primary_id=primary_id(aliases),
        aliases=aliases,
        records=tuple(
            RecordRef(n.record.source, n.record.record_id, n.record.url)
            for n in ordered
        ),
        titles=tuple(n.record.title for n in ordered if n.record.title is not None),
        descriptions=tuple(
            n.record.description for n in ordered if n.record.description is not None
        ),
        scores=tuple(s for n in ordered for s in n.record.scores),
        affected=tuple(a for n in ordered for a in n.record.affected),
        kev=tuple(k for n in ordered for k in n.record.kev),
        references=tuple(references.values()),
        published_at=min(published) if published else None,
        updated_at=max(updated) if updated else None,
    )


def normalize_all(records: Iterable[SourceRecord]) -> list[NormalizedRecord]:
    """Normalized records; stored records always have a usable alias."""
    return [n for record in records if (n := normalize(record)) is not None]


@dataclass(frozen=True, slots=True)
class CorrelationResult:
    # Vulnerabilities whose content changed (or that were merged).
    touched: frozenset[int]
    # Records processed (skipped ones excluded).
    stored: int
    # Records inserted or with a new content hash.
    changed: int
    # Records without any usable alias.
    skipped: int


def correlate(
    repository: Repository, records: Iterable[SourceRecord], now: datetime
) -> CorrelationResult:
    """Store `records`, merge what they link, and report what changed."""
    touched: set[int] = set()
    stored_count = changed_count = skipped = 0
    for record in records:
        normalized = normalize(record)
        if normalized is None:
            skipped += 1
            log.debug(
                "pipeline.record_skipped",
                source=record.source,
                record_id=record.record_id,
            )
            continue
        stored_count += 1
        stored = repository.find_source_record(record.source, record.record_id)
        keys = merge_keys(normalized)
        candidates = repository.vulnerability_ids_for_aliases(keys)
        if stored is not None:
            candidates.add(stored.vulnerability_id)
        merged = len(candidates) > 1
        if not candidates:
            vulnerability_id = repository.create_vulnerability(now)
        else:
            # The oldest vulnerability survives.
            vulnerability_id = min(candidates)
            if merged:
                absorbed = candidates - {vulnerability_id}
                repository.merge_vulnerabilities(vulnerability_id, absorbed)
                touched -= absorbed
                log.info("pipeline.vulnerabilities_merged", count=len(absorbed))
        repository.add_aliases(vulnerability_id, keys)

        activity_at = activity_time(record) or now
        if stored is None:
            repository.insert_source_record(vulnerability_id, record, activity_at, now)
            changed = True
        elif (
            stored.content_hash != content_hash(record)
            or stored.vulnerability_id != vulnerability_id
        ):
            repository.update_source_record(
                stored, vulnerability_id, record, activity_at, now
            )
            changed = True
        else:
            repository.mark_seen(stored, now)
            changed = False
        if changed:
            changed_count += 1
        if changed or merged:
            touched.add(vulnerability_id)

    # One refresh per touched vulnerability, once all its records are stored;
    # decisions are made only after it.
    for vulnerability_id in sorted(touched):
        stored_records = repository.load_records([vulnerability_id])[vulnerability_id]
        aliases = frozenset(
            alias for n in normalize_all(stored_records) for alias in n.aliases
        )
        repository.refresh_vulnerability(
            vulnerability_id, primary_id(aliases), now, changed=True
        )
    return CorrelationResult(
        touched=frozenset(touched),
        stored=stored_count,
        changed=changed_count,
        skipped=skipped,
    )
