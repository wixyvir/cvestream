"""Table definitions: the only place tables are declared.

Portable column types only (`Integer`, `Text`, `String`, `Boolean`, `JSON`,
`UTCDateTime`). Constraint and index names come from `NAMING_CONVENTION`, never
from hand-written names. Queries live in `state/repository.py`.
"""

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    UniqueConstraint,
)
from sqlalchemy.engine import Dialect
from sqlalchemy.types import TypeDecorator, TypeEngine

from cvestream.errors import CvestreamError

NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    # unique constraints are named ux_*
    "uq": "ux_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

metadata = MetaData(naming_convention=NAMING_CONVENTION)

_UTC_FORMAT = "%Y-%m-%dT%H:%M:%S.%fZ"


class StateError(CvestreamError):
    """The state store failed or holds data this version cannot read."""


class NaiveDatetimeError(StateError):
    """A naive datetime reached the state layer: only aware datetimes are stored."""


def format_utc(value: datetime) -> str:
    """UTC text with 6 microsecond digits, so text order equals time order."""
    if value.tzinfo is None or value.utcoffset() is None:
        raise NaiveDatetimeError("naive datetime: only aware datetimes are stored")
    return value.astimezone(UTC).strftime(_UTC_FORMAT)


def parse_utc(text: str) -> datetime:
    """Aware UTC datetime from ISO 8601 text (the trailing `Z` is accepted)."""
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        raise StateError("stored datetime is not ISO 8601") from None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise StateError("stored datetime has no timezone")
    return parsed.astimezone(UTC)


class UTCDateTime(TypeDecorator[datetime]):
    """Aware UTC datetimes only: text on SQLite, `timestamptz` on PostgreSQL."""

    impl = String
    cache_ok = True

    def load_dialect_impl(self, dialect: Dialect) -> TypeEngine[Any]:
        if dialect.name == "postgresql":
            return dialect.type_descriptor(DateTime(timezone=True))
        return dialect.type_descriptor(String(32))

    def process_bind_param(
        self, value: datetime | None, dialect: Dialect
    ) -> datetime | str | None:
        if value is None:
            return None
        if value.tzinfo is None or value.utcoffset() is None:
            raise NaiveDatetimeError("naive datetime: only aware datetimes are stored")
        if dialect.name == "postgresql":
            return value.astimezone(UTC)
        return format_utc(value)

    def process_result_value(
        self, value: datetime | str | None, dialect: Dialect
    ) -> datetime | None:
        if value is None:
            return None
        if isinstance(value, datetime):
            return value.astimezone(UTC)
        return parse_utc(value)


vulnerability = Table(
    "vulnerability",
    metadata,
    Column("id", Integer, primary_key=True),
    # Current display ID, recomputed when aliases change.
    Column("primary_id", Text, nullable=False),
    # Latest source activity of its records.
    Column("activity_at", UTCDateTime, nullable=False),
    Column("created_at", UTCDateTime, nullable=False),
    # Last content change of any of its records.
    Column("changed_at", UTCDateTime, nullable=False),
    Index(None, "primary_id"),
    Index(None, "activity_at"),
)

alias = Table(
    "alias",
    metadata,
    Column("id", Integer, primary_key=True),
    # Normalized CVE or EUVD ID: merge keys only.
    Column("alias", Text, nullable=False),
    Column("vulnerability_id", ForeignKey("vulnerability.id"), nullable=False),
    UniqueConstraint("alias"),
    Index(None, "vulnerability_id"),
)

source_record = Table(
    "source_record",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("vulnerability_id", ForeignKey("vulnerability.id"), nullable=False),
    Column("source", Text, nullable=False),
    Column("record_id", Text, nullable=False),
    Column("record_json", JSON, nullable=False),
    # sha256 hex of the record without its observation times.
    Column("content_hash", Text, nullable=False),
    Column("activity_at", UTCDateTime, nullable=False),
    Column("first_seen_at", UTCDateTime, nullable=False),
    Column("changed_at", UTCDateTime, nullable=False),
    Column("last_seen_at", UTCDateTime, nullable=False),
    UniqueConstraint("source", "record_id"),
    Index(None, "vulnerability_id"),
)

publication = Table(
    "publication",
    metadata,
    Column("id", Integer, primary_key=True),
    # Config slug, no foreign key: endpoints live in the config provider.
    Column("endpoint_id", Text, nullable=False),
    Column("vulnerability_id", ForeignKey("vulnerability.id"), nullable=False),
    # `new`, `kev` or `band-<band>`.
    Column("kind", Text, nullable=False),
    # Computed once at decision time, never recomputed.
    Column("guid", Text, nullable=False),
    # The columns below record what was true at decision time.
    Column("primary_id", Text, nullable=False),
    Column("band", Text, nullable=False),
    Column("is_known_exploited", Boolean, nullable=False),
    Column("products_json", JSON, nullable=False),
    Column("labels_json", JSON, nullable=False),
    Column("published_at", UTCDateTime, nullable=False),
    UniqueConstraint("guid"),
    Index(None, "endpoint_id", "vulnerability_id"),
    Index(None, "endpoint_id", "published_at"),
)

source_cursor = Table(
    "source_cursor",
    metadata,
    Column("id", Integer, primary_key=True),
    # Adapter name: `euvd`, `cisa_kev`.
    Column("source", Text, nullable=False),
    Column("cursor", Text, nullable=True),
    Column("updated_at", UTCDateTime, nullable=False),
    UniqueConstraint("source"),
)

feed_cache = Table(
    "feed_cache",
    metadata,
    Column("id", Integer, primary_key=True),
    # Config slug, no foreign key (like `publication.endpoint_id`). Never a token.
    Column("endpoint_id", Text, nullable=False),
    # The RSS document, UTF-8 decoded.
    Column("xml", Text, nullable=False),
    # Quoted SHA-256 hex of the XML bytes.
    Column("etag", Text, nullable=False),
    Column("item_count", Integer, nullable=False),
    # Last render.
    Column("rendered_at", UTCDateTime, nullable=False),
    # Last time the ETag changed: the feed's `Last-Modified`.
    Column("changed_at", UTCDateTime, nullable=False),
    UniqueConstraint("endpoint_id"),
)
