"""Shared fixtures: deterministic clock, anyio backend and a temporary state."""

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from cvestream.state.db import State


class FakeClock:
    """Controllable `Clock` for tests. Aware UTC datetimes only."""

    def __init__(self, start: datetime | None = None) -> None:
        self._now = _require_aware(start or datetime(2026, 1, 1, tzinfo=UTC))

    def now(self) -> datetime:
        return self._now

    def advance(self, delta: timedelta) -> None:
        self._now += delta

    def set(self, value: datetime) -> None:
        self._now = _require_aware(value)


def _require_aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        msg = "FakeClock requires an aware datetime"
        raise ValueError(msg)
    return value


@pytest.fixture
def fake_clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def state(tmp_path: Path) -> Iterator[State]:
    """A migrated SQLite state database in a temporary directory."""
    database = State(f"sqlite:///{tmp_path / 'state.db'}")
    database.migrate()
    yield database
    database.dispose()
