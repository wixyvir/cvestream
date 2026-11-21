"""Every query of the state store.

A `Repository` is bound to one connection and is handed out only by
`State.transaction()`, which is the single commit point: this module never
commits, never logs and never opens a connection of its own. Portable SQL only:
upserts go through `_upsert()`, no raw SQL, no `RETURNING`.
"""

from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from itertools import batched

from sqlalchemy import Connection, Table, delete, func, insert, select, update
from sqlalchemy.dialects import postgresql, sqlite

from cvestream.domain.matcher import PassThrough
from cvestream.domain.model import SourceRecord
from cvestream.domain.publication import PastPublication, PublicationDecision
from cvestream.domain.severity import Band
from cvestream.state import schema
from cvestream.state.codec import content_hash, record_from_json, record_to_json
from cvestream.state.schema import StateError

# `IN (...)` lists are chunked: keeps PostgreSQL and very large backfills safe.
_CHUNK = 500


def _chunks[T](items: Iterable[T]) -> Iterator[tuple[T, ...]]:
    return batched(items, _CHUNK, strict=False)


def _upsert(
    connection: Connection,
    table: Table,
    values: Mapping[str, object],
    *,
    keys: Sequence[str],
    update: Sequence[str] = (),
) -> None:
    """Insert `values`; on a `keys` conflict update `update` or do nothing."""
    dialect = connection.dialect.name
    if dialect == "sqlite":
        statement = sqlite.insert(table).values(**values)
    elif dialect == "postgresql":
        statement = postgresql.insert(table).values(**values)
    else:
        raise StateError(f"upserts are not supported on dialect {dialect}")
    if update:
        statement = statement.on_conflict_do_update(
            index_elements=list(keys),
            set_={column: statement.excluded[column] for column in update},
        )
    else:
        statement = statement.on_conflict_do_nothing(index_elements=list(keys))
    connection.execute(statement)


@dataclass(frozen=True, slots=True)
class StoredRecord:
    id: int
    vulnerability_id: int
    content_hash: str


@dataclass(frozen=True, slots=True)
class StoredPublication:
    """One publication row: what was decided, as recorded at decision time."""

    vulnerability_id: int
    kind: str
    guid: str
    primary_id: str
    band: Band
    is_known_exploited: bool
    products: tuple[str, ...]
    labels: frozenset[PassThrough]
    published_at: datetime
    # `None` on rows older than the `is_fresh` column.
    is_fresh: bool | None


@dataclass(frozen=True, slots=True)
class StoredFeed:
    endpoint_id: str
    xml: str
    etag: str
    item_count: int
    rendered_at: datetime
    changed_at: datetime


class Repository:
    """Queries of one transaction. Create it with `State.transaction()`."""

    def __init__(self, connection: Connection) -> None:
        self._connection = connection

    # correlation

    def vulnerability_ids_for_aliases(self, aliases: Iterable[str]) -> set[int]:
        found: set[int] = set()
        for chunk in _chunks(set(aliases)):
            rows = self._connection.execute(
                select(schema.alias.c.vulnerability_id).where(
                    schema.alias.c.alias.in_(chunk)
                )
            )
            found.update(row[0] for row in rows)
        return found

    def find_source_record(self, source: str, record_id: str) -> StoredRecord | None:
        row = self._connection.execute(
            select(
                schema.source_record.c.id,
                schema.source_record.c.vulnerability_id,
                schema.source_record.c.content_hash,
            ).where(
                schema.source_record.c.source == source,
                schema.source_record.c.record_id == record_id,
            )
        ).one_or_none()
        if row is None:
            return None
        return StoredRecord(
            id=row.id,
            vulnerability_id=row.vulnerability_id,
            content_hash=row.content_hash,
        )

    def create_vulnerability(self, now: datetime) -> int:
        # `primary_id` is a placeholder: `refresh_vulnerability` sets it before
        # the transaction ends (every created vulnerability gets a record).
        result = self._connection.execute(
            insert(schema.vulnerability).values(
                primary_id="", activity_at=now, created_at=now, changed_at=now
            )
        )
        primary_key = result.inserted_primary_key
        if primary_key is None:
            raise StateError("insert did not return a primary key")
        return int(primary_key[0])

    def merge_vulnerabilities(self, survivor: int, absorbed: Iterable[int]) -> None:
        """Re-point aliases, source records and publications, then delete."""
        ids = sorted(set(absorbed) - {survivor})
        for chunk in _chunks(ids):
            for table in (schema.alias, schema.source_record, schema.publication):
                self._connection.execute(
                    update(table)
                    .where(table.c.vulnerability_id.in_(chunk))
                    .values(vulnerability_id=survivor)
                )
            self._connection.execute(
                delete(schema.vulnerability).where(schema.vulnerability.c.id.in_(chunk))
            )

    def add_aliases(self, vulnerability_id: int, aliases: Iterable[str]) -> None:
        for alias in sorted(set(aliases)):
            _upsert(
                self._connection,
                schema.alias,
                {"alias": alias, "vulnerability_id": vulnerability_id},
                keys=["alias"],
            )

    def insert_source_record(
        self,
        vulnerability_id: int,
        record: SourceRecord,
        activity_at: datetime,
        now: datetime,
    ) -> None:
        self._connection.execute(
            insert(schema.source_record).values(
                vulnerability_id=vulnerability_id,
                source=record.source,
                record_id=record.record_id,
                record_json=record_to_json(record),
                content_hash=content_hash(record),
                activity_at=activity_at,
                first_seen_at=now,
                changed_at=now,
                last_seen_at=now,
            )
        )

    def update_source_record(
        self,
        stored: StoredRecord,
        vulnerability_id: int,
        record: SourceRecord,
        activity_at: datetime,
        now: datetime,
    ) -> None:
        self._connection.execute(
            update(schema.source_record)
            .where(schema.source_record.c.id == stored.id)
            .values(
                vulnerability_id=vulnerability_id,
                record_json=record_to_json(record),
                content_hash=content_hash(record),
                activity_at=activity_at,
                changed_at=now,
                last_seen_at=now,
            )
        )

    def mark_seen(self, stored: StoredRecord, now: datetime) -> None:
        self._connection.execute(
            update(schema.source_record)
            .where(schema.source_record.c.id == stored.id)
            .values(last_seen_at=now)
        )

    def refresh_vulnerability(
        self, vulnerability_id: int, primary_id: str, now: datetime, *, changed: bool
    ) -> None:
        activity_at = self._connection.execute(
            select(func.max(schema.source_record.c.activity_at)).where(
                schema.source_record.c.vulnerability_id == vulnerability_id
            )
        ).scalar_one()
        values: dict[str, object] = {
            "primary_id": primary_id,
            "activity_at": now if activity_at is None else activity_at,
        }
        if changed:
            values["changed_at"] = now
        self._connection.execute(
            update(schema.vulnerability)
            .where(schema.vulnerability.c.id == vulnerability_id)
            .values(**values)
        )

    # decisions

    def load_records(
        self, vulnerability_ids: Iterable[int]
    ) -> dict[int, list[SourceRecord]]:
        """Records per vulnerability, in `(source, record_id)` order."""
        records: dict[int, list[SourceRecord]] = {}
        for chunk in _chunks(set(vulnerability_ids)):
            rows = self._connection.execute(
                select(
                    schema.source_record.c.vulnerability_id,
                    schema.source_record.c.record_json,
                )
                .where(schema.source_record.c.vulnerability_id.in_(chunk))
                .order_by(
                    schema.source_record.c.source, schema.source_record.c.record_id
                )
            )
            for row in rows:
                records.setdefault(row.vulnerability_id, []).append(
                    record_from_json(row.record_json)
                )
        return records

    def active_since(self, since: datetime) -> list[int]:
        """Vulnerabilities whose source activity is at or after `since`."""
        rows = self._connection.execute(
            select(schema.vulnerability.c.id)
            .where(schema.vulnerability.c.activity_at >= since)
            .order_by(schema.vulnerability.c.id)
        )
        return [row[0] for row in rows]

    def filter_active_since(
        self, vulnerability_ids: Iterable[int], since: datetime
    ) -> list[int]:
        found: list[int] = []
        for chunk in _chunks(sorted(set(vulnerability_ids))):
            rows = self._connection.execute(
                select(schema.vulnerability.c.id)
                .where(
                    schema.vulnerability.c.id.in_(chunk),
                    schema.vulnerability.c.activity_at >= since,
                )
                .order_by(schema.vulnerability.c.id)
            )
            found.extend(row[0] for row in rows)
        return found

    def endpoints_with_publications(self, endpoint_ids: Iterable[str]) -> set[str]:
        found: set[str] = set()
        for chunk in _chunks(set(endpoint_ids)):
            rows = self._connection.execute(
                select(schema.publication.c.endpoint_id)
                .where(schema.publication.c.endpoint_id.in_(chunk))
                .distinct()
            )
            found.update(row[0] for row in rows)
        return found

    def publication_history(
        self, endpoint_ids: Iterable[str], vulnerability_ids: Iterable[int]
    ) -> dict[tuple[str, int], list[PastPublication]]:
        endpoints = set(endpoint_ids)
        history: dict[tuple[str, int], list[PastPublication]] = {}
        if not endpoints:
            return history
        for chunk in _chunks(set(vulnerability_ids)):
            rows = self._connection.execute(
                select(
                    schema.publication.c.endpoint_id,
                    schema.publication.c.vulnerability_id,
                    schema.publication.c.kind,
                    schema.publication.c.guid,
                    schema.publication.c.band,
                    schema.publication.c.is_known_exploited,
                )
                .where(
                    schema.publication.c.vulnerability_id.in_(chunk),
                    schema.publication.c.endpoint_id.in_(endpoints),
                )
                .order_by(schema.publication.c.id)
            )
            for row in rows:
                history.setdefault((row.endpoint_id, row.vulnerability_id), []).append(
                    PastPublication(
                        kind=row.kind,
                        guid=row.guid,
                        band=Band(row.band),
                        is_known_exploited=row.is_known_exploited,
                    )
                )
        return history

    def publication_guid_exists(self, guid: str) -> bool:
        """Whether any endpoint and vulnerability already published `guid`."""
        row = self._connection.execute(
            select(schema.publication.c.id)
            .where(schema.publication.c.guid == guid)
            .limit(1)
        ).first()
        return row is not None

    def add_publication(
        self,
        vulnerability_id: int,
        decision: PublicationDecision,
        published_at: datetime,
    ) -> None:
        self._connection.execute(
            insert(schema.publication).values(
                endpoint_id=decision.endpoint_id,
                vulnerability_id=vulnerability_id,
                kind=decision.kind,
                guid=decision.guid,
                primary_id=decision.primary_id,
                band=decision.band.value,
                is_known_exploited=decision.is_known_exploited,
                is_fresh=decision.is_fresh,
                products_json=list(decision.products),
                labels_json=sorted(label.value for label in decision.labels),
                published_at=published_at,
            )
        )

    # feeds

    def publications_since(
        self, endpoint_ids: Iterable[str], since: datetime
    ) -> dict[str, list[StoredPublication]]:
        """Publications at or after `since` per endpoint, newest first."""
        found: dict[str, list[StoredPublication]] = {}
        for chunk in _chunks(sorted(set(endpoint_ids))):
            rows = self._connection.execute(
                select(
                    schema.publication.c.endpoint_id,
                    schema.publication.c.vulnerability_id,
                    schema.publication.c.kind,
                    schema.publication.c.guid,
                    schema.publication.c.primary_id,
                    schema.publication.c.band,
                    schema.publication.c.is_known_exploited,
                    schema.publication.c.is_fresh,
                    schema.publication.c.products_json,
                    schema.publication.c.labels_json,
                    schema.publication.c.published_at,
                )
                .where(
                    schema.publication.c.endpoint_id.in_(chunk),
                    schema.publication.c.published_at >= since,
                )
                .order_by(
                    schema.publication.c.published_at.desc(),
                    schema.publication.c.id.desc(),
                )
            )
            for row in rows:
                found.setdefault(row.endpoint_id, []).append(
                    StoredPublication(
                        vulnerability_id=row.vulnerability_id,
                        kind=row.kind,
                        guid=row.guid,
                        primary_id=row.primary_id,
                        band=Band(row.band),
                        is_known_exploited=row.is_known_exploited,
                        products=tuple(row.products_json),
                        labels=frozenset(PassThrough(v) for v in row.labels_json),
                        published_at=row.published_at,
                        is_fresh=row.is_fresh,
                    )
                )
        return found

    def feeds(self) -> dict[str, StoredFeed]:
        rows = self._connection.execute(select(schema.feed_cache))
        return {
            row.endpoint_id: StoredFeed(
                endpoint_id=row.endpoint_id,
                xml=row.xml,
                etag=row.etag,
                item_count=row.item_count,
                rendered_at=row.rendered_at,
                changed_at=row.changed_at,
            )
            for row in rows
        }

    def save_feed(self, feed: StoredFeed) -> None:
        _upsert(
            self._connection,
            schema.feed_cache,
            {
                "endpoint_id": feed.endpoint_id,
                "xml": feed.xml,
                "etag": feed.etag,
                "item_count": feed.item_count,
                "rendered_at": feed.rendered_at,
                "changed_at": feed.changed_at,
            },
            keys=["endpoint_id"],
            update=["xml", "etag", "item_count", "rendered_at", "changed_at"],
        )

    # cursors

    def cursors(self) -> dict[str, str | None]:
        rows = self._connection.execute(
            select(schema.source_cursor.c.source, schema.source_cursor.c.cursor)
        )
        return {row.source: row.cursor for row in rows}

    def set_cursor(self, source: str, cursor: str | None, now: datetime) -> None:
        _upsert(
            self._connection,
            schema.source_cursor,
            {"source": source, "cursor": cursor, "updated_at": now},
            keys=["source"],
            update=["cursor", "updated_at"],
        )
