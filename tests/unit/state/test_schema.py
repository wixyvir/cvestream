from datetime import UTC, datetime, timedelta, timezone

import pytest
from sqlalchemy import Column, MetaData, Table, create_engine, insert, select
from sqlalchemy.exc import StatementError

from cvestream.state import schema
from cvestream.state.schema import (
    NaiveDatetimeError,
    StateError,
    UTCDateTime,
    format_utc,
    parse_utc,
)


def _names(table: str) -> set[str]:
    t = schema.metadata.tables[table]
    names = {c.name for c in t.constraints if c.name is not None}
    names |= {i.name for i in t.indexes if i.name is not None}
    return names


def test_the_six_tables_exist() -> None:
    assert set(schema.metadata.tables) == {
        "vulnerability",
        "alias",
        "source_record",
        "publication",
        "source_cursor",
        "feed_cache",
    }


def test_names_follow_the_convention() -> None:
    assert "pk_publication" in _names("publication")
    assert "ux_publication_guid" in _names("publication")
    assert "ux_source_record_source_record_id" in _names("source_record")
    assert "fk_publication_vulnerability_id_vulnerability" in _names("publication")
    assert "ix_publication_endpoint_id_published_at" in _names("publication")
    assert "ix_publication_endpoint_id_vulnerability_id" in _names("publication")
    assert "ux_alias_alias" in _names("alias")
    assert "ux_source_cursor_source" in _names("source_cursor")
    assert {"pk_feed_cache", "ux_feed_cache_endpoint_id"} <= _names("feed_cache")


def test_every_constraint_is_named() -> None:
    for table in schema.metadata.tables.values():
        for constraint in table.constraints:
            assert constraint.name is not None, (table.name, constraint)
        for index in table.indexes:
            assert index.name is not None, (table.name, index)


def test_format_utc_has_six_digits_and_orders_like_time() -> None:
    base = datetime(2026, 1, 1, tzinfo=UTC)
    texts = [
        format_utc(base),
        format_utc(base + timedelta(milliseconds=500)),
        format_utc(base + timedelta(seconds=1)),
    ]
    assert texts == [
        "2026-01-01T00:00:00.000000Z",
        "2026-01-01T00:00:00.500000Z",
        "2026-01-01T00:00:01.000000Z",
    ]
    assert texts == sorted(texts)


def test_format_utc_converts_offsets() -> None:
    plus_two = datetime(2026, 1, 1, 2, tzinfo=timezone(timedelta(hours=2)))
    assert format_utc(plus_two) == "2026-01-01T00:00:00.000000Z"


def test_parse_utc_round_trip() -> None:
    value = datetime(2026, 3, 4, 5, 6, 7, 123456, tzinfo=UTC)
    parsed = parse_utc(format_utc(value))
    assert parsed == value
    assert parsed.utcoffset() == timedelta(0)


@pytest.mark.parametrize("text", ["2026-01-01T00:00:00", "not a date"])
def test_parse_utc_rejects_naive_or_garbage(text: str) -> None:
    with pytest.raises(StateError):
        parse_utc(text)


def test_format_utc_rejects_naive() -> None:
    with pytest.raises(NaiveDatetimeError):
        format_utc(datetime(2026, 1, 1))  # noqa: DTZ001


_probe = Table("probe", MetaData(), Column("at", UTCDateTime))


def _round_trip(value: datetime | None) -> datetime | None:
    engine = create_engine("sqlite://")
    _probe.create(engine)
    with engine.begin() as connection:
        connection.execute(insert(_probe).values(at=value))
        return connection.execute(select(_probe.c.at)).scalar_one()


def test_utc_datetime_round_trips_as_utc() -> None:
    value = datetime(2026, 5, 6, 7, 8, 9, 10, tzinfo=UTC)
    result = _round_trip(value)
    assert result == value
    assert result is not None
    assert result.tzinfo is not None
    assert result.utcoffset() == timedelta(0)


def test_utc_datetime_converts_offsets_to_utc() -> None:
    value = datetime(2026, 5, 6, 9, tzinfo=timezone(timedelta(hours=2)))
    assert _round_trip(value) == datetime(2026, 5, 6, 7, tzinfo=UTC)


def test_utc_datetime_stores_none() -> None:
    assert _round_trip(None) is None


def test_utc_datetime_rejects_naive() -> None:
    # SQLAlchemy wraps errors raised while binding parameters
    with pytest.raises(StatementError) as excinfo:
        _round_trip(datetime(2026, 1, 1))  # noqa: DTZ001
    assert isinstance(excinfo.value.orig, NaiveDatetimeError)
